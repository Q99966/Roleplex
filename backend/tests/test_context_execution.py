"""经真实调度与 fake Provider 验证上下文来源、工具轮和并发一致性。"""
import asyncio

import pytest
from sqlalchemy import select

from test_conversation_context import source
from test_orchestrator import setup_group, command_root, isolated_command_database


async def finished(cid, count):
    from app.db import SessionLocal
    from app.models import AgentExecution
    for _ in range(300):
        async with SessionLocal() as session:
            rows = (await session.scalars(select(AgentExecution).where(
                AgentExecution.conversation_id == cid).order_by(AgentExecution.id))).all()
            if len(rows) == count and all(row.status in {'completed', 'failed', 'stopped'} for row in rows):
                return rows
        await asyncio.sleep(.025)
    raise AssertionError('受控执行未收口')


@pytest.mark.anyio
async def test_preview_matches_actual_first_request_and_group_next_role_sees_prior_result(command_root, isolated_command_database, monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.db import SessionLocal
    from app.models import Message, ModelCallUsage
    from app.services import chat
    captured = []
    entered, release = asyncio.Event(), asyncio.Event()

    class Model(ScriptedChatModel):
        async def _astream(self, messages, **kwargs):
            captured.append([{'type': row.type, 'content': row.content} for row in messages])
            if len(captured) == 1:
                entered.set()
                await asyncio.wait_for(release.wait(), 10)
            async for chunk in super()._astream(messages, **kwargs):
                yield chunk

    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Model(turns=[ScriptedTurn(text='前序角色已完成')], delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        async with SessionLocal() as session:
            old = await source(session, cid, text='原始共享材料', sender_id=ids[0])
            old_id = old.id
            await session.commit()
        url = f'/api/conversations/{cid}/context/preview'
        preview = (await client.post(url, headers=headers,
            json={'role_id': ids[0], 'draft': '依次协作', 'include_content': True})).json()
        sent = await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '依次协作'}], 'mentions': ids[:2]})
        assert sent.status_code == 202
        await asyncio.wait_for(entered.wait(), 10)
        assert captured[0] == preview['messages']
        async with SessionLocal() as session:
            row = await session.get(Message, old_id)
            row.parts_json = [{'type': 'text', 'text': '材料已经修订'}]
            row.revision += 1
            await session.commit()
        release.set()
        executions = await finished(cid, 2)
        assert all(row.status == 'completed' for row in executions)
        assert executions[0].context_snapshot_json['material']['sources'][0]['revision'] == 0
        assert executions[1].context_snapshot_json['material']['sources'][0]['revision'] == 1
        assert '原始共享材料' in str(captured[0]) and '材料已经修订' not in str(captured[0])
        assert any(f'[role:{ids[0]}] 前序角色已完成' == row['content'] for row in captured[1])
        assert captured[1][-1]['content'] == '依次协作'
        async with SessionLocal() as session:
            call = await session.scalar(select(ModelCallUsage).where(ModelCallUsage.execution_id == executions[0].execution_id))
            assert call.input_estimate_json['estimated_tokens'] == preview['request']['estimated_tokens']
            assert call.input_tokens is None


@pytest.mark.anyio
async def test_tool_round_estimates_grow_and_latest_call_is_not_cumulative(command_root, isolated_command_database, monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.db import SessionLocal
    from app.models import ModelCallUsage
    from app.services import chat
    from app.context.budget import estimate_messages_tokens
    captured = []
    secret = 'PRIVATE_CONTROLLED_TOOL_SAMPLE'
    (command_root / 'context.txt').write_text(secret * 30)

    class Model(ScriptedChatModel):
        async def _astream(self, messages, **kwargs):
            captured.append(estimate_messages_tokens(messages))
            async for chunk in super()._astream(messages, **kwargs):
                yield chunk

    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Model(turns=[
        ScriptedTurn(tool_calls=[{'name': 'workspace_read', 'args': {'path': 'context.txt'}, 'id': 'read-context'}]),
        ScriptedTurn(text='已读取受控文件'),
    ], delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        sent = await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '读取受控文件'}], 'mentions': [ids[0]]})
        assert sent.status_code == 202
        execution = (await finished(cid, 1))[0]
        assert execution.status == 'completed'
        async with SessionLocal() as session:
            calls = (await session.scalars(select(ModelCallUsage).where(ModelCallUsage.execution_id == execution.execution_id)
                .order_by(ModelCallUsage.call_index))).all()
            assert len(calls) == 2
            first, second = [call.input_estimate_json for call in calls]
            assert [first['message_tokens'], second['message_tokens']] == captured
            assert second['estimated_tokens'] > first['estimated_tokens'] + len(secret * 30)
            assert first['tool_message_count'] == 0 and second['tool_message_count'] == 1
            assert secret not in str(second) and secret not in str(execution.context_snapshot_json)
        response = await client.post(f'/api/conversations/{cid}/context/preview', headers=headers, json={'role_id': ids[0]})
        assert response.status_code == 200
        latest = response.json()['latest_call']
        from datetime import datetime, timedelta
        assert datetime.fromisoformat(latest['recorded_at']).utcoffset() == timedelta(0)
        assert datetime.fromisoformat(response.json()['shared']['updated_at']).utcoffset() == timedelta(0)
        assert latest['call_index'] == 2 and latest['input_estimate'] == second
        assert latest['provider_usage']['input_tokens'] is None
        assert latest['snapshot']['request']['estimated_tokens'] == first['estimated_tokens']


@pytest.mark.anyio
async def test_simultaneous_source_commits_do_not_lose_shared_revision(command_root, isolated_command_database):
    from app.db import SessionLocal, with_locked_retry
    from app.models import ConversationContext, ConversationContextEntry
    async with setup_group(command_root) as (_, _, cid, ids, _):
        arrived, ready = 0, asyncio.Event()
        async with SessionLocal() as session:
            initial = (await session.get(ConversationContext, cid)).revision

        async def writer(rid):
            first = True
            async def operation():
                nonlocal arrived, first
                async with SessionLocal() as session:
                    # 两个事务明确读到同一版本，验证原子递增而非本地旧值覆盖。
                    await session.get(ConversationContext, cid)
                    if first:
                        first = False
                        arrived += 1
                        if arrived == 2:
                            ready.set()
                    await asyncio.wait_for(ready.wait(), 5)
                    await source(session, cid, sender_id=rid)
                    await session.commit()
            await with_locked_retry(operation)
        await asyncio.gather(*(writer(rid) for rid in ids[:2]))
        async with SessionLocal() as session:
            assert (await session.get(ConversationContext, cid)).revision == initial + 2
            assert len((await session.scalars(select(ConversationContextEntry).where(
                ConversationContextEntry.conversation_id == cid))).all()) == 2
