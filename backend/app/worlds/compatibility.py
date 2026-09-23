"""世界数据库与当前软件迁移版本的只读兼容检查。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError


class WorldRequiresNewerRoleplex(RuntimeError):
    """世界由更新软件写入，当前版本必须拒绝打开。"""


class WorldTypeUnavailable(ValueError):
    """存档的类型或类型版本没有已安装实现；不能降格成普通世界。"""


def _script_directory() -> ScriptDirectory:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    return ScriptDirectory.from_config(config)


def assert_world_compatible(database_path: str | Path) -> None:
    """只读确认世界当前 revision 是本软件认识的版本。

    Args:
        database_path：待启动世界的 SQLite 数据库。

    Raises:
        WorldRequiresNewerRoleplex：数据库 revision 不在当前迁移图中。
    """
    database = Path(database_path)
    if not database.is_file() or database.stat().st_size == 0:
        return
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        if not exists:
            return
        revision = connection.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
    if not revision:
        return
    try:
        _script_directory().get_revision(revision[0])
    except CommandError as exc:
        raise WorldRequiresNewerRoleplex(
            "这个世界来自更新版本的 Roleplex，请升级软件后再打开；世界数据没有被修改。"
        ) from exc
