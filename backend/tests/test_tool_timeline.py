"""有序工具 part、私有详情与历史正文兼容测试。"""
from __future__ import annotations

import pytest


def test_text_segments_preserve_tool_position_and_context():
    """跨工具边界追加 Unicode 文本，不挪动工具或改变历史正文。"""
    from app.services.chat import _with_text_part
    from app.context.projection import parts_text
    parts = [{'type': 'text', 'part_id': 'text-0', 'text': '前😀'},
             {'type': 'tool_call', 'call_id': 'a', 'tool_name': 'workspace_read', 'status': 'running'}]
    updated = _with_text_part(parts, '前😀后文')
    assert [part['type'] for part in updated] == ['text', 'tool_call', 'text']
    assert updated[0]['text'] == '前😀' and updated[2]['text'] == '后文'
    assert updated[2]['part_id'] == 'text-2'
    assert parts_text(updated) == parts_text([
        {'type': 'text', 'text': '前😀后文'}, parts[1],
    ])


def test_private_capture_is_bounded_and_unknown_tools_are_not_recorded():
    """只采集已登记工具字段，截断不破坏 UTF-8，未知工具不保存正文。"""
    from app.agent.tool_capture import capture_input, capture_output
    assert capture_input('mcp_unknown', {'secret': 'placeholder'}) is None
    capture = capture_input('workspace_read', {'path': 'hello.txt', 'secret': 'placeholder'})
    assert 'placeholder' not in capture['text']
    output = capture_output('workspace_read', '字' * 30000 + '\x1b[31m')
    assert output['truncated'] and len(output['text'].encode()) <= 65536
    assert '\x1b' not in output['text']


@pytest.mark.anyio
async def test_real_tool_timeline_and_owner_details(command_root, monkeypatch):
    """真实工具循环保存顺序与密文，Guest 无法取得 Owner 详情。

    Args:
        command_root：分层目录下的隔离工作区。
        monkeypatch：模拟不同 World 密钥以验证解密隔离。
    """
    from test_workspace_commands import command_conversation, send_command, wait_command_messages
    from app.db import SessionLocal
    from app.models import ToolExecutionDetail
    from sqlalchemy import select
    from accounts import TEST_PASSWORD, guest_username
    (command_root / 'hello.txt').write_text('private-placeholder-result', encoding='utf-8')
    async with command_conversation(command_root) as (client, headers, conversation_id, _, _):
        await send_command(client, headers, conversation_id, '[TOOL_TIMELINE_FAKE]')
        message = await wait_command_messages(client, headers, conversation_id)
        assert message['timeline_version'] == 1
        parts = message['parts_json']
        assert [part['type'] for part in parts] == ['text', 'tool_call', 'text', 'tool_call', 'text']
        assert [part['text'] for part in parts if part['type'] == 'text'] == ['先读取😀。', '读取完成，再统计。', '处理完成。']
        route = f"/api/conversations/{conversation_id}/messages/{message['id']}/tools/{parts[1]['call_id']}"
        detail = await client.get(route, headers=headers)
        assert detail.status_code == 200 and detail.headers['cache-control'] == 'no-store'
        body = detail.json()
        assert body['status'] == 'success' and body['availability'] == 'available'
        assert 'hello.txt' in body['input']['text'] and 'private-placeholder-result' in body['output']['text']
        assert 'private-placeholder-result' not in str(parts) and 'hello.txt' not in str(parts)
        guest = await client.post('/api/auth/register', json={
            'username': guest_username('tool-detail'), 'password': TEST_PASSWORD, 'nickname': 'Guest',
        })
        denied = await client.get(route, headers={'Authorization': f"Bearer {guest.json()['access_token']}"})
        assert denied.status_code == 403
        wrong = await client.get(route.replace(f"/messages/{message['id']}/", '/messages/999999/'), headers=headers)
        assert wrong.status_code == 404
        async with SessionLocal() as session:
            rows = (await session.scalars(select(ToolExecutionDetail).where(ToolExecutionDetail.message_id == message['id'])
                                           .order_by(ToolExecutionDetail.id))).all()
            assert len(rows) == 2
            assert all('hello.txt' not in row.input_encrypted for row in rows)
            assert all('private-placeholder-result' not in row.output_encrypted for row in rows)
            from app.models import ConversationMember, EventLog
            from datetime import datetime, timedelta, timezone
            session.add(ConversationMember(conversation_id=conversation_id, member_type='user',
                member_id=guest.json()['user']['id'], joined_at=datetime.now(timezone.utc)))
            await session.commit()
            events = (await session.scalars(select(EventLog).where(EventLog.conversation_id == conversation_id))).all()
            assert all('private-placeholder-result' not in str(event.payload_json) for event in events)
            first_id, second_output = rows[0].id, rows[1].output_encrypted
            original_output = rows[0].output_encrypted
            rows[0].output_encrypted = second_output
            await session.commit()
        # 同一 World 内把另一调用密文替换过来也必须失败。
        assert (await client.get(route, headers=headers)).json()['availability'] == 'unavailable'
        async with SessionLocal() as session:
            row = await session.get(ToolExecutionDetail, first_id)
            row.output_encrypted = original_output
            await session.commit()
        from app.services import tool_details
        from cryptography.fernet import Fernet
        with monkeypatch.context() as patch:
            patch.setattr(tool_details, '_cipher', lambda: Fernet(Fernet.generate_key()))
            assert (await client.get(route, headers=headers)).json()['availability'] == 'unavailable'
        guest_headers = {'Authorization': f"Bearer {guest.json()['access_token']}"}
        shared = (await client.get(f'/api/conversations/{conversation_id}/messages', headers=guest_headers)).json()
        assert 'private-placeholder-result' not in str(shared)
        assert (await client.get(route, headers=guest_headers)).status_code == 403
        async with SessionLocal() as session:
            row = await session.get(ToolExecutionDetail, first_id)
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
        expired = (await client.get(route, headers=headers)).json()
        assert expired['availability'] == 'expired' and expired['input'] is None and expired['output'] is None
        async with SessionLocal() as session:
            await tool_details.recover_details(session)
            await session.commit()
            row = await session.get(ToolExecutionDetail, first_id)
            assert row.input_encrypted is None and row.output_encrypted is None
            from sqlalchemy import delete, func
            from app.models import Message
            await session.execute(delete(Message).where(Message.id == message['id']))
            await session.commit()
            assert await session.scalar(select(func.count()).select_from(ToolExecutionDetail).where(
                ToolExecutionDetail.message_id == message['id'])) == 0


