"""World 操作必须协调运行入口，不能覆盖在途切换或备份运行中的登记。"""
import io
import zipfile

import pytest
from test_workspace_commands import command_conversation, command_root, isolated_command_database


def test_cli_can_delete_configured_but_inactive_world(tmp_path, monkeypatch):
    """配置中的默认 World 名不等于实际运行租约，正常关闭后应允许显式删除。

    Args:
        tmp_path：本轮空世界目录。
        monkeypatch：只改变本测试的显示世界名。
    """
    from app.config import settings
    from app.worlds import WorldManager
    from scripts.manage_worlds import main
    root = tmp_path / 'worlds'
    world = WorldManager(root).create('offline')
    monkeypatch.setattr(settings, 'world_name', 'offline')
    monkeypatch.setattr(settings, '_world_managed', True)
    assert main(['--worlds-dir', str(root), 'delete', 'offline', '--yes']) == 0
    assert not world.path.exists()


@pytest.mark.anyio
async def test_interrupted_export_removes_sensitive_temporary_file(tmp_path):
    """下载通道异常也清理临时导出，不能依赖正常响应后的后台回调。

    Args:
        tmp_path：测试文件的独占父目录。
    """
    import tempfile
    from pathlib import Path
    from app.routers.worlds import WorldExportResponse
    temporary = tempfile.TemporaryDirectory(dir=tmp_path)
    archive = Path(temporary.name) / 'fixture.zip'
    archive.write_bytes(b'non-sensitive-test-placeholder')
    response = WorldExportResponse(temporary, archive)
    async def receive():
        """提供 ASGI 断线消息。"""
        return {'type': 'http.disconnect'}
    async def send(message):
        """Args:
            message：不读取或记录任何响应字节。
        """
        raise ConnectionError('download fixture disconnected')
    with pytest.raises(ConnectionError):
        await response({'type': 'http', 'method': 'GET', 'headers': []}, receive, send)
    assert not archive.parent.exists()


@pytest.mark.parametrize('operation', ['backup', 'delete'])
def test_offline_operation_holds_lease_against_concurrent_start(tmp_path, monkeypatch, operation):
    """离线备份/删除在整个文件操作期持有租约，不能与另一个后端启动交错。

    Args:
        tmp_path：独占世界目录。
        monkeypatch：在文件操作中尝试竞争租约。
        operation：受测离线操作。
    """
    import shutil
    from pathlib import Path
    from app.worlds import WorldManager, WorldActiveError
    manager = WorldManager(tmp_path / 'worlds')
    world = manager.create('offline')
    competitor = WorldManager(tmp_path / 'worlds')
    visited = []
    def competing():
        """尝试从另一管理器获得相同物理 World 的活动租约。"""
        with pytest.raises(WorldActiveError):
            competitor.acquire('offline')
        visited.append(True)
    if operation == 'backup':
        original = manager._archive_world
        def archive(name, destination):
            """Args:
                name：目标世界。
                destination：测试导出目录。
            """
            competing()
            return original(name, destination)
        monkeypatch.setattr(manager, '_archive_world', archive)
        manager.backup('offline', tmp_path / 'backup')
        assert not manager.is_active('offline')
    else:
        original = shutil.rmtree
        def remove(path, *args, **kwargs):
            """Args:
                path：删除目标；只观察本轮世界。
                args：原文件 API 参数。
                kwargs：原文件 API 参数。
            """
            if Path(path).name.startswith('.deleted-'):
                # 已从原名称原子移出；竞争者不能再打开被删世界，之后同名新建属于新资源。
                with pytest.raises(FileNotFoundError):
                    competitor.acquire('offline')
                visited.append(True)
            return original(path, *args, **kwargs)
        monkeypatch.setattr(shutil, 'rmtree', remove)
        manager.delete('offline')
        assert not world.path.exists()
    assert visited == [True]


