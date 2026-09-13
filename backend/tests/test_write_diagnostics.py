"""S2 原生修改的准确拒绝诊断，不放开服务占用规则。"""
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from test_workspace_commands import command_root, isolated_command_database, command_conversation
from test_service_discovery import seed_entries, clear_entries


async def mutation_tools(client, headers, cid, rid, wid, *, query=True):
    """通过真实配置/工厂建立测试执行，返回实际工具集合。

    Args:
        client：本轮 API 客户端。
        headers：Owner 认证。
        cid：当前会话。
        rid：当前角色。
        wid：绑定工作区。
        query：是否显式启用状态查询工具。
    """
    from app.db import SessionLocal
    from app.models import Role, Generation, AgentExecution
    from app.workspaces.tools import create_workspace_tools
    await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
    async with SessionLocal() as session:
        role = await session.get(Role, rid)
        role.builtin_tools_json = ['workspace_write', 'workspace_edit', *(['workspace_service_status'] if query else [])]
        generation = Generation(conversation_id=cid, status='running', stream_epoch='diagnostics-test')
        session.add(generation)
        await session.flush()
        execution = AgentExecution(execution_id=uuid4().hex, conversation_id=cid, generation_id=generation.id,
            role_id=rid, chain_id='diagnostics-test', execution_kind='single', status='running', created_at=datetime.now(timezone.utc))
        session.add(execution)
        await session.commit()
        tools = await create_workspace_tools(session, execution_id=execution.execution_id, conversation_id=cid, role=role,
            triggered_by_user_id=role.created_by, allow_dangerous=True)
        return {tool.name: tool for tool in tools}, role.created_by, execution.execution_id


def body(output):
    """Args:
        output：工具的固定前缀与 JSON；不将原文附入失败日志。
    """
    return json.loads(output.split('] ', 1)[1])


@pytest.mark.anyio
@pytest.mark.parametrize('state,code', [('ready', 'WORKSPACE_SERVICE_ACTIVE'), ('stopping', 'WORKSPACE_SERVICE_STOPPING'),
                                      ('cleanup_required', 'WORKSPACE_CLEANUP_REQUIRED')])
async def test_write_diagnostic_reports_real_service_blocker(command_root, isolated_command_database, state, code):
    """Args:
        command_root：受控空目录。
        isolated_command_database：每项独立数据库。
        state：阻塞服务的真实登记状态。
        code：应返回的固定原因。
    """
    from app.db import SessionLocal
    from app.runtime.models import RuntimeEntry
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        tools, owner, _ = await mutation_tools(client, headers, cid, rid, wid)
        await seed_entries(owner, cid, wid, count=1)
        try:
            async with SessionLocal() as session:
                (await session.get(RuntimeEntry, f'{1:032x}')).state = state
                await session.commit()
            result = body(await tools['workspace_write'].ainvoke({'path': 'new.txt', 'content': 'test'}))
            assert result['error_code'] == code
            diagnostic = result['diagnostic']
            assert diagnostic['executed'] is False and diagnostic['scope'] == 'workspace'
            assert diagnostic['services'] == [{'runtime_id': f'{1:032x}', 'state': state}]
            assert diagnostic['recommended_tool'] == 'workspace_service_status'
            assert not (command_root / 'new.txt').exists()
        finally:
            await clear_entries()


@pytest.mark.anyio
@pytest.mark.parametrize('query', [True, False])
async def test_diagnostics_do_not_expose_foreign_ids_or_missing_tools(command_root, isolated_command_database, query):
    """Args:
        command_root：受控目录。
        isolated_command_database：独立库。
        query：是否确实向本轮模型暴露状态查询。
    """
    from app.db import SessionLocal
    from app.runtime.models import RuntimeEntry
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        tools, owner, _ = await mutation_tools(client, headers, cid, rid, wid, query=query)
        other = (await client.post('/api/conversations', headers=headers, json={'title': 'PRIVATE-OTHER-CONVERSATION',
            'type': 'single', 'role_ids': [rid]})).json()['id']
        await seed_entries(owner, cid, wid, count=6)
        try:
            async with SessionLocal() as session:
                foreign = await session.get(RuntimeEntry, f'{6:032x}')
                foreign.conversation_id = foreign.conversation_ref_id = other
                foreign.state = 'cleanup_required'
                await session.commit()
            result = body(await tools['workspace_write'].ainvoke({'path': 'new.txt', 'content': 'x'}))
            value = result['diagnostic']
            assert result['error_code'] == 'WORKSPACE_CLEANUP_REQUIRED'
            assert value['other_sessions_blocking'] is True
            assert f'{6:032x}' not in json.dumps(result) and 'PRIVATE-OTHER-CONVERSATION' not in json.dumps(result)
            assert len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()) <= 2048
            if query:
                assert len(value['services']) == 3 and value['services_truncated']
            else:
                assert value['services'] is None and value['recommended_tool'] is None
                assert 'workspace_service_status' not in json.dumps(value) and '/ps' in json.dumps(value)
        finally:
            await clear_entries()


