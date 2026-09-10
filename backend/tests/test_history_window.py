"""最近历史窗口的体积、连续游标、恢复及资源边界。"""
import json
import base64
from datetime import datetime, timezone

from test_ws_recovery import client, _owner_token, _single_chat, _authenticate


def seed_messages(client, conversation_id, texts):
    """在本轮隔离库播种真实 ORM 消息，不调用模型。

    Args:
        client：已启动的测试应用。
        conversation_id：本用例会话。
        texts：从旧到新的占位正文。
    """
    async def insert():
        """短事务插入本用例的历史数据。"""
        from app.db import SessionLocal
        from app.models import Message
        async with SessionLocal() as session:
            rows = [Message(conversation_id=conversation_id, sender_type='user', sender_id=1,
                parts_json=[{'type': 'text', 'text': text}], created_at=datetime.now(timezone.utc)) for text in texts]
            session.add_all(rows)
            await session.commit()
            return [row.id for row in rows]
    return client.portal.call(insert)


def test_window_counts_bytes_and_walks_contiguous_messages(client):
    """Unicode、代码、超预算单条都完整返回；连续向前无漏无重。

    Args:
        client：本轮应用。
    """
    token = _owner_token(client)
    cid = _single_chat(client, token, '有界历史')
    texts = ['短消息'] * 65 + ['🙂中文\\\n' * 10000, '```python\nprint("占位")\n```' * 800] + ['新消息'] * 60
    ids = seed_messages(client, cid, texts)
    headers = {'Authorization': f'Bearer {token}'}
    seen = []
    cursor = None
    oversized = 0
    while True:
        params = {'window': 'recent', **({'before': cursor} if cursor else {})}
        response = client.get(f'/api/conversations/{cid}/messages', headers=headers, params=params)
        assert response.status_code == 200
        page = response.json()
        assert page['page_bytes'] == len(response.content)
        assert len(page['items']) <= 50
        assert response.headers['cache-control'] == 'no-store'
        if page['oversized']:
            oversized += 1
            assert len(page['items']) == 1 and page['page_bytes'] > 65536
        else:
            assert page['page_bytes'] <= 65536
        seen = [item['id'] for item in page['items']] + seen
        if not page['has_more']:
            assert page['next_cursor'] is None
            break
        assert page['next_cursor'] and page['next_cursor'] != cursor
        cursor = page['next_cursor']
    assert seen == ids and oversized == 1
    # 旧调用方式保持兼容，不影响 ContextBuilder 或旧客户端。
    assert len(client.get(f'/api/conversations/{cid}/messages', headers=headers).json()['items']) == len(ids)


def test_window_cursor_scope_epoch_and_bounded_snapshot(client):
    """游标不是授权凭据，恢复快照也不得回退为全量读取。

    Args:
        client：本轮应用。
    """
    token = _owner_token(client)
    cid = _single_chat(client, token, '窗口恢复')
    other = _single_chat(client, token, '其他窗口')
    ids = seed_messages(client, cid, ['历史占位' * 1000] * 20)
    headers = {'Authorization': f'Bearer {token}'}
    url = f'/api/conversations/{cid}/messages'
    first = client.get(url, headers=headers, params={'window': 'recent'}).json()
    assert len(first['items']) < len(ids)
    for invalid in ['invalid', first['next_cursor']]:
        target = cid if invalid == 'invalid' else other
        response = client.get(f'/api/conversations/{target}/messages', headers=headers,
            params={'window': 'recent', 'before': invalid})
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'HISTORY_CURSOR_INVALID'
    assert client.get(url, params={'window': 'recent'}).status_code == 401
    expired = base64.urlsafe_b64encode(json.dumps([1, 'old-epoch', cid, ids[-1]]).encode()).decode()
    response = client.get(url, headers=headers, params={'window': 'recent', 'before': expired})
    assert response.status_code == 409 and response.json()['error']['code'] == 'HISTORY_CURSOR_EXPIRED'
    # 并发新增不会使 before 漂移，也不混入前一页。
    seed_messages(client, cid, ['后来新消息'])
    second = client.get(url, headers=headers, params={'window': 'recent', 'before': first['next_cursor']}).json()
    assert max(item['id'] for item in second['items']) < min(item['id'] for item in first['items'])
    with client.websocket_connect('/api/ws') as ws:
        _authenticate(ws, token)
        ws.send_json({'type': 'subscribe', 'conversation_id': cid, 'subscription_id': 'bounded',
            'stream_epoch': 'expired', 'after_event_seq': 0, 'history_window': 'recent'})
        frame = ws.receive_json()
        assert frame['type'] == 'snapshot'
        assert frame['payload']['has_more']
        assert frame['payload']['page_bytes'] == len(json.dumps(frame, ensure_ascii=False, separators=(',', ':')).encode())
        assert frame['payload']['page_bytes'] <= 65536
        assert ws.receive_json()['type'] == 'sync_complete'


