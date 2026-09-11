"""常驻服务与逐次批准的 Shell 共存，仍受权限、配额和清理门槛约束。"""
import asyncio
import sys

import httpx
import pytest
from test_workspace_commands import command_conversation, command_root, isolated_command_database, send_command, wait_command_messages
from test_runtime_service import enable_service, launch
from test_shell_approvals import enable_shell, pending

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='后台服务当前仅在 Linux 开放')


@pytest.mark.anyio
async def test_scope_cleanup_still_blocks_shell_before_approval(command_root, isolated_command_database, monkeypatch):
    """即使服务仍正常存活，批量清理门槛关闭后也不得发起 Shell 审批。

    Args:
        command_root：独占目录。
        isolated_command_database：新库。
        monkeypatch：暂停实际停止，稳定覆盖范围已冻结但服务尚未退出的窗口。
    """
    from app.runtime.manager import manager
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_service(client, headers, rid, wid)
        await launch(client, headers, cid)
        await enable_shell(client, headers, rid, wid)
        reached, release = asyncio.Event(), asyncio.Event()
        original = manager._stop_one
        async def held(*args, **kwargs):
            """Args:
                args：真实停止身份。
                kwargs：真实停止上下文。
            """
            reached.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(manager, '_stop_one', held)
        async def cleanup():
            """执行已确认的工作区范围清理。"""
            async with manager.cleanup_scope('workspace', wid, 'workspace_disabled', 1):
                pass
        task = asyncio.create_task(cleanup())
        try:
            await asyncio.wait_for(reached.wait(), 3)
            await send_command(client, headers, cid, '[SHELL_APPROVAL_FAKE]')
            message = await wait_command_messages(client, headers, cid)
            assert any(part.get('error_code') == 'RUNTIME_SCOPE_CLOSING' for part in message['parts_json'])
            assert (await client.get(f'/api/conversations/{cid}/tool-approvals', headers=headers)).json() == []
            assert not (command_root / 'shell-proof.txt').exists()
        finally:
            release.set()
            await task


@pytest.mark.anyio
@pytest.mark.parametrize('other_conversation', [False, True])
async def test_shell_is_approved_while_shared_workspace_service_stays_running(command_root, isolated_command_database, other_conversation):
    """同会话或其他会话的服务不阻止审批；拒绝不执行，批准只执行一次且不回收服务。

    Args:
        command_root：本轮独占工作区。
        isolated_command_database：逐用例迁移的新数据库。
        other_conversation：Shell 是否来自共享工作区的另一个会话。
    """
    from app.runtime import registry
    from accounts import TEST_PASSWORD, guest_username
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_service(client, headers, rid, wid)
        service = await launch(client, headers, cid)
        await enable_shell(client, headers, rid, wid)
        shell_cid = (await client.post('/api/conversations', headers=headers, json={'type': 'single', 'title': '共享工作区 Shell',
            'role_ids': [rid], 'workspace_binding_id': wid})).json()['id'] if other_conversation else cid
        proof = command_root / 'shell-proof.txt'
        for decision in ['reject', 'approve']:
            await send_command(client, headers, shell_cid, '[SHELL_APPROVAL_FAKE]')
            approval = await pending(client, headers, shell_cid)
            assert approval['tool_name'] == 'workspace_run_shell'
            assert approval['active_service_count'] == 1
            assert not proof.exists()
            route = f"/api/conversations/{shell_cid}/tool-approvals/{approval['id']}/decision"
            if decision == 'approve':
                guest = (await client.post('/api/auth/register', json={'username': guest_username('live-shell'),
                    'password': TEST_PASSWORD, 'nickname': 'Guest'})).json()
                assert (await client.post(route, headers={'Authorization': f"Bearer {guest['access_token']}"},
                    json={'decision': 'approve', 'request_digest': approval['request_digest']})).status_code == 403
            assert (await client.post(route, headers=headers, json={'decision': decision,
                'request_digest': approval['request_digest']})).status_code == 200
            await wait_command_messages(client, headers, shell_cid)
        assert proof.read_text() == 'approved\n'
        assert (await registry.get(service['id'])).state == 'ready'
        async with httpx.AsyncClient(trust_env=False) as external:
            assert (await external.get(f"http://127.0.0.1:{service['port']}" )).status_code == 200
        quota = await registry.quota_view('workspace', wid)
        await registry.configure('workspace', wid, limit=1, expected_revision=quota['revision'], actor_id=1)
        await send_command(client, headers, shell_cid, '[SHELL_APPROVAL_FAKE]')
        message = await wait_command_messages(client, headers, shell_cid)
        assert any(part.get('error_code') == 'RUNTIME_WORKSPACE_LIMIT' for part in message['parts_json'])
        assert proof.read_text() == 'approved\n'
