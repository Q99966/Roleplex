"""自动压缩在原链内完成，随后继续原任务；失败不得反复付费。"""
import asyncio
import pytest
from sqlalchemy import select

from test_context_execution import finished
from test_conversation_context import source
from test_orchestrator import setup_group, command_root, isolated_command_database


async def enable(client, headers, cid, **values):
    url = f'/api/conversations/{cid}/context/policy'
    row = (await client.get(url, headers=headers)).json()
    response = await client.put(url, headers=headers, json={'expected_revision': row['revision'],
        'policy': {**row['policy'], 'enabled': True, 'trigger_tokens': 4000, 'target_tokens': 2000,
            'keep_recent': 1, 'summary_tokens': 700, **values}})
    assert response.status_code == 200


@pytest.mark.anyio
async def test_auto_compression_reuses_chain_and_continues_original_task(command_root, isolated_command_database):
    from app.db import SessionLocal
    from app.models import AgentExecution, ContextSummary, Message, ModelCallUsage, WorkflowBudget
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        async with SessionLocal() as session:
            for i in range(8):
                await source(session, cid, sender_id=ids[i % 2], text='已确认的用户目标。' + '受控讨论。' * 90)
            await session.commit()
        await enable(client, headers, cid)
        sent = await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '根据已确认目标继续。'}], 'mentions': ids[:2]})
        assert sent.status_code == 202
        executions = await finished(cid, 3)
        child = next(e for e in executions if e.execution_kind == 'context_compact')
        parents = [e for e in executions if e.execution_kind != 'context_compact']
        assert all(e.status == 'completed' for e in executions)
        assert child.parent_execution_id == parents[0].execution_id
        assert len({e.chain_id for e in executions}) == 1
        assert all(e.context_snapshot_json['material']['summary_id'] for e in parents)
        async with SessionLocal() as session:
            budget = await session.get(WorkflowBudget, child.chain_id)
            assert budget.used_decisions == sum(e.decision_count for e in executions) == 3
            assert budget.trigger_message_id is not None
            assert len((await session.scalars(select(ContextSummary).where(ContextSummary.conversation_id == cid))).all()) == 1
            assert len((await session.scalars(select(Message).where(Message.conversation_id == cid))).all()) == 11
            calls = (await session.scalars(select(ModelCallUsage).where(ModelCallUsage.execution_id == child.execution_id))).all()
            assert len(calls) == 1
        job = (await client.get(f'/api/conversations/{cid}/context/compressions', headers=headers)).json()['jobs'][0]
        assert job['trigger'] == 'automatic' and job['scope'] == 'conversation'