@pytest.mark.anyio
@pytest.mark.parametrize('mode,expected', [('world_closing', 'WORKSPACE_SCOPE_CLOSING'),
    ('scope_cleanup', 'WORKSPACE_SCOPE_CLEANUP'), ('failed_cleanup', 'WORKSPACE_CLEANUP_REQUIRED')])
async def test_diagnostic_scope_priority(command_root, isolated_command_database, mode, expected):
    """Args:
        command_root：受控目录。
        isolated_command_database：独立库。
        mode：与普通运行服务并存的生命周期门槛。
        expected：最高优先级的实际拒绝码。
    """
    from sqlalchemy import delete
    from app.db import SessionLocal
    from app.runtime.models import RuntimeGate, CleanupOperation
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        tools, owner, _ = await mutation_tools(client, headers, cid, rid, wid)
        await seed_entries(owner, cid, wid, count=1)
        try:
            async with SessionLocal() as session:
                gate = await session.get(RuntimeGate, 1)
                gate.closing = mode != 'scope_cleanup'
                if mode != 'world_closing':
                    session.add(CleanupOperation(id='diagnostic-fixture', scope='world', scope_id=0, reason='test',
                        process_instance_id='test', state='failed' if mode == 'failed_cleanup' else 'running',
                        target_count=0, created_at=datetime.now(timezone.utc)))
                await session.commit()
            result = body(await tools['workspace_edit'].ainvoke({'path': 'missing.txt', 'old_text': 'a', 'new_text': 'b', 'expected_sha256': '0' * 64}))
            assert result['error_code'] == expected and result['diagnostic']['scope'] == 'world'
        finally:
            await clear_entries()
            async with SessionLocal() as session:
                await session.execute(delete(CleanupOperation).where(CleanupOperation.id == 'diagnostic-fixture'))
                (await session.get(RuntimeGate, 1)).closing = False
                await session.commit()


@pytest.mark.anyio
@pytest.mark.parametrize('change,expected', [('capability', 'WORKSPACE_TOOL_CAPABILITY_CHANGED'),
    ('role_tool', 'WORKSPACE_TOOL_CAPABILITY_CHANGED'), ('binding', 'WORKSPACE_BINDING_CHANGED'),
    ('lease', 'WORKSPACE_LEASE_UNAVAILABLE'), ('member', 'WORKSPACE_TOOL_NOT_AVAILABLE'), ('owner', 'WORKSPACE_TOOL_NOT_AVAILABLE')])
async def test_diagnostic_permission_and_binding_changes(command_root, isolated_command_database, change, expected):
    """Args:
        command_root：受控目录。
        isolated_command_database：每个权限变体独立库。
        change：创建工具后改变的条件。
        expected：应披露的原因；身份/归属失效不附加资源诊断。
    """
    from sqlalchemy import delete, select
    from app.db import SessionLocal
    from app.models import WorkspaceBinding, Role, Conversation, ConversationMember, ExecutionWorkspace, User
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        tools, _, execution_id = await mutation_tools(client, headers, cid, rid, wid)
        async with SessionLocal() as session:
            if change == 'capability':
                (await session.get(WorkspaceBinding, wid)).file_tools_enabled = False
            elif change == 'owner':
                foreign = User(username='diagnostic-foreign', nickname='测试外部账号', password_hash='not-a-real-password-hash',
                    is_owner=False, created_at=datetime.now(timezone.utc))
                session.add(foreign)
                await session.flush()
                (await session.get(WorkspaceBinding, wid)).created_by = foreign.id
            elif change == 'role_tool':
                (await session.get(Role, rid)).builtin_tools_json = ['workspace_service_status']
            elif change == 'binding':
                (await session.get(Conversation, cid)).workspace_binding_id = None
            elif change == 'lease':
                lease = await session.scalar(select(ExecutionWorkspace).where(ExecutionWorkspace.execution_id == execution_id))
                lease.status = 'retained'
            else:
                await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == cid,
                    ConversationMember.member_type == 'user'))
            await session.commit()
        result = body(await tools['workspace_write'].ainvoke({'path': 'new.txt', 'content': 'x'}))
        assert result['error_code'] == expected
        if change in {'owner', 'member'}:
            assert 'diagnostic' not in result
        else:
            assert result['diagnostic']['services'] is None
        assert not (command_root / 'new.txt').exists()