def test_timeline_websocket_disconnect_replays_order_and_snapshot(command_root):
    """工具执行中断线后准确回放，过期 epoch 快照保留顺序且没有私有正文。

    Args:
        command_root：本轮独立测试工作区。
    """
    import time
    from starlette.testclient import TestClient
    from accounts import ensure_owner_sync
    from app.main import app
    from test_ws_recovery import _authenticate, _read_until
    (command_root / 'hello.txt').write_text('ws-private-placeholder', encoding='utf-8')
    with TestClient(app) as client:
        token = ensure_owner_sync(client)['access_token']
        headers = {'Authorization': f'Bearer {token}'}
        config = client.post('/api/model-configs', headers=headers, json={
            'name': command_root.name, 'provider_type': 'openai_compatible', 'api_key': 'sk-placeholder',
        }).json()
        role = client.post('/api/roles', headers=headers, json={
            'name': command_root.name, 'model_config_id': config['id'], 'model_name': 'fake-model',
            'system_prompt': '工具恢复测试', 'builtin_tools': ['workspace_run_command'],
        }).json()
        binding = client.post('/api/workspaces', headers=headers, json={
            'display_name': command_root.name, 'root_path': str(command_root), 'acknowledge_existing_content': True,
        }).json()
        client.patch(f"/api/workspaces/{binding['id']}", headers=headers, json={'basic_commands_enabled': True})
        conversation = client.post('/api/conversations', headers=headers, json={
            'title': command_root.name, 'type': 'single', 'role_ids': [role['id']], 'workspace_binding_id': binding['id'],
        }).json()['id']
        with client.websocket_connect('/api/ws') as ws:
            epoch = _authenticate(ws, token)
            ws.send_json({'type': 'subscribe', 'conversation_id': conversation, 'after_event_seq': 0, 'stream_epoch': epoch})
            assert ws.receive_json()['type'] == 'subscribed'
            client.post(f'/api/conversations/{conversation}/messages', headers=headers,
                json={'parts': [{'type': 'text', 'text': '[TOOL_TIMELINE_FAKE]'}]})
            live = _read_until(ws, 'message_part_update')
        cut = live[-1]['event_seq']
        for _ in range(200):
            history = client.get(f'/api/conversations/{conversation}/messages', headers=headers).json()
            if len(history['items']) == 2 and history['items'][1]['status'] == 'done':
                break
            time.sleep(.03)
        assert history['items'][1]['status'] == 'done'
        latest = history['event_seq']
        with client.websocket_connect('/api/ws') as ws:
            _authenticate(ws, token)
            ws.send_json({'type': 'subscribe', 'conversation_id': conversation, 'after_event_seq': cut, 'stream_epoch': epoch})
            assert ws.receive_json()['type'] == 'subscribed'
            replay = [ws.receive_json() for _ in range(latest - cut)]
        assert [frame['event_seq'] for frame in replay] == list(range(cut + 1, latest + 1))
        assert replay[-1]['payload']['message']['parts_json'] == history['items'][1]['parts_json']
        assert [part['type'] for part in history['items'][1]['parts_json']] == ['text', 'tool_call', 'text', 'tool_call', 'text']
        assert 'ws-private-placeholder' not in str(replay)
        with client.websocket_connect('/api/ws') as ws:
            _authenticate(ws, token)
            ws.send_json({'type': 'subscribe', 'conversation_id': conversation, 'after_event_seq': latest, 'stream_epoch': 'old-epoch'})
            snapshot = ws.receive_json()
        assert snapshot['type'] == 'snapshot'
        assert snapshot['payload']['messages'][1]['parts_json'] == history['items'][1]['parts_json']


# 复用工作区夹具的分层、隔离与保留规则。
from test_workspace_commands import command_root  # noqa: E402,F401
