"""S1：当前会话服务发现，不依赖可写工作区租用。"""
import json
from datetime import datetime, timezone

import pytest
from test_workspace_commands import command_root, isolated_command_database, command_conversation


def test_service_status_schema_has_list_and_legacy_modes():
    """空参数列服务，旧 ID 查询兼容，两种参数形式不可混合。"""
    from pydantic import ValidationError
    from app.workspaces.service_query import ServiceStatusInput
    assert ServiceStatusInput().limit == 50
    assert ServiceStatusInput(runtime_id='a' * 32).runtime_id == 'a' * 32
    for args in [{'runtime_id': None}, {'runtime_id': ''}, {'runtime_id': 'a' * 32, 'limit': 50},
                 {'runtime_id': 'a' * 32, 'cursor': None}, {'limit': True}, {'limit': 101}, {'conversation_id': 1}]:
        with pytest.raises(ValidationError):
            ServiceStatusInput.model_validate(args)


async def execution_tools(cid, rid):
    """创建受控运行中 execution，通过真实工厂取得工具。

    Args:
        cid：本轮会话。
        rid：显式启用 status 的角色。
    """
    from uuid import uuid4
    from app.db import SessionLocal
    from app.models import Role, Generation, AgentExecution
    from app.workspaces.tools import create_workspace_tools
    async with SessionLocal() as session:
        role = await session.get(Role, rid)
        role.builtin_tools_json = ['workspace_service_status']
        generation = Generation(conversation_id=cid, status='running', stream_epoch='discovery-test')
        session.add(generation)
        await session.flush()
        execution = AgentExecution(execution_id=uuid4().hex, conversation_id=cid, generation_id=generation.id,
            role_id=rid, chain_id='discovery-test', execution_kind='single', status='running', created_at=datetime.now(timezone.utc))
        session.add(execution)
        await session.commit()
        tools = await create_workspace_tools(session, execution_id=execution.execution_id, conversation_id=cid,
            role=role, triggered_by_user_id=role.created_by, allow_dangerous=True)
        return {tool.name: tool for tool in tools}, role.created_by, execution.execution_id


@pytest.mark.anyio
async def test_service_discovery_without_binding_or_lease(command_root, isolated_command_database):
    """Args:
        command_root：受控空目录。
        isolated_command_database：独立迁移数据库。
    """
    from sqlalchemy import select, func
    from app.db import SessionLocal
    from app.models import Conversation, ExecutionWorkspace, Role
    from app.workspaces.tools import workspace_tool_policy
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        async with SessionLocal() as session:
            conversation = await session.get(Conversation, cid)
            conversation.workspace_binding_id = None
            await session.commit()
        tools, owner, _ = await execution_tools(cid, rid)
        assert set(tools) == {'workspace_service_status'}
        assert json.loads(await tools['workspace_service_status'].ainvoke({})) == {'items': [], 'has_more': False, 'next_cursor': None}
        async with SessionLocal() as session:
            assert await session.scalar(select(func.count()).select_from(ExecutionWorkspace)) == 0
            policy = await workspace_tool_policy(session, conversation=await session.get(Conversation, cid),
                role=await session.get(Role, rid), triggered_by_user_id=owner)
            assert [item['name'] for item in policy['exposed_tools']] == ['workspace_service_status']


async def seed_entries(owner, cid, wid, count=115):
    """只插入登记固件，不创建任何真实进程；调用者在退出测试应用前清理。

    Args:
        owner：Owner 身份。
        cid：本轮会话。
        wid：登记时的历史工作区引用。
        count：超过默认配额的规模，验证分页不依赖默认三个实例。
    """
    from app.db import SessionLocal
    from app.runtime.models import RuntimeEntry
    from app.runtime.registry import ACTIVE
    async with SessionLocal() as session:
        for index in range(1, count + 1):
            session.add(RuntimeEntry(id=f'{index:032x}', sequence=index, owner_id=owner, conversation_id=cid,
                conversation_ref_id=cid, workspace_id=wid, execution_id='discovery-fixture', role_id=1,
                tool_call_id=f'fixture-{index}', tool_name='workspace_start_service', kind='service',
                state=ACTIVE[(index - 1) % len(ACTIVE)], process_instance_id='fixture-only',
                port=20000 + index, health_code=200, created_at=datetime.now(timezone.utc),
                log_encrypted='PRIVATE-LOG-MUST-NOT-BE-SELECTED'))
        await session.commit()


