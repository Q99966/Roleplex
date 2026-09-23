"""固定世界岗位、配置导入及原角色隔离；全部使用隔离 World 和 fake Provider。"""
import pytest
from test_orchestrator import setup_group, command_root, isolated_command_database


@pytest.mark.anyio
async def test_world_creates_one_manager_and_import_changes_configuration_not_identity(command_root, isolated_command_database):
    from app.world_orchestrator import service
    async with setup_group(command_root) as (client, headers, _, ids, _):
        initial = (await client.get('/api/world-orchestrator', headers=headers)).json()
        assert initial.get('fixed_identity') is True
        assert initial['status'] == 'needs_configuration'
        manager_id, cid = initial['role_id'], initial['conversation']['id']
        assert manager_id not in ids
        assert manager_id not in {r['id'] for r in (await client.get('/api/roles', headers=headers)).json()}
        loaded = await client.post('/api/world-orchestrator/import-role', headers=headers,
            json={'role_id': ids[3], 'expected_revision': initial['revision']})
        assert loaded.status_code == 200
        current = loaded.json()
        assert current['role_id'] == manager_id and current['conversation']['id'] == cid
        assert current['available'] and current['profile']['managed_kind'] == 'world_manager'
        assert (await client.delete(f'/api/roles/{manager_id}', headers=headers)).status_code == 409
        assert (await client.post('/api/conversations', headers=headers,
            json={'type': 'single', 'title': '不能冒用系统岗位', 'role_ids': [manager_id]})).status_code == 422
        assert (await client.put('/api/world-orchestrator', headers=headers,
            json={'role_id': ids[0], 'expected_revision': current['revision']})).status_code == 409
        # 普通角色后来改人设，不会偷偷改动世界管理者的独立配置。
        source = (await client.get(f'/api/roles/{ids[3]}', headers=headers)).json()
        source['system_prompt'] = '只修改普通角色'
        await client.put(f'/api/roles/{ids[3]}', headers=headers, json={**source, 'expected_revision': source['revision']})
        await service.initialize()
        after = (await client.get('/api/world-orchestrator', headers=headers)).json()
        assert after['profile']['system_prompt'] == current['profile']['system_prompt']
        assert after['role_id'] == manager_id and after['conversation']['id'] == cid
        paused = await client.post('/api/world-orchestrator/enabled', headers=headers,
            json={'enabled': False, 'expected_revision': after['revision']})
        assert paused.status_code == 200 and paused.json()['status'] == 'disabled'
        assert paused.json()['role_id'] == manager_id


@pytest.mark.anyio
async def test_old_appointment_is_adopted_without_rewriting_role_or_history(command_root, isolated_command_database):
    from sqlalchemy import delete
    from app.db import SessionLocal
    from app.models import WorldOrchestrator, Role, ConversationMember
    from app.world_orchestrator import service
    async with setup_group(command_root) as (client, headers, _, ids, _):
        initial = (await client.get('/api/world-orchestrator', headers=headers)).json()
        cid = initial['conversation']['id']
        # 模拟已经停机升级的旧任命；原普通角色还属于自己的原会话。
        async with SessionLocal() as session:
            state = await session.get(WorldOrchestrator, 1)
            original_manager = await session.get(Role, state.role_id)
            original_manager.managed_kind = None; original_manager.deleted_at = state.updated_at
            state.role_id = ids[3]
            await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == cid,
                ConversationMember.member_type == 'role'))
            await session.commit()
        await service.initialize(); await service.initialize()
        recovered = (await client.get('/api/world-orchestrator', headers=headers)).json()
        assert recovered['role_id'] != ids[3] and recovered['conversation']['id'] == cid
        assert recovered['available']
        original = (await client.get(f'/api/roles/{ids[3]}', headers=headers)).json()
        assert not original.get('managed_kind') and original['deleted_at'] is None
        assert (await client.get('/api/world-orchestrator', headers=headers)).json()['role_id'] == recovered['role_id']
