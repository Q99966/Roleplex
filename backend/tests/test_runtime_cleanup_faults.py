"""用确定性故障验证批次不能被取消或审计故障截成半份清单。"""
import asyncio

import pytest
from test_workspace_commands import command_conversation, command_root, isolated_command_database


@pytest.mark.anyio
async def test_final_audit_failure_keeps_durable_retry_link(command_root, isolated_command_database, monkeypatch):
    """终态审计写失败时数据库仍保留原因，成功重试能关联到原批次。

    Args:
        command_root：独立目录。
        isolated_command_database：新库。
        monkeypatch：只破坏批次完成事件写入。
    """
    from app.runtime import registry
    from app.runtime.models import CleanupOperation
    from app.db import SessionLocal
    async with command_conversation(command_root) as (_client, _headers, cid, _rid, _wid):
        operation, _items = await registry.begin_cleanup('conversation', cid, reason='conversation_delete', actor_id=1)
        original = registry.logger.info
        def broken(event, **kwargs):
            """Args:
                event：固定事件名。
                kwargs：安全元数据，不读取或输出。
            """
            if event == 'runtime.cleanup_completed':
                raise OSError('final audit fixture unavailable')
            return original(event, **kwargs)
        with monkeypatch.context() as scoped:
            scoped.setattr(registry.logger, 'info', broken)
            with pytest.raises(registry.RuntimeRejected, match='RUNTIME_AUDIT_UNAVAILABLE'):
                await registry.end_cleanup(operation.id, success=True)
        async with SessionLocal() as session:
            saved = await session.get(CleanupOperation, operation.id)
            assert saved.state == 'failed' and saved.error_code == 'RUNTIME_AUDIT_UNAVAILABLE'
        retry, _items = await registry.begin_cleanup('conversation', cid, reason='conversation_delete', actor_id=1)
        await registry.end_cleanup(retry.id, success=True)
        async with SessionLocal() as session:
            saved = await session.get(CleanupOperation, operation.id)
            assert saved.state == 'superseded' and saved.superseded_by_id == retry.id


@pytest.mark.anyio
@pytest.mark.parametrize('scope', ['conversation', 'workspace'])
async def test_new_reservation_after_preview_still_requires_confirmation(command_root, isolated_command_database, monkeypatch, scope):
    """删除预览为空不等于冻结时仍为空；新实例不能未经确认被停止。

    Args:
        command_root：独立目录。
        isolated_command_database：新库。
        monkeypatch：观察空预览完成的精确边界。
        scope：会话或工作区删除。
    """
    from app.runtime import registry
    from app.runtime.manager import manager
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        observed = asyncio.Event()
        original = registry.quota_view
        async def view(kind, identity):
            """Args:
                kind：预览范围。
                identity：预览资源。
            """
            result = await original(kind, identity)
            observed.set()
            return result
        monkeypatch.setattr(registry, 'quota_view', view)
        await manager.cleanup_lock.acquire()
        task = asyncio.create_task(client.delete(f'/api/conversations/{cid}' if scope == 'conversation' else f'/api/workspaces/{wid}', headers=headers))
        try:
            await asyncio.wait_for(observed.wait(), 3)
            row = await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
                execution_id='preview-race', role_id=rid, tool_call_id='new', tool_name='workspace_run_command', kind='command')
        finally:
            manager.cleanup_lock.release()
        response = await task
        assert response.status_code == 409 and response.json()['error']['code'] == 'RUNTIME_CLEANUP_CONFIRM_REQUIRED'
        assert (await registry.get(row.id)).state == 'pending'


@pytest.mark.anyio
async def test_slow_stop_does_not_block_remaining_targets(command_root, isolated_command_database, monkeypatch):
    """中间项超时后仍处理下一项，重复请求复用未结束的停止任务。

    Args:
        command_root：独立目录。
        isolated_command_database：新库。
        monkeypatch：精确控制中间项的停止交接与等待预算。
    """
    from app.runtime import registry
    from app.runtime import manager as module
    manager = module.manager
    async with command_conversation(command_root) as (_client, _headers, cid, rid, wid):
        rows = [await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
            execution_id='slow-stop', role_id=rid, tool_call_id=str(i), tool_name='workspace_run_command', kind='command') for i in range(3)]
        release = asyncio.Event()
        original = manager._stop_one
        visited = []
        async def slow(identity, reason, cleanup_id):
            """Args:
                identity：资源身份。
                reason：停止原因。
                cleanup_id：冻结批次。
            """
            visited.append(identity)
            if identity == rows[1].id:
                await release.wait()
            return await original(identity, reason, cleanup_id)
        monkeypatch.setattr(module, 'STOP_BUDGET_SECONDS', .2)
        monkeypatch.setattr(manager, '_stop_one', slow)
        try:
            with pytest.raises(registry.RuntimeRejected):
                async with manager.cleanup_scope('conversation', cid, 'conversation_delete', 1):
                    raise AssertionError('超时不得完成资源删除')
            assert visited == [row.id for row in reversed(rows)]
            assert (await registry.quota_view('conversation', cid))['used'] == 1
        finally:
            release.set()
        await manager.stop_one(rows[1].id)
        assert visited.count(rows[1].id) == 1
        async with manager.cleanup_scope('conversation', cid, 'conversation_delete', 1):
            pass


