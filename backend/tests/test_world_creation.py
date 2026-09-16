"""界面创建 World 的权限、并发与失败语义。"""
import asyncio
import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient
from accounts import ensure_owner_async, guest_username, TEST_PASSWORD


@pytest.mark.anyio
async def test_creation_contract(tmp_path, monkeypatch):
    """同名并发不覆盖数据；拒绝 Guest、非法路径与切换期间的写入。"""
    from app.config import settings
    from app.main import app
    from app.routers import worlds
    from app.runtime.manager import manager as runtime
    from app.worlds import WorldManager

    manager = WorldManager(tmp_path / 'worlds')
    monkeypatch.setattr(worlds, 'manager', manager)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            owner = await ensure_owner_async(client)
            headers = {'Authorization': f"Bearer {owner['access_token']}"}
            assert (await client.post('/api/worlds', json={'name': 'anonymous'})).status_code == 401
            guest = (await client.post('/api/auth/register', json={
                'username': guest_username('world_create'), 'nickname': 'Guest', 'password': TEST_PASSWORD,
            })).json()
            response = await client.post('/api/worlds', json={'name': 'guest'}, headers={
                'Authorization': f"Bearer {guest['access_token']}"})
            assert response.status_code == 403
            assert response.json()['error']['code'] == 'OWNER_REQUIRED'
            response = await client.post('/api/worlds', headers=headers, json={'name': 'compat'})
            assert response.status_code == 409
            assert response.json()['error']['code'] == 'WORLD_OPERATION_REQUIRES_MANAGED'
            monkeypatch.setattr(settings, '_world_managed', True)
            monkeypatch.setattr(settings, 'worlds_dir', str(tmp_path / 'auth-worlds'))
            # 运行模式改变会切换 JWT 密钥；重新签发，不能沿用兼容模式 Token。
            owner = await ensure_owner_async(client)
            headers = {'Authorization': f"Bearer {owner['access_token']}"}
            assert (await client.get('/api/worlds', headers=headers)).json()['creation_supported'] is True
            for name in ['../escape', ' ', '.', 'a/b', 'bad\\name', 'x\x00y']:
                response = await client.post('/api/worlds', headers=headers, json={'name': name})
                assert response.status_code == 422
                assert response.json()['error']['code'] == 'WORLD_NAME_INVALID'
            for body in [{'name': ''}, {'name': 'x' * 65}, {'name': 'valid', 'path': '/tmp'}]:
                assert (await client.post('/api/worlds', headers=headers, json=body)).status_code == 422
            first, second = await asyncio.gather(*[
                client.post('/api/worlds', headers=headers, json={'name': ' 新世界 '}) for _ in range(2)
            ])
            assert sorted([first.status_code, second.status_code]) == [201, 409]
            created = first if first.status_code == 201 else second
            assert set(created.json()) == {'name', 'current', 'created_at'}
            assert created.json()['name'] == '新世界'
            assert created.json()['current'] is False
            world = manager.get('新世界')
            secret = (world.path / '.jwt-secret').read_bytes()
            duplicate = await client.post('/api/worlds', headers=headers, json={'name': '新世界'})
            assert duplicate.json()['error']['code'] == 'WORLD_ALREADY_EXISTS'
            assert (world.path / '.jwt-secret').read_bytes() == secret
            listing = (await client.get('/api/worlds', headers=headers)).json()
            assert [item['name'] for item in listing['items']] == ['新世界']
            with sqlite3.connect(world.database_path) as db:
                assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
            with monkeypatch.context() as scoped:
                scoped.setattr(runtime, 'switch_target', 'elsewhere')
                response = await client.post('/api/worlds', headers=headers, json={'name': 'blocked'})
                assert response.json()['error']['code'] == 'WORLD_OPERATION_IN_PROGRESS'
            for error_type in [OSError, sqlite3.OperationalError]:
                def fail_create(name):
                    raise error_type('private storage path')
                monkeypatch.setattr(manager, 'create', fail_create)
                failed = await client.post('/api/worlds', headers=headers, json={'name': 'failed'})
                assert failed.status_code == 503
                assert failed.json()['error']['code'] == 'WORLD_OPERATION_FAILED'
                assert 'private storage path' not in failed.text
            assert [world.name for world in manager.list_worlds()] == ['新世界']
