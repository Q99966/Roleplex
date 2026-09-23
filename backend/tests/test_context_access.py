"""上下文的 Owner/成员范围、在途撤权、来源分页与启动恢复。"""
import asyncio

import pytest
from sqlalchemy import delete

from test_conversation_context import source
from test_context_execution import finished
from test_orchestrator import setup_group, command_root, isolated_command_database


@pytest.mark.anyio
async def test_context_authorization_paging_and_recycle(command_root, isolated_command_database):
    from app.db import SessionLocal, now_utc
    from app.models import ConversationMember
    from accounts import TEST_PASSWORD, guest_username
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        guest = (await client.post('/api/auth/register', json={'username': guest_username('context'),
            'password': TEST_PASSWORD, 'nickname': '受控访客'})).json()
        auth = {'Authorization': 'Bearer ' + guest['access_token']}
        url = f'/api/conversations/{cid}/context'
        async with SessionLocal() as session:
            session.add(ConversationMember(conversation_id=cid, member_type='user', member_id=guest['user']['id'],
                joined_at=now_utc()))
            for i in range(5):
                await source(session, cid, text=f'受控来源 {i}', sender_id=ids[0])
            await session.commit()
        assert (await client.get(url)).status_code == 401
        assert (await client.get(url, headers=auth)).status_code == 403
        assert (await client.post(url + '/preview', headers=auth, json={'role_id': ids[0]})).status_code == 403
        assert (await client.post(url + '/preview', headers=headers, json={'role_id': 999999})).status_code == 404
        first = (await client.get(url + '?limit=2', headers=headers)).json()
        assert first['counts']['included'] == 5 and len(first['entries']) == 2
        second = (await client.get(url, headers=headers, params={'limit': 2, 'before': first['next_before'],
            'expected_revision': first['revision']})).json()
        assert {row['message_id'] for row in first['entries']}.isdisjoint(row['message_id'] for row in second['entries'])
        async with SessionLocal() as session:
            await source(session, cid, text='新来源', sender_id=ids[0])
            await session.commit()
        assert (await client.get(url, headers=headers, params={'before': first['next_before'],
            'expected_revision': first['revision']})).status_code == 409
        assert (await client.delete(f'/api/conversations/{cid}', headers=headers)).status_code == 204
        assert (await client.get(url, headers=headers)).status_code == 404
        assert (await client.post(f'/api/conversations/{cid}/restore', headers=headers)).status_code == 200
        restored = (await client.get(url, headers=headers)).json()
        assert restored['counts']['included'] == 6
        async with SessionLocal() as session:
            await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == cid,
                ConversationMember.member_type == 'user', ConversationMember.member_id != guest['user']['id']))
            await session.commit()
        assert (await client.get(url, headers=headers)).status_code == 404


@pytest.mark.anyio
async def test_preview_revalidates_membership_after_waiting(command_root, isolated_command_database, monkeypatch):
    from app.db import SessionLocal
    from app.models import ConversationMember
    from app.routers import conversation_context
    entered, release = asyncio.Event(), asyncio.Event()
    original = conversation_context.build_context
    async def paused(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        await asyncio.wait_for(release.wait(), 10)
        return result
    monkeypatch.setattr(conversation_context, 'build_context', paused)
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        task = asyncio.create_task(client.post(f'/api/conversations/{cid}/context/preview', headers=headers,
            json={'role_id': ids[0], 'include_content': True}))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            async with SessionLocal() as session:
                await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == cid,
                    ConversationMember.member_type == 'role', ConversationMember.member_id == ids[0]))
                await session.commit()
            release.set()
            response = await task
            assert response.status_code == 404 and 'messages' not in response.json()
        finally:
            release.set()
            await task


