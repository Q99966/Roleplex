"""G：共同执行授权复核，不改变各工具独立开关或合法脚本能力。"""
import socket
import sys

import pytest
from test_workspace_commands import command_conversation, command_root, isolated_command_database, send_command, wait_command_messages
from test_shell_approvals import enable_shell, pending
from test_runtime_service import enable_service

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='服务链路只在 Linux 验证')


@pytest.mark.anyio
@pytest.mark.parametrize('service', [False, True])
@pytest.mark.parametrize('member_type', ['user', 'role'])
async def test_pending_execution_rechecks_membership(command_root, isolated_command_database, service, member_type):
    """工具创建后成员被撤销，普通 Shell 和服务共同授权均不得继续通过。

    Args:
        command_root：本轮独立目录。
        isolated_command_database：每个参数用例使用全新迁移库。
        service：是否走服务审批。
        member_type：撤销触发 Owner 或执行角色的成员身份。
    """
    from sqlalchemy import delete
    from app.db import SessionLocal
    from app.models import ConversationMember, ToolApprovalRequest
    from app.workspaces.approvals import _authorized, decrypt_request

    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        if service:
            await enable_service(client, headers, rid, wid)
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            prompt = f'[SERVICE_FAKE:{port}]'
        else:
            await enable_shell(client, headers, rid, wid)
            prompt = '[SHELL_APPROVAL_FAKE]'
        await send_command(client, headers, cid, prompt)
        approval = await pending(client, headers, cid)
        async with SessionLocal() as session:
            row = await session.get(ToolApprovalRequest, approval['id'])
            request = decrypt_request(row)
        assert await _authorized(request)
        async with SessionLocal() as session:
            await session.execute(delete(ConversationMember).where(
                ConversationMember.conversation_id == cid, ConversationMember.member_type == member_type))
            await session.commit()
        assert not await _authorized(request)
        if member_type == 'role':
            decision = await client.post(f"/api/conversations/{cid}/tool-approvals/{approval['id']}/decision", headers=headers,
                json={'decision': 'approve', 'request_digest': approval['request_digest']})
            assert decision.status_code == (200 if service else 409)
            if service:
                assert decision.json()['status'] == 'rejected'
            else:
                assert decision.json()['error']['code'] == 'WORKSPACE_TOOL_NOT_AVAILABLE'
                await client.post(f"/api/conversations/{cid}/tool-approvals/{approval['id']}/decision", headers=headers,
                    json={'decision': 'reject', 'request_digest': approval['request_digest']})
            await wait_command_messages(client, headers, cid)
        assert not (command_root / 'shell-proof.txt').exists()


@pytest.mark.anyio
async def test_service_rechecks_authorization_before_delivering_script(command_root, isolated_command_database, monkeypatch):
    """审批后到宿主准备期间撤销能力，不得将脚本送入已创建的空监管进程。

    Args:
        command_root：本轮独占目录。
        isolated_command_database：全新迁移库。
        monkeypatch：只在宿主状态持久化边界撤销角色工具能力。
    """
    from app.db import SessionLocal
    from app.models import Role
    from app.runtime import registry

    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_service(client, headers, rid, wid)
        original = registry.change
        revoked = False

        async def revoke_at_handoff(runtime_id, **values):
            """在脚本交付之前制造实际配置变化。

            Args:
                runtime_id：真实实例身份。
                values：真实状态变更字段。
            """
            nonlocal revoked
            row = await original(runtime_id, **values)
            if values.get('state') == 'waiting_ready':
                async with SessionLocal() as session:
                    role = await session.get(Role, rid)
                    role.builtin_tools_json = []
                    await session.commit()
                revoked = True
            return row

        monkeypatch.setattr(registry, 'change', revoke_at_handoff)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        await send_command(client, headers, cid, f'[SERVICE_FAKE:{port}]')
        approval = await pending(client, headers, cid)
        response = await client.post(f"/api/conversations/{cid}/tool-approvals/{approval['id']}/decision", headers=headers,
            json={'decision': 'approve', 'request_digest': approval['request_digest']})
        assert response.status_code == 200
        await wait_command_messages(client, headers, cid)
        assert revoked
        row = await registry.get(approval['runtime_id'])
        assert row.state in registry.TERMINAL
        assert row.error_code == 'WORKSPACE_TOOL_NOT_AVAILABLE'
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', port)) != 0


@pytest.mark.anyio
async def test_read_tool_returns_full_hash_and_rejects_stale_update(command_root, isolated_command_database):
    """实际工具链分段读取保留全文件 hash；版本变化须拒绝旧 hash，不提前实现 edit。

    Args:
        command_root：本轮独立目录。
        isolated_command_database：全新迁移库。
    """
    import hashlib
    import json
    from datetime import datetime, timezone
    from app.db import SessionLocal
    from app.models import AgentExecution, Generation, Role
    from app.workspaces.tools import create_workspace_tools

    content = '中文🙂\n' * 12000
    target = command_root / 'hash-sample.txt'
    target.write_text(content, encoding='utf-8')
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
        async with SessionLocal() as session:
            role = await session.get(Role, rid)
            role.builtin_tools_json = ['workspace_read', 'workspace_write']
            generation = Generation(conversation_id=cid, status='running', stream_epoch='hash-test')
            session.add(generation)
            await session.flush()
            execution = AgentExecution(execution_id=command_root.name, conversation_id=cid, generation_id=generation.id,
                role_id=rid, chain_id='hash-test', execution_kind='single', status='running', created_at=datetime.now(timezone.utc))
            session.add(execution)
            await session.commit()
            tools = {tool.name: tool for tool in await create_workspace_tools(session, execution_id=execution.execution_id,
                conversation_id=cid, role=role, triggered_by_user_id=role.created_by, allow_dangerous=True)}
        first = json.loads(await tools['workspace_read'].ainvoke({'path': target.name, 'max_bytes': 16}))
        full_page = json.loads(await tools['workspace_read'].ainvoke({'path': target.name, 'max_bytes': 65536}))
        second = json.loads(await tools['workspace_read'].ainvoke({'path': target.name, 'offset_bytes': first['next_offset'], 'max_bytes': 16}))
        assert not first['eof']
        assert first['sha256'] == second['sha256'] == hashlib.sha256(content.encode()).hexdigest()
        assert full_page['sha256'] == first['sha256'] and not full_page['eof']
        assert first['sha256'] != hashlib.sha256(first['text'].encode()).hexdigest()
        target.write_text(content + '外部变化', encoding='utf-8')
        fresh = json.loads(await tools['workspace_read'].ainvoke({'path': target.name, 'max_bytes': 16}))
        assert fresh['sha256'] != first['sha256']
        rejected = await tools['workspace_write'].ainvoke({'path': target.name, 'content': '不应写入', 'expected_sha256': first['sha256']})
        assert 'WORKSPACE_FILE_REVISION_CONFLICT' in rejected
        assert target.read_text(encoding='utf-8') == content + '外部变化'
