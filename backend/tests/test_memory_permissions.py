"""检索开启、Guest 隔离、游标、在途成员变更与工具结果的再次派发边界。"""
import asyncio

import pytest
from sqlalchemy import delete, select

from test_conversation_context import source
from test_context_execution import finished
from test_orchestrator import setup_group, command_root, isolated_command_database


@pytest.mark.anyio
async def test_owner_routes_and_invalid_cursor_do_not_leak_sources(command_root, isolated_command_database):
    from app.db import SessionLocal
    from accounts import TEST_PASSWORD, guest_username
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        guest = (await client.post('/api/auth/register', json={'username': guest_username('memory'), 'password': TEST_PASSWORD, 'nickname': '受控访客'})).json()
        unauthorized = {'Authorization': 'Bearer ' + guest['access_token']}
        base = f'/api/conversations/{cid}'
        assert (await client.get(base + '/context/compressions', headers=unauthorized)).status_code == 403
        assert (await client.post(base + '/context/compressions', headers=unauthorized, json={
            'role_id': ids[0], 'request_key': 'denied', 'expected_revision': 0})).status_code == 403
        assert (await client.post(base + '/memory/search', headers=unauthorized, json={'role_id': ids[0], 'query': '权限'})).status_code == 403
        assert (await client.get(base + '/memory/references', headers=unauthorized, params={'role_id': ids[0]})).status_code == 403
        async with SessionLocal() as session:
            for i in range(5):
                await source(session, cid, text=f'分页协议来源 {i}', sender_id=ids[0])
            await session.commit()
        body = {'role_id': ids[0], 'query': '分页协议', 'limit': 2}
        first = (await client.post(base + '/memory/search', headers=headers, json=body)).json()
        assert len(first['results']) == 2 and first['next_cursor']
        second = (await client.post(base + '/memory/search', headers=headers, json={**body, 'cursor': first['next_cursor']})).json()
        assert {row['source_id'] for row in first['results']}.isdisjoint(row['source_id'] for row in second['results'])
        altered = await client.post(base + '/memory/search', headers=headers, json={**body, 'query': '另一查询', 'cursor': first['next_cursor']})
        assert altered.status_code == 409
        async with SessionLocal() as session:
            await source(session, cid, text='新的分页协议来源', sender_id=ids[0])
            await session.commit()
        assert (await client.post(base + '/memory/search', headers=headers, json={**body, 'cursor': first['next_cursor']})).status_code == 409


@pytest.mark.anyio
async def test_related_source_revoked_after_tool_read_is_not_sent_to_next_model_call(command_root, isolated_command_database, monkeypatch):
    from app.db import SessionLocal
    from app.models import ConversationMember, ModelCallUsage
    from app.memory import service
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.read
    async def paused(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set(); await asyncio.wait_for(release.wait(), 15)
        return result
    monkeypatch.setattr(service, 'read', paused)
    async with setup_group(command_root) as (client, headers, source_cid, ids, _):
        role = (await client.get(f'/api/roles/{ids[0]}', headers=headers)).json()
        assert (await client.put(f'/api/roles/{ids[0]}', headers=headers, json={**role,
            'expected_revision': role['revision'], 'builtin_tools': ['memory_search', 'memory_read']})).status_code == 200
        dest = (await client.post('/api/conversations', headers=headers,
            json={'title': '检索目的地', 'type': 'single', 'role_ids': [ids[0]]})).json()['id']
        async with SessionLocal() as session:
            await source(session, source_cid, text='访问变更协议：受控资料 REVOCATION_MEMORY_VALUE', sender_id=ids[1])
            await session.commit()
        assert (await client.post(f'/api/conversations/{dest}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '[MEMORY_PROBE] 访问变更协议'}]})).status_code == 202
        try:
            await asyncio.wait_for(entered.wait(), 10)
            async with SessionLocal() as session:
                await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == source_cid,
                    ConversationMember.member_type == 'role', ConversationMember.member_id == ids[0]))
                await session.commit()
        finally:
            release.set()
        execution = (await finished(dest, 1))[0]
        assert execution.status == 'failed' and execution.error_code == 'CONTEXT_SOURCE_CHANGED'
        async with SessionLocal() as session:
            calls = (await session.scalars(select(ModelCallUsage).where(ModelCallUsage.execution_id == execution.execution_id))).all()
            assert len(calls) == 2
        records = (await client.get(f'/api/conversations/{dest}/memory/references', headers=headers, params={'role_id': ids[0]})).json()
        assert records['items'] and all(not row['available'] and 'source' not in row for row in records['items'])