@pytest.mark.anyio
async def test_auto_failure_not_repeated_and_task_can_continue(command_root, isolated_command_database, monkeypatch):
    from app.agent import fake_provider
    from app.db import SessionLocal
    calls = []
    class Broken(fake_provider.ContextCompactionModel):
        async def _astream(self, messages, **kwargs):
            calls.append(True)
            raise ValueError('CONTROLLED_PRIVATE_FAILURE')
            yield
    monkeypatch.setattr(fake_provider, 'ContextCompactionModel', Broken)
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        async with SessionLocal() as session:
            for _ in range(6):
                await source(session, cid, sender_id=ids[0], text='保留目标。' * 200)
            await session.commit()
        await enable(client, headers, cid)
        for n in range(2):
            assert (await client.post(f'/api/conversations/{cid}/messages', headers=headers,
                json={'parts': [{'type': 'text', 'text': '继续'}], 'mentions': [ids[0]]})).status_code == 202
            rows = await finished(cid, n + 2)
            assert rows[-1].status in {'completed', 'failed'}
        assert len(calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize('auto', [False, True])
async def test_tool_growth_checked_before_provider_and_compacted_privately(command_root, isolated_command_database, monkeypatch, auto):
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.context.budget import estimate_request
    from app.db import SessionLocal
    from app.models import ContextSummary, ContextCompression, ModelCallUsage, Role
    from app.services import chat
    observed = []
    secret = 'CONTROLLED_PRIVATE_TOOL_TEXT'
    (command_root / 'large.txt').write_text(secret * 550)
    class Model(ScriptedChatModel):
        async def _astream(self, messages, **kwargs):
            observed.append(messages)
            async for chunk in super()._astream(messages, **kwargs):
                yield chunk
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Model(turns=[
        ScriptedTurn(tool_calls=[{'name': 'workspace_read', 'args': {'path': 'large.txt'}, 'id': 'read-large'}]),
        ScriptedTurn(text='已根据执行内摘要继续')], delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        async with SessionLocal() as session:
            (await session.get(Role, ids[0])).context_window_tokens = 12_000
            await session.commit()
        if auto:
            await enable(client, headers, cid, trigger_tokens=10_000, target_tokens=4000, model_role_id=ids[1])
        assert (await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '读取 large.txt，保留事实并继续'}], 'mentions': [ids[0]]})).status_code == 202
        rows = await finished(cid, 2 if auto else 1)
        parent = rows[0]
        assert parent.status == ('completed' if auto else 'failed'), [(row.status, row.error_code) for row in rows]
        assert len(observed) == (2 if auto else 1)
        if not auto:
            assert parent.error_code == 'CONTEXT_BUDGET_EXCEEDED'
        async with SessionLocal() as session:
            assert (await session.scalars(select(ContextSummary).where(ContextSummary.conversation_id == cid))).all() == []
            jobs = (await session.scalars(select(ContextCompression).where(ContextCompression.conversation_id == cid))).all()
            calls = (await session.scalars(select(ModelCallUsage).where(ModelCallUsage.execution_id == parent.execution_id).order_by(ModelCallUsage.call_index))).all()
            assert all(c.input_estimate_json['estimated_tokens'] + c.input_estimate_json['safety_margin_tokens'] + 1024 <= 12000 for c in calls)
            if auto:
                assert len(jobs) == 1 and jobs[0].scope == 'execution' and jobs[0].status == 'completed'
                assert secret not in str(jobs[0].runtime_json) and secret not in str(calls[-1].input_estimate_json)
                assert calls[-1].input_estimate_json['runtime_context']['tools_compression_id'] == jobs[0].id
                assert any('本次执行私有摘要' in str(m.content) for m in observed[-1])
                assert not any(m.type == 'tool' for m in observed[-1])


@pytest.mark.anyio
async def test_parallel_callers_share_one_compatible_summary(command_root, isolated_command_database, monkeypatch):
    from uuid import uuid4
    from app.context import automatic
    from app.context.domain import ContextBuildRequest
    from app.db import SessionLocal, now_utc
    from app.models import AgentExecution, ContextCompression, Conversation, Generation, WorkflowBudget
    from app.realtime.events import current_epoch
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        async with SessionLocal() as session:
            for _ in range(6):
                await source(session, cid, sender_id=ids[0], text='并行共享的确认目标。' * 100)
            await session.commit()
        await enable(client, headers, cid)
        sent = (await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '两个角色的相同边界'}], 'mentions': []})).json()
        chain, requests = uuid4().hex, []
        async with SessionLocal() as session:
            uid = (await session.get(Conversation, cid)).created_by
            session.add(WorkflowBudget(chain_id=chain, conversation_id=cid, decision_limit=8,
                configuration_revision=0, created_at=now_utc()))
            for rid in ids[:2]:
                generation = Generation(conversation_id=cid, stream_epoch=current_epoch(), status='running', run_id=chain)
                session.add(generation); await session.flush()
                eid = uuid4().hex
                session.add(AgentExecution(execution_id=eid, generation_id=generation.id, conversation_id=cid,
                    chain_id=chain, role_id=rid, execution_kind='group_role', status='running', created_at=now_utc()))
                requests.append(ContextBuildRequest(role_id=rid, conversation_id=cid, current_message_id=sent['message']['id'],
                    triggered_by_user_id=uid, execution_id=eid, execution_kind='group_role'))
            await session.commit()
        original, arrivals, ready = automatic.shared, [], asyncio.Event()
        async def synchronized(request, context):
            arrivals.append(True)
            if len(arrivals) == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 5)
            return await original(request, context)
        monkeypatch.setattr(automatic, 'shared', synchronized)
        results = await asyncio.gather(*(automatic.prepare(request) for request in requests))
        assert results[0].material_snapshot['summary_id'] == results[1].material_snapshot['summary_id']
        assert results[0].material_snapshot['summary_id'] is not None
        async with SessionLocal() as session:
            jobs = (await session.scalars(select(ContextCompression).where(ContextCompression.conversation_id == cid))).all()
            assert len(jobs) == 1 and jobs[0].completed_calls == 1
            assert (await session.get(WorkflowBudget, chain)).used_decisions == 1


