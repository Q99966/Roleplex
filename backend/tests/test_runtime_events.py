"""运行状态与可重放事件必须原子保存，失败不能留下客户端永远看不到的状态。"""
import pytest
from test_workspace_commands import command_conversation, command_root, isolated_command_database


@pytest.mark.anyio
async def test_state_and_replay_event_commit_together(command_root, isolated_command_database, monkeypatch):
    """事件落库失败回滚状态；成功后只有一条对应版本的重放事件。

    Args:
        command_root：隔离目录。
        isolated_command_database：新迁移库。
        monkeypatch：在事件追加边界注入失败。
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import EventLog
    from app.realtime import store
    from app.runtime import registry
    from app.runtime.manager import manager
    async with command_conversation(command_root) as (_client, _headers, cid, rid, wid):
        row = await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
            execution_id='event-fixture', role_id=rid, tool_call_id='event', tool_name='workspace_start_service', kind='service')
        async def unavailable(*args, **kwargs):
            """Args:
                args：原事件上下文，不输出。
                kwargs：原安全摘要，不输出。
            """
            raise OSError('event fixture unavailable')
        with monkeypatch.context() as scoped:
            scoped.setattr(store, 'append_event', unavailable)
            with pytest.raises(OSError):
                await registry.change(row.id, state='starting')
        saved = await registry.get(row.id)
        assert saved.state == 'pending' and saved.revision == row.revision
        changed = await registry.change(row.id, state='starting')
        await manager.notify(changed)
        async with SessionLocal() as session:
            events = (await session.scalars(select(EventLog).where(EventLog.conversation_id == cid,
                EventLog.event_type == 'runtime_changed'))).all()
        bodies = [event.payload_json for event in events]
        assert [body['state'] for body in bodies] == ['pending', 'starting']
        assert [body['revision'] for body in bodies] == [0, 1]


def test_runtime_disconnect_replays_each_version_and_snapshot_refreshes(command_root):
    """真实 WebSocket 断线期间的状态版本连续回放，旧 epoch 后仍能读取最终登记。

    Args:
        command_root：本轮唯一目录与测试资源名称。
    """
    from starlette.testclient import TestClient
    from accounts import ensure_owner_sync
    from app.main import app
    from app.runtime import registry
    from test_ws_recovery import _authenticate, _read_until
    with TestClient(app) as client:
        token = ensure_owner_sync(client)['access_token']
        headers = {'Authorization': f'Bearer {token}'}
        config = client.post('/api/model-configs', headers=headers,
            json={'name': command_root.name, 'provider_type': 'openai_compatible', 'api_key': 'sk-placeholder'}).json()
        role = client.post('/api/roles', headers=headers, json={'name': command_root.name,
            'model_config_id': config['id'], 'model_name': 'fake-model', 'system_prompt': '运行状态恢复测试'}).json()
        workspace = client.post('/api/workspaces', headers=headers, json={'display_name': command_root.name,
            'root_path': str(command_root), 'acknowledge_existing_content': True}).json()
        cid = client.post('/api/conversations', headers=headers, json={'title': command_root.name,
            'type': 'single', 'role_ids': [role['id']], 'workspace_binding_id': workspace['id']}).json()['id']
        with client.websocket_connect('/api/ws') as ws:
            epoch = _authenticate(ws, token)
            ws.send_json({'type': 'subscribe', 'conversation_id': cid, 'after_event_seq': 0, 'stream_epoch': epoch})
            assert ws.receive_json()['type'] == 'subscribed'
            row = client.portal.call(lambda: registry.reserve(owner_id=1, conversation_id=cid, workspace_id=workspace['id'],
                execution_id=command_root.name, role_id=role['id'], tool_call_id='replay', tool_name='workspace_run_command', kind='command'))
            first = _read_until(ws, 'runtime_changed')[-1]
        client.portal.call(lambda: registry.change(row.id, state='starting'))
        client.portal.call(lambda: registry.finish(row.id, 'stopped'))
        with client.websocket_connect('/api/ws') as ws:
            _authenticate(ws, token)
            ws.send_json({'type': 'subscribe', 'conversation_id': cid, 'after_event_seq': first['event_seq'], 'stream_epoch': epoch})
            assert ws.receive_json()['type'] == 'subscribed'
            replay = [_read_until(ws, 'runtime_changed')[-1] for _ in range(2)]
            assert [event['event_seq'] for event in replay] == [first['event_seq'] + 1, first['event_seq'] + 2]
            assert [event['payload']['revision'] for event in replay] == [1, 2]
            assert all(set(event['payload']) == {'runtime_id', 'revision', 'state'} for event in replay)
        with client.websocket_connect('/api/ws') as ws:
            _authenticate(ws, token)
            ws.send_json({'type': 'subscribe', 'conversation_id': cid, 'after_event_seq': replay[-1]['event_seq'], 'stream_epoch': 'old-epoch'})
            _read_until(ws, 'snapshot')
            rows = client.get(f'/api/conversations/{cid}/processes', headers=headers).json()['items']
            assert rows[0]['state'] == 'stopped' and rows[0]['revision'] == 2