@pytest.mark.anyio
async def test_batch_stops_after_new_blocker_preserving_first_write(command_root, isolated_command_database, monkeypatch):
    """Args:
        command_root：本轮真实文件目录。
        isolated_command_database：隔离库。
        monkeypatch：两项预检和首项提交后出现新的服务登记。
    """
    from app.workspaces import tools as module
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        tools, owner, _ = await mutation_tools(client, headers, cid, rid, wid)
        original = module._service_decision
        calls = 0
        async def changed(**identity):
            """Args:
                identity：宿主身份保持不变，仅改变真实登记状态。
            """
            nonlocal calls
            calls += 1
            if calls == 4:
                await seed_entries(owner, cid, wid, count=1)
            return await original(**identity)
        monkeypatch.setattr(module, '_service_decision', changed)
        try:
            result = body(await tools['workspace_write'].ainvoke({'items': [
                {'path': 'a.txt', 'content': 'first'}, {'path': 'b.txt', 'content': 'second'}]}))
            assert result['status'] == 'partial' and result['error_code'] == 'WORKSPACE_BATCH_PARTIAL'
            assert result['items'][0]['applied'] is True and 'diagnostic' not in result['items'][0]
            assert result['items'][1]['applied'] is False and result['items'][1]['diagnostic']['executed'] is False
            assert result['items'][1]['error_code'] == 'WORKSPACE_SERVICE_ACTIVE'
            assert 'diagnostic' not in result
            assert (command_root / 'a.txt').read_text() == 'first' and not (command_root / 'b.txt').exists()
        finally:
            await clear_entries()


@pytest.mark.anyio
async def test_diagnostic_is_private_in_real_agent_pipeline(command_root, isolated_command_database):
    """Args:
        command_root：不应发生写入的受控目录。
        isolated_command_database：独立消息/工具详情数据库。
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import Role, EventLog
    from test_workspace_commands import send_command
    from test_workspace_edit import wait_reply
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
        async with SessionLocal() as session:
            role = await session.get(Role, rid)
            owner = role.created_by
            role.builtin_tools_json = ['workspace_write', 'workspace_edit', 'workspace_service_status']
            await session.commit()
        await seed_entries(owner, cid, wid, count=1)
        try:
            sent = await send_command(client, headers, cid, '[WRITE_DIAGNOSTIC_FAKE]')
            message = await wait_reply(client, headers, cid, sent['message']['id'])
            calls = [part for part in message['parts_json'] if part['type'] == 'tool_call']
            assert len(calls) == 2 and all(call['error_code'] == 'WORKSPACE_SERVICE_ACTIVE' for call in calls)
            for call in calls:
                detail = (await client.get(f"/api/conversations/{cid}/messages/{message['id']}/tools/{call['call_id']}", headers=headers)).json()
                value = detail.get('diagnostic') or detail['write_batch']['items'][0]['diagnostic']
                assert value['reason'] == 'service_active' and value['services'][0]['runtime_id'] == f'{1:032x}'
            async with SessionLocal() as session:
                for event in (await session.scalars(select(EventLog).where(EventLog.conversation_id == cid))).all():
                    assert f'{1:032x}' not in json.dumps(event.payload_json)
                    assert 'next_steps' not in json.dumps(event.payload_json)
            assert not (command_root / 'blocked.txt').exists()
        finally:
            await clear_entries()


@pytest.mark.anyio
@pytest.mark.parametrize('initial_query', [True, False])
async def test_diagnostic_recommends_only_current_and_exposed_query(command_root, isolated_command_database, initial_query):
    """Args:
        command_root：受控目录。
        isolated_command_database：独立库。
        initial_query：创建时与调用时权限相反，交集都不应包含 status。
    """
    from app.db import SessionLocal
    from app.models import Role
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        tools, owner, _ = await mutation_tools(client, headers, cid, rid, wid, query=initial_query)
        async with SessionLocal() as session:
            role = await session.get(Role, rid)
            role.builtin_tools_json = ['workspace_write', 'workspace_edit', *([] if initial_query else ['workspace_service_status'])]
            await session.commit()
        await seed_entries(owner, cid, wid, count=1)
        try:
            value = body(await tools['workspace_write'].ainvoke({'path': 'new.txt', 'content': 'x'}))['diagnostic']
            assert value['services'] is None and value['recommended_tool'] is None
            assert 'workspace_service_status' not in json.dumps(value)
        finally:
            await clear_entries()


@pytest.mark.anyio
async def test_directory_unavailable_is_not_reported_as_service_block(command_root, isolated_command_database, monkeypatch):
    """Args:
        command_root：目录原本可租用。
        isolated_command_database：独立库。
        monkeypatch：在创建工具之后模拟目录复核失败。
    """
    from app.workspaces import tools as module
    from app.workspaces.paths import WorkspacePathError
    def missing(binding):
        """Args:
            binding：本次已归属校验的工作区记录。
        """
        raise WorkspacePathError('WORKSPACE_ROOT_NOT_AVAILABLE')
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        tools, _, _ = await mutation_tools(client, headers, cid, rid, wid)
        monkeypatch.setattr(module, 'binding_root', missing)
        value = body(await tools['workspace_write'].ainvoke({'path': 'new.txt', 'content': 'x'}))
        assert value['error_code'] == 'WORKSPACE_UNAVAILABLE'
        assert value['diagnostic']['reason'] == 'workspace_unavailable'
        assert value['diagnostic']['services'] is None and not (command_root / 'new.txt').exists()
