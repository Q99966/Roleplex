"""真实进程验证就绪/输出边界；审批与权限在工具集成测试中独立覆盖。"""
import asyncio
import shlex
import socket
import sys

import pytest
from test_workspace_commands import command_conversation, command_root, isolated_command_database

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='受托管服务当前仅在 Linux 开放')


@pytest.fixture(autouse=True)
def authorized_probe_fixture(monkeypatch, isolated_command_database):
    """探针单元用例直接构造 Host，不构造审批身份；实际授权由工具集成矩阵覆盖。

    Args:
        monkeypatch：仅在本模块隔离新增加的审批后授权依赖，不替换配额、清理或真实进程检查。
        isolated_command_database：先完成模块重载，再为本轮新模块设置探针依赖。
    """
    from app.workspaces import approvals

    async def allowed(_request):
        """接受本模块受控启动输入。

        Args:
            _request：手工构造的探针固件，不是模型调用。
        """
        return True

    monkeypatch.setattr(approvals, 'authorized_execution', allowed)


@pytest.mark.anyio
async def test_cleanup_during_spawn_never_delivers_script(command_root, isolated_command_database, monkeypatch):
    """冻结清单覆盖尚未交付的 spawn，进程出现后不再交付用户脚本。

    Args:
        command_root：专用测试目录。
        isolated_command_database：新库。
        monkeypatch：在实际创建子进程前暂停交接。
    """
    from app.runtime import registry
    from app.runtime.manager import manager, Host
    from app.workspaces.shell import shell_configuration
    async with command_conversation(command_root) as (_client, _headers, cid, rid, wid):
        port = unused_port()
        row = await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
            execution_id='spawn-fixture', role_id=rid, tool_call_id='held', tool_name='workspace_start_service', kind='service', port=port)
        reached, release = asyncio.Event(), asyncio.Event()
        original = asyncio.create_subprocess_exec
        async def spawning(*args, **kwargs):
            """Args:
                args：宿主生成的解释器参数，不输出。
                kwargs：冻结的 cwd/环境/管道，不输出。
            """
            reached.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawning)
        host = Host(row.id, ready=asyncio.get_running_loop().create_future())
        manager.hosts[row.id] = host
        request = {**shell_configuration(), 'script': 'printf forbidden > should-not-exist.txt', 'root_path': str(command_root),
            'port': port, 'health_path': '/', 'lifetime_seconds': 10, 'ready_timeout_seconds': 1, 'approval_id': None}
        host.task = asyncio.create_task(manager._host(host, request))
        await asyncio.wait_for(reached.wait(), 3)
        async def clean():
            """回收在途启动，期间不允许新预留越过门槛。"""
            async with manager.cleanup_scope('conversation', cid, 'conversation_delete', 1):
                pass
        cleanup = asyncio.create_task(clean())
        try:
            for _ in range(100):
                if host.stop.is_set():
                    break
                await asyncio.sleep(.01)
            assert host.stop.is_set()
        finally:
            release.set()
        await asyncio.wait_for(cleanup, 12)
        assert not (command_root / 'should-not-exist.txt').exists()
        assert (await registry.quota_view('conversation', cid))['used'] == 0


def unused_port():
    """从 OS 分配本轮临时端口；竞争时应明确失败，不能接管占用者。"""
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


