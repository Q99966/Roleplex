"""提示词配置的产品入口、版本竞争、角色配置保留与真实输入生效。"""
import asyncio
from uuid import uuid4

import pytest

from test_orchestrator import setup_group, command_root, isolated_command_database


async def put_world(client, headers, revision, *, platform=None, world=''):
    return await client.put('/api/prompt-settings', headers=headers, json={
        'expected_revision': revision, 'platform_override': platform, 'world_prompt': world,
    })


async def put_conversation(client, headers, cid, revision, prompt):
    return await client.put(f'/api/conversations/{cid}/prompt-settings', headers=headers,
        json={'expected_revision': revision, 'prompt': prompt})


@pytest.mark.anyio
async def test_prompt_defaults_override_clear_and_effective_layers(command_root, isolated_command_database):
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        initial = await client.get('/api/prompt-settings', headers=headers)
        assert initial.status_code == 200
        assert initial.headers['cache-control'] == 'no-store'
        defaults = initial.json()
        assert defaults['platform_override'] is None and defaults['revision'] == 0
        assert defaults['platform_default'] and defaults['template_version']
        changed = await put_world(client, headers, 0, platform='平台配置样例', world='世界配置样例')
        assert changed.status_code == 200 and changed.json()['revision'] == 1
        changed = await put_conversation(client, headers, cid, 0, '会话配置样例')
        assert changed.status_code == 200 and changed.json()['revision'] == 1
        preview = await client.get(f'/api/conversations/{cid}/prompt-preview', headers=headers, params={'role_id': ids[0]})
        assert preview.status_code == 200
        assert preview.headers['cache-control'] == 'no-store'
        layers = {layer['key']: layer for layer in preview.json()['layers']}
        assert list(layers) == ['runtime', 'platform', 'world', 'role', 'skills', 'conversation']
        assert layers['platform']['source'] == 'override' and layers['platform']['text'] == '平台配置样例'
        assert layers['world']['text'] == '世界配置样例'
        assert '会话配置样例' in layers['conversation']['text']
        assert preview.json()['revisions']['world'] == 1 and preview.json()['revisions']['conversation'] == 1
        cleared = await put_world(client, headers, 1, platform='', world='')
        assert cleared.status_code == 200 and cleared.json()['platform_override'] == ''
        restored = await put_world(client, headers, 2)
        assert restored.status_code == 200 and restored.json()['platform_override'] is None
        assert restored.json()['platform_default'] == defaults['platform_default']
        assert (await client.get(f'/api/conversations/{cid}/prompt-settings', headers=headers)).json()['prompt'] == '会话配置样例'


@pytest.mark.anyio
async def test_prompt_writes_use_compare_and_swap_and_preserve_other_scope(command_root, isolated_command_database):
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        # 同一基准同时更新，唯一获胜者的整份配置保留；失败者不能覆盖部分字段。
        responses = await asyncio.gather(
            put_world(client, headers, 0, platform='规则甲', world='世界甲'),
            put_world(client, headers, 0, platform='规则乙', world='世界乙'),
        )
        assert sorted(r.status_code for r in responses) == [200, 409]
        winner = next(r.json() for r in responses if r.status_code == 200)
        assert (await client.get('/api/prompt-settings', headers=headers)).json() == winner
        assert next(r for r in responses if r.status_code == 409).json()['error']['code'] == 'PROMPT_REVISION_CONFLICT'
        responses = await asyncio.gather(
            put_conversation(client, headers, cid, 0, '会话甲'), put_conversation(client, headers, cid, 0, '会话乙'),
        )
        assert sorted(r.status_code for r in responses) == [200, 409]
        other = (await client.post('/api/conversations', headers=headers,
            json={'type': 'single', 'role_ids': [ids[0]], 'title': '其他会话'})).json()['id']
        assert (await client.get(f'/api/conversations/{other}/prompt-settings', headers=headers)).json()['prompt'] == ''
        assert (await client.get('/api/prompt-settings', headers=headers)).json() == winner
        unknown_scope = await client.put('/api/prompt-settings', headers=headers, json={
            'expected_revision': 1, 'platform_override': None, 'world_prompt': '', 'world_name': 'another-world',
        })
        assert unknown_scope.status_code == 422
        assert (await put_world(client, headers, 2**128)).status_code == 422
        assert (await put_conversation(client, headers, cid, 2**128, '无效版本')).status_code == 422


