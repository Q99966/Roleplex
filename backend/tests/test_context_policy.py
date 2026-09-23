"""会话阈值采用全部有效成员的最小窗口，配置继承和动态收紧可见。"""
import pytest

from test_orchestrator import setup_group, command_root, isolated_command_database


@pytest.mark.anyio
async def test_policy_cap_recommendation_revision_and_inheritance(command_root, isolated_command_database):
    from app.db import SessionLocal
    from app.models import Role
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        async with SessionLocal() as session:
            for rid, window in zip(ids, [200_000, 1_000_000, 100_000, 200_000]):
                (await session.get(Role, rid)).context_window_tokens = window
            await session.commit()
        url = f'/api/conversations/{cid}/context/policy'
        response = await client.get(url, headers=headers)
        assert response.status_code == 200
        view = response.json()
        assert view['inherited'] and view['limits']['ceiling_tokens'] == 100_000
        assert view['limits']['recommended_trigger_tokens'] == 50_000
        assert view['limits']['recommended_reserve_tokens'] == 50_000
        policy = {**view['policy'], 'enabled': True, 'trigger_tokens': 100_001}
        assert (await client.put(url, headers=headers, json={'expected_revision': 0, 'policy': policy})).status_code == 422
        policy['trigger_tokens'] = 90_000
        saved = await client.put(url, headers=headers, json={'expected_revision': 0, 'policy': policy})
        assert saved.status_code == 200 and saved.json()['effective']['trigger_tokens'] == 90_000
        assert (await client.put(url, headers=headers, json={'expected_revision': 0, 'policy': policy})).status_code == 409
        # 未发言的较小窗口成员一样约束会话；保留用户原配置并显示实际收紧值。
        async with SessionLocal() as session:
            (await session.get(Role, ids[3])).context_window_tokens = 32_000
            await session.commit()
        view = (await client.get(url, headers=headers)).json()
        assert view['policy']['trigger_tokens'] == 90_000
        assert view['effective']['trigger_tokens'] == 32_000
        assert view['limits']['recommended_trigger_tokens'] == 16_000
        assert 'threshold_clamped' in view['notices'] and 'reserve_adjusted' in view['notices']
        world = (await client.get('/api/context-policy', headers=headers)).json()
        world['policy']['enabled'] = True
        world['policy']['instructions'] = '优先保留已确认目标和未完成事项。'
        assert (await client.put('/api/context-policy', headers=headers,
            json={'expected_revision': world['revision'], 'policy': world['policy']})).status_code == 200
        inherited = await client.put(url, headers=headers, json={'expected_revision': 1, 'policy': None})
        assert inherited.status_code == 200 and inherited.json()['inherited']
        assert inherited.json()['policy']['instructions'] == world['policy']['instructions']


@pytest.mark.anyio
async def test_policy_owner_and_model_membership(command_root, isolated_command_database):
    from accounts import guest_username, TEST_PASSWORD
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        guest = (await client.post('/api/auth/register', json={'username': guest_username('contextpolicy'),
            'nickname': 'Guest', 'password': TEST_PASSWORD})).json()
        gh = {'Authorization': f"Bearer {guest['access_token']}"}
        for url in ['/api/context-policy', f'/api/conversations/{cid}/context/policy']:
            assert (await client.get(url, headers=gh)).status_code == 403
        url = f'/api/conversations/{cid}/context/policy'
        policy = (await client.get(url, headers=headers)).json()['policy']
        assert (await client.put(url, headers=headers, json={'expected_revision': 0,
            'policy': {**policy, 'model_role_id': 999999}})).status_code == 422
