"""主动压缩使用真实执行链，保留原消息，并可回退与检索。"""
import asyncio
from uuid import uuid4

import pytest

from test_conversation_context import source
from test_orchestrator import setup_group, command_root, isolated_command_database


async def wait_compaction(client, headers, cid, job_id):
    for _ in range(300):
        response = await client.get(f'/api/conversations/{cid}/context/compressions', headers=headers)
        assert response.status_code == 200
        job = next(row for row in response.json()['jobs'] if row['id'] == job_id)
        if job['status'] not in {'queued', 'running', 'stopping'}:
            return job
        await asyncio.sleep(.025)
    raise AssertionError('压缩维护请求未收口')


@pytest.mark.anyio
async def test_compaction_publishes_smaller_material_without_rewriting_messages(command_root, isolated_command_database):
    from app.db import SessionLocal
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        async with SessionLocal() as session:
            for i in range(8):
                await source(session, cid, text=f'第 {i} 项已确认：登录采用邮箱验证码。' + '受控讨论记录。' * 70, sender_id=ids[i % 2])
            await session.commit()
        base = f'/api/conversations/{cid}/context'
        before = (await client.get(base, headers=headers)).json()
        messages_before = (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()['items']
        body = {'request_key': uuid4().hex, 'expected_revision': before['revision'], 'role_id': ids[0],
            'keep_recent': 2, 'target_tokens': 900, 'instructions': '优先保留登录方式和待办。'}
        created = await client.post(base + '/compressions', headers=headers, json=body)
        assert created.status_code == 202
        repeated = await client.post(base + '/compressions', headers=headers, json=body)
        assert repeated.json()['id'] == created.json()['id']
        job = await wait_compaction(client, headers, cid, created.json()['id'])
        assert job['status'] == 'completed' and job['output_tokens_estimate'] < job['input_tokens_estimate']
        assert (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()['items'] == messages_before
        active = (await client.get(base, headers=headers)).json()
        assert active['active_summary']['id'] == job['id'] and active['active_summary']['source_count'] == 6
        # 生成摘要所用的角色模型不是压缩对象：两个角色都采用同一个会话摘要。
        for role_id in ids[:2]:
            preview = (await client.post(base + '/preview', headers=headers, json={'role_id': role_id, 'include_content': True})).json()
            assert preview['material']['summary_id'] == job['id']
            assert any('会话历史摘要' in row['content'] for row in preview['messages'])
        from sqlalchemy import func, select
        from app.models import ContextSummary, ContextCompressionSource, Message
        async with SessionLocal() as session:
            assert await session.scalar(select(func.count()).select_from(ContextSummary).where(ContextSummary.conversation_id == cid)) == 1
            senders = set((await session.scalars(select(Message.sender_id).join(ContextCompressionSource,
                ContextCompressionSource.message_id == Message.id).where(ContextCompressionSource.compression_id == job['id']))).all())
            assert senders == set(ids[:2])
        restored = await client.post(base + '/restore', headers=headers,
            json={'expected_revision': active['revision'], 'summary_id': None})
        assert restored.status_code == 200 and restored.json()['active_summary'] is None
