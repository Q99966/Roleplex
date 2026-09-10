"""Shell 私有详情：原审批关联、输出保留和旧调用降级。"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from test_workspace_commands import command_conversation, command_root, isolated_command_database, send_command
from test_shell_approvals import enable_shell, pending


@pytest.mark.anyio
async def test_shell_details_and_legacy_approval_lookup(command_root, isolated_command_database):
    """新调用保留空输出事实，旧调用只恢复脚本，过期不再解密。

    Args:
        command_root：本轮外部目录。
        isolated_command_database：逐用例全新迁移库。
    """
    from sqlalchemy import select, delete
    from app.db import SessionLocal
    from app.models import ToolExecutionDetail, ToolApprovalRequest
    from accounts import TEST_PASSWORD, guest_username
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_shell(client, headers, rid, wid)
        await send_command(client, headers, cid, '[SHELL_APPROVAL_FAKE]')
        approval = await pending(client, headers, cid)
        await client.post(f"/api/conversations/{cid}/tool-approvals/{approval['id']}/decision", headers=headers,
            json={'decision': 'approve', 'request_digest': approval['request_digest']})
        for _ in range(150):
            history = (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()
            if not history['active_generation_ids']:
                break
            await asyncio.sleep(.02)
        reply = history['items'][-1]
        part = next(part for part in reply['parts_json'] if part['type'] == 'tool_call')
        url = f"/api/conversations/{cid}/messages/{reply['id']}/tools/{part['call_id']}"
        response = await client.get(url, headers=headers)
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        detail = response.json()
        assert detail['availability'] == 'available'
        assert detail['shell']['script']['text'] == approval['script']
        assert detail['shell']['approval_status'] == 'approved'
        assert detail['shell']['approval_wait_ms'] >= 0
        assert detail['shell']['execution_duration_ms'] >= 0
        assert detail['shell']['output_availability'] == 'recorded'
        assert detail['shell']['stdout']['text'] == '' and detail['shell']['stderr']['text'] == ''
        guest = (await client.post('/api/auth/register', json={'username': guest_username('shell_details'), 'password': TEST_PASSWORD, 'nickname': 'Guest'})).json()
        assert (await client.get(url, headers={'Authorization': f"Bearer {guest['access_token']}"})).status_code == 403
        async with SessionLocal() as session:
            row = await session.scalar(select(ToolExecutionDetail).where(ToolExecutionDetail.call_id == part['call_id']))
            assert row.input_encrypted is None
            assert 'shell-v1' not in row.output_encrypted
            await session.execute(delete(ToolExecutionDetail).where(ToolExecutionDetail.id == row.id))
            await session.commit()
        legacy = (await client.get(url, headers=headers)).json()
        assert legacy['shell']['script']['text'] == approval['script']
        assert legacy['shell']['output_availability'] == 'not_recorded'
        assert legacy['shell']['execution_duration_ms'] is None
        # 查询没有重放 Shell；本轮脚本 append 只能发生一次。
        assert (command_root / 'shell-proof.txt').read_text() == 'approved\n'
        async with SessionLocal() as session:
            row = await session.get(ToolApprovalRequest, approval['id'])
            row.requested_at = datetime.now(timezone.utc) - timedelta(days=8)
            await session.commit()
        expired = (await client.get(url, headers=headers)).json()
        assert expired['availability'] == 'expired'
        assert expired.get('shell') is None


def test_shell_capture_preserves_two_streams_and_boundaries():
    """结构化采集不截坏 JSON，同时保留运行器截断和详情再次截断标记。"""
    from app.agent.tool_capture import capture_output, capture_input
    script = 'placeholder-not-copied'
    assert capture_input('workspace_run_shell', {'script': script}) is None
    value = capture_output('workspace_run_shell', json.dumps({'stdout': '\x1b[31m' + '中' * 30000,
        'stderr': 'error', 'stdout_bytes': 100000, 'stderr_bytes': 5, 'truncated': True,
        'duration_ms': 123, 'status': 'exited', 'exit_code': 0}))
    assert value['format'] == 'shell-v1'
    assert sum(len(value[name]['text'].encode()) for name in ('stdout', 'stderr')) <= 65536
    assert '\x1b' not in value['stdout']['text']
    assert value['stdout']['truncated'] and value['stdout']['bytes'] == 100000
    assert value['execution_duration_ms'] == 123
    assert value['stderr']['text'] == 'error'


@pytest.mark.anyio
async def test_shell_rejection_and_cipher_binding(command_root, isolated_command_database):
    """拒绝明确无进程输出，跨调用密文替换失败关闭。

    Args:
        command_root：本轮外部目录。
        isolated_command_database：独立数据库。
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import ToolExecutionDetail, ToolApprovalRequest
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_shell(client, headers, rid, wid)
        await send_command(client, headers, cid, '[SHELL_APPROVAL_FAKE]')
        approval = await pending(client, headers, cid)
        await client.post(f"/api/conversations/{cid}/tool-approvals/{approval['id']}/decision", headers=headers,
            json={'decision': 'reject', 'request_digest': approval['request_digest']})
        for _ in range(150):
            history = (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()
            if not history['active_generation_ids']:
                break
            await asyncio.sleep(.02)
        reply = history['items'][-1]
        url = f"/api/conversations/{cid}/messages/{reply['id']}/tools/{approval['tool_call_id']}"
        detail = (await client.get(url, headers=headers)).json()
        assert detail['shell']['approval_status'] == 'rejected'
        assert detail['shell']['output_availability'] == 'not_executed'
        assert detail['shell']['stdout'] is None and detail['shell']['execution_duration_ms'] is None
        async with SessionLocal() as session:
            row = await session.get(ToolApprovalRequest, approval['id'])
            row.request_digest = '0' * 64
            await session.commit()
        invalid = (await client.get(url, headers=headers)).json()
        assert invalid['availability'] == 'unavailable' and invalid['shell'] is None
        async with SessionLocal() as session:
            row = await session.scalar(select(ToolExecutionDetail))
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
        expired = (await client.get(url, headers=headers)).json()
        assert expired['availability'] == 'expired' and expired['shell'] is None
