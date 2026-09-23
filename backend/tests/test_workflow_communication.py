"""任务广播与独立执行输入的真实边界，避免把自动安排记成 Owner 发言。"""
from uuid import uuid4
import pytest
from sqlalchemy import select
from test_orchestrator import setup_group, wait_state, command_root, isolated_command_database


@pytest.mark.anyio
async def test_three_role_tasks_have_one_broadcast_and_separate_complete_inputs(command_root, isolated_command_database, monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.services import chat
    from app.db import SessionLocal
    from app.models import Message, ConversationContextEntry
    captured = []
    class Model(ScriptedChatModel):
        async def _astream(self, messages, **kwargs):
            captured.append(messages[-1].content)
            async for chunk in super()._astream(messages, **kwargs): yield chunk
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Model(turns=[ScriptedTurn(text='受控工作已完成')], delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        did, goal = uuid4().hex, 'ONLY_ONCE_PUBLIC_GOAL'
        graph = {'runtime_version': 2, 'concurrency': 2, 'entries': ['a','b','c'], 'nodes': [
            {'id': name, 'kind': 'role', 'title': '任务 ' + name, 'role_id': rid, 'task': 'PRIVATE_NODE_' + name, 'tools': []}
            for name, rid in zip(['a','b','c'], ids)], 'edges': []}
        assert (await client.put(f'/api/conversations/{cid}/workflows/definitions/{did}', headers=headers,
            json={'name': '同批三个角色', 'expected_revision': 0, 'graph': graph})).status_code == 200
        started = await client.post(f'/api/conversations/{cid}/workflows/runs', headers=headers,
            json={'definition_id': did, 'expected_revision': 1, 'request_key': uuid4().hex, 'input_text': goal})
        assert started.status_code == 202
        run = await wait_state(client, headers, cid, started.json()['id'], lambda r: r['status'] == 'completed')
        history = (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()['items']
        notices = [m for m in history if m.get('communication', {}).get('kind') == 'workflow_dispatch']
        assert len(notices) == 1
        notice = notices[0]
        assert notice['sender_type'] == 'system'
        assert {r['id'] for r in notice['communication']['recipients']} == set(ids[:3])
        assert sum(goal in str(m['parts_json']) for m in history) == 1
        assert not any('PRIVATE_NODE_' in str(m['parts_json']) for m in history)
        assert len(captured) == 3 and all(goal in text for text in captured)
        assert all(sum('PRIVATE_NODE_' + name in text for name in ['a','b','c']) == 1 for text in captured)
        card = await client.get(f'/api/conversations/{cid}/messages/{notice["id"]}/dispatch', headers=headers)
        assert card.status_code == 200 and len(card.json()['items']) == 3
        assert all(item['status'] == 'completed' for item in card.json()['items'])
        for attempt in run['attempts']:
            result = await client.get(f'/api/conversations/{cid}/workflows/runs/{run["id"]}/attempts/{attempt["id"]}/input', headers=headers)
            assert result.status_code == 200 and goal in result.json()['text']
        async with SessionLocal() as session:
            entries = (await session.scalars(select(ConversationContextEntry).where(ConversationContextEntry.conversation_id == cid))).all()
            assert sum(goal in row.text for row in entries) == 1
            assert not any('PRIVATE_NODE_' in row.text for row in entries)
        from accounts import guest_username, TEST_PASSWORD
        from app.models import ConversationMember
        from app.db import now_utc
        guest = (await client.post('/api/auth/register', json={'username': guest_username('communication'),
            'nickname': '受控访客', 'password': TEST_PASSWORD})).json()
        gh = {'Authorization': 'Bearer ' + guest['access_token']}
        card_url = f'/api/conversations/{cid}/messages/{notice["id"]}/dispatch'
        assert (await client.get(card_url, headers=gh)).status_code == 404
        async with SessionLocal() as session:
            session.add(ConversationMember(conversation_id=cid, member_type='user', member_id=guest['user']['id'], joined_at=now_utc()))
            await session.commit()
        public = await client.get(card_url, headers=gh)
        assert public.status_code == 200 and 'PRIVATE_NODE_' not in public.text
        assert (await client.get(f'/api/conversations/{cid}/workflows/runs/{run["id"]}/attempts/{run["attempts"][0]["id"]}/input', headers=gh)).status_code == 403


@pytest.mark.anyio
async def test_legacy_input_keeps_original_record_but_invalidates_summary_and_memory(command_root, isolated_command_database, monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.services import chat
    from app.db import SessionLocal, now_utc
    from app.models import Message, WorkflowAttempt, WorkflowRun, ExecutionInput, ConversationContextEntry
    from app.communication import backfill
    from test_orchestrator import launch
    from test_context_compaction import wait_compaction
    from test_context_execution import finished
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: ScriptedChatModel(turns=[ScriptedTurn(text='受控执行完成')], delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        rid = await launch(client, headers, cid, {'runtime_version': 2, 'nodes': [
            {'id': 'work', 'kind': 'role', 'title': '旧节点', 'role_id': ids[0], 'task': '受控任务', 'tools': []}], 'edges': []})
        await wait_state(client, headers, cid, rid, lambda row: row['status'] == 'completed')
        async with SessionLocal() as session:
            run = await session.get(WorkflowRun, rid)
            attempt = await session.scalar(select(WorkflowAttempt).where(WorkflowAttempt.run_id == rid))
            stored = await session.get(ExecutionInput, attempt.execution_id)
            await session.delete(stored)
            original = Message(conversation_id=cid, sender_type='user', sender_id=run.owner_id, chain_id=run.chain_id,
                parts_json=[{'type': 'text', 'text': 'LEGACY_PRIVATE_INPUT ' + '这是一份内部任务输入。' * 350}],
                status='done', revision=0, mentions_json=[ids[0]], created_at=now_utc(),
                meta_json={'workflow_run_id': rid, 'workflow_attempt_id': attempt.id})
            session.add(original); await session.flush(); attempt.input_message_id = original.id
            await session.commit(); mid = original.id
        base = f'/api/conversations/{cid}'
        found = await client.post(base + '/memory/search', headers=headers,
            json={'role_id': ids[0], 'query': 'LEGACY_PRIVATE_INPUT', 'scope': 'current'})
        reference = found.json()['results'][0]['reference']
        context = (await client.get(base + '/context', headers=headers)).json()
        compression = await client.post(base + '/context/compressions', headers=headers,
            json={'request_key': uuid4().hex, 'expected_revision': context['revision'], 'role_id': ids[0],
                'keep_recent': 0, 'target_tokens': 900, 'instructions': '压缩受控历史'})
        assert compression.status_code == 202
        assert (await wait_compaction(client, headers, cid, compression.json()['id']))['status'] == 'completed'
        await backfill.run(); await backfill.run()
        async with SessionLocal() as session:
            row = await session.get(Message, mid)
            assert row.sender_type == 'user' and row.parts_json == original.parts_json and row.revision == 1
            entry = await session.get(ConversationContextEntry, mid)
            assert entry.state == 'excluded' and entry.reason == 'execution_input'
            assert (await session.get(ExecutionInput, attempt.execution_id)).text.startswith('LEGACY_PRIVATE_INPUT')
        history = (await client.get(base + '/messages', headers=headers)).json()['items']
        corrected = next(m for m in history if m['id'] == mid)
        assert corrected['sender_type'] == 'system' and 'LEGACY_PRIVATE_INPUT' not in str(corrected['parts_json'])
        assert (await client.get(base + f'/messages/{mid}/input', headers=headers)).json()['text'].startswith('LEGACY_PRIVATE_INPUT')
        denied = await client.post(base + '/memory/read', headers=headers, json={'role_id': ids[0], 'reference': reference})
        assert denied.status_code == 409
        refreshed = (await client.get(base + '/context', headers=headers)).json()
        assert refreshed['active_summary'] is None


@pytest.mark.anyio
async def test_message_text_and_claimed_actor_do_not_create_dispatch_or_impersonate_manager(command_root, isolated_command_database):
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        response = await client.post(f'/api/conversations/{cid}/messages', headers=headers, json={
            'parts': [{'type': 'text', 'text': '@全部 请执行这段普通文字'}], 'mentions': [],
            'sender_type': 'system', 'communication': {'kind': 'workflow_dispatch', 'actor': {'kind': 'world_manager', 'id': 1}}})
        assert response.status_code == 202
        value = response.json()
        assert value['generation_ids'] == []
        assert value['message']['sender_type'] == 'user'
        assert value['message']['communication']['actor']['kind'] == 'user'
        assert value['message']['communication']['kind'] == 'chat'


@pytest.mark.anyio
async def test_opposite_condition_branches_are_not_grouped_into_one_broadcast(command_root, isolated_command_database, monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.services import chat
    from test_orchestrator import launch
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[{'name': 'workflow_result', 'args': {'values': {'approved': True}}, 'id': 'branch-result'}]), ScriptedTurn(text='受控分支完成')], delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        graph = {'runtime_version': 2, 'nodes': [{'id': 'gate', 'kind': 'judge', 'title': '选择分支', 'role_id': ids[2], 'task': '报告受控判断', 'tools': [], 'condition': {'sources': ['$self'], 'key': 'approved', 'value': True}},
            {'id': 'yes', 'kind': 'role', 'title': '通过分支', 'role_id': ids[0], 'task': '通过时执行', 'tools': []},
            {'id': 'no', 'kind': 'role', 'title': '拒绝分支', 'role_id': ids[1], 'task': '拒绝时执行', 'tools': []}],
            'edges': [['gate','yes'],['gate','no']], 'edge_rules': [
                {'source': 'gate', 'target': 'yes', 'when': 'true'}, {'source': 'gate', 'target': 'no', 'when': 'false'}]}
        rid = await launch(client, headers, cid, graph)
        await wait_state(client, headers, cid, rid, lambda row: row['status'] == 'completed')
        history = (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()['items']
        cards = [m for m in history if (m.get('communication') or {}).get('kind') == 'workflow_dispatch']
        assert len(cards) == 2
        assert [[r['id'] for r in card['communication']['recipients']] for card in cards] == [[ids[2]], [ids[0]]]
