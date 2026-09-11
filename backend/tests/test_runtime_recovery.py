"""监管器死亡与 PID 复用必须保留未知状态，不能假装后代已回收。"""
import asyncio
import os
import signal
import sys

import psutil
import pytest
from test_workspace_commands import command_conversation, command_root, isolated_command_database
from test_runtime_service import enable_service, launch

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='受托管服务当前仅在 Linux 开放')


@pytest.mark.anyio
async def test_killed_guardian_does_not_release_unknown_descendants(command_root, isolated_command_database):
    """单独杀监管器时缺少回收证明；重启核查也不能只看根 PID 就释放名额。

    Args:
        command_root：专用 HTTP 固件目录。
        isolated_command_database：新库。
    """
    from app.runtime import registry
    from app.runtime.manager import manager
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_service(client, headers, rid, wid)
        row = await launch(client, headers, cid)
        host = manager.hosts[row['id']]
        children = psutil.Process(host.process.pid).children(recursive=True)
        try:
            os.kill(host.process.pid, signal.SIGKILL)
            await asyncio.wait_for(asyncio.shield(host.task), 5)
            assert (await registry.get(row['id'])).state == 'cleanup_required'
            await manager.initialize()
            assert (await registry.get(row['id'])).state == 'cleanup_required'
            assert (await registry.quota_view('conversation', cid))['used'] == 1
        finally:
            # 测试端只清理启动后捕获的真实子进程句柄；psutil 的句柄会校验 PID 出生身份。
            for child in reversed(children):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            for _ in range(100):
                if all(not child.is_running() or child.status() == psutil.STATUS_ZOMBIE for child in children):
                    break
                await asyncio.sleep(.02)
            assert all(not child.is_running() or child.status() == psutil.STATUS_ZOMBIE for child in children)
            await registry.finish(row['id'], 'interrupted', verified=True)
            await manager.stop_one(row['id'])


@pytest.mark.anyio
async def test_birth_mismatch_never_kills_foreign_pid(command_root, isolated_command_database, monkeypatch):
    """用当前测试进程模拟已复用的裸 PID，任何发送信号行为都使测试失败。

    Args:
        command_root：独立目录。
        isolated_command_database：新库。
        monkeypatch：禁止目标身份不匹配时发送信号。
    """
    from app.runtime import registry
    from app.runtime.manager import manager
    async with command_conversation(command_root) as (_client, _headers, cid, rid, wid):
        row = await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
            execution_id='reused-pid', role_id=rid, tool_call_id='foreign', tool_name='workspace_start_service', kind='service')
        await registry.change(row.id, state='running', pid=os.getpid(), birth='not-the-current-process')
        def forbidden(*args):
            """Args:
                args：不得用于向陌生 PID 发送信号。
            """
            raise AssertionError('不得对出生身份不符的进程发送信号')
        with monkeypatch.context() as scoped:
            scoped.setattr(os, 'kill', forbidden)
            scoped.setattr(os, 'killpg', forbidden)
            await manager.stop_one(row.id)
            await manager.initialize()
            assert (await registry.get(row.id)).state == 'cleanup_required'
        # 该条登记完全由测试伪造，没有实际受托管进程，不调用任何 OS 终止操作。
        await registry.finish(row.id, 'interrupted', verified=True)


@pytest.mark.anyio
async def test_new_kernel_boot_releases_old_unknown_instance_without_signals(command_root, isolated_command_database, monkeypatch):
    """内核启动身份变化可解除旧未知记录，不能向复用的 PID 发送信号。

    Args:
        command_root：测试目录。
        isolated_command_database：新库。
        monkeypatch：模拟启动 UUID 变化，不修改真实主机时钟或重启主机。
    """
    from app.runtime import registry
    from app.runtime.manager import manager
    async with command_conversation(command_root) as (_client, _headers, cid, rid, wid):
        monkeypatch.setattr(registry, 'boot_identity', lambda: 'old-boot-fixture')
        row = await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
            execution_id='boot-fixture', role_id=rid, tool_call_id='old', tool_name='workspace_start_service', kind='service')
        await registry.change(row.id, state='cleanup_required', pid=os.getpid(), birth='old-process')
        monkeypatch.setattr(registry, 'boot_identity', lambda: 'new-boot-fixture')
        def forbidden(*args):
            """Args:
                args：新启动期的 PID 不能作为旧实例被终止。
            """
            raise AssertionError('不能终止新启动期的进程')
        with monkeypatch.context() as scoped:
            scoped.setattr(os, 'kill', forbidden)
            scoped.setattr(os, 'killpg', forbidden)
            await manager.initialize()
        assert (await registry.get(row.id)).state == 'interrupted'
        assert (await registry.quota_view('conversation', cid))['used'] == 0