def test_empty_window_guest_boundary_and_active_generation_outside_page(client):
    """未加载区域的活动生成仍可见，Guest 游标不能绕过成员授权。

    Args:
        client：本轮隔离应用。
    """
    from accounts import TEST_PASSWORD, guest_username
    token = _owner_token(client)
    cid = _single_chat(client, token, '窗口活动与权限')
    headers = {'Authorization': f'Bearer {token}'}
    url = f'/api/conversations/{cid}/messages'
    empty = client.get(url, headers=headers, params={'window': 'recent'}).json()
    assert empty['items'] == [] and empty['has_more'] is False and empty['next_cursor'] is None
    ids = seed_messages(client, cid, ['旧活动消息'] + ['新终态' * 3000] * 10)
    async def active():
        """为旧消息创建活动 generation，模拟窗口之外仍有生成的会话。"""
        from app.db import SessionLocal
        from app.models import Generation
        async with SessionLocal() as session:
            row = Generation(conversation_id=cid, assistant_message_id=ids[0], status='running', stream_epoch=empty['stream_epoch'])
            session.add(row)
            await session.commit()
            return row.id
    generation_id = client.portal.call(active)
    page = client.get(url, headers=headers, params={'window': 'recent'}).json()
    assert ids[0] not in [item['id'] for item in page['items']]
    assert generation_id in page['active_generation_ids']
    guest = client.post('/api/auth/register', json={'username': guest_username('history'), 'password': TEST_PASSWORD, 'nickname': '窗口 Guest'}).json()
    denied = client.get(url, headers={'Authorization': f"Bearer {guest['access_token']}"},
        params={'window': 'recent', 'before': page['next_cursor']})
    assert denied.status_code == 404 and denied.json()['error']['code'] == 'CONVERSATION_NOT_FOUND'


def test_first_window_uses_bounded_index_queries(client):
    """首屏候选查询带 LIMIT，不因整段历史变长先反序列化全表。

    Args:
        client：本轮应用。
    """
    from sqlalchemy import event
    from app.db import engine
    token = _owner_token(client)
    cid = _single_chat(client, token, '窗口查询界限')
    seed_messages(client, cid, ['历史'] * 500)
    reads = []
    def observe(conn, cursor, statement, parameters, context, executemany):
        """只记录 SQL 形状，不记录参数或消息。

        Args:
            conn：测试连接。
            cursor：驱动游标。
            statement：ORM 产生的查询形状。
            parameters：不得记录的绑定值。
            context：执行上下文。
            executemany：是否批处理。
        """
        if 'FROM messages' in statement:
            reads.append(statement)
    event.listen(engine.sync_engine, 'before_cursor_execute', observe)
    try:
        response = client.get(f'/api/conversations/{cid}/messages?window=recent', headers={'Authorization': f'Bearer {token}'})
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', observe)
    assert len(response.json()['items']) == 50
    assert len(reads) <= 51 and all('LIMIT' in query and 'messages.conversation_id' in query for query in reads)
