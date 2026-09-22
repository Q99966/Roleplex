"""压缩分段、维护额度、执行事实、版本失效及重启收口。"""
from uuid import uuid4

import pytest
from sqlalchemy import select

from test_compaction_boundaries import prepare
from test_context_compaction import wait_compaction
from test_orchestrator import setup_group, command_root, isolated_command_database


@pytest.mark.anyio
@pytest.mark.parametrize('limit', [1, 40])
async def test_long_history_uses_bounded_chunks_and_one_shared_decision_budget(command_root, isolated_command_database, monkeypatch, limit):
    from app.agent import fake_provider
    from app.context.budget import estimate_messages_tokens, token_estimate
    from app.db import SessionLocal
    from app.models import AgentExecution, InstanceSettings, ModelCallUsage, WorkflowBudget
    observed = []
    original = fake_provider.ContextCompactionModel
    class Measured(original):
        async def _astream(self, messages, **kwargs):
            measured = token_estimate(estimate_messages_tokens(messages))
            assert measured.estimated_tokens + measured.safety_margin_tokens + 400 <= 4096
            observed.append(measured.estimated_tokens)
            async for chunk in super()._astream(messages, **kwargs):
                yield chunk
    monkeypatch.setattr(fake_provider, 'ContextCompactionModel', Measured)
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        role = (await client.get(f'/api/roles/{ids[0]}', headers=headers)).json()
        assert (await client.put(f'/api/roles/{ids[0]}', headers=headers, json={**role,
            'expected_revision': role['revision'], 'context_window_tokens': 4096, 'builtin_tools': []})).status_code == 200
        async with SessionLocal() as session:
            (await session.get(InstanceSettings, 1)).decision_limit = limit
            await session.commit()
        _, body = await prepare(client, headers, cid, ids, count=10, text='分段约定：保留目标。' + '甲乙丙丁。' * 60)
        body.update(target_tokens=400, keep_recent=1)
        base = f'/api/conversations/{cid}/context'
        created = await client.post(base + '/compressions', headers=headers, json=body)
        assert created.status_code == 202
        job = await wait_compaction(client, headers, cid, created.json()['id'])
        if limit == 1:
            assert job['status'] == 'failed' and job['error_code'] == 'CONTEXT_COMPRESSION_BUDGET_EXCEEDED'
            assert len(observed) == 1
            assert (await client.get(base, headers=headers)).json()['active_summary'] is None
        else:
            assert job['status'] == 'completed' and len(observed) >= 3
        async with SessionLocal() as session:
            execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == job['execution_id']))
            budget = await session.get(WorkflowBudget, execution.chain_id)
            calls = (await session.scalars(select(ModelCallUsage).where(ModelCallUsage.execution_id == execution.execution_id)
                .order_by(ModelCallUsage.call_index))).all()
            assert budget.trigger_message_id is None and budget.used_decisions == len(observed)
            assert execution.decision_count == len(observed)
            assert [call.call_index for call in calls] == list(range(1, len(observed) + 1))
            assert all(call.input_tokens is None for call in calls)


@pytest.mark.anyio
async def test_summary_keeps_server_execution_facts_and_invalidates_after_source_change(command_root, isolated_command_database):
    from app.db import SessionLocal
    from app.models import Message
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        mid, body = await prepare(client, headers, cid, ids)
        async with SessionLocal() as session:
            row = await session.get(Message, mid)
            row.status = 'stopped'; row.revision += 1; row.meta_json = {'stop_reason': 'user_cancelled'}
            row.parts_json = [*row.parts_json,
                {'type': 'tool_call', 'tool_name': 'workspace_write', 'call_id': 'applied', 'status': 'success', 'effect_state': 'applied', 'confirmed_applied_items': 1},
                {'type': 'tool_call', 'tool_name': 'unknown_tool', 'call_id': 'unknown', 'status': 'interrupted', 'effect_state': 'unknown'}]
            await session.commit()
        base = f'/api/conversations/{cid}/context'
        body.update(expected_revision=(await client.get(base, headers=headers)).json()['revision'], target_tokens=1800)
        created = (await client.post(base + '/compressions', headers=headers, json=body)).json()
        job = await wait_compaction(client, headers, cid, created['id'])
        assert job['status'] == 'completed'
        state = (await client.get(base, headers=headers)).json()
        text = state['active_summary']['text']
        assert 'execution_facts' in text and 'applied' in text and 'unknown' in text and 'user_cancelled' in text
        async with SessionLocal() as session:
            row = await session.get(Message, mid)
            row.parts_json = [{'type': 'text', 'text': '来源内容已修订'}]; row.revision += 1
            await session.commit()
        changed = (await client.get(base, headers=headers)).json()
        assert changed['active_summary'] is None and changed['summary_unavailable'] == 'source_changed'
        history = (await client.get(base + '/compressions', headers=headers)).json()
        assert history['versions'][0]['valid'] is False and history['versions'][0]['text'] is None
        assert (await client.post(base + '/restore', headers=headers,
            json={'expected_revision': changed['revision'], 'summary_id': job['id']})).status_code == 409