@pytest.mark.anyio
async def test_managed_backup_stops_runtime_and_reopens_admission(command_root, isolated_command_database, monkeypatch, tmp_path):
    """当前 World 备份先回收实例；导出完成后允许新申请，不自动重启旧实例。

    Args:
        command_root：受控外部目录。
        isolated_command_database：隔离库。
        monkeypatch：把测试路由绑定到其真实数据库所在 World。
        tmp_path：本用例世界目录。
    """
    from pathlib import Path
    from app.config import settings
    from app.routers import worlds
    from app.runtime import registry
    from app.worlds import WorldManager
    from app.db import engine
    # 生命周期使用隔离数据库；世界目录快照复制该库，不伪造备份字节。
    manager = WorldManager(tmp_path / 'worlds')
    world = manager.create('snapshot')
    monkeypatch.setattr(worlds, 'manager', manager)
    monkeypatch.setattr(settings, 'world_name', 'snapshot')
    monkeypatch.setattr(settings, 'worlds_dir', str(tmp_path / 'worlds'))
    monkeypatch.setattr(settings, '_world_managed', True)
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        # 仅本用例把世界快照入口指向正在使用的测试 DB，仍执行 SQLite backup API。
        original = manager._snapshot_database
        monkeypatch.setattr(manager, '_snapshot_database', lambda source, destination: original(Path(engine.url.database), destination))
        try:
            row = await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
                execution_id='backup-fixture', role_id=rid, tool_call_id='pending', tool_name='workspace_start_service', kind='service')
            assert (await client.post('/api/worlds/backup', headers=headers, json={'confirm_cleanup': False})).status_code == 409
            from accounts import TEST_PASSWORD, guest_username
            guest = (await client.post('/api/auth/register', json={'username': guest_username('backup'),
                'password': TEST_PASSWORD, 'nickname': 'Guest'})).json()
            assert (await client.post('/api/worlds/backup', headers={'Authorization': f"Bearer {guest['access_token']}"},
                json={'confirm_cleanup': True})).status_code == 403
            assert (await registry.get(row.id)).state == 'pending'
            def failed_archive(*args):
                """Args:
                    args：本轮导出参数，不输出路径或内容。
                """
                raise OSError('archive fixture unavailable')
            with monkeypatch.context() as scoped:
                scoped.setattr(manager, '_archive_world', failed_archive)
                failed = await client.post('/api/worlds/backup', headers=headers, json={'confirm_cleanup': True})
                assert failed.status_code == 503 and failed.json()['error']['code'] == 'WORLD_OPERATION_FAILED'
            response = await client.post('/api/worlds/backup', headers=headers, json={'confirm_cleanup': True})
            assert response.status_code == 200
            assert response.headers['cache-control'] == 'no-store'
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                assert 'snapshot/roleplex.db' in archive.namelist()
            assert (await registry.get(row.id)).state in registry.TERMINAL
            assert (await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
                execution_id='backup-after', role_id=rid, tool_call_id='next', tool_name='workspace_run_command', kind='command')).id
        finally:
            manager.release('snapshot')


@pytest.mark.anyio
async def test_switch_failure_is_retryable_and_pending_target_cannot_be_overwritten(command_root, isolated_command_database, monkeypatch, tmp_path):
    """写切换目标失败保留失败门槛，可显式重试；成功后不能被另一目标覆盖。

    Args:
        command_root：受控目录。
        isolated_command_database：新库。
        monkeypatch：模拟控制文件写失败，禁用真实后端退出。
        tmp_path：隔离目标世界。
    """
    from app.config import settings
    from app.routers import worlds
    from app.worlds import WorldManager
    manager = WorldManager(tmp_path / 'worlds')
    manager.create('beta')
    manager.create('gamma')
    monkeypatch.setattr(worlds, 'manager', manager)
    monkeypatch.setattr(worlds, 'schedule_shutdown', lambda: None)
    monkeypatch.setattr(settings, 'world_name', 'alpha')
    monkeypatch.setattr(settings, 'worlds_dir', str(tmp_path / 'worlds'))
    monkeypatch.setattr(settings, '_world_managed', True)
    monkeypatch.setattr(settings, 'world_control_file', str(tmp_path / 'target'))
    async with command_conversation(command_root) as (client, headers, _cid, _rid, _wid):
        def unavailable(*args):
            """Args:
                args：冻结目标与控制文件，不记录路径。
            """
            raise OSError('controlled write failure')
        with monkeypatch.context() as scoped:
            scoped.setattr(manager, 'request_switch', unavailable)
            assert (await client.post('/api/worlds/switch', headers=headers, json={'name': 'beta'})).status_code == 503
        assert (await client.post('/api/worlds/switch', headers=headers, json={'name': 'beta'})).status_code == 202
        assert (await client.post('/api/worlds/switch', headers=headers, json={'name': 'gamma'})).status_code == 409
        assert (tmp_path / 'target').read_text() == 'beta'
