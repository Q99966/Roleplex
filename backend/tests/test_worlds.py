"""A1 世界目录、备份、接管与切换协议测试。"""
from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from accounts import ensure_owner_async


def test_settings_derive_database_and_secret_paths_per_world(tmp_path: Path):
    """未显式给数据库时路径按世界推导；显式地址保持最高优先级。"""
    from app.config import Settings

    managed = Settings(_env_file=None, worlds_dir=str(tmp_path), world_name="alpha", database_url="")
    assert managed.world_managed is True
    assert managed.world_dir == tmp_path / "alpha"
    assert managed.database_url == f"sqlite+aiosqlite:///{(tmp_path / 'alpha' / 'roleplex.db').as_posix()}"
    assert managed.secret_dir == tmp_path / "alpha"
    assert managed.files_dir == tmp_path / "alpha" / "files"

    explicit = Settings(
        _env_file=None,
        worlds_dir=str(tmp_path),
        world_name="ignored-for-database",
        database_url="sqlite+aiosqlite:////tmp/explicit-roleplex.db",
    )
    assert explicit.world_managed is False
    assert explicit.database_url == "sqlite+aiosqlite:////tmp/explicit-roleplex.db"


def test_world_manager_creates_isolated_secrets_and_rejects_traversal(tmp_path: Path):
    """每个世界有独立密钥，世界名不能逃逸世界根目录。"""
    from app.worlds.manager import WorldManager

    manager = WorldManager(tmp_path)
    alpha = manager.create("alpha")
    beta = manager.create("测试世界")

    assert alpha.path == tmp_path / "alpha"
    assert (alpha.path / "roleplex.db").exists()
    assert (alpha.path / "files").is_dir()
    assert (alpha.path / ".jwt-secret").read_text(encoding="utf-8")
    assert (alpha.path / ".api-key-secret").read_text(encoding="utf-8")
    assert (alpha.path / ".jwt-secret").read_text() != (beta.path / ".jwt-secret").read_text()
    assert [world.name for world in manager.list_worlds()] == ["alpha", "测试世界"]

    with pytest.raises(ValueError):
        manager.create("../escape")
    with pytest.raises(ValueError):
        manager.create("bad/name")

    from app.config import Settings

    alpha_settings = Settings(
        _env_file=None, worlds_dir=str(tmp_path), world_name="alpha", database_url="", jwt_secret="shared-override"
    )
    beta_settings = Settings(
        _env_file=None, worlds_dir=str(tmp_path), world_name="测试世界", database_url="", jwt_secret="shared-override"
    )
    assert alpha_settings.resolved_jwt_secret() != beta_settings.resolved_jwt_secret()


def test_active_world_cannot_be_deleted(tmp_path: Path):
    """后端持有活动租约时拒绝删除；正常释放后才允许删除。"""
    from app.worlds.manager import WorldActiveError, WorldManager

    manager = WorldManager(tmp_path)
    manager.create("active")
    manager.acquire("active")
    with pytest.raises(WorldActiveError):
        manager.delete("active")
    manager.release("active")
    manager.delete("active")
    assert not (tmp_path / "active").exists()


def test_ensure_recovers_only_safe_incomplete_world_directory(tmp_path: Path):
    """启动可补齐中断创建的安全半成品，但不接管含未知文件的目录。"""
    from app.worlds.manager import WorldManager

    manager = WorldManager(tmp_path)
    partial = tmp_path / "partial"
    (partial / "files").mkdir(parents=True)
    recovered = manager.ensure("partial")
    assert recovered.database_path.exists()
    assert (recovered.path / "world.json").exists()

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    (unsafe / "unknown.txt").write_text("do not adopt", encoding="utf-8")
    with pytest.raises(ValueError, match="未知文件"):
        manager.ensure("unsafe")


def test_world_backup_is_consistent_and_contains_keys_and_files(tmp_path: Path):
    """备份使用 SQLite 快照，并携带世界密钥、元数据和附件目录。"""
    from app.worlds.manager import WorldManager

    manager = WorldManager(tmp_path / "worlds")
    world = manager.create("backup-source")
    with sqlite3.connect(world.database_path) as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES ('kept')")
    (world.files_path / "note.txt").write_text("附件", encoding="utf-8")

    archive = manager.backup("backup-source", tmp_path / "backups")
    assert archive.suffix == ".zip"
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert {
            "backup-source/world.json",
            "backup-source/roleplex.db",
            "backup-source/.jwt-secret",
            "backup-source/.api-key-secret",
            "backup-source/files/note.txt",
        } <= names
        bundle.extract("backup-source/roleplex.db", tmp_path / "restored")
    with sqlite3.connect(tmp_path / "restored" / "backup-source" / "roleplex.db") as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == ("kept",)