@pytest.mark.anyio
async def test_stale_disable_does_not_stop_instances(command_root, isolated_command_database, monkeypatch):
    """停用请求排队期间配置被修改，旧版本不得先停止资源再报告冲突。

    Args:
        command_root：测试工作区。
        isolated_command_database：新库。
        monkeypatch：记录配置读取完成的精确边界。
    """
    from app.runtime import registry
    from app.runtime.manager import manager
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await registry.configure('workspace', wid, limit=5, expected_revision=0, actor_id=1, services_enabled=True)
        row = await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
            execution_id='stale-disable', role_id=rid, tool_call_id='pending', tool_name='workspace_start_service', kind='service')
        reached = asyncio.Event()
        original = registry.quota_view
        async def observed(scope, identity):
            """Args:
                scope：配置范围。
                identity：配置身份。
            """
            result = await original(scope, identity)
            reached.set()
            return result
        monkeypatch.setattr(registry, 'quota_view', observed)
        await manager.cleanup_lock.acquire()
        task = asyncio.create_task(client.put('/api/runtime/config', headers=headers, json={
            'scope': 'workspace', 'scope_id': wid, 'limit': 5, 'expected_revision': 1,
            'services_enabled': False, 'confirm_cleanup': True}))
        try:
            await asyncio.wait_for(reached.wait(), 3)
            await registry.configure('workspace', wid, limit=6, expected_revision=1, actor_id=1)
        finally:
            manager.cleanup_lock.release()
        result = await task
        assert result.status_code == 409
        assert result.json()['error']['code'] == 'RUNTIME_REVISION_CONFLICT'
        assert (await registry.get(row.id)).state == 'pending'


@pytest.mark.anyio
@pytest.mark.parametrize('fault', ['cancel', 'cancel_freeze', 'audit', 'begin_audit', 'result'])
async def test_cleanup_finishes_frozen_targets_before_reporting_failure(command_root, isolated_command_database, monkeypatch, fault):
    """取消请求或审计写失败都不允许跳过后续冻结目标。

    Args:
        command_root：独占测试目录。
        isolated_command_database：全新迁移库。
        monkeypatch：只在指定边界注入故障。
        fault：请求取消或机器日志故障。
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.runtime import registry
    from app.runtime.manager import manager
    from app.runtime.models import CleanupOperation
    async with command_conversation(command_root) as (_client, _headers, cid, rid, wid):
        rows = [await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
            execution_id='fault-fixture', role_id=rid, tool_call_id=str(i), tool_name='workspace_run_command', kind='command') for i in range(3)]
        reached, release = asyncio.Event(), asyncio.Event()
        visited = []
        original = manager.stop_one
        async def stop(identity, reason='owner_stop', cleanup_id=None):
            """Args:
                identity：冻结的资源身份。
                reason：可信清理原因。
                cleanup_id：本次批次。
            """
            visited.append(identity)
            if fault == 'cancel' and len(visited) == 1:
                reached.set()
                await release.wait()
            return await original(identity, reason, cleanup_id)
        monkeypatch.setattr(manager, 'stop_one', stop)
        if fault == 'cancel_freeze':
            original_begin = registry.begin_cleanup
            async def begun(*args, **kwargs):
                """Args:
                    args：冻结范围。
                    kwargs：已授权操作元数据。
                """
                result = await original_begin(*args, **kwargs)
                reached.set()
                await release.wait()
                return result
            monkeypatch.setattr(registry, 'begin_cleanup', begun)
        if fault == 'result':
            from sqlalchemy.ext.asyncio import AsyncSession
            execute = AsyncSession.execute
            injected = False
            async def execute_with_fault(session, statement, *args, **kwargs):
                """Args:
                    session：本轮短事务。
                    statement：只按表名识别结果保存，不记录 SQL 参数。
                    args：原执行选项。
                    kwargs：原执行选项。
                """
                nonlocal injected
                if not injected and getattr(getattr(statement, 'table', None), 'name', None) == 'runtime_cleanup_items':
                    injected = True
                    raise OSError('result fixture unavailable')
                return await execute(session, statement, *args, **kwargs)
            monkeypatch.setattr(AsyncSession, 'execute', execute_with_fault)
        log = registry.logger.info
        def write_event(event, **kwargs):
            """Args:
                event：固定事件名。
                kwargs：测试不读取或输出字段。
            """
            if (fault == 'audit' and event == 'runtime.cleanup_target_started') or (fault == 'begin_audit' and event == 'runtime.cleanup_started'):
                raise OSError('audit fixture unavailable')
            return log(event, **kwargs)
        monkeypatch.setattr(registry.logger, 'info', write_event)
        async def clean():
            """模拟资源删除请求，失败时不得提交资源删除。"""
            async with manager.cleanup_scope('conversation', cid, 'conversation_delete', 1):
                raise AssertionError('故障批次不能提交资源变更')
        task = asyncio.create_task(clean())
        if fault.startswith('cancel'):
            await asyncio.wait_for(reached.wait(), 3)
            task.cancel()
            await asyncio.sleep(.02)
            release.set()
        with pytest.raises((asyncio.CancelledError, registry.RuntimeRejected)):
            await task
        assert visited == [row.id for row in reversed(rows)]
        assert (await registry.quota_view('conversation', cid))['used'] == 0
        async with SessionLocal() as session:
            operation = await session.scalar(select(CleanupOperation).where(CleanupOperation.scope == 'conversation'))
            assert operation.state == 'failed'
        monkeypatch.setattr(registry.logger, 'info', log)
