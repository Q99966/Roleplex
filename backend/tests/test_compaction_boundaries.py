"""压缩期间的消息追加、来源修订、取消、撤权及回退竞争。"""
import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from test_conversation_context import source
from test_context_compaction import wait_compaction
from test_orchestrator import setup_group, command_root, isolated_command_database


async def prepare(client, headers, cid, ids, *, count=6, text=None):
    from app.db import SessionLocal
    async with SessionLocal() as session:
        for i in range(count):
            row = await source(session, cid, text=text or f'约定 {i}：保留邮件登录。' + '受控历史背景。' * 80, sender_id=ids[i % 2])
            if i == 0:
                first = row.id
        await session.commit()
    state = (await client.get(f'/api/conversations/{cid}/context', headers=headers)).json()
    return first, {'request_key': uuid4().hex, 'role_id': ids[0], 'expected_revision': state['revision'],
        'keep_recent': 1, 'target_tokens': 900, 'instructions': '保留既定约定。'}


@pytest.mark.anyio
@pytest.mark.parametrize('change', ['append', 'edit', 'delete', 'cancel', 'revoke', 'restore'])
async def test_publication_obeys_concurrent_user_intent(command_root, isolated_command_database, monkeypatch, change):
    from app.agent import fake_provider
    from app.db import SessionLocal
    from app.models import Message, AgentExecution, ModelCallUsage
    entered, release = asyncio.Event(), asyncio.Event()
    original = fake_provider.ContextCompactionModel
    class Paused(original):
        async def _astream(self, messages, **kwargs):
            entered.set()
            await asyncio.wait_for(release.wait(), 15)
            async for part in super()._astream(messages, **kwargs):
                yield part
    monkeypatch.setattr(fake_provider, 'ContextCompactionModel', Paused)
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        first, body = await prepare(client, headers, cid, ids)
        base = f'/api/conversations/{cid}/context'
        response = await client.post(base + '/compressions', headers=headers, json=body)
        assert response.status_code == 202
        job_id = response.json()['id']
        try:
            await asyncio.wait_for(entered.wait(), 10)
            for query in ['', '?window=recent']:
                snapshot = (await client.get(f'/api/conversations/{cid}/messages{query}', headers=headers)).json()
                assert snapshot['active_generation_ids'] == []
            if change == 'append':
                stopped = await client.post(f'/api/conversations/{cid}/stop', headers=headers)
                assert stopped.json()['stopped'] is False
                sent = await client.post(f'/api/conversations/{cid}/messages', headers=headers,
                    json={'parts': [{'type': 'text', 'text': '压缩期间追加的新要求'}], 'mentions': []})
                assert sent.status_code == 202
            elif change in {'edit', 'delete'}:
                async with SessionLocal() as session:
                    row = await session.get(Message, first)
                    if change == 'delete':
                        await session.delete(row)
                    else:
                        row.parts_json = [{'type': 'text', 'text': '旧约定已经撤回'}]
                        row.revision += 1
                    await session.commit()
            elif change == 'cancel':
                assert (await client.post(base + f'/compressions/{job_id}/cancel', headers=headers)).status_code == 200
            elif change == 'revoke':
                result = await client.put(f'/api/conversations/{cid}/members', headers=headers,
                    json={'expected_revision': 0, 'role_ids': ids[1:]})
                assert result.status_code == 200
            elif change == 'restore':
                state = (await client.get(base, headers=headers)).json()
                result = await client.post(base + '/restore', headers=headers,
                    json={'expected_revision': state['revision'], 'summary_id': None})
                assert result.status_code == 200
        finally:
            release.set()
        job = await wait_compaction(client, headers, cid, job_id)
        assert job['status'] == ('completed' if change == 'append' else 'cancelled' if change == 'cancel' else 'stale')
        state = (await client.get(base, headers=headers)).json()
        assert bool(state['active_summary']) == (change == 'append')
        if change == 'append':
            preview = (await client.post(base + '/preview', headers=headers, json={'role_id': ids[0], 'include_content': True})).json()
            assert any('压缩期间追加的新要求' in message['content'] for message in preview['messages'])
        async with SessionLocal() as session:
            execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == job['execution_id']))
            calls = (await session.scalars(select(ModelCallUsage).where(ModelCallUsage.execution_id == execution.execution_id))).all()
            assert len(calls) == 1
            assert calls[0].status == ('unconfirmed' if change == 'cancel' else 'completed')
            assert calls[0].input_tokens is None


@pytest.mark.anyio
async def test_duplicate_creation_and_queued_cancel_do_not_pay_twice(command_root, isolated_command_database, monkeypatch):
    from app.scheduling import conversation_scheduler
    from app.db import SessionLocal
    from app.models import ContextCompression, ModelCallUsage
    async def parked(cid, jid):
        pass
    monkeypatch.setattr(conversation_scheduler, 'enqueue', parked)
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        _, body = await prepare(client, headers, cid, ids)
        base = f'/api/conversations/{cid}/context'
        responses = await asyncio.gather(*(client.post(base + '/compressions', headers=headers, json=body) for _ in range(2)))
        assert all(response.status_code == 202 for response in responses)
        assert responses[0].json()['id'] == responses[1].json()['id']
        mismatch = await client.post(base + '/compressions', headers=headers, json={**body, 'instructions': '不同请求'})
        assert mismatch.status_code == 409
        different = await client.post(base + '/compressions', headers=headers, json={**body, 'request_key': uuid4().hex})
        assert different.status_code == 409
        cancelled = await client.post(base + f"/compressions/{responses[0].json()['id']}/cancel", headers=headers)
        assert cancelled.json()['status'] == 'cancelled'
        async with SessionLocal() as session:
            assert len((await session.scalars(select(ContextCompression))).all()) == 1
            assert (await session.scalars(select(ModelCallUsage))).all() == []


@pytest.mark.anyio
@pytest.mark.parametrize('outcome', ['no_gain', 'invalid_reference', 'provider_error'])
async def test_unsuitable_output_keeps_previous_material(command_root, isolated_command_database, monkeypatch, outcome):
    from app.agent import fake_provider
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        first, body = await prepare(client, headers, cid, ids, count=2, text='短约定。' if outcome == 'no_gain' else None)
        if outcome == 'invalid_reference':
            monkeypatch.setattr(fake_provider, 'ContextCompactionModel', lambda: fake_provider.ScriptedChatModel(delay=0, turns=[fake_provider.ScriptedTurn(text=json.dumps({
                'facts': [{'text': '无效来源', 'sources': [999999]}], 'open_items': [], 'conflicts': [], 'inferences': []}))]))
        elif outcome == 'provider_error':
            class Failed(fake_provider.ContextCompactionModel):
                async def _astream(self, messages, **kwargs):
                    raise RuntimeError('controlled provider failure')
                    yield
            monkeypatch.setattr(fake_provider, 'ContextCompactionModel', Failed)
        base = f'/api/conversations/{cid}/context'
        created = (await client.post(base + '/compressions', headers=headers, json=body)).json()
        job = await wait_compaction(client, headers, cid, created['id'])
        assert job['status'] == ('unchanged' if outcome == 'no_gain' else 'failed')
        after = (await client.get(base, headers=headers)).json()
        assert after['revision'] == body['expected_revision'] and after['active_summary'] is None