@pytest.mark.anyio
async def test_prompt_endpoints_require_owner_membership_and_role_ownership(command_root, isolated_command_database):
    from accounts import TEST_PASSWORD, guest_username
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        guest = (await client.post('/api/auth/register', json={
            'username': guest_username('prompts'), 'password': TEST_PASSWORD, 'nickname': 'Guest',
        })).json()
        guest_headers = {'Authorization': f"Bearer {guest['access_token']}"}
        from app.db import SessionLocal, now_utc
        from app.models import ConversationMember
        async with SessionLocal() as session:
            session.add(ConversationMember(conversation_id=cid, member_type='user', member_id=guest['user']['id'], joined_at=now_utc()))
            await session.commit()
        for route in ['/api/prompt-settings', f'/api/conversations/{cid}/prompt-settings', f'/api/conversations/{cid}/prompt-preview?role_id={ids[0]}']:
            assert (await client.get(route)).status_code == 401
            assert (await client.get(route, headers=guest_headers)).status_code == 403
        assert (await put_world(client, guest_headers, 0, world='无权修改')).status_code == 403
        assert (await put_conversation(client, guest_headers, cid, 0, '无权修改')).status_code == 403
        assert (await client.get('/api/conversations/999999/prompt-settings', headers=headers)).status_code == 404
        only = (await client.post('/api/conversations', headers=headers,
            json={'type': 'single', 'role_ids': [ids[0]], 'title': '角色范围'})).json()['id']
        assert (await client.get(f'/api/conversations/{only}/prompt-preview?role_id={ids[1]}', headers=headers)).status_code == 404
        async with SessionLocal() as session:
            from sqlalchemy import delete
            await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == only, ConversationMember.member_type == 'user'))
            await session.commit()
        assert (await put_conversation(client, headers, only, 0, '已退出')).status_code == 404


@pytest.mark.anyio
async def test_role_update_preserves_omitted_skills_and_mcp_and_rejects_stale_revision(command_root, isolated_command_database):
    async with setup_group(command_root) as (client, headers, _, ids, _):
        role = (await client.get(f'/api/roles/{ids[0]}', headers=headers)).json()
        payload = {**role, 'expected_revision': role['revision'],
            'skills': [{'name': '受控技能', 'instructions': '保留配置'}],
            'mcp_servers': [{'name': '受控服务', 'command': 'placeholder-command', 'env': {'EXAMPLE': 'placeholder'}}]}
        saved = await client.put(f'/api/roles/{ids[0]}', headers=headers, json=payload)
        assert saved.status_code == 200
        saved = saved.json()
        edit = {key: value for key, value in saved.items() if key not in {'skills', 'mcp_servers'}}
        edit.update(name='仅修改名称', expected_revision=saved['revision'])
        updated = await client.put(f'/api/roles/{ids[0]}', headers=headers, json=edit)
        assert updated.status_code == 200
        assert updated.json()['skills'] == saved['skills'] and updated.json()['mcp_servers'] == saved['mcp_servers']
        stale = await client.put(f'/api/roles/{ids[0]}', headers=headers, json=edit)
        assert stale.status_code == 409 and stale.json()['error']['code'] == 'ROLE_REVISION_CONFLICT'
        clear = await client.put(f'/api/roles/{ids[0]}', headers=headers, json={
            **edit, 'expected_revision': updated.json()['revision'], 'skills': [], 'mcp_servers': [],
        })
        assert clear.status_code == 200 and clear.json()['skills'] == [] and clear.json()['mcp_servers'] == []


@pytest.mark.anyio
async def test_real_generation_uses_frozen_prompt_versions_then_next_request_uses_changes(command_root, isolated_command_database, monkeypatch):
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
        from app.db import SessionLocal
        from app.models import AgentExecution
        from app.services import chat
        from sqlalchemy import select
        captured = []
        entered, release = asyncio.Event(), asyncio.Event()

        class RecordingModel(ScriptedChatModel):
            async def _astream(self, messages, **kwargs):
                captured.append(str(messages[0].content))
                entered.set()
                await asyncio.wait_for(release.wait(), 10)
                async for chunk in super()._astream(messages, **kwargs):
                    yield chunk

        monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: RecordingModel(turns=[ScriptedTurn(text='输入核对完成')], delay=0))
        assert (await put_world(client, headers, 0, platform='平台旧版本', world='世界旧版本')).status_code == 200
        assert (await put_conversation(client, headers, cid, 0, '会话旧版本')).status_code == 200
        before = (await client.get(f'/api/conversations/{cid}/prompt-preview?role_id={ids[0]}', headers=headers)).json()
        response = await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '第一次输入'}], 'mentions': [ids[0]]})
        assert response.status_code == 202
        await asyncio.wait_for(entered.wait(), 10)
        assert (await put_world(client, headers, 1, platform='平台新版本', world='世界新版本')).status_code == 200
        assert (await put_conversation(client, headers, cid, 1, '会话新版本')).status_code == 200
        release.set()

        async def finished(count):
            for _ in range(200):
                async with SessionLocal() as session:
                    rows = (await session.scalars(select(AgentExecution).where(AgentExecution.conversation_id == cid).order_by(AgentExecution.id))).all()
                    if len(rows) == count and all(row.status in {'completed', 'failed', 'stopped'} for row in rows):
                        return rows
                await asyncio.sleep(.025)
            raise AssertionError('提示词生成未收口')

        first = (await finished(1))[0]
        assert first.status == 'completed'
        assert all(text in captured[0] for text in ['平台旧版本', '世界旧版本', '会话旧版本'])
        assert '世界新版本' not in captured[0]
        assert first.context_snapshot_json['revisions'] == before['revisions']
        old_snapshot = first.context_snapshot_json
        response = await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '第二次输入'}], 'mentions': [ids[0]]})
        assert response.status_code == 202
        rows = await finished(2)
        assert rows[0].context_snapshot_json == old_snapshot
        assert rows[1].context_snapshot_json['revisions']['world'] == 2
        assert rows[1].context_snapshot_json['revisions']['conversation'] == 2
        assert all(text in captured[1] for text in ['平台新版本', '世界新版本', '会话新版本'])
        assert '世界新版本' not in str(rows[1].context_snapshot_json)


