"""回收实际服务时，数据库或机器日志故障不能截断进程清理。"""
import asyncio
import socket
import sys

import pytest
from test_workspace_commands import command_conversation, command_root, isolated_command_database
from test_runtime_service import enable_service, launch

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='受托管服务当前仅在 Linux 开放')


@pytest.mark.anyio
@pytest.mark.parametrize('fault', ['audit', 'database', 'logs'])
async def test_service_is_reaped_despite_persistence_fault(command_root, isolated_command_database, monkeypatch, fault):
    """故障期间优先回收已持有句柄，恢复后可重试登记而不重启脚本。

    Args:
        command_root：本轮独立目录。
        isolated_command_database：新数据库。
        monkeypatch：在实际服务启动后注入故障。
        fault：机器审计、数据库读取或私有日志保存。
    """
    from app.runtime import registry, logs
    from app.runtime.manager import manager
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_service(client, headers, rid, wid)
        row = await launch(client, headers, cid)
        route = f"/api/conversations/{cid}/processes/{row['id']}/stop"
        with monkeypatch.context() as scoped:
            if fault == 'audit':
                original = registry.logger.info
                def failed(event, **kwargs):
                    """Args:
                        event：固定事件名。
                        kwargs：安全关联字段，不打印。
                    """
                    if event == 'runtime.stop_dispatched':
                        raise OSError('audit fixture')
                    return original(event, **kwargs)
                scoped.setattr(registry.logger, 'info', failed)
            else:
                async def failed(*args, **kwargs):
                    """Args:
                        args：调用参数，不打印。
                        kwargs：调用参数，不打印。
                    """
                    raise OSError('persistence fixture')
                scoped.setattr(registry if fault == 'database' else logs, 'get' if fault == 'database' else 'save', failed)
            response = await client.post(route, headers=headers)
            assert response.status_code == (409 if fault == 'database' else 200)
            for _ in range(100):
                with socket.socket() as probe:
                    probe.settimeout(.1)
                    closed = probe.connect_ex(('127.0.0.1', row['port'])) != 0
                if closed:
                    break
                await asyncio.sleep(.02)
            assert closed
        retry = await client.post(route, headers=headers)
        assert retry.status_code == 200
        assert retry.json()['state'] == 'stopped'
        assert (await registry.quota_view('conversation', cid))['used'] == 0
        assert row['id'] not in manager.hosts
