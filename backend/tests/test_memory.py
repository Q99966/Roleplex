"""主动检索的中文/代码引用、来源版本与目的地共享权限。"""
import pytest

from test_conversation_context import source
from test_orchestrator import setup_group, command_root, isolated_command_database


@pytest.mark.anyio
async def test_memory_search_read_and_source_revision(command_root, isolated_command_database):
    from app.db import SessionLocal
    from app.models import Message
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        async with SessionLocal() as session:
            message = await source(session, cid, text='登录方案使用邮箱验证码。实现入口是 verify_login_code，保留 rate_limit 检查。', sender_id=ids[0])
            mid = message.id
            await source(session, cid, text='另一个无关的话题', sender_id=ids[1])
            await session.commit()
        url = f'/api/conversations/{cid}/memory'
        for query in ['登录方案', '邮箱 验证码', 'verify_login_code', 'VERIFY_LOGIN_CODE']:
            response = await client.post(url + '/search', headers=headers, json={'role_id': ids[0], 'query': query})
            assert response.status_code == 200
            results = response.json()['results']
            assert results[0]['message_id'] == mid and results[0]['source_revision'] == 0
            assert results[0]['matched_terms'] and results[0]['kind'] == 'message'
        reference = results[0]['reference']
        read = await client.post(url + '/read', headers=headers, json={'role_id': ids[0], 'reference': reference, 'max_characters': 12})
        assert read.status_code == 200 and read.json()['truncated'] is True
        assert read.json()['next_offset'] == 12
        assert read.json()['text'] == '登录方案使用邮箱验证码。'
        async with SessionLocal() as session:
            message = await session.get(Message, mid)
            message.parts_json = [{'type': 'text', 'text': '已经修订的来源'}]
            message.revision += 1
            await session.commit()
        stale = await client.post(url + '/read', headers=headers, json={'role_id': ids[0], 'reference': reference})
        assert stale.status_code == 409 and stale.json()['error']['code'] == 'MEMORY_SOURCE_CHANGED'
        assert '邮箱验证码' not in stale.text


@pytest.mark.anyio
async def test_related_memory_must_be_shareable_with_every_destination_member(command_root, isolated_command_database):
    from app.db import SessionLocal
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        private = (await client.post('/api/conversations', headers=headers,
            json={'title': '角色私有会话', 'type': 'single', 'role_ids': [ids[0]]})).json()['id']
        async with SessionLocal() as session:
            await source(session, private, text='PRIVATE_MEMORY_CODE_A', sender_id=ids[0])
            await source(session, cid, text='SHARED_MEMORY_CODE_B', sender_id=ids[1])
            await session.commit()
        group = await client.post(f'/api/conversations/{cid}/memory/search', headers=headers,
            json={'role_id': ids[0], 'query': 'PRIVATE_MEMORY_CODE_A', 'scope': 'related'})
        assert group.status_code == 200 and group.json()['results'] == []
        single = await client.post(f'/api/conversations/{private}/memory/search', headers=headers,
            json={'role_id': ids[0], 'query': 'SHARED_MEMORY_CODE_B', 'scope': 'related'})
        assert single.status_code == 200 and single.json()['results'][0]['conversation_id'] == cid
        # 合法引用也绑定读取的角色及回复目的地，不能转贴到无权共享的群继续读取。
        private_result = (await client.post(f'/api/conversations/{private}/memory/search', headers=headers,
            json={'role_id': ids[0], 'query': 'PRIVATE_MEMORY_CODE_A'})).json()['results'][0]
        denied = await client.post(f'/api/conversations/{cid}/memory/read', headers=headers,
            json={'role_id': ids[0], 'reference': private_result['reference']})
        assert denied.status_code == 404 and 'PRIVATE_MEMORY_CODE_A' not in denied.text