@pytest.mark.anyio
@pytest.mark.parametrize('scenario,expected', [
    ('status_500', 'RUNTIME_READY_TIMEOUT'), ('redirect', 'RUNTIME_READY_TIMEOUT'),
    ('wrong_port', 'RUNTIME_LISTENER_MISMATCH'), ('wrong_address', 'RUNTIME_LISTENER_MISMATCH'),
    ('exit', 'RUNTIME_START_FAILED'), ('exit125', 'RUNTIME_START_FAILED'), ('large_logs', None), ('clock_rollback', None),
])
async def test_http_readiness_requires_owned_valid_listener(command_root, isolated_command_database, scenario, expected, monkeypatch):
    """错误 HTTP、偏离声明的监听与提前退出不能变成 ready，大输出仍能排空。

    Args:
        command_root：仅本用例可写目录。
        isolated_command_database：新库。
        scenario：受控故障类型。
        expected：固定预期错误码；空表示成功的大输出路径。
        monkeypatch：仅替换服务宿主的墙钟，不影响 Token 或系统时钟。
    """
    from app.runtime import registry
    from app.runtime.logs import PAGE_BYTES, RING_BYTES
    from app.runtime.manager import manager, Host
    from app.workspaces.shell import shell_configuration
    port = unused_port()
    actual_port = unused_port() if scenario == 'wrong_port' else port
    address = '127.0.0.2' if scenario == 'wrong_address' else '127.0.0.1'
    status = 500 if scenario == 'status_500' else 302 if scenario == 'redirect' else 200
    program = '\n'.join([
        'from http.server import BaseHTTPRequestHandler, HTTPServer',
        "print('中' * 400000, flush=True)" if scenario == 'large_logs' else 'pass',
        'class Handler(BaseHTTPRequestHandler):',
        ' def do_GET(self):',
        "  self.send_response(200 if self.path == '/redirect-must-not-be-followed' else 302)" if scenario == 'redirect' else f'  self.send_response({status})',
        "  self.send_header('Location', '/redirect-must-not-be-followed')",
        '  self.end_headers()',
        f"HTTPServer(('{address}', {actual_port}), Handler).serve_forever()",
    ])
    script = 'exit 125' if scenario == 'exit125' else 'exit 7' if scenario == 'exit' else f'{shlex.quote(sys.executable)} -u -c {shlex.quote(program)}'
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:9')
    monkeypatch.setenv('NO_PROXY', '')
    async with command_conversation(command_root) as (_client, _headers, cid, rid, wid):
        row = await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
            execution_id='probe-fixture', role_id=rid, tool_call_id=scenario, tool_name='workspace_start_service', kind='service', port=port)
        host = Host(row.id, ready=asyncio.get_running_loop().create_future())
        manager.hosts[row.id] = host
        request = {**shell_configuration(), 'script': script, 'root_path': str(command_root), 'port': port,
            'health_path': '/ready', 'lifetime_seconds': 1 if scenario == 'clock_rollback' else 10, 'ready_timeout_seconds': .8, 'approval_id': None}
        host.task = asyncio.create_task(manager._host(host, request))
        try:
            ready = await asyncio.wait_for(asyncio.shield(host.ready), 5)
            if expected:
                assert ready.state == 'failed' and ready.error_code == expected
            else:
                assert ready.state == 'ready'
                if scenario == 'clock_rollback':
                    from datetime import datetime, timedelta
                    from app.runtime import manager as module
                    class RewoundClock(datetime):
                        """模拟宿主墙钟回退一天，寿命仍必须按单调时钟结束。"""
                        @classmethod
                        def now(cls, tz=None):
                            """Args:
                                tz：原时间区间，不改变其类型。
                            """
                            return datetime.now(tz) - timedelta(days=1)
                    monkeypatch.setattr(module, 'datetime', RewoundClock)
                    await asyncio.wait_for(asyncio.shield(host.task), 3)
                    assert (await registry.get(row.id)).state == 'expired'
                else:
                    assert host.ring.bytes <= RING_BYTES
                    page = host.ring.page(0)
                    assert page['gap'] and sum(item['bytes'] for item in page['items']) <= PAGE_BYTES
                    assert all('\ufffd' not in item['text'] for item in page['items'])
        finally:
            host.stop.set()
            await asyncio.wait_for(asyncio.shield(host.task), 12)
        assert (await registry.quota_view('conversation', cid))['used'] == 0
        with socket.socket() as check:
            check.settimeout(.1)
            assert check.connect_ex(('127.0.0.1', actual_port)) != 0


@pytest.mark.anyio
async def test_foreign_http_200_is_not_accepted_or_stopped(command_root, isolated_command_database):
    """占用端口的陌生 HTTP 200 不能冒充就绪，拒绝后原服务仍可访问。

    Args:
        command_root：独立目录。
        isolated_command_database：新库。
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import httpx
    from app.runtime import registry
    from app.runtime.manager import manager, Host
    from app.workspaces.shell import shell_configuration
    class Handler(BaseHTTPRequestHandler):
        """本测试进程中的旁路服务，不属于受测监管器的后代。"""
        def do_GET(self):
            """返回受控 200，不访问用户文件。"""
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            """Args:
                args：测试请求摘要，不需要额外打印。
            """
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    try:
        async with command_conversation(command_root) as (_client, _headers, cid, rid, wid):
            row = await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid, execution_id='foreign-port',
                role_id=rid, tool_call_id='foreign', tool_name='workspace_start_service', kind='service', port=port)
            host = Host(row.id, ready=asyncio.get_running_loop().create_future())
            manager.hosts[row.id] = host
            request = {**shell_configuration(), 'script': 'exit 0', 'root_path': str(command_root), 'port': port,
                'health_path': '/', 'lifetime_seconds': 10, 'ready_timeout_seconds': 1, 'approval_id': None}
            host.task = asyncio.create_task(manager._host(host, request))
            await asyncio.wait_for(host.task, 3)
            result = await registry.get(row.id)
            assert result.state == 'failed' and result.error_code == 'RUNTIME_PORT_BUSY'
            assert host.process is None
            async with httpx.AsyncClient(trust_env=False) as client:
                assert (await client.get(f'http://127.0.0.1:{port}')).status_code == 200
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)