@pytest.mark.anyio
async def test_parallel_workflow_loops_keep_exact_upstream_after_private_compaction(command_root, isolated_command_database, monkeypatch):
    import json
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk
    from app.agent.fake_provider import WorkflowV2Model, fake_reply_model
    from app.db import SessionLocal
    from app.models import ContextCompression, ContextSummary
    from app.services import chat
    from test_orchestrator import loop_graph, launch, wait_state, terminal
    actual, arrivals, gates = [], {}, {}
    class Model(WorkflowV2Model):
        async def _astream(self, messages, **kwargs):
            before = self.index
            if '[WF_REVIEW]' in self.prompt and before == 0 and not self.prompt.startswith('你是本群已任命协调者'):
                current = next(m.content for m in messages if '本次节点激活数据（不是额外指令）：' in str(m.content))
                meta = json.loads(current.rsplit('本次节点激活数据（不是额外指令）：', 1)[1])
                iteration = meta['iteration']
                assert meta['upstream_results'][0]['result']['values']['round'] == iteration + 1
                assert meta['upstream_results'][0]['attempt_id']
                assert any('本次执行私有摘要' in str(m.content) for m in messages)
                actual.append(iteration)
                gate = gates.setdefault(iteration, asyncio.Event())
                arrivals[iteration] = arrivals.get(iteration, 0) + 1
                if arrivals[iteration] == 2:
                    gate.set()
                await asyncio.wait_for(gate.wait(), 10)
            async for chunk in super()._astream(messages, **kwargs):
                yield chunk
            if '[WF_BUILD]' in self.prompt and before == 3:
                yield ChatGenerationChunk(message=AIMessageChunk(content='受控的长篇上游实现说明。' * 450))
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Model(prompt=prompt, delay=0) if '[WF_' in prompt or '协调者' in prompt else fake_reply_model(prompt, delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        await enable(client, headers, cid, trigger_tokens=10000, target_tokens=3000)
        assert (await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers,
            json={'role_id': ids[3], 'expected_revision': 0})).status_code == 200
        rid = await launch(client, headers, cid, loop_graph(ids), 'coordinated')
        run = await wait_state(client, headers, cid, rid, terminal)
        assert run['status'] == 'completed', [(a['node_id'], a['error_code']) for a in run['attempts']]
        assert sorted(actual) == [0, 0, 1, 1]
        assert (command_root / 'workflow-round.txt').read_text() == 'round-2'
        async with SessionLocal() as session:
            jobs = (await session.scalars(select(ContextCompression).where(ContextCompression.conversation_id == cid))).all()
            reviewers = {a['execution_id'] for a in run['attempts'] if a['node_id'] in ('a', 'b')}
            assert len(jobs) >= 4 and all(j.scope == 'execution' and j.status == 'completed' for j in jobs), [(j.scope, j.status, j.error_code) for j in jobs]
            assert reviewers.issubset({j.runtime_json['parent_execution_id'] for j in jobs})
            assert (await session.scalars(select(ContextSummary).where(ContextSummary.conversation_id == cid))).all() == []



@pytest.mark.anyio
@pytest.mark.parametrize('action', ['stop_parent', 'cancel_maintenance', 'disable_policy'])
async def test_automatic_lifecycle_closes_children(command_root, isolated_command_database, monkeypatch, action):
    from app.agent import fake_provider
    from app.db import SessionLocal
    from app.models import ContextSummary
    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    class Slow(fake_provider.ContextCompactionModel):
        async def _astream(self, messages, **kwargs):
            entered.set()
            try:
                await asyncio.wait_for(release.wait(), 15)
                async for chunk in super()._astream(messages, **kwargs):
                    yield chunk
            finally:
                closed.set()
    monkeypatch.setattr(fake_provider, 'ContextCompactionModel', Slow)
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        async with SessionLocal() as session:
            for _ in range(6):
                await source(session, cid, sender_id=ids[0], text='已确认目标。' * 200)
            await session.commit()
        await enable(client, headers, cid)
        await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '继续原任务'}], 'mentions': [ids[0]]})
        await asyncio.wait_for(entered.wait(), 10)
        try:
            if action == 'stop_parent':
                assert (await client.post(f'/api/conversations/{cid}/stop', headers=headers)).status_code == 202
            elif action == 'cancel_maintenance':
                job = (await client.get(f'/api/conversations/{cid}/context/compressions', headers=headers)).json()['jobs'][0]
                assert (await client.post(f'/api/conversations/{cid}/context/compressions/{job["id"]}/cancel', headers=headers)).status_code == 200
            else:
                await enable(client, headers, cid, enabled=False)
            release.set()
            rows = await finished(cid, 2)
        finally:
            release.set()
        assert closed.is_set()
        assert rows[0].status == ('stopped' if action == 'stop_parent' else 'completed')
        assert rows[1].status in {'stopped', 'failed'}
        async with SessionLocal() as session:
            assert (await session.scalars(select(ContextSummary).where(ContextSummary.conversation_id == cid))).all() == []