@pytest.mark.anyio
async def test_preview_and_execution_share_native_tool_definitions_without_extra_grants(command_root, isolated_command_database, monkeypatch):
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
        from app.services import chat
        bound = []

        class Model(ScriptedChatModel):
            def bind_tools(self, tools, **kwargs):
                from langchain_core.utils.function_calling import convert_to_openai_tool
                bound.append([convert_to_openai_tool(tool)['function'] for tool in tools])
                return self

        monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Model(turns=[ScriptedTurn(text='工具定义核对完成')], delay=0))
        preview = await client.get(f'/api/conversations/{cid}/prompt-preview?role_id={ids[0]}', headers=headers)
        assert preview.status_code == 200
        specs = preview.json()['capabilities']['tools']
        assert {item['source'] for item in specs} == {'workspace'}
        assert {item['name'] for item in specs} == {'workspace_read', 'workspace_write'}
        assert not any('mcp' in item['name'] or 'workflow_' in item['name'] for item in specs)
        response = await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '核对实际工具'}], 'mentions': [ids[0]]})
        assert response.status_code == 202
        for _ in range(200):
            if bound: break
            await asyncio.sleep(.025)
        assert bound
        assert bound[0] == [{key: item[key] for key in ['name', 'description', 'parameters']} for item in specs]


@pytest.mark.anyio
async def test_prompt_storage_error_does_not_expose_sql_parameters(command_root, isolated_command_database, monkeypatch, caplog):
    async with setup_group(command_root) as (client, headers, cid, _, _):
        from app.routers import prompt_settings
        from sqlalchemy.exc import OperationalError
        marker = 'PRIVATE_PROMPT_ERROR_SAMPLE'
        async def unavailable(operation):
            raise OperationalError('UPDATE instance_settings', {'world_prompt': marker}, RuntimeError('controlled storage failure'))
        monkeypatch.setattr(prompt_settings, 'with_locked_retry', unavailable)
        for response in [await put_world(client, headers, 0, world=marker), await put_conversation(client, headers, cid, 0, marker)]:
            assert response.status_code == 503
            assert response.json()['error']['code'] == 'PROMPT_STORAGE_UNAVAILABLE'
            assert marker not in response.text
        assert marker not in caplog.text


@pytest.mark.anyio
async def test_context_rejection_has_no_fabricated_adopted_snapshot(command_root, isolated_command_database):
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        role = (await client.get(f'/api/roles/{ids[0]}', headers=headers)).json()
        changed = await client.put(f'/api/roles/{ids[0]}', headers=headers, json={
            **role, 'expected_revision': role['revision'], 'context_window_tokens': 4096, 'builtin_tools': [],
        })
        assert changed.status_code == 200
        assert (await put_world(client, headers, 0, world='受控长规则' * 2000)).status_code == 200
        sent = await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '最小请求'}], 'mentions': [ids[0]]})
        assert sent.status_code == 202
        from app.db import SessionLocal
        from app.models import AgentExecution, ModelCallUsage
        from sqlalchemy import select
        for _ in range(200):
            async with SessionLocal() as session:
                row = await session.scalar(select(AgentExecution).where(AgentExecution.conversation_id == cid))
                if row and row.status == 'failed':
                    assert row.error_code == 'CONTEXT_BUDGET_EXCEEDED'
                    assert row.context_snapshot_json is None
                    assert await session.scalar(select(ModelCallUsage.id).where(ModelCallUsage.execution_id == row.execution_id)) is None
                    break
            await asyncio.sleep(.025)
        else:
            raise AssertionError('上下文拒绝没有收口')
