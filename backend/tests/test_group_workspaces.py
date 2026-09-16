"""群聊文件权限、串行租用交接与停止边界。"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from test_workspace_commands import command_root, isolated_command_database, command_conversation


@asynccontextmanager
async def group_workspace(root):
    """真实 REST 创建写入与编辑角色，额外开启命令以验证群聊不会暴露它。"""
    async with command_conversation(root) as (client, headers, _, rid, wid):
        first = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
        await client.put(f'/api/roles/{rid}', headers=headers, json={**first,
            'builtin_tools': ['workspace_write', 'workspace_run_command', 'workspace_run_shell', 'workspace_service_status']})
        second = (await client.post('/api/roles', headers=headers, json={
            'name': '群聊编辑角色', 'model_config_id': first['model_config_id'], 'model_name': 'fake-model',
            'system_prompt': '编辑前一角色的文件', 'builtin_tools': ['workspace_read', 'workspace_edit'],
        })).json()['id']
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
        group = await client.post('/api/conversations', headers=headers, json={
            'title': '群聊文件测试', 'type': 'group', 'role_ids': [rid, second], 'workspace_binding_id': wid})
        assert group.status_code == 201
        yield client, headers, group.json()['id'], rid, second, wid


async def send(client, headers, cid, roles):
    """通过消息入口触发真实队列。"""
    response = await client.post(f'/api/conversations/{cid}/messages', headers=headers, json={
        'parts': [{'type': 'text', 'text': '[GROUP_FILES_FAKE]'}], 'mentions': roles})
    assert response.status_code == 202
    return response.json()


async def finished(client, headers, cid):
    """同时等消息链和租用终结，避免消息终态早于 finally 收口。"""
    from app.db import SessionLocal
    from app.models import AgentExecution, ExecutionWorkspace
    for _ in range(400):
        history = (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()
        async with SessionLocal() as session:
            ready = await session.scalar(select(ExecutionWorkspace.id).join(AgentExecution,
                AgentExecution.execution_id == ExecutionWorkspace.execution_id).where(
                AgentExecution.conversation_id == cid, ExecutionWorkspace.status == 'ready'))
        if not history['active_generation_ids'] and ready is None:
            return history['items']
        await asyncio.sleep(.02)
    raise AssertionError('群聊任务未收口')


@pytest.mark.anyio
async def test_group_serial_files_and_guest_denial(command_root, isolated_command_database):
    from app.db import SessionLocal
    from app.models import AgentExecution, ExecutionWorkspace, ConversationMember
    from accounts import guest_username, TEST_PASSWORD
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        await send(client, headers, cid, [writer, editor])
        messages = await finished(client, headers, cid)
        assert (command_root / 'group.txt').read_text() == 'second'
        replies = [row for row in messages if row['sender_type'] == 'role']
        assert [row['sender_id'] for row in replies] == [writer, editor]
        assert all(row['status'] == 'done' for row in replies)
        assert [[p['tool_name'] for p in row['parts_json'] if p['type'] == 'tool_call'] for row in replies] == [
            ['workspace_write'], ['workspace_read', 'workspace_edit']]
        async with SessionLocal() as session:
            executions = list((await session.scalars(select(AgentExecution).where(
                AgentExecution.conversation_id == cid).order_by(AgentExecution.id))).all())
            assert len(executions) == 2 and all(row.execution_kind == 'group_role' for row in executions)
            assert executions[0].ended_at <= executions[1].started_at
            leases = list((await session.scalars(select(ExecutionWorkspace).where(
                ExecutionWorkspace.execution_id.in_([row.execution_id for row in executions])))).all())
            assert len(leases) == 2 and all(row.status == 'retained' for row in leases)
        guest = (await client.post('/api/auth/register', json={'username': guest_username('group_files'),
            'password': TEST_PASSWORD, 'nickname': 'Guest'})).json()
        gh = {'Authorization': f"Bearer {guest['access_token']}"}
        async with SessionLocal() as session:
            session.add(ConversationMember(conversation_id=cid, member_type='user', member_id=guest['user']['id'], joined_at=datetime.now(timezone.utc)))
            await session.commit()
        assert (await client.put(f'/api/conversations/{cid}/workspace', headers=gh,
            json={'workspace_binding_id': None, 'expected_revision': 0})).status_code == 403
        await send(client, gh, cid, [writer, editor])
        guest_replies = [row for row in await finished(client, gh, cid) if row['sender_type'] == 'role'][-2:]
        assert all(not any(p['type'] == 'tool_call' for p in row['parts_json']) for row in guest_replies)
        assert (command_root / 'group.txt').read_text() == 'second'


@pytest.mark.anyio
@pytest.mark.parametrize('revoke', ['role_member', 'user_member', 'role_tool', 'workspace_tool', 'stop', 'execution_kind'])
async def test_group_file_call_rechecks_authority(command_root, isolated_command_database, revoke):
    from app.db import SessionLocal
    from app.models import Role, Conversation, ConversationMember, AgentExecution, Generation, WorkspaceBinding
    from app.workspaces.tools import create_workspace_tools, workspace_tool_policy, retain_execution_workspace
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        async with SessionLocal() as session:
            role = await session.get(Role, writer)
            owner_id = role.created_by
            generation = Generation(conversation_id=cid, status='running', stream_epoch='test')
            session.add(generation); await session.flush()
            execution = AgentExecution(execution_id=uuid4().hex, conversation_id=cid, generation_id=generation.id,
                role_id=writer, chain_id=uuid4().hex, execution_kind='group_role', status='running', created_at=datetime.now(timezone.utc))
            session.add(execution); await session.commit()
            eid, gid = execution.execution_id, generation.id
            policy = await workspace_tool_policy(session, conversation=await session.get(Conversation, cid), role=role, triggered_by_user_id=owner_id)
            assert [row['name'] for row in policy['exposed_tools']] == ['workspace_write']
            tools = await create_workspace_tools(session, execution_id=eid, conversation_id=cid,
                role=role, triggered_by_user_id=owner_id, allow_dangerous=True)
            assert [tool.name for tool in tools] == ['workspace_write']
        async with SessionLocal() as session:
            if revoke in ['role_member', 'user_member']:
                kind, member = ('role', writer) if revoke == 'role_member' else ('user', owner_id)
                await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == cid,
                    ConversationMember.member_type == kind, ConversationMember.member_id == member))
            elif revoke == 'role_tool':
                (await session.get(Role, writer)).builtin_tools_json = []
            elif revoke == 'workspace_tool':
                (await session.get(WorkspaceBinding, wid)).file_tools_enabled = False
            elif revoke == 'stop':
                (await session.get(Generation, gid)).stop_requested_at = datetime.now(timezone.utc)
            else:
                row = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == eid))
                row.execution_kind = 'single'
            await session.commit()
        result = await tools[0].ainvoke({'path': 'denied.txt', 'content': 'must-not-write'})
        assert 'WORKSPACE_TOOL_NOT_AVAILABLE' in result or 'WORKSPACE_TOOL_CAPABILITY_CHANGED' in result
        assert not (command_root / 'denied.txt').exists()
        await retain_execution_workspace(eid)


@pytest.mark.anyio
async def test_group_stop_preserves_write_and_blocks_next_role_and_rebind(command_root, isolated_command_database, monkeypatch):
    from app.agent.fake_provider import GroupFileModel
    from app.services import chat
    reached, release = asyncio.Event(), asyncio.Event()

    class PausedModel(GroupFileModel):
        async def _astream(self, messages, **kwargs):
            if self.index == 1:
                reached.set()
                await release.wait()
            async for chunk in super()._astream(messages, **kwargs):
                yield chunk

    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: PausedModel(delay=0))
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        await send(client, headers, cid, [writer, editor])
        try:
            await asyncio.wait_for(reached.wait(), 10)
            rejected = await client.put(f'/api/conversations/{cid}/workspace', headers=headers,
                json={'workspace_binding_id': None, 'expected_revision': 0})
            assert rejected.status_code == 409 and rejected.json()['error']['code'] == 'WORKSPACE_BUSY'
            assert (await client.post(f'/api/conversations/{cid}/stop', headers=headers)).status_code == 202
            messages = await finished(client, headers, cid)
            assert (command_root / 'group.txt').read_text() == 'first'
            assert [row['sender_id'] for row in messages if row['sender_type'] == 'role'] == [writer]
            unbound = await client.put(f'/api/conversations/{cid}/workspace', headers=headers,
                json={'workspace_binding_id': None, 'expected_revision': 0})
            assert unbound.status_code == 200
        finally:
            release.set()


@pytest.mark.anyio
async def test_concurrent_group_rebind_uses_one_revision(command_root, isolated_command_database):
    """两个窗口同时解绑只有一个成功，不静默覆盖版本。"""
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        results = await asyncio.gather(*[client.put(f'/api/conversations/{cid}/workspace', headers=headers,
            json={'workspace_binding_id': None, 'expected_revision': 0}) for _ in range(2)])
        assert sorted(row.status_code for row in results) == [200, 409]
        conflict = next(row for row in results if row.status_code == 409)
        assert conflict.json()['error']['code'] == 'CONVERSATION_REVISION_CONFLICT'
        current = next(row for row in (await client.get('/api/conversations', headers=headers)).json() if row['id'] == cid)
        assert current['revision'] == 1 and current['workspace_binding_id'] is None
