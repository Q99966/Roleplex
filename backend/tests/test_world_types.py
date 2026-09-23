"""类型身份、初始化和类型资料通过真实世界/会话接口生效。"""
import json
from pathlib import Path

import pytest

from test_orchestrator import setup_group, command_root, isolated_command_database


def test_manifest_type_is_persistent_and_legacy_remains_general(tmp_path):
    from app.world_types.registry import register, unregister
    from app.world_types.contracts import WorldTypeDescriptor
    from app.worlds.manager import WorldManager
    from app.worlds.compatibility import WorldTypeUnavailable
    register(WorldTypeDescriptor(id='controlled', version=1, name='受控类型'))
    try:
        manager = WorldManager(tmp_path)
        world = manager.create('typed', world_type='controlled')
        assert world.world_type == 'controlled' and world.type_version == 1 and world.format_version == 2
        assert json.loads((world.path / 'world.json').read_text())['world_type'] == 'controlled'
        import zipfile
        archive = manager.backup('typed', tmp_path / 'backups')
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(tmp_path / 'restored')
        restored = WorldManager(tmp_path / 'restored').get('typed')
        assert (restored.world_type, restored.type_version, restored.format_version) == ('controlled', 1, 2)
        assert (restored.path / 'world.json').read_bytes() == (world.path / 'world.json').read_bytes()
        unregister('controlled', 1)
        assert manager.list_worlds()[0].unavailable_reason == 'WORLD_TYPE_UNAVAILABLE'
        before = (world.path / 'world.json').read_bytes()
        with pytest.raises(WorldTypeUnavailable):
            manager.ensure('typed')
        assert (world.path / 'world.json').read_bytes() == before
        old = manager.create('legacy')
        manifest = {'name': old.name, 'created_at': old.created_at, 'format_version': 1}
        (old.path / 'world.json').write_text(json.dumps(manifest))
        assert manager.get('legacy').world_type == 'general'
        assert manager.get('legacy').type_version == 1
        with pytest.raises(WorldTypeUnavailable):
            manager.create('missing', world_type='does_not_exist')
        assert not (tmp_path / 'missing').exists()
    finally:
        unregister('controlled', 1)


def test_type_version_and_manifest_shape_are_strict(tmp_path):
    from app.worlds.manager import WorldManager
    from app.worlds.compatibility import WorldTypeUnavailable
    manager = WorldManager(tmp_path)
    world = manager.create('strict')
    path = world.path / 'world.json'
    base = json.loads(path.read_text())
    for patch in [{'type_version': True}, {'world_type': '../module'}, {'format_version': True}, {'type_version': 0}]:
        path.write_text(json.dumps({**base, **patch}))
        with pytest.raises(ValueError):
            manager.get('strict')
    path.write_text(json.dumps({**base, 'type_version': 999}))
    with pytest.raises(WorldTypeUnavailable):
        manager.ensure('strict')


def test_missing_manifest_never_relabels_existing_database(tmp_path):
    import sqlite3
    from app.worlds.manager import WorldManager
    manager = WorldManager(tmp_path)
    world = manager.create('existing')
    with sqlite3.connect(world.database_path) as db:
        db.execute('CREATE TABLE controlled (value INTEGER)')
    (world.path / 'world.json').unlink()
    before = world.database_path.read_bytes()
    with pytest.raises(ValueError):
        manager.ensure('existing')
    assert world.database_path.read_bytes() == before and not (world.path / 'world.json').exists()


