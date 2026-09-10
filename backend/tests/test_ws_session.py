"""登录会话级 WebSocket 的订阅切换、同步水位与授权测试。"""
from __future__ import annotations

from test_ws_recovery import client, _owner_token, _single_chat, _authenticate, _read_until, _send_message, _wait_until_done


def test_same_socket_switches_subscriptions_and_ignores_stale_unsubscribe(client):
    """A/B/A 共用连接；旧取消不能清理新订阅。

    Args:
        client：本轮真实应用测试客户端。
    """
    token = _owner_token(client)
    a = _single_chat(client, token, '订阅-A')
    b = _single_chat(client, token, '订阅-B')
    with client.websocket_connect('/api/ws') as ws:
        epoch = _authenticate(ws, token)
        for conversation_id, sid in [(a, 'a-first'), (b, 'b'), (a, 'a-again')]:
            ws.send_json({'type': 'subscribe', 'conversation_id': conversation_id,
                'after_event_seq': 0, 'stream_epoch': epoch, 'subscription_id': sid})
            accepted = ws.receive_json()
            assert accepted.get('subscription_id') == sid
            ready = ws.receive_json()
            assert ready == {'type': 'sync_complete', 'conversation_id': conversation_id,
                'subscription_id': sid, 'stream_epoch': epoch, 'through_event_seq': 0}
        ws.send_json({'type': 'unsubscribe', 'subscription_id': 'a-first'})
        assert ws.receive_json() == {'type': 'unsubscribed', 'subscription_id': 'a-first', 'removed': False}
        _send_message(client, token, a, 'session-live')
        frames = _read_until(ws, 'message_done')
        assert all(frame['subscription_id'] == 'a-again' and frame['conversation_id'] == a for frame in frames)
        ws.send_json({'type': 'unsubscribe', 'subscription_id': 'a-again'})
        assert ws.receive_json()['removed'] is True
        ws.send_json({'type': 'ping'})
        assert ws.receive_json()['type'] == 'pong'


def test_sync_complete_follows_replay_and_snapshot(client):
    """同步确认位于恢复数据之后，不提前宣称 ready，也不占业务序号。

    Args:
        client：本轮应用测试客户端。
    """
    token = _owner_token(client)
    conversation = _single_chat(client, token, '同步水位')
    _send_message(client, token, conversation, 'session-recovery')
    latest = _wait_until_done(client, token, conversation)
    with client.websocket_connect('/api/ws') as ws:
        epoch = _authenticate(ws, token)
        ws.send_json({'type': 'subscribe', 'conversation_id': conversation, 'subscription_id': 'replay',
            'after_event_seq': 0, 'stream_epoch': epoch})
        frames = _read_until(ws, 'sync_complete')
        assert frames[0]['type'] == 'subscribed'
        assert frames[-2]['type'] == 'message_done'
        assert frames[-1]['through_event_seq'] == latest
        assert [frame['event_seq'] for frame in frames[1:-1]] == list(range(1, latest + 1))
        ws.send_json({'type': 'subscribe', 'conversation_id': conversation, 'subscription_id': 'snapshot',
            'after_event_seq': latest, 'stream_epoch': 'old-epoch'})
        snapshot = ws.receive_json()
        assert snapshot['type'] == 'snapshot' and snapshot['subscription_id'] == 'snapshot'
        assert ws.receive_json()['through_event_seq'] == snapshot['payload']['event_seq']


def test_established_socket_rechecks_revoked_token(client):
    """已认证连接收到控制帧时仍检查撤销状态。

    Args:
        client：本轮应用测试客户端。
    """
    token = _owner_token(client)
    with client.websocket_connect('/api/ws') as ws:
        _authenticate(ws, token)
        async def revoke():
            """仅在本轮测试库撤销当前 Owner 的既有 Token。"""
            from app.db import SessionLocal
            from app.models import User
            from sqlalchemy import select
            async with SessionLocal() as session:
                owner = await session.scalar(select(User).where(User.is_owner.is_(True)))
                owner.token_version += 1
                await session.commit()
        client.portal.call(revoke)
        ws.send_json({'type': 'ping'})
        assert ws.receive_json() == {'type': 'error', 'payload': {'code': 'AUTH_INVALID'}}


def test_subscription_errors_and_membership_loss_are_scoped(client):
    """错误与取消带操作身份；会话失效只释放该订阅，不误伤整个登录连接。

    Args:
        client：本轮测试客户端。
    """
    token = _owner_token(client)
    a = _single_chat(client, token, '权限会话-A')
    b = _single_chat(client, token, '权限会话-B')
    with client.websocket_connect('/api/ws') as ws:
        epoch = _authenticate(ws, token)
        ws.send_json({'type': 'subscribe', 'conversation_id': a, 'subscription_id': 'active',
            'stream_epoch': epoch, 'after_event_seq': 0})
        _read_until(ws, 'sync_complete')
        ws.send_json({'type': 'subscribe', 'conversation_id': 999999, 'subscription_id': 'denied'})
        assert ws.receive_json() == {'type': 'error', 'subscription_id': 'denied', 'conversation_id': 999999,
                                    'payload': {'code': 'CONVERSATION_NOT_FOUND'}}
        ws.send_json({'type': 'subscribe', 'conversation_id': {'unknown': 'placeholder'}, 'subscription_id': 'invalid'})
        assert ws.receive_json() == {'type': 'error', 'payload': {'code': 'VALIDATION_ERROR'}}
        response = client.delete(f'/api/conversations/{a}', headers={'Authorization': f'Bearer {token}'})
        assert response.status_code == 204
        ws.send_json({'type': 'ping'})
        revoked = ws.receive_json()
        assert revoked['subscription_id'] == 'active' and revoked['payload']['code'] == 'CONVERSATION_NOT_FOUND'
        assert ws.receive_json()['type'] == 'pong'
        ws.send_json({'type': 'subscribe', 'conversation_id': b, 'subscription_id': 'remaining',
            'stream_epoch': epoch, 'after_event_seq': 0})
        assert _read_until(ws, 'sync_complete')[-1]['subscription_id'] == 'remaining'