@pytest.mark.anyio
async def test_no_next_model_call_after_role_revocation(command_root, isolated_command_database, monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.context import access
    from app.db import SessionLocal
    from app.models import ConversationMember, Message
    from app.services import chat
    entered, release = asyncio.Event(), asyncio.Event()
    original = access.require_context_access
    checks, model_calls = 0, 0

    async def pause_next(*args, **kwargs):
        nonlocal checks
        checks += 1
        # 调用前可有多层授权检查；暂停的是已执行一次模型/工具后的安全边界。
        if model_calls == 1 and not entered.is_set():
            entered.set()
            await asyncio.wait_for(release.wait(), 10)
        return await original(*args, **kwargs)

    class Model(ScriptedChatModel):
        async def _astream(self, messages, **kwargs):
            nonlocal model_calls
            model_calls += 1
            async for chunk in super()._astream(messages, **kwargs):
                yield chunk

    monkeypatch.setattr(access, 'require_context_access', pause_next)
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Model(turns=[ScriptedTurn(tool_calls=[{
        'name': 'workspace_write', 'args': {'path': 'already.txt', 'content': '已提交'}, 'id': 'already-applied'}]),
        ScriptedTurn(text='不应调用')], delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        sent = await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '受控写入后收回角色'}], 'mentions': [ids[0]]})
        assert sent.status_code == 202
        try:
            await asyncio.wait_for(entered.wait(), 10)
            async with SessionLocal() as session:
                await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == cid,
                    ConversationMember.member_type == 'role', ConversationMember.member_id == ids[0]))
                await session.commit()
        finally:
            release.set()
        execution = (await finished(cid, 1))[0]
        assert model_calls == 1 and execution.status == 'failed'
        assert execution.error_code == 'CONVERSATION_NOT_FOUND'
        assert (command_root / 'already.txt').read_text() == '已提交'
        async with SessionLocal() as session:
            from sqlalchemy import select
            row = await session.scalar(select(Message).where(Message.conversation_id == cid, Message.sender_type == 'role'))
            assert any(part.get('effect_state') == 'applied' for part in row.parts_json)


@pytest.mark.anyio
async def test_restart_rebuilds_sources_and_preserves_existing_versions(command_root, isolated_command_database):
    from app.db import SessionLocal, recover_interrupted_messages
    from app.context.store import backfill_contexts
    from app.models import ConversationContext, ConversationContextEntry
    async with setup_group(command_root) as (_, _, cid, ids, _):
        async with SessionLocal() as session:
            stable = await source(session, cid, sender_id=ids[0])
            pending = await source(session, cid, sender_id=ids[0], status='generating', text='崩溃前片段')
            stable_id, pending_id = stable.id, pending.id
            await session.commit()
        await recover_interrupted_messages()
        async with SessionLocal() as session:
            assert (await session.get(ConversationContextEntry, stable_id)).source_revision == 0
            interrupted = await session.get(ConversationContextEntry, pending_id)
            assert interrupted.state == 'excluded' and interrupted.reason == 'interrupted'
            version = (await session.get(ConversationContext, cid)).revision
        await backfill_contexts(batch_size=1)
        async with SessionLocal() as session:
            assert (await session.get(ConversationContext, cid)).revision == version


@pytest.mark.anyio
async def test_invalid_cached_source_cannot_expose_other_conversation(command_root, isolated_command_database):
    from app.db import SessionLocal
    from app.models import ConversationContextEntry
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        other = (await client.post('/api/conversations', headers=headers,
            json={'type': 'single', 'title': '另一个来源范围', 'role_ids': [ids[0]]})).json()['id']
        async with SessionLocal() as session:
            message = await source(session, other, text='PRIVATE_OTHER_CONTEXT_SAMPLE', sender_id=ids[0])
            await session.commit()
            entry = await session.get(ConversationContextEntry, message.id)
            entry.conversation_id = cid  # 故意破坏派生缓存，原消息归属仍是权威。
            await session.commit()
        response = await client.get(f'/api/conversations/{cid}/context', headers=headers)
        assert response.status_code == 409 and 'PRIVATE_OTHER_CONTEXT_SAMPLE' not in response.text
        response = await client.post(f'/api/conversations/{cid}/context/preview', headers=headers,
            json={'role_id': ids[0], 'include_content': True})
        assert response.status_code == 409 and 'PRIVATE_OTHER_CONTEXT_SAMPLE' not in response.text