@pytest.mark.anyio
async def test_type_configuration_initialization_and_context_are_one_product_path(command_root, isolated_command_database):
    from pydantic import BaseModel, ConfigDict
    from sqlalchemy import func, select
    from app.world_types import registry, service
    from app.world_types.contracts import Initialization, WorldMaterial, WorldTypeDescriptor
    from app.db import SessionLocal
    from app.models import Conversation, ConversationMember
    from app.db import now_utc
    class Configuration(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        label: str = ''
    async def initialize(session, ctx, config, resources):
        if not config['label']:
            return Initialization(status='needs_configuration', required_fields=('label',))
        if not resources:
            row = Conversation(type='group', title='受控类型资源', created_by=ctx.owner_id, created_at=now_utc())
            session.add(row); await session.flush()
            session.add(ConversationMember(conversation_id=row.id, member_type='user', member_id=ctx.owner_id, joined_at=now_utc()))
            resources = {'conversation_id': row.id}
        return Initialization(resources=resources)
    async def material(session, ctx, config):
        return [WorldMaterial(source_id='config', revision=1, text='WORLD_TYPE_CONTROLLED_CONTEXT')]
    async def validate(session, ctx, receipt):
        return True
    registry.register(WorldTypeDescriptor(id='controlled', version=1, name='受控类型', configuration_model=Configuration,
        initializer=initialize, build_context=material, validate_sources=validate))
    try:
        async with setup_group(command_root) as (client, headers, cid, ids, _):
            service.configure('controlled-world', 'controlled', 1)
            before = await client.get('/api/world-type', headers=headers)
            assert before.status_code == 200 and before.json()['initialization']['status'] == 'pending'
            assert 'controlled' in [t['id'] for t in (await client.get('/api/world-types', headers=headers)).json()['items']]
            initialized = await client.post('/api/world-type/initialize', headers=headers, json={'expected_revision': before.json()['revision']})
            assert initialized.status_code == 200 and initialized.json()['initialization']['status'] == 'needs_configuration'
            changed = await client.put('/api/world-type/config', headers=headers,
                json={'expected_revision': initialized.json()['revision'], 'configuration': {'label': '受控设置'}})
            assert changed.status_code == 200 and changed.json()['initialization']['status'] == 'ready'
            revision = changed.json()['revision']
            repeated = await client.post('/api/world-type/initialize', headers=headers, json={'expected_revision': revision})
            assert repeated.status_code == 200 and repeated.json()['revision'] == revision
            assert (await client.put('/api/world-type/config', headers=headers,
                json={'expected_revision': 0, 'configuration': {'label': '旧修改'}})).status_code == 409
            assert (await client.put('/api/world-type/config', headers=headers,
                json={'expected_revision': revision, 'configuration': {'unknown': 'invalid'}})).status_code == 422
            async with SessionLocal() as session:
                assert await session.scalar(select(func.count()).select_from(Conversation).where(Conversation.title == '受控类型资源')) == 1
            preview = await client.post(f'/api/conversations/{cid}/context/preview', headers=headers,
                json={'role_id': ids[0], 'include_content': True})
            assert preview.status_code == 200 and 'WORLD_TYPE_CONTROLLED_CONTEXT' in str(preview.json()['messages'])
            assert preview.json()['material']['world_type']['id'] == 'controlled'
    finally:
        registry.unregister('controlled', 1)
        service.configure('default', 'general', 1)


@pytest.mark.anyio
async def test_initialization_failure_rolls_back_resources_and_retry_is_idempotent(command_root, isolated_command_database):
    from pydantic import BaseModel, ConfigDict
    from sqlalchemy import func, select
    from app.db import SessionLocal, now_utc
    from app.models import Conversation, User
    from app.world_types import registry, service
    from app.world_types.contracts import WorldTypeDescriptor, Initialization
    from accounts import guest_username, TEST_PASSWORD
    fail = [True]
    class Configuration(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        label: str = 'rollback'
    async def initialize(session, ctx, config, resources):
        row = Conversation(type='group', title='TYPE_ATOMIC_INITIALIZATION', created_by=ctx.owner_id, created_at=now_utc())
        session.add(row); await session.flush()
        if fail[0]:
            raise ValueError('CONTROLLED_PRIVATE_INITIALIZER_FAILURE')
        return Initialization(resources={'conversation_id': row.id})
    registry.register(WorldTypeDescriptor(id='failing_fixture', version=1, name='失败恢复固件', configuration_model=Configuration, initializer=initialize))
    try:
        async with setup_group(command_root) as (client, headers, _, _, _):
            service.configure('controlled-world', 'failing_fixture', 1)
            guest = (await client.post('/api/auth/register', json={'username': guest_username('type'), 'nickname': 'Guest', 'password': TEST_PASSWORD})).json()
            gh = {'Authorization': f"Bearer {guest['access_token']}"}
            for path in ['/api/world-types', '/api/world-type']:
                assert (await client.get(path, headers=gh)).status_code == 403
            view = (await client.get('/api/world-type', headers=headers)).json()
            response = await client.post('/api/world-type/initialize', headers=headers, json={'expected_revision': view['revision']})
            assert response.status_code == 200 and response.json()['initialization']['status'] == 'failed'
            assert 'CONTROLLED_PRIVATE_INITIALIZER_FAILURE' not in response.text
            async with SessionLocal() as session:
                assert await session.scalar(select(func.count()).select_from(Conversation).where(Conversation.title == 'TYPE_ATOMIC_INITIALIZATION')) == 0
            fail[0] = False
            import asyncio
            expected = response.json()['revision']
            replies = await asyncio.gather(*(client.post('/api/world-type/initialize', headers=headers,
                json={'expected_revision': expected}) for _ in range(2)))
            assert sorted(r.status_code for r in replies) == [200, 409]
            async with SessionLocal() as session:
                assert await session.scalar(select(func.count()).select_from(Conversation).where(Conversation.title == 'TYPE_ATOMIC_INITIALIZATION')) == 1
    finally:
        registry.unregister('failing_fixture', 1)
        service.configure('default', 'general', 1)


@pytest.mark.anyio
async def test_type_sources_revalidated_before_next_real_model_call(command_root, isolated_command_database, monkeypatch):
    from app.world_types import registry, service
    from app.world_types.contracts import WorldTypeDescriptor, WorldMaterial
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.services import chat
    from test_context_execution import finished
    source_revision = [1]
    calls = []
    async def material(session, ctx, config):
        return [WorldMaterial(source_id='controlled-source', revision=source_revision[0], text='受控类型背景')]
    async def validate(session, ctx, receipt):
        return receipt['sources'][0]['revision'] == source_revision[0]
    class Model(ScriptedChatModel):
        async def _astream(self, messages, **kwargs):
            calls.append(True)
            source_revision[0] += 1
            async for chunk in super()._astream(messages, **kwargs):
                yield chunk
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Model(turns=[ScriptedTurn(tool_calls=[{
        'name': 'workspace_read', 'args': {'path': 'source.txt'}, 'id': 'read-one'}]), ScriptedTurn(text='不应继续')], delay=0))
    registry.register(WorldTypeDescriptor(id='source_fixture', version=1, name='资料版本固件', build_context=material, validate_sources=validate))
    try:
        async with setup_group(command_root) as (client, headers, cid, ids, _):
            service.configure('controlled-world', 'source_fixture', 1)
            await service.initialize()
            (command_root / 'source.txt').write_text('受控读取')
            await client.post(f'/api/conversations/{cid}/messages', headers=headers,
                json={'parts': [{'type': 'text', 'text': '读取后继续'}], 'mentions': [ids[0]]})
            execution = (await finished(cid, 1))[0]
            assert calls == [True] and execution.status == 'failed'
            assert execution.error_code == 'CONTEXT_SOURCE_CHANGED'
    finally:
        registry.unregister('source_fixture', 1)
        service.configure('default', 'general', 1)
