"""世界任命、真实协调会话和普通角色读取范围相互隔离。"""
import pytest
from sqlalchemy import select

from test_orchestrator import setup_group, command_root, isolated_command_database
from test_context_execution import finished


@pytest.mark.anyio
async def test_binding_creates_persistent_coordination_and_role_replacement_keeps_history(command_root, isolated_command_database):
    from app.db import SessionLocal
    from app.models import AgentExecution
    async with setup_group(command_root) as (client, headers, _, ids, _):
        before = await client.get('/api/world-orchestrator', headers=headers)
        assert before.status_code == 200 and before.json()['fixed_identity'] is True and before.json()['status'] == 'needs_configuration'
        bound = await client.post('/api/world-orchestrator/import-role', headers=headers,
            json={'role_id': ids[3], 'expected_revision': 0})
        assert bound.status_code == 200
        state = bound.json(); cid = state['conversation']['id']
        assert cid not in {row['id'] for row in (await client.get('/api/conversations', headers=headers)).json()}
        sent = await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': 'COORDINATION_PRIVATE_MARKER 记录当前世界目标'}]})
        assert sent.status_code == 202
        executions = await finished(cid, 1)
        assert executions[0].execution_kind == 'world_coord' and executions[0].status == 'completed'
        snapshot = executions[0].context_snapshot_json
        assert snapshot['world_orchestrator']['appointment_revision'] == state['revision']
        assert (await client.post('/api/world-orchestrator/import-role', headers=headers,
            json={'role_id': ids[0], 'expected_revision': 0})).status_code == 409
        changed = await client.post('/api/world-orchestrator/import-role', headers=headers,
            json={'role_id': ids[0], 'expected_revision': state['revision']})
        assert changed.status_code == 200 and changed.json()['conversation']['id'] == cid
        history = (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()['items']
        assert any('COORDINATION_PRIVATE_MARKER' in str(row['parts_json']) for row in history)
        assert (await client.put(f'/api/conversations/{cid}/members', headers=headers,
            json={'role_ids': [ids[1]], 'expected_revision': changed.json()['conversation']['revision']})).status_code == 409
        # 更换任职角色后仍压缩同一份岗位会话，摘要不依附旧人格。
        from test_conversation_context import source
        from test_context_compaction import wait_compaction
        from uuid import uuid4
        async with SessionLocal() as session:
            for index in range(6):
                await source(session, cid, sender_id=state['role_id'], text=f'岗位材料 {index}：' + '受控长期目标。' * 80)
            await session.commit()
        base = f'/api/conversations/{cid}/context'
        before = (await client.get(base, headers=headers)).json()
        compression = await client.post(base + '/compressions', headers=headers, json={'request_key': uuid4().hex,
            'expected_revision': before['revision'], 'role_id': state['role_id'], 'keep_recent': 1, 'target_tokens': 900, 'instructions': '保留世界目标。'})
        assert compression.status_code == 202
        job = await wait_compaction(client, headers, cid, compression.json()['id'])
        assert job['status'] == 'completed'
        preview = (await client.post(base + '/preview', headers=headers, json={'role_id': state['role_id'], 'include_content': True})).json()
        assert preview['material']['summary_id'] == job['id']
        assert any('会话历史摘要' in item['content'] for item in preview['messages'])


@pytest.mark.anyio
async def test_world_history_cannot_be_read_from_same_roles_ordinary_chat(command_root, isolated_command_database):
    from test_conversation_context import source
    from app.db import SessionLocal
    async with setup_group(command_root) as (client, headers, _, ids, _):
        bound = await client.post('/api/world-orchestrator/import-role', headers=headers,
            json={'role_id': ids[0], 'expected_revision': 0})
        assert bound.status_code == 200
        cid = bound.json()['conversation']['id']
        async with SessionLocal() as session:
            await source(session, cid, sender_id=bound.json()['role_id'], text='WORLD_PRIVATE_SOURCE_ONLY')
            await session.commit()
        ordinary = (await client.post('/api/conversations', headers=headers,
            json={'type': 'single', 'title': '普通角色会话', 'role_ids': [ids[0]]})).json()['id']
        result = await client.post(f'/api/conversations/{ordinary}/memory/search', headers=headers,
            json={'role_id': ids[0], 'query': 'WORLD_PRIVATE_SOURCE_ONLY', 'scope': 'related'})
        assert result.status_code == 200 and result.json()['results'] == []
        allowed = await client.post(f'/api/conversations/{cid}/memory/search', headers=headers,
            json={'role_id': bound.json()['role_id'], 'query': 'WORLD_PRIVATE_SOURCE_ONLY', 'scope': 'current'})
        assert allowed.status_code == 200 and len(allowed.json()['results']) == 1


@pytest.mark.anyio
async def test_rebind_stops_old_execution_and_guest_cannot_probe_coordination(command_root, isolated_command_database, monkeypatch):
    import asyncio
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.services import chat
    from accounts import guest_username, TEST_PASSWORD
    entered, closed = asyncio.Event(), asyncio.Event()
    class Slow(ScriptedChatModel):
        async def _astream(self, messages, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
                async for chunk in super()._astream(messages, **kwargs):
                    yield chunk
            finally:
                closed.set()
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Slow(turns=[ScriptedTurn(text='不能继续')], delay=0))
    async with setup_group(command_root) as (client, headers, _, ids, _):
        bound = (await client.post('/api/world-orchestrator/import-role', headers=headers,
            json={'role_id': ids[0], 'expected_revision': 0})).json()
        cid = bound['conversation']['id']
        guest = (await client.post('/api/auth/register', json={'username': guest_username('worldcoord'),
            'nickname': 'Guest', 'password': TEST_PASSWORD})).json()
        gh = {'Authorization': f"Bearer {guest['access_token']}"}
        assert (await client.get('/api/world-orchestrator', headers=gh)).status_code == 403
        assert (await client.post(f'/api/conversations/{cid}/messages', headers=gh,
            json={'parts': [{'type': 'text', 'text': '无权探测'}]})).status_code == 404
        await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '等待换绑'}]})
        await asyncio.wait_for(entered.wait(), 10)
        changed = await client.post('/api/world-orchestrator/import-role', headers=headers,
            json={'role_id': ids[1], 'expected_revision': bound['revision']})
        assert changed.status_code == 200
        rows = await finished(cid, 1)
        assert closed.is_set() and rows[0].status == 'stopped'
        returned = await client.post('/api/world-orchestrator/import-role', headers=headers,
            json={'role_id': ids[0], 'expected_revision': changed.json()['revision']})
        assert returned.status_code == 200 and returned.json()['revision'] > bound['revision']
        from app.db import SessionLocal
        from app.models import WorldCoordinationGrant
        async with SessionLocal() as session:
            assert (await session.get(WorldCoordinationGrant, rows[0].execution_id)).appointment_revision == bound['revision']
