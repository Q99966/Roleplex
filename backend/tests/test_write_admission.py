"""写入准入和审批互斥的确定性回归。"""
import asyncio
import pytest
from test_workspace_commands import command_root, isolated_command_database, command_conversation, send_command


@pytest.mark.anyio
async def test_single_and_batch_share_admission_and_cancel_waiters():
    """写入满载时有界排队，取消后不能迟到占槽。"""
    from app.workspaces.write_admission import WriteAdmission
    pool = WriteAdmission(capacity=1, waiting=1, queued_limit=10, timeout=.1)
    entered = asyncio.Event()
    async def queued():
        """等待期间不执行任务正文。"""
        async with pool.slot(5):
            entered.set()
    async with pool.slot(5):
        task = asyncio.create_task(queued())
        await asyncio.sleep(0)
        assert pool.queued_bytes == 5
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pool.queued_bytes == 0 and not entered.is_set()
    assert not pool.active

@pytest.mark.anyio
@pytest.mark.parametrize('decision', ['approve', 'revoke', 'cancel'])
async def test_pending_approval_does_not_block_write_and_rechecks_after_lock(command_root, isolated_command_database, monkeypatch, decision):
    """Args:
        command_root：受控文件目录。
        isolated_command_database：本轮新数据库。
        monkeypatch：记录运行器与最终授权检查，不改变审批流程。
        decision：批准后正常执行、撤权或取消等待锁。
    """
    from app.workspaces import approvals, tools as module
    from test_shell_approvals import pending as wait_pending
    from test_workspace_edit import wait_reply
    starts = []
    checked_before_lock = asyncio.Event()
    original_authorized = approvals._authorized
    checks = 0
    async def authorized(request):
        """Args:
            request：宿主冻结请求，只观察检查次数。
        """
        nonlocal checks
        value = await original_authorized(request)
        checks += 1
        if checks == 2:
            checked_before_lock.set()
        return value
    original_run = approvals.run_shell
    async def run(request):
        """Args:
            request：已批准且最终授权通过的请求。
        """
        starts.append(True)
        return await original_run(request)
    monkeypatch.setattr(approvals, '_authorized', authorized)
    monkeypatch.setattr(approvals, 'run_shell', run)
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'shell_enabled': True, 'file_tools_enabled': True})
        role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
        await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_run_shell', 'workspace_write']})
        sent = await send_command(client, headers, cid, '[SHELL_WRITE_WAIT_FAKE]')
        approval = await wait_pending(client, headers, cid)
        async with asyncio.timeout(2):
            while not (command_root / 'pending/proof.txt').exists():
                await asyncio.sleep(.01)
        assert starts == []
        lock = module._COMMAND_CALL_LOCKS[wid]
        await lock.acquire()
        try:
            await client.post(f"/api/conversations/{cid}/tool-approvals/{approval['id']}/decision", headers=headers,
                json={'decision': 'approve', 'request_digest': approval['request_digest']})
            await asyncio.wait_for(checked_before_lock.wait(), 2)
            assert starts == []
            if decision == 'revoke':
                await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'shell_enabled': False})
            elif decision == 'cancel':
                await client.post(f'/api/conversations/{cid}/stop', headers=headers)
        finally:
            lock.release()
        await wait_reply(client, headers, cid, sent['message']['id'])
        assert len(starts) == (1 if decision == 'approve' else 0)
        assert (command_root / 'pending/proof.txt').read_text() == 'written-before-approval'

@pytest.mark.anyio
@pytest.mark.parametrize('reason', ['queue_full', 'queue_bytes', 'queue_timeout', 'closed'])
async def test_queue_bounds_have_distinct_reasons_and_no_late_execution(reason):
    """Args:
        reason：分别验证数量、字节、时间与关闭边界。
    """
    from app.workspaces.write_admission import WriteAdmission
    from app.workspaces.files import WorkspaceFileError
    pool = WriteAdmission(capacity=1, waiting=0 if reason == 'queue_full' else 1, queued_limit=1 if reason == 'queue_bytes' else 10, timeout=.02)
    executed = False
    async def attempt():
        """等待失败时不进入业务正文。"""
        nonlocal executed
        async with pool.slot(2):
            executed = True
    if reason == 'closed':
        await pool.close()
        with pytest.raises(WorkspaceFileError) as error:
            await attempt()
    else:
        async with pool.slot(1):
            with pytest.raises(WorkspaceFileError) as error:
                await attempt()
    assert error.value.details == {'phase': 'queue', 'reason': reason}
    assert not executed and not pool.active and not pool.queue and pool.queued_bytes == 0