def test_adopt_copies_legacy_database_secrets_and_files_without_deleting_source(tmp_path: Path):
    """接管旧开发目录使用一致性复制，成功后旧数据仍可回退。"""
    from app.worlds.manager import WorldManager

    legacy = tmp_path / "data"
    legacy.mkdir()
    with sqlite3.connect(legacy / "roleplex.db") as connection:
        connection.execute("CREATE TABLE sample (value INTEGER)")
        connection.execute("INSERT INTO sample VALUES (7)")
    (legacy / ".jwt-secret").write_text("legacy-jwt", encoding="utf-8")
    (legacy / ".api-key-secret").write_text("legacy-key", encoding="utf-8")
    (legacy / "files").mkdir()
    (legacy / "files" / "kept.txt").write_text("kept", encoding="utf-8")

    manager = WorldManager(tmp_path / "worlds")
    adopted = manager.adopt_legacy("default", legacy)

    assert (legacy / "roleplex.db").exists(), "接管不应破坏可回退的旧数据"
    assert (adopted.path / ".jwt-secret").read_text() == "legacy-jwt"
    assert (adopted.files_path / "kept.txt").read_text() == "kept"
    with sqlite3.connect(adopted.database_path) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == (7,)


def test_newer_world_revision_is_blocked_with_readable_error(tmp_path: Path):
    """旧软件打开新世界时只读检查并给出可读错误，不执行 stamp 或降级。"""
    from app.worlds.compatibility import WorldRequiresNewerRoleplex, assert_world_compatible

    database = tmp_path / "future.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('9999_future_roleplex')")

    with pytest.raises(WorldRequiresNewerRoleplex, match="来自更新版本"):
        assert_world_compatible(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("9999_future_roleplex",)


@pytest.mark.anyio
async def test_health_exposes_world_and_switch_requires_wrapper():
    """兼容数据库模式公开世界名，但绝不能让无包装器服务自行退出。"""
    from app.main import app

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            health = await client.get("/api/health")
            assert health.status_code == 200
            assert health.json()["world_name"] == "default"
            assert health.json()["world_managed"] is False

            owner = await ensure_owner_async(client)
            headers = {"Authorization": f"Bearer {owner['access_token']}"}
            worlds = await client.get("/api/worlds", headers=headers)
            assert worlds.status_code == 200
            assert worlds.json()["switching_supported"] is False

            rejected = await client.post("/api/worlds/switch", headers=headers, json={"name": "another"})
            assert rejected.status_code == 409
            assert rejected.json()["error"]["code"] == "WORLD_SWITCH_REQUIRES_WRAPPER"


@pytest.mark.anyio
async def test_managed_switch_writes_target_without_reusing_current_token(tmp_path: Path, monkeypatch):
    """包装器模式只写受控目标；重启后不同 JWT 密钥会让旧 Token 自然失效。"""
    from app.config import settings
    from app.main import app
    from app.routers import worlds as worlds_router
    from app.worlds import WorldManager

    manager = WorldManager(tmp_path / "worlds")
    manager.create("alpha")
    manager.create("beta")
    control = tmp_path / "control" / "target"
    monkeypatch.setattr(worlds_router, "manager", manager)
    monkeypatch.setattr(worlds_router, "schedule_shutdown", lambda: None)
    monkeypatch.setattr(settings, "world_name", "alpha")
    monkeypatch.setattr(settings, "worlds_dir", str(tmp_path / "worlds"))
    monkeypatch.setattr(settings, "world_control_file", str(control))
    monkeypatch.setattr(settings, "_world_managed", True)

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            owner = await ensure_owner_async(client)
            headers = {"Authorization": f"Bearer {owner['access_token']}"}
            switched = await client.post("/api/worlds/switch", headers=headers, json={"name": "beta"})

    assert switched.status_code == 202
    assert switched.json() == {"target": "beta", "restarting": True}
    assert control.read_text(encoding="utf-8") == "beta"
