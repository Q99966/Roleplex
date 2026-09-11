"""W1d 配额、范围门槛和回收清单的数据库并发测试。"""
import asyncio

import pytest
from test_workspace_commands import command_conversation, command_root, isolated_command_database


@pytest.mark.anyio
@pytest.mark.parametrize('same_call', [False, True])
async def test_port_and_call_identity_reserve_only_once(command_root, isolated_command_database, same_call):
    """并发端口竞争或重复调用只占一个名额；拒绝不创建第二份资源。

    Args:
        command_root：本轮目录。
        isolated_command_database：新库。
        same_call：是否模拟同一执行调用重复到达。
    """
    from app.runtime import registry
    async with command_conversation(command_root) as (_client, _headers, cid, rid, wid):
        async def claim(index):
            """Args:
                index：竞争请求序号。
            """
            return await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
                execution_id='identity-fixture', role_id=rid, tool_call_id='same' if same_call else str(index),
                tool_name='workspace_start_service', kind='service', port=45678)
        results = await asyncio.gather(claim(0), claim(1), return_exceptions=True)
        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert [result.code for result in results if isinstance(result, registry.RuntimeRejected)] == [
            'RUNTIME_REQUEST_CONFLICT' if same_call else 'RUNTIME_PORT_BUSY']
        assert (await registry.quota_view('conversation', cid))['used'] == 1


@pytest.mark.anyio
async def test_runtime_owner_boundary_and_invalid_limits(command_root, isolated_command_database):
    """Guest 不能查配置、日志或停止，Owner 非法配置与旧版本明确拒绝。

    Args:
        command_root：隔离工作区。
        isolated_command_database：全新数据库。
    """
    from accounts import TEST_PASSWORD, guest_username
    from app.runtime import registry
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        row = await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
            execution_id='auth-fixture', role_id=rid, tool_call_id='owned', tool_name='workspace_run_command', kind='command')
        guest = (await client.post('/api/auth/register', json={'username': guest_username('runtime'),
            'password': TEST_PASSWORD, 'nickname': 'Guest'})).json()
        guest_headers = {'Authorization': f"Bearer {guest['access_token']}"}
        for route in ['/api/runtime/config?scope=world&scope_id=0',
            f'/api/conversations/{cid}/processes', f'/api/conversations/{cid}/processes/{row.id}',
            f'/api/conversations/{cid}/processes/{row.id}/logs']:
            assert (await client.get(route, headers=guest_headers)).status_code == 403
        assert (await client.post(f'/api/conversations/{cid}/processes/{row.id}/stop', headers=guest_headers)).status_code == 403
        payload = {'scope': 'conversation', 'scope_id': cid, 'limit': 3, 'expected_revision': 0}
        assert (await client.put('/api/runtime/config', json=payload, headers=guest_headers)).status_code == 403
        for limit in [0, -1, True, 1.5, 2**31]:
            assert (await client.put('/api/runtime/config', json={**payload, 'limit': limit}, headers=headers)).status_code == 422
        assert (await client.put('/api/runtime/config', json=payload, headers=headers)).status_code == 200
        assert (await client.put('/api/runtime/config', json=payload, headers=headers)).status_code == 409
        assert (await registry.get(row.id)).state == 'pending'


@pytest.mark.anyio
async def test_purged_conversation_identity_cannot_expose_old_runtime(command_root, isolated_command_database):
    """会话物理清除后，即便整数 ID 被复用也不能读到旧运行实例。

    Args:
        command_root：隔离目录。
        isolated_command_database：全新迁移数据库。
    """
    from sqlalchemy import delete
    from app.db import SessionLocal
    from app.models import Conversation
    from app.runtime import registry
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        row = await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
            execution_id='purge-fixture', role_id=rid, tool_call_id='old', tool_name='workspace_run_command', kind='command')
        await registry.finish(row.id, 'rejected')
        async with SessionLocal() as session:
            await session.execute(delete(Conversation).where(Conversation.id == cid))
            await session.commit()
        fresh = (await client.post('/api/conversations', headers=headers,
            json={'type': 'single', 'title': '新身份', 'role_ids': [rid], 'workspace_binding_id': wid})).json()['id']
        assert (await client.get(f'/api/conversations/{fresh}/processes', headers=headers)).json()['items'] == []
        assert (await client.get(f'/api/conversations/{fresh}/processes/{row.id}', headers=headers)).status_code == 404
        assert (await registry.get(row.id)).conversation_id == cid


@pytest.mark.anyio
async def test_three_level_quotas_are_atomic_and_configurable(command_root, isolated_command_database):
    """并发申请不会越过会话上限，调低上限不自动回收已有实例。

    Args:
        command_root：本轮外部目录。
        isolated_command_database：本用例专用迁移数据库。
    """
    from app.runtime.registry import reserve, configure, quota_view, finish, RuntimeRejected
    async with command_conversation(command_root) as (_client, _headers, cid, rid, wid):
        async def claim(index):
            """模拟由执行层绑定身份的顶层命令预留。

            Args:
                index：独立调用身份。
            """
            return await reserve(owner_id=1, conversation_id=cid, workspace_id=wid, execution_id='fixture-execution',
                role_id=rid, tool_call_id=f'quota-{index}', tool_name='workspace_run_command', kind='command')
        results = await asyncio.gather(*(claim(i) for i in range(4)), return_exceptions=True)
        rows = [item for item in results if not isinstance(item, Exception)]
        assert len(rows) == 3
        assert any(isinstance(item, RuntimeRejected) and item.code == 'RUNTIME_CONVERSATION_LIMIT' for item in results)
        before = await quota_view('conversation', cid)
        assert before['limit'] == 3 and before['used'] == 3
        await configure('conversation', cid, limit=2, expected_revision=before['revision'], actor_id=1)
        assert (await quota_view('conversation', cid))['used'] == 3
        with pytest.raises(RuntimeRejected):
            await claim(5)
        await finish(rows[0].id, 'rejected')
        await finish(rows[1].id, 'rejected')
        assert (await claim(6)).id


