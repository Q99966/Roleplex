"""世界约定有版本和来源，角色交接不会放开普通会话访问。"""
import pytest
from uuid import uuid4

from test_orchestrator import setup_group, command_root, isolated_command_database


@pytest.mark.anyio
async def test_memory_revision_disable_restore_and_owner_boundary(command_root, isolated_command_database):
    from accounts import guest_username, TEST_PASSWORD
    async with setup_group(command_root) as (client, headers, _, ids, _):
        base = '/api/world-orchestrator/memories'
        payload = {'request_key': uuid4().hex, 'category': 'constraint', 'text': '使用受控世界约定。'}
        created = await client.post(base, headers=headers, json=payload)
        assert created.status_code == 201
        row = created.json()
        duplicate = await client.post(base, headers=headers, json=payload)
        assert duplicate.json()['id'] == row['id']
        assert (await client.post(base, headers=headers, json={**payload, 'text': '不同请求'})).status_code == 409
        changed = await client.put(base + '/' + row['id'], headers=headers,
            json={'expected_revision': 1, 'text': '修订后的世界约定。', 'status': 'active'})
        assert changed.status_code == 200 and changed.json()['revision'] == 2
        assert (await client.get(base + '/' + row['id'] + '?revision=1', headers=headers)).status_code == 409
        stopped = await client.put(base + '/' + row['id'], headers=headers,
            json={'expected_revision': 2, 'text': '修订后的世界约定。', 'status': 'disabled'})
        assert stopped.status_code == 200 and stopped.json()['available'] is False
        from app.world_orchestrator import memory
        from app.db import SessionLocal
        from app.models import InstanceSettings
        async with SessionLocal() as session: uid = (await session.get(InstanceSettings, 1)).owner_user_id
        assert (await memory.search(uid, '修订'))['items'] == []
        versions = (await client.get(base + '/' + row['id'] + '/versions', headers=headers)).json()['items']
        assert [v['revision'] for v in versions] == [3, 2, 1]
        assert versions[-1]['text'] == payload['text']
        guest = (await client.post('/api/auth/register', json={'username': guest_username('worldmemory'),
            'nickname': 'Guest', 'password': TEST_PASSWORD})).json()
        assert (await client.get(base, headers={'Authorization': f"Bearer {guest['access_token']}"})).status_code == 403