@pytest.mark.anyio
async def test_agent_actually_searches_reads_and_keeps_private_queries_out_of_receipts(command_root, isolated_command_database):
    from app.db import SessionLocal
    from app.models import AgentExecution, MemoryReference, Message, ModelCallUsage
    from test_context_execution import finished
    from sqlalchemy import select
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        role = (await client.get(f'/api/roles/{ids[0]}', headers=headers)).json()
        assert (await client.put(f'/api/roles/{ids[0]}', headers=headers, json={**role,
            'expected_revision': role['revision'], 'builtin_tools': ['memory_search', 'memory_read']})).status_code == 200
        detached = await client.put(f'/api/conversations/{cid}/workspace', headers=headers,
            json={'workspace_binding_id': None, 'expected_revision': 0})
        assert detached.status_code == 200 and detached.json()['workspace_binding_id'] is None
        secret = 'MEMORY_CONTROLLED_VALUE_27'
        async with SessionLocal() as session:
            old = await source(session, cid, text='历史协议档案：受控核对值为 ' + secret, sender_id=ids[1])
            mid = old.id
            await session.commit()
        sent = await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '[MEMORY_PROBE] 历史协议档案'}], 'mentions': [ids[0]]})
        assert sent.status_code == 202
        execution = (await finished(cid, 1))[0]
        assert execution.status == 'completed'
        async with SessionLocal() as session:
            reply = await session.scalar(select(Message).where(Message.conversation_id == cid, Message.sender_id == ids[0], Message.sender_type == 'role'))
            assert secret in str(reply.parts_json)
            parts = [part for part in reply.parts_json if part.get('type') == 'tool_call']
            assert [part['tool_name'] for part in parts] == ['memory_search', 'memory_read']
            assert all(part['effect_state'] == 'not_applicable' for part in parts)
            refs = (await session.scalars(select(MemoryReference).where(MemoryReference.execution_id == execution.execution_id))).all()
            assert {row.action for row in refs} == {'search', 'read'}
            assert all(row.source_id == str(mid) for row in refs)
            assert secret not in str(execution.context_snapshot_json)
            calls = (await session.scalars(select(ModelCallUsage).where(ModelCallUsage.execution_id == execution.execution_id))).all()
            assert len(calls) == 3 and all(row.input_tokens is None for row in calls)
        records = (await client.get(f'/api/conversations/{cid}/memory/references', headers=headers, params={'role_id': ids[0]})).json()
        assert len(records['items']) == 2 and all(row['available'] for row in records['items'])


@pytest.mark.anyio
async def test_workflow_can_assign_memory_without_a_workspace(command_root, isolated_command_database):
    from app.db import SessionLocal
    from test_orchestrator import launch, wait_state
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        role = (await client.get(f'/api/roles/{ids[0]}', headers=headers)).json()
        assert (await client.put(f'/api/roles/{ids[0]}', headers=headers, json={**role,
            'expected_revision': role['revision'], 'builtin_tools': ['memory_search', 'memory_read']})).status_code == 200
        assert (await client.put(f'/api/conversations/{cid}/workspace', headers=headers,
            json={'workspace_binding_id': None, 'expected_revision': 0})).status_code == 200
        async with SessionLocal() as session:
            await source(session, cid, text='旧协议档案：历史受控值 WORKFLOW_MEMORY_CHECK', sender_id=ids[1])
            await session.commit()
        graph = {'runtime_version': 2, 'nodes': [{'id': 'lookup', 'kind': 'role', 'title': '检索历史', 'role_id': ids[0],
            'task': '[MEMORY_PROBE] 旧协议档案', 'tools': ['memory_search', 'memory_read']}], 'edges': [], 'loops': [], 'concurrency': 1}
        rid = await launch(client, headers, cid, graph)
        state = await wait_state(client, headers, cid, rid, lambda run: run['status'] in {'completed', 'failed', 'stopped'})
        assert state['status'] == 'completed'
        history = (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()['items']
        replies = [row for row in history if row['sender_id'] == ids[0] and row['sender_type'] == 'role']
        assert any('WORKFLOW_MEMORY_CHECK' in str(row['parts_json']) for row in replies)