@pytest.mark.anyio
async def test_cleanup_freezes_exact_scope_and_audits_all_targets(command_root, isolated_command_database):
    """冻结后禁止补进新进程；清单只包含目标会话并具有稳定逆序。

    Args:
        command_root：本轮目录。
        isolated_command_database：新数据库。
    """
    from app.runtime.registry import reserve, begin_cleanup, end_cleanup, quota_view, RuntimeRejected
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        other = (await client.post('/api/conversations', headers=headers,
            json={'type': 'single', 'title': '保留会话', 'role_ids': [rid], 'workspace_binding_id': wid})).json()['id']
        async def claim(conversation, call):
            """登记不同会话的同工作区实例。

            Args:
                conversation：所属会话。
                call：唯一调用身份。
            """
            return await reserve(owner_id=1, conversation_id=conversation, workspace_id=wid, execution_id='scope-fixture',
                role_id=rid, tool_call_id=call, tool_name='workspace_run_command', kind='command')
        first, second, retained = await claim(cid, 'a'), await claim(cid, 'b'), await claim(other, 'c')
        operation, targets = await begin_cleanup('conversation', cid, reason='conversation_delete', actor_id=1)
        assert [target.runtime_id for target in targets] == [second.id, first.id]
        with pytest.raises(RuntimeRejected, match='RUNTIME_SCOPE_CLOSING'):
            await claim(cid, 'late')
        assert (await quota_view('conversation', other))['used'] == 1
        await end_cleanup(operation.id, success=False)
        with pytest.raises(RuntimeRejected):
            await claim(cid, 'still-blocked')


@pytest.mark.anyio
@pytest.mark.parametrize('scope,limit,code', [('workspace', 5, 'RUNTIME_WORKSPACE_LIMIT'), ('world', 20, 'RUNTIME_WORLD_LIMIT')])
async def test_workspace_and_world_defaults_and_overrides(command_root, isolated_command_database, scope, limit, code):
    """单独验证工作区/World 默认上限，配置提高后可超出默认值。

    Args:
        command_root：外部目录。
        isolated_command_database：全新库。
        scope：受测配额层级。
        limit：默认上限。
        code：该层稳定拒绝码。
    """
    from app.runtime import registry
    async with command_conversation(command_root) as (_client, _headers, cid, rid, wid):
        for other, identity in [('world', 0), ('workspace', wid), ('conversation', cid)]:
            if other != scope:
                await registry.configure(other, identity, limit=30, expected_revision=0, actor_id=1)
        async def claim(index):
            """Args:
                index：独立宿主调用。
            """
            return await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid, execution_id='limit-fixture',
                role_id=rid, tool_call_id=str(index), tool_name='workspace_run_command', kind='command')
        results = await asyncio.gather(*(claim(i) for i in range(limit + 1)), return_exceptions=True)
        assert sum(not isinstance(item, Exception) for item in results) == limit
        assert [item.code for item in results if isinstance(item, registry.RuntimeRejected)] == [code]
        sid = 0 if scope == 'world' else wid
        await registry.configure(scope, sid, limit=limit + 1, expected_revision=0, actor_id=1)
        assert (await claim(100)).id


@pytest.mark.anyio
async def test_cleanup_continues_after_failure_and_retry_reconciles_gate(command_root, isolated_command_database, monkeypatch):
    """中间项失败仍继续后续项，成功重试才解除原失败门槛。

    Args:
        command_root：本轮目录。
        isolated_command_database：全新库。
        monkeypatch：在一个目标注入回收失败。
    """
    from app.runtime import registry
    from app.runtime.manager import manager
    async with command_conversation(command_root) as (_client, _headers, cid, rid, wid):
        rows = [await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid, execution_id='cleanup-fixture',
            role_id=rid, tool_call_id=str(i), tool_name='workspace_run_command', kind='command') for i in range(3)]
        original = manager.stop_one
        visited = []
        async def stop(rid, reason='owner_stop', cleanup_id=None):
            """Args:
                rid：目标身份。
                reason：回收原因。
                cleanup_id：批次身份。
            """
            visited.append(rid)
            if rid == rows[1].id:
                raise registry.RuntimeRejected('RUNTIME_CLEANUP_UNCONFIRMED')
            return await original(rid, reason, cleanup_id)
        monkeypatch.setattr(manager, 'stop_one', stop)
        with pytest.raises(registry.RuntimeRejected):
            async with manager.cleanup_scope('conversation', cid, 'conversation_delete', 1):
                raise AssertionError('失败清单不得进入资源变更')
        assert visited == [row.id for row in reversed(rows)]
        assert (await registry.quota_view('conversation', cid))['used'] == 1
        monkeypatch.setattr(manager, 'stop_one', original)
        async with manager.cleanup_scope('conversation', cid, 'conversation_delete', 1):
            pass
        assert (await registry.quota_view('conversation', cid))['used'] == 0
        assert (await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid, execution_id='retry-fixture',
            role_id=rid, tool_call_id='after', tool_name='workspace_run_command', kind='command')).id