@pytest.mark.anyio
async def test_shared_wait_budget_does_not_reset_between_locks(monkeypatch):
    """Args:
        monkeypatch：受控单调时钟模拟第一次等待已消耗额度。
    """
    from app.workspaces import write_admission as module
    from app.workspaces.files import WorkspaceFileError
    tick = [100.0]
    monkeypatch.setattr(module, 'monotonic', lambda: tick[0])
    pool = module.current_pool()
    monkeypatch.setattr(pool, 'timeout', 30)
    class DelayedLock(asyncio.Lock):
        """模拟一次成功获取锁前已经等待了全部时间。"""
        async def acquire(self):
            """获取原锁后推进受控时间，不修改真实任务时钟。"""
            result = await super().acquire()
            tick[0] += 30
            return result
    async with module.admitted(1):
        async with module.locked(DelayedLock()):
            pass
        lock = asyncio.Lock()
        with pytest.raises(WorkspaceFileError) as error:
            async with module.locked(lock):
                pytest.fail('第二次锁等待不能重新获得30秒')
        assert error.value.details['reason'] == 'lock_timeout'
        assert not lock.locked()
    assert not pool.active


@pytest.mark.anyio
async def test_close_and_handoff_cancel_release_all_capacity():
    """关闭和移交竞争时，等待者不能迟到写入或泄漏队列字节。"""
    from app.workspaces.write_admission import WriteAdmission
    from app.workspaces.files import WorkspaceFileError
    pool = WriteAdmission(capacity=1, waiting=2)
    held = asyncio.Event()
    blocker = asyncio.Event()
    async def active():
        """占用执行位置直到被关闭取消。"""
        async with pool.slot(1):
            held.set()
            await blocker.wait()
    async def waiting():
        """排队请求不得在关闭后进入正文。"""
        async with pool.slot(2):
            pytest.fail('关闭后迟到执行')
    first = asyncio.create_task(active())
    await held.wait()
    second = asyncio.create_task(waiting())
    await asyncio.sleep(0)
    await pool.close()
    result = await asyncio.gather(first, second, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert isinstance(result[1], WorkspaceFileError) and result[1].details['reason'] == 'closed'
    assert not pool.active and not pool.queue and pool.queued_bytes == 0
    pool = WriteAdmission(capacity=1)
    async with pool.slot(1):
        second = asyncio.create_task(waiting())
        await asyncio.sleep(0)
    second.cancel()
    await asyncio.gather(second, return_exceptions=True)
    assert not pool.active and not pool.queue and pool.queued_bytes == 0

@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['success', 'revoke', 'cancel', 'timeout'])
async def test_real_single_and_batch_wait_together_and_reauthorize(command_root, isolated_command_database, monkeypatch, mode):
    """Args:
        command_root：真实文件工具的隔离目录。
        isolated_command_database：本轮新数据库。
        monkeypatch：缩小本轮池容量和超时以确定性覆盖准入。
        mode：正常出队、撤权、取消或队列超时。
    """
    import json
    from app.workspaces.write_admission import current_pool
    from test_write_diagnostics import mutation_tools
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        tools, _, _ = await mutation_tools(client, headers, cid, rid, wid)
        pool = current_pool()
        monkeypatch.setattr(pool, 'capacity', 1)
        monkeypatch.setattr(pool, 'timeout', .05 if mode == 'timeout' else 2)
        async with pool.slot(0):
            tasks = [asyncio.create_task(tools['workspace_write'].ainvoke(args)) for args in [
                {'path': 'single.txt', 'content': 'single'}, {'items': [{'path': 'batch.txt', 'content': 'batch'}]}]]
            async with asyncio.timeout(2):
                while len(pool.queue) != 2:
                    await asyncio.sleep(0)
            assert pool.queued_bytes > 0 and not (command_root / 'single.txt').exists()
            if mode == 'revoke':
                await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': False})
            elif mode == 'cancel':
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            elif mode == 'timeout':
                await asyncio.gather(*tasks)
        outputs = await asyncio.gather(*tasks, return_exceptions=True)
        if mode == 'success':
            assert (command_root / 'single.txt').read_text() == 'single'
            assert (command_root / 'batch.txt').read_text() == 'batch'
        else:
            assert not (command_root / 'single.txt').exists() and not (command_root / 'batch.txt').exists()
        if mode == 'timeout':
            values = [json.loads(output.split('] ', 1)[-1]) for output in outputs]
            assert values[0]['details']['reason'] == values[1]['wait_diagnostic']['reason'] == 'queue_timeout'
            assert all(node['status'] == 'not_executed' for node in values[1]['items'])
        assert not pool.active and not pool.queue and pool.queued_bytes == 0