async def clear_entries():
    """仅清理本测试的无进程登记，避免应用收尾把固件当真实服务。"""
    from sqlalchemy import delete
    from app.db import SessionLocal
    from app.runtime.models import RuntimeEntry
    async with SessionLocal() as session:
        await session.execute(delete(RuntimeEntry).where(RuntimeEntry.execution_id == 'discovery-fixture'))
        await session.commit()


@pytest.mark.anyio
async def test_service_discovery_pages_states_and_live_changes(command_root, isolated_command_database, monkeypatch):
    """Args:
        command_root：测试工作区。
        isolated_command_database：独立库。
        monkeypatch：模拟 World/进程 epoch 改变，游标不得跨范围使用。
    """
    from app.db import SessionLocal
    from app.runtime.models import RuntimeEntry
    from app.runtime.registry import ACTIVE
    from app.workspaces import service_query
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        tools, owner, _ = await execution_tools(cid, rid)
        await seed_entries(owner, cid, wid + 1000)
        try:
            tool = tools['workspace_service_status']
            first = json.loads(await tool.ainvoke({}))
            assert len(first['items']) == 50 and first['has_more']
            assert set(first['items'][0]) == {'runtime_id', 'state', 'port', 'health_code', 'workspace_binding_id'}
            assert set(item['state'] for item in first['items']) == set(ACTIVE)
            second = json.loads(await tool.ainvoke({'cursor': first['next_cursor']}))
            third = json.loads(await tool.ainvoke({'cursor': second['next_cursor']}))
            ids = [item['runtime_id'] for page in [first, second, third] for item in page['items']]
            assert ids == [f'{index:032x}' for index in range(115, 0, -1)]
            assert third['next_cursor'] is None and not third['has_more']
            large = await tool.ainvoke({'limit': 100})
            assert len(large.encode()) <= 65536 and len(json.loads(large)['items']) == 100
            # 已退出实例不再出现在列表中，已知 ID 仍可读取终态；返回旧四字段。
            async with SessionLocal() as session:
                row = await session.get(RuntimeEntry, f'{1:032x}')
                row.state = 'stopped'
                row = await session.get(RuntimeEntry, f'{2:032x}')
                row.kind = 'command'
                await session.commit()
            terminal = json.loads(await tool.ainvoke({'runtime_id': f'{1:032x}'}))
            assert terminal == {'runtime_id': f'{1:032x}', 'state': 'stopped', 'port': 20001, 'health_code': 200}
            tail = json.loads(await tool.ainvoke({'cursor': second['next_cursor']}))
            assert len(tail['items']) == 13
            assert json.loads(await tool.ainvoke({'runtime_id': f'{2:032x}'}))['runtime_id'] == f'{2:032x}'
            assert 'RUNTIME_NOT_FOUND' in await tool.ainvoke({'runtime_id': 'f' * 32})
            assert 'RUNTIME_QUERY_CURSOR_INVALID' in await tool.ainvoke({'cursor': 'invalid-cursor'})
            monkeypatch.setattr(service_query, 'current_epoch', lambda: 'another-world')
            assert 'RUNTIME_QUERY_CURSOR_EXPIRED' in await tool.ainvoke({'cursor': first['next_cursor']})
        finally:
            await clear_entries()


