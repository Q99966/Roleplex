"""包装器正常退出及硬退出不能留下独立会话中的后端。"""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import httpx
import psutil
import pytest


@pytest.mark.skipif(os.name != 'posix', reason='验证 POSIX 包装器管道生命周期')
@pytest.mark.parametrize('termination', [signal.SIGTERM, signal.SIGKILL])
def test_wrapper_exit_reaps_backend(tmp_path, termination):
    """真实包装器丢失后后端主动退出，测试最后只清理自己创建的句柄。

    Args:
        tmp_path：隔离测试世界目录。
        termination：正常终止或不可捕获退出。
    """
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    backend = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, str(backend / 'scripts/run_world_server.py'),
        '--worlds-dir', str(tmp_path / 'worlds'), '--host', '127.0.0.1', '--port', str(port)],
        cwd=backend, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    child = None
    try:
        with httpx.Client(trust_env=False, timeout=.3) as client:
            for _ in range(200):
                try:
                    if client.get(f'http://127.0.0.1:{port}/api/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
            else:
                pytest.fail('Wrapper fixture did not become ready')
        child = psutil.Process(process.pid).children()[0]
        process.send_signal(termination)
        for _ in range(160):
            if not child.is_running() or child.status() == psutil.STATUS_ZOMBIE:
                break
            time.sleep(.05)
        else:
            pytest.fail('Backend survived wrapper termination')
        process.wait(timeout=3)
    finally:
        if child is not None and child.is_running():
            child.kill()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