@pytest.mark.anyio
async def test_queued_maintenance_is_interrupted_on_restart_and_retry_key_does_not_replay(command_root, isolated_command_database, monkeypatch):
    from app.scheduling import conversation_scheduler
    from app.context.compaction import recover
    from app.db import SessionLocal
    from app.models import ModelCallUsage
    async def parked(cid, jid):
        pass
    monkeypatch.setattr(conversation_scheduler, 'enqueue', parked)
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        _, body = await prepare(client, headers, cid, ids)
        base = f'/api/conversations/{cid}/context'
        first = (await client.post(base + '/compressions', headers=headers, json=body)).json()
        await conversation_scheduler.shutdown()
        from app.services import chat
        await conversation_scheduler.start(chat.run_scheduled_generation)
        await recover()
        repeated = (await client.post(base + '/compressions', headers=headers, json=body)).json()
        assert repeated['id'] == first['id'] and repeated['status'] == 'interrupted'
        assert (await client.get(base, headers=headers)).json()['active_summary'] is None
        async with SessionLocal() as session:
            assert (await session.scalars(select(ModelCallUsage))).all() == []


@pytest.mark.anyio
async def test_restore_older_summary_preserves_messages_added_after_it(command_root, isolated_command_database):
    from app.db import SessionLocal
    from test_conversation_context import source
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        _, body = await prepare(client, headers, cid, ids)
        base = f'/api/conversations/{cid}/context'
        first = (await client.post(base + '/compressions', headers=headers, json=body)).json()
        assert (await wait_compaction(client, headers, cid, first['id']))['status'] == 'completed'
        async with SessionLocal() as session:
            for i in range(3):
                await source(session, cid, text=f'新消息 {i}：新的阶段要求。' + '受控新增说明。' * 40, sender_id=ids[0])
            await session.commit()
        state = (await client.get(base, headers=headers)).json()
        second = (await client.post(base + '/compressions', headers=headers, json={**body, 'request_key': uuid4().hex,
            'expected_revision': state['revision'], 'target_tokens': 1800})).json()
        assert (await wait_compaction(client, headers, cid, second['id']))['status'] == 'completed'
        state = (await client.get(base, headers=headers)).json()
        restored = await client.post(base + '/restore', headers=headers,
            json={'expected_revision': state['revision'], 'summary_id': first['id']})
        assert restored.status_code == 200
        assert restored.json()['active_summary']['id'] == first['id'] and restored.json()['counts']['included'] == 9
        preview = (await client.post(base + '/preview', headers=headers, json={'role_id': ids[0], 'include_content': True})).json()
        assert any('新消息 2' in row['content'] for row in preview['messages'])


@pytest.mark.anyio
async def test_model_snapshot_excludes_unknown_params_and_config_change_prevents_dispatch(command_root, isolated_command_database, monkeypatch):
    from app.scheduling import conversation_scheduler
    from app.db import SessionLocal
    from app.models import ContextCompression, ModelConfig, ModelCallUsage
    captured = []
    original_enqueue = conversation_scheduler.enqueue
    async def parked(cid, jid):
        captured.append((cid, jid))
    monkeypatch.setattr(conversation_scheduler, 'enqueue', parked)
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        role = (await client.get(f'/api/roles/{ids[0]}', headers=headers)).json()
        assert (await client.put(f'/api/roles/{ids[0]}', headers=headers, json={**role,
            'expected_revision': role['revision'], 'params': {'temperature': .2, 'api_key': 'CONTROLLED_DISALLOWED_PARAMETER'}})).status_code == 200
        _, body = await prepare(client, headers, cid, ids)
        base = f'/api/conversations/{cid}/context'
        created = (await client.post(base + '/compressions', headers=headers, json=body)).json()
        async with SessionLocal() as session:
            job = await session.get(ContextCompression, created['id'])
            assert 'api_key' not in job.model_snapshot_json['params']
            assert 'CONTROLLED_DISALLOWED_PARAMETER' not in str(job.model_snapshot_json)
            config = await session.get(ModelConfig, role['model_config_id'])
            config.base_url = 'https://changed.example.invalid/v1'
            await session.commit()
        await original_enqueue(*captured[0])
        done = await wait_compaction(client, headers, cid, created['id'])
        assert done['status'] == 'failed' and done['error_code'] == 'CONTEXT_COMPRESSION_MODEL_CHANGED'
        async with SessionLocal() as session:
            assert (await session.scalars(select(ModelCallUsage))).all() == []