@pytest.mark.anyio
async def test_service_discovery_ignores_write_gates_but_not_membership(command_root, isolated_command_database, monkeypatch):
    """Args:
        command_root：隔离目录。
        isolated_command_database：独立库。
        monkeypatch：使 Shell 平台不可用，查询仍不应依赖它。
    """
    from sqlalchemy import delete, select
    from app.db import SessionLocal
    from app.models import ConversationMember, WorkspaceBinding, ExecutionWorkspace
    from app.runtime.models import RuntimeGate, CleanupOperation
    from app.workspaces import shell
    from app.workspaces.commands import WorkspaceCommandError
    def unsupported():
        """只模拟平台能力查询失败。"""
        raise WorkspaceCommandError('SHELL_NOT_SUPPORTED')
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        async with SessionLocal() as session:
            binding = await session.get(WorkspaceBinding, wid)
            binding.services_enabled = False
            binding.root_path = str(command_root / 'missing-root')
            gate = await session.get(RuntimeGate, 1)
            gate.closing = True
            session.add(CleanupOperation(id='fixture-cleanup', scope='world', scope_id=0, reason='fixture',
                process_instance_id='fixture-only', state='failed', target_count=0, created_at=datetime.now(timezone.utc)))
            await session.commit()
        monkeypatch.setattr(shell, 'shell_configuration', unsupported)
        tools, owner, _ = await execution_tools(cid, rid)
        await seed_entries(owner, cid, wid, count=1)
        try:
            assert json.loads(await tools['workspace_service_status'].ainvoke({}))['items'][0]['runtime_id'] == f'{1:032x}'
            async with SessionLocal() as session:
                assert not (await session.execute(select(ExecutionWorkspace))).first()
                await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == cid,
                    ConversationMember.member_type == 'user'))
                await session.commit()
            assert 'WORKSPACE_TOOL_NOT_AVAILABLE' in await tools['workspace_service_status'].ainvoke({})
        finally:
            await clear_entries()
            async with SessionLocal() as session:
                await session.execute(delete(CleanupOperation).where(CleanupOperation.id == 'fixture-cleanup'))
                gate = await session.get(RuntimeGate, 1)
                gate.closing = False
                await session.commit()


@pytest.mark.anyio
async def test_service_query_failure_is_not_empty_list(command_root, isolated_command_database, monkeypatch):
    """Args:
        command_root：隔离目录。
        isolated_command_database：独立库。
        monkeypatch：模拟数据库故障，不回显异常原文。
    """
    from app.workspaces import service_query
    async def fail(*args, **kwargs):
        """Args:
            args：查询上下文。
            kwargs：已鉴权归属，不写入异常或日志。
        """
        raise RuntimeError('PRIVATE-DATABASE-DETAIL')
    async with command_conversation(command_root) as (_, _, cid, rid, _):
        tools, _, _ = await execution_tools(cid, rid)
        monkeypatch.setattr(service_query, 'query_status', fail)
        result = await tools['workspace_service_status'].ainvoke({})
        assert result.startswith('[工具执行失败]') and 'RUNTIME_QUERY_FAILED' in result
        assert 'PRIVATE-DATABASE' not in result and 'items' not in result