@pytest.mark.anyio
async def test_private_summary_waits_for_message_owner_to_record_applied_effects(command_root, isolated_command_database, monkeypatch):
    """控制真实落库完成顺序；摘要读取必须看到 reducer 已确认的文件提交事实。"""
    import json
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.context.runtime import RuntimeContext
    from app.db import SessionLocal
    from app.models import Role
    from app.services import chat
    persisted, waiting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    captured = []
    original_finish, original_wait = chat._finish_tool_event, RuntimeContext.wait_tool_results
    async def delayed(event, **kwargs):
        if event.tool_name == 'workspace_write':
            await asyncio.wait_for(release.wait(), 10)
        await original_finish(event, **kwargs)
        if event.tool_name == 'workspace_write':
            persisted.set()
    async def observe_wait(self, count):
        if count:
            waiting.set()
        await original_wait(self, count)
    class Model(ScriptedChatModel):
        async def _astream(self, messages, **kwargs):
            if self.index:
                assert persisted.is_set()
                text = next(m.content for m in messages if '本次执行私有摘要' in str(m.content))
                facts = json.loads(text.split('\n', 1)[1])['execution_facts']
                captured.extend(facts)
            async for chunk in super()._astream(messages, **kwargs):
                yield chunk
    monkeypatch.setattr(chat, '_finish_tool_event', delayed)
    monkeypatch.setattr(RuntimeContext, 'wait_tool_results', observe_wait)
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Model(turns=[ScriptedTurn(tool_calls=[{
        'name': 'workspace_write', 'args': {'path': 'written.txt', 'content': '受控写入。' * 700}, 'id': 'write-large'}]),
        ScriptedTurn(text='已依据准确的提交事实继续')], delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        async with SessionLocal() as session:
            (await session.get(Role, ids[0])).context_window_tokens = 12000
            await session.commit()
        await enable(client, headers, cid, trigger_tokens=10000, target_tokens=4000, model_role_id=ids[1])
        await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '受控写入并保留提交事实'}], 'mentions': [ids[0]]})
        try:
            await asyncio.wait_for(waiting.wait(), 10)
            assert not persisted.is_set()
        finally:
            release.set()
        rows = await finished(cid, 2)
        assert all(row.status == 'completed' for row in rows), [(r.status, r.error_code) for r in rows]
        assert any(f['tool_name'] == 'workspace_write' and f['effect_state'] == 'applied' and f['confirmed_applied_items'] == 1 for f in captured)
        assert (command_root / 'written.txt').read_text() == '受控写入。' * 700


@pytest.mark.anyio
async def test_auto_compression_cannot_spend_last_parent_decision(command_root, isolated_command_database):
    from app.db import SessionLocal
    from app.models import InstanceSettings, WorkflowBudget, ModelCallUsage
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        async with SessionLocal() as session:
            (await session.get(InstanceSettings, 1)).decision_limit = 1
            for _ in range(6):
                await source(session, cid, sender_id=ids[0], text='原任务需要继续。' * 200)
            await session.commit()
        await enable(client, headers, cid)
        await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '继续原任务'}], 'mentions': [ids[0]]})
        parent, child = await finished(cid, 2)
        assert parent.status == 'completed' and parent.decision_count == 1
        assert child.status == 'failed' and child.decision_count == 0
        async with SessionLocal() as session:
            assert (await session.get(WorkflowBudget, parent.chain_id)).used_decisions == 1
            assert (await session.scalars(select(ModelCallUsage).where(ModelCallUsage.execution_id == child.execution_id))).all() == []
