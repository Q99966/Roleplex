"""持久共享上下文的事务同步、角色投影、调用占用与授权回归。"""
import asyncio

import pytest
from sqlalchemy import select

from test_orchestrator import setup_group, command_root, isolated_command_database


async def source(session, cid, *, text='受控来源', status='done', sender_type='role', sender_id=None):
    from app.db import now_utc
    from app.models import Message
    row = Message(conversation_id=cid, sender_type=sender_type, sender_id=sender_id,
        parts_json=[{'type': 'text', 'text': text}], status=status, revision=0, pinned=False,
        mentions_json=[], meta_json={}, created_at=now_utc())
    session.add(row)
    await session.flush()
    return row


@pytest.mark.anyio
async def test_context_is_shared_persistent_and_streaming_only_publishes_terminal(command_root, isolated_command_database):
    from app.db import SessionLocal
    from app.models import ConversationContext, ConversationContextEntry, Message
    async with setup_group(command_root) as (_, _, cid, ids, _):
        async with SessionLocal() as session:
            assert await session.get(ConversationContext, cid) is not None
            row = await source(session, cid, status='generating', sender_id=ids[0])
            mid = row.id
            await session.commit()
        async with SessionLocal() as session:
            state = await session.get(ConversationContext, cid)
            revision = state.revision
            entry = await session.get(ConversationContextEntry, mid)
            assert entry.state == 'pending' and entry.text == ''
            row = await session.get(Message, mid)
            row.parts_json = [{'type': 'text', 'text': '流式片段'}]
            row.revision += 1
            await session.commit()
        async with SessionLocal() as session:
            assert (await session.get(ConversationContext, cid)).revision == revision
            row = await session.get(Message, mid)
            row.status = 'stopped'
            row.meta_json = {'stop_reason': 'user_cancelled'}
            row.revision += 1
            await session.commit()
        async with SessionLocal() as session:
            entry = await session.get(ConversationContextEntry, mid)
            assert entry.source_revision == 2 and entry.state == 'included'
            assert '流式片段' in entry.text and '用户停止' in entry.text
            assert (await session.get(ConversationContext, cid)).revision > revision
            assert len((await session.scalars(select(ConversationContextEntry).where(
                ConversationContextEntry.conversation_id == cid))).all()) == 1


@pytest.mark.anyio
async def test_source_revision_rollback_delete_and_backfill_preserve_truth(command_root, isolated_command_database):
    from app.db import SessionLocal
    from app.models import ConversationContextEntry, Message
    from app.context.store import backfill_contexts
    async with setup_group(command_root) as (_, _, cid, ids, _):
        async with SessionLocal() as session:
            row = await source(session, cid, sender_id=ids[0])
            mid = row.id
            await session.commit()
        async with SessionLocal() as session:
            row = await session.get(Message, mid)
            row.parts_json = [{'type': 'text', 'text': '不应提交'}]
            row.revision += 1
            await session.flush()
            await session.rollback()
        async with SessionLocal() as session:
            assert (await session.get(ConversationContextEntry, mid)).text == '受控来源'
            await session.delete(await session.get(ConversationContextEntry, mid))
            await session.commit()
        await backfill_contexts(batch_size=1)
        async with SessionLocal() as session:
            assert (await session.get(ConversationContextEntry, mid)).text == '受控来源'
            await session.delete(await session.get(Message, mid))
            await session.commit()
        async with SessionLocal() as session:
            assert await session.get(ConversationContextEntry, mid) is None


@pytest.mark.anyio
async def test_preview_reports_pressure_before_truncation_and_keeps_role_identity(command_root, isolated_command_database):
    from app.db import SessionLocal
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        async with SessionLocal() as session:
            await source(session, cid, text='历史' * 4000, sender_id=ids[0])
            small = await source(session, cid, text='最新完成结果', sender_id=ids[0])
            small_id = small.id
            await source(session, cid, status='error', text='失败片段', sender_id=ids[1])
            await session.commit()
        role = (await client.get(f'/api/roles/{ids[0]}', headers=headers)).json()
        assert (await client.put(f'/api/roles/{ids[0]}', headers=headers, json={
            **role, 'expected_revision': role['revision'], 'context_window_tokens': 4096, 'builtin_tools': [],
        })).status_code == 200
        preview = await client.post(f'/api/conversations/{cid}/context/preview', headers=headers,
            json={'role_id': ids[0], 'draft': '后续要求', 'include_content': True})
        assert preview.status_code == 200
        data = preview.json()
        assert data['request']['before_truncation_tokens'] > data['request']['effective_context_window']
        assert data['request']['truncated_message_count'] == 1
        assert data['request']['estimated_tokens'] < data['request']['before_truncation_tokens']
        assert data['request']['is_provider_exact'] is False
        assert data['latest_call'] is None
        assert data['material']['sources'] == [{'message_id': small_id, 'revision': 0, 'status': 'done'}]
        assert data['messages'][-2] == {'type': 'ai', 'content': '最新完成结果'}
        assert data['messages'][-1]['content'] == '后续要求'
        other = (await client.post(f'/api/conversations/{cid}/context/preview', headers=headers,
            json={'role_id': ids[1], 'draft': '后续要求', 'include_content': True})).json()
        assert {'type': 'human', 'content': f'[role:{ids[0]}] 最新完成结果'} in other['messages']
        assert any('最近中断执行数据' in message['content'] for message in other['messages'])
        assert (await client.get(f'/api/conversations/{cid}/context', headers=headers)).json()['counts']['excluded'] == 1


@pytest.mark.anyio
async def test_backfill_resumes_committed_batches_without_recopying_them(command_root, isolated_command_database, monkeypatch):
    from sqlalchemy import delete, func
    from app.db import SessionLocal
    from app.models import ConversationContext, ConversationContextEntry
    from app.context import store
    async with setup_group(command_root) as (_, _, cid, ids, _):
        async with SessionLocal() as session:
            for i in range(6):
                await source(session, cid, text=f'旧消息 {i}', sender_id=ids[0])
            await session.commit()
            # 模拟迁移前有完整消息、没有投影；不破坏来源事实。
            await session.execute(delete(ConversationContextEntry).where(ConversationContextEntry.conversation_id == cid))
            await session.commit()
        original = store.with_locked_retry
        calls = 0
        async def interrupt(operation):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise asyncio.CancelledError()
            return await original(operation)
        monkeypatch.setattr(store, 'with_locked_retry', interrupt)
        with pytest.raises(asyncio.CancelledError):
            await store.backfill_contexts(batch_size=2)
        async with SessionLocal() as session:
            assert await session.scalar(select(func.count()).select_from(ConversationContextEntry).where(
                ConversationContextEntry.conversation_id == cid)) == 2
            revision = (await session.get(ConversationContext, cid)).revision
        monkeypatch.setattr(store, 'with_locked_retry', original)
        await store.backfill_contexts(batch_size=2)
        async with SessionLocal() as session:
            assert (await session.get(ConversationContext, cid)).revision == revision + 2
            assert [row.text for row in (await session.scalars(select(ConversationContextEntry).where(
                ConversationContextEntry.conversation_id == cid).order_by(ConversationContextEntry.message_id))).all()] == [f'旧消息 {i}' for i in range(6)]