@pytest.mark.anyio
async def test_discovery_uses_query_not_remembered_id(command_root, isolated_command_database):
    """Args:
        command_root：真实受控 HTTP 服务目录。
        isolated_command_database：独立数据库，不读取用户环境服务。
    """
    import sys
    if sys.platform != 'linux':
        pytest.skip('真实托管服务仅 Linux 开放')
    from test_runtime_service import enable_service, launch
    from test_workspace_commands import send_command
    from test_workspace_edit import wait_reply
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import ToolCall, EventLog
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_service(client, headers, rid, wid)
        running = await launch(client, headers, cid)
        try:
            history = (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()
            assert running['id'] not in json.dumps([row['parts_json'] for row in history['items']])
            role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
            await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_service_status']})
            sent = await send_command(client, headers, cid, '[SERVICE_DISCOVERY_FAKE]')
            reply = await wait_reply(client, headers, cid, sent['message']['id'])
            call = next(part for part in reply['parts_json'] if part.get('tool_name') == 'workspace_service_status')
            assert call['status'] == 'success' and call['detail_available']
            detail = (await client.get(f"/api/conversations/{cid}/messages/{reply['id']}/tools/{call['call_id']}", headers=headers)).json()
            assert json.loads(detail['input']['text']) == {}
            assert json.loads(detail['output']['text'])['items'][0]['runtime_id'] == running['id']
            async with SessionLocal() as session:
                record = await session.scalar(select(ToolCall).where(ToolCall.message_id == reply['id']))
                assert json.loads(record.args_summary) == {'mode': 'list', 'has_cursor': False}
                for event in (await session.scalars(select(EventLog).where(EventLog.generation_id == sent['generation_id']))).all():
                    assert running['id'] not in json.dumps(event.payload_json)
        finally:
            stopped = await client.post(f"/api/conversations/{cid}/processes/{running['id']}/stop", headers=headers)
            assert stopped.status_code == 200 and stopped.json()['state'] == 'stopped'


@pytest.mark.anyio
async def test_discovery_scope_and_revocations(command_root, isolated_command_database):
    """Args:
        command_root：本轮目录。
        isolated_command_database：隔离数据库，测试外会话与已撤销身份。
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import Role, Generation, AgentExecution, Conversation
    from app.runtime.models import RuntimeEntry
    from app.workspaces.tools import create_workspace_tools
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        tools, owner, execution_id = await execution_tools(cid, rid)
        tool = tools['workspace_service_status']
        other = (await client.post('/api/conversations', headers=headers, json={'title': '其他会话', 'type': 'single', 'role_ids': [rid]})).json()['id']
        await seed_entries(owner, cid, wid, count=3)
        try:
            first = json.loads(await tool.ainvoke({'limit': 1}))
            other_tools, _, _ = await execution_tools(other, rid)
            assert 'RUNTIME_NOT_FOUND' in await other_tools['workspace_service_status'].ainvoke({'runtime_id': f'{1:032x}'})
            assert 'RUNTIME_QUERY_CURSOR_INVALID' in await other_tools['workspace_service_status'].ainvoke({'cursor': first['next_cursor']})
            async with SessionLocal() as session:
                (await session.get(RuntimeEntry, f'{2:032x}')).conversation_ref_id = None
                (await session.get(RuntimeEntry, f'{3:032x}')).owner_id = owner + 1000
                await session.commit()
            assert [row['runtime_id'] for row in json.loads(await tool.ainvoke({}))['items']] == [f'{1:032x}']
            async with SessionLocal() as session:
                role = await session.get(Role, rid)
                assert await create_workspace_tools(session, execution_id=execution_id, conversation_id=cid, role=role,
                    triggered_by_user_id=owner, allow_dangerous=False) == []
                role.builtin_tools_json = []
                await session.commit()
            assert 'WORKSPACE_TOOL_NOT_AVAILABLE' in await tool.ainvoke({})
            async with SessionLocal() as session:
                (await session.get(Role, rid)).builtin_tools_json = ['workspace_service_status']
                execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == execution_id))
                generation = await session.get(Generation, execution.generation_id)
                generation.stop_requested_at = datetime.now(timezone.utc)
                await session.commit()
            assert 'WORKSPACE_TOOL_NOT_AVAILABLE' in await tool.ainvoke({})
            async with SessionLocal() as session:
                generation = await session.get(Generation, generation.id)
                generation.stop_requested_at = None
                (await session.get(Conversation, cid)).deleted_at = datetime.now(timezone.utc)
                await session.commit()
            assert 'WORKSPACE_TOOL_NOT_AVAILABLE' in await tool.ainvoke({})
        finally:
            await clear_entries()


@pytest.mark.anyio
@pytest.mark.parametrize('change', ['owner', 'role_deleted', 'role_inactive', 'execution', 'group'])
async def test_discovery_rejects_invalid_actor(command_root, isolated_command_database, change):
    """Args:
        command_root：测试目录。
        isolated_command_database：每项独立数据库。
        change：创建工具后的身份失效方式。
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import User, Role, AgentExecution, Conversation
    async with command_conversation(command_root) as (_, _, cid, rid, _):
        tools, owner, execution_id = await execution_tools(cid, rid)
        async with SessionLocal() as session:
            if change == 'owner':
                (await session.get(User, owner)).is_owner = False
            elif change == 'role_deleted':
                (await session.get(Role, rid)).deleted_at = datetime.now(timezone.utc)
            elif change == 'role_inactive':
                (await session.get(Role, rid)).active = False
            elif change == 'group':
                (await session.get(Conversation, cid)).type = 'group'
            else:
                execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == execution_id))
                execution.status = 'completed'
            await session.commit()
        result = await tools['workspace_service_status'].ainvoke({})
        assert result.startswith('[工具被拒绝]') and 'WORKSPACE_TOOL_NOT_AVAILABLE' in result
