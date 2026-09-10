"""真实前台 HTTP 进程经审批成为独立服务；不使用 Docker 或未知宿主文件。"""
import asyncio
import socket

import httpx
import pytest
from test_workspace_commands import command_conversation, command_root, isolated_command_database, send_command
from test_shell_approvals import pending


@pytest.mark.anyio
async def test_cancel_waits_for_process_registration_handoff(command_root, monkeypatch):
    """取消不能切断短登记事务；交接完成后才杀子进程和结束调用。

    Args:
        command_root：本轮隔离运行目录。
        monkeypatch：在登记边界暂停，确定性模拟事务尚未完成。
    """
    import sys
    from app.runtime.manager import manager
    from app.workspaces.commands import run_process
    reached, release = asyncio.Event(), asyncio.Event()
    completed = False
    async def held_registration(process):
        """暂停登记边界，但不打印脚本或 PID 之外的信息。

        Args:
            process：运行器实际创建的子进程。
        """
        nonlocal completed
        reached.set()
        await release.wait()
        completed = True
    monkeypatch.setattr(manager, 'attach_command', held_registration)
    task = asyncio.create_task(run_process((sys.executable, '-I', '-c', 'import time;time.sleep(60)'),
        root=command_root, payload=b'', timeout=10, output_limit=1024))
    try:
        await asyncio.wait_for(reached.wait(), 5)
        task.cancel()
        await asyncio.sleep(.05)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    assert completed


