"""本地草稿分区只提供给当前会话的 Owner，分区不是访问凭据。"""
import pytest
from test_orchestrator import setup_group, command_root, isolated_command_database


@pytest.mark.anyio
async def test_draft_scope_stable_and_authorized(command_root, isolated_command_database):
    from accounts import guest_username, TEST_PASSWORD
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        url = f'/api/conversations/{cid}/workflows/draft-scope'
        first = await client.get(url, headers=headers)
        assert first.status_code == 200
        assert first.headers['cache-control'] == 'no-store'
        assert len(first.json()['scope']) == 64
        assert (await client.get(url, headers=headers)).json() == first.json()
        other = await client.post('/api/conversations', headers=headers, json={'type': 'single', 'role_ids': [ids[3]], 'title': '隔离会话'})
        assert other.status_code in (200, 201), other.text
        assert (await client.get(f"/api/conversations/{other.json()['id']}/workflows/draft-scope", headers=headers)).json() != first.json()
        assert (await client.get(url)).status_code == 401
        guest = (await client.post('/api/auth/register', json={'username': guest_username('draft'), 'nickname': 'Guest', 'password': TEST_PASSWORD})).json()
        assert (await client.get(url, headers={'Authorization': f"Bearer {guest['access_token']}"})).status_code == 403
        assert (await client.get('/api/conversations/999999/workflows/draft-scope', headers=headers)).status_code == 404