async def enable_service(client, headers, rid, wid):
    """通过公开配置开启服务，保留默认限额。

    Args:
        client：本轮真实 ASGI 客户端。
        headers：Owner 认证头。
        rid：执行角色。
        wid：工作区身份。
    """
    config = (await client.get(f'/api/runtime/config?scope=workspace&scope_id={wid}', headers=headers)).json()
    assert (await client.put('/api/runtime/config', headers=headers, json={'scope': 'workspace', 'scope_id': wid,
        'limit': config['limit'], 'expected_revision': config['revision'], 'services_enabled': True})).status_code == 200
    role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
    assert (await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_start_service']})).status_code == 200


async def launch(client, headers, cid):
    """申请并批准实际 HTTP 服务，等待来源 execution 收尾后返回资源。

    Args:
        client：本轮客户端。
        headers：Owner 认证。
        cid：目标会话。
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import AgentExecution
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    sent = await send_command(client, headers, cid, f'[SERVICE_FAKE:{port}]')
    approval = await pending(client, headers, cid)
    assert (await client.post(f"/api/conversations/{cid}/tool-approvals/{approval['id']}/decision", headers=headers,
        json={'decision': 'approve', 'request_digest': approval['request_digest']})).status_code == 200
    for _ in range(300):
        async with SessionLocal() as session:
            state = await session.scalar(select(AgentExecution.status).where(AgentExecution.generation_id == sent['generation_id']))
        if state == 'completed':
            break
        await asyncio.sleep(.02)
    rows = (await client.get(f'/api/conversations/{cid}/processes', headers=headers)).json()['items']
    row = next(row for row in rows if row['port'] == port)
    assert row['state'] == 'ready'
    return row


@pytest.mark.anyio
async def test_service_survives_generation_and_stops_explicitly(command_root, isolated_command_database):
    """回答结束仍提供 HTTP，停止后登记和实际端口都回收。

    Args:
        command_root：本轮独占外部目录。
        isolated_command_database：全新迁移库。
    """
    (command_root / 'index.html').write_text('<h1>Hello managed service</h1>', encoding='utf-8')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        config = (await client.get(f'/api/runtime/config?scope=workspace&scope_id={wid}', headers=headers)).json()
        enabled = await client.put('/api/runtime/config', headers=headers, json={'scope': 'workspace', 'scope_id': wid,
            'limit': 5, 'expected_revision': config['revision'], 'services_enabled': True})
        assert enabled.status_code == 200
        role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
        await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_start_service']})
        await send_command(client, headers, cid, f'[SERVICE_FAKE:{port}]')
        approval = await pending(client, headers, cid)
        assert approval['tool_name'] == 'workspace_start_service'
        assert approval['port'] == port
        decision = await client.post(f"/api/conversations/{cid}/tool-approvals/{approval['id']}/decision", headers=headers,
            json={'decision': 'approve', 'request_digest': approval['request_digest']})
        assert decision.status_code == 200
        for _ in range(200):
            history = (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()
            if not history['active_generation_ids']:
                break
            await asyncio.sleep(.03)
        rows = (await client.get(f'/api/conversations/{cid}/processes', headers=headers)).json()['items']
        assert len(rows) == 1 and rows[0]['state'] == 'ready'
        async with httpx.AsyncClient(trust_env=False) as browser:
            response = await browser.get(f'http://127.0.0.1:{port}')
            assert response.status_code == 200 and 'Hello managed service' in response.text
        runtime_id = rows[0]['id']
        stopped = await client.post(f'/api/conversations/{cid}/processes/{runtime_id}/stop', headers=headers)
        assert stopped.status_code == 200 and stopped.json()['state'] == 'stopped'
        assert (await client.get(f'/api/runtime/config?scope=workspace&scope_id={wid}', headers=headers)).json()['used'] == 0
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', port)) != 0


@pytest.mark.anyio
async def test_deletion_drains_only_its_scope_and_keeps_shared_workspace_service(command_root, isolated_command_database):
    """会话删除不误停共享工作区其他会话，工作区删除跨会话收口且保留目录。

    Args:
        command_root：本轮外部目录。
        isolated_command_database：新数据库。
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.runtime.models import CleanupItem, CleanupOperation
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_service(client, headers, rid, wid)
        other = (await client.post('/api/conversations', headers=headers, json={'type': 'single', 'title': '另一个服务会话',
            'role_ids': [rid], 'workspace_binding_id': wid})).json()['id']
        first, second = await launch(client, headers, cid), await launch(client, headers, other)
        assert (await client.delete(f'/api/conversations/{cid}', headers=headers)).status_code == 409
        assert (await client.delete(f'/api/conversations/{cid}?confirm_cleanup=true', headers=headers)).status_code == 204
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', first['port'])) != 0
        async with httpx.AsyncClient(trust_env=False) as browser:
            assert (await browser.get(f"http://127.0.0.1:{second['port']}")).status_code == 200
        async with SessionLocal() as session:
            operation = await session.scalar(select(CleanupOperation).where(CleanupOperation.reason == 'conversation_delete', CleanupOperation.state == 'complete'))
            items = list((await session.scalars(select(CleanupItem).where(CleanupItem.operation_id == operation.id))).all())
            assert [item.runtime_id for item in items] == [first['id']]
            assert all(item.state == 'complete' for item in items)
        assert (await client.delete(f'/api/workspaces/{wid}?confirm_cleanup=true', headers=headers)).status_code == 204
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', second['port'])) != 0
        assert command_root.is_dir()


@pytest.mark.anyio
async def test_lowered_quota_rejects_and_releases_pending_start(command_root, isolated_command_database):
    """降低配置不杀已运行者，尚未启动者在决定时拒绝并释放自身预留。

    Args:
        command_root：本轮目录。
        isolated_command_database：全新数据库。
    """
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_service(client, headers, rid, wid)
        running = await launch(client, headers, cid)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        await send_command(client, headers, cid, f'[SERVICE_FAKE:{port}]')
        approval = await pending(client, headers, cid)
        config = (await client.get(f'/api/runtime/config?scope=conversation&scope_id={cid}', headers=headers)).json()
        assert config['used'] == 2
        assert (await client.put('/api/runtime/config', headers=headers, json={'scope': 'conversation', 'scope_id': cid,
            'limit': 1, 'expected_revision': config['revision']})).status_code == 200
        decision = await client.post(f"/api/conversations/{cid}/tool-approvals/{approval['id']}/decision", headers=headers,
            json={'decision': 'approve', 'request_digest': approval['request_digest']})
        assert decision.json()['status'] == 'rejected'
        for _ in range(100):
            if (await client.get(f'/api/runtime/config?scope=conversation&scope_id={cid}', headers=headers)).json()['used'] == 1:
                break
            await asyncio.sleep(.02)
        assert (await client.get(f'/api/runtime/config?scope=conversation&scope_id={cid}', headers=headers)).json()['used'] == 1
        async with httpx.AsyncClient(trust_env=False) as browser:
            assert (await browser.get(f"http://127.0.0.1:{running['port']}")).status_code == 200


@pytest.mark.anyio
async def test_backend_hard_exit_reaps_service_and_restart_does_not_replay(command_root, isolated_command_database):
    """杀死真实后端宿主后，控制通道 EOF 回收后代；重启只记录中断。

    Args:
        command_root：本轮精确目录。
        isolated_command_database：子进程和随后恢复共享的本用例独立数据库。
    """
    import json
    import sys
    from pathlib import Path
    import psutil
    process = await asyncio.create_subprocess_exec(sys.executable, 'tests/runtime_crash_worker.py', str(command_root),
        cwd=Path(__file__).resolve().parents[1], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        info = json.loads(await asyncio.wait_for(process.stdout.readline(), 15))
        assert info['children']
        guardian = psutil.Process(info['pid'])
        process.kill()
        await process.wait()
        for _ in range(200):
            remaining = []
            for child in info['children']:
                try:
                    actual = psutil.Process(child['pid'])
                    if str(actual.create_time()) == child['birth'] and actual.status() != psutil.STATUS_ZOMBIE:
                        remaining.append(child['pid'])
                except psutil.NoSuchProcess:
                    pass
            guardian_gone = not guardian.is_running() or guardian.status() == psutil.STATUS_ZOMBIE
            with socket.socket() as probe:
                probe.settimeout(.1)
                port_closed = probe.connect_ex(('127.0.0.1', info['port'])) != 0
            # 后代快照退出不等于监管器及其继承句柄已经收口；在同一预算内核对全部证据。
            if not remaining and guardian_gone and port_closed:
                break
            await asyncio.sleep(.02)
        assert remaining == []
        assert guardian_gone and port_closed
        from app.main import app
        from app.runtime import registry
        async with app.router.lifespan_context(app):
            restored = await registry.get(info['id'])
            assert restored.state == 'interrupted'
            assert (await registry.quota_view('world', 0))['used'] == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
