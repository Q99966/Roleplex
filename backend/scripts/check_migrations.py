"""迁移可重放性检查：SQLite 实跑 + 与 ORM metadata 比对 + PostgreSQL 离线 SQL 渲染。

用法（在 backend 目录下，使用项目环境的解释器）：
    python scripts/check_migrations.py

检查项：
1. 在临时 SQLite 数据库上 `alembic upgrade head`，确认迁移链本身可执行；
2. 用 alembic 的 compare_metadata 比对迁移结果与当前 ORM metadata，
   任何差异都说明有 schema 变更没有写进迁移；
3. 以 PostgreSQL 方言渲染离线 SQL，确认迁移不含只在 SQLite 可用的写法。
   该步骤不连接数据库、也不加载 app.db，因此本机无需安装 PostgreSQL 驱动。
4. 核对 SQLite batch 重建后的外键仍指向正确表，且 `foreign_key_check` 无违规。
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
# 占位连接串：离线模式只按方言渲染 SQL，不会真正建立连接。
POSTGRES_URL = "postgresql://roleplex:placeholder@localhost:5432/roleplex"


def run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    """在独立子进程中执行 alembic，确保配置按传入的数据库地址重新加载。

    Args:
        database_url：本次执行使用的数据库地址，通过环境变量传入。
        args：追加到 `alembic` 之后的命令行参数。

    Returns:
        子进程执行结果，调用方负责检查返回码。
    """
    env = {**os.environ, "DATABASE_URL": database_url, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
        # 迁移日志包含中文，Windows 默认按 GBK 解码子进程输出会直接抛错。
        encoding="utf-8", errors="replace",
    )


def compare_with_metadata(sqlite_path: Path) -> list[object]:
    """返回迁移结果与 ORM metadata 之间的差异列表，空列表表示两者一致。"""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from app.db import Base
    from app import models  # noqa: F401  仅用于注册所有 ORM 表

    engine = create_engine(f"sqlite:///{sqlite_path.as_posix()}")
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            return list(compare_metadata(context, Base.metadata))
    finally:
        engine.dispose()


def check_sqlite_foreign_keys(sqlite_path: Path) -> list[str]:
    """核对被 0003 batch 重建影响的外键定义和现存数据。

    Args:
        sqlite_path：已经升级到 head 的 SQLite 数据库。

    Returns:
        发现的问题列表；空列表表示引用与数据均完整。
    """
    from sqlalchemy import create_engine

    expected = {
        "conversations": {("orchestrator_role_id", "roles")},
        "tool_calls": {("role_id", "roles")},
    }
    failures: list[str] = []
    engine = create_engine(f"sqlite:///{sqlite_path.as_posix()}")
    try:
        with engine.connect() as connection:
            # 新连接默认同迁移引擎一样为 OFF；这里检查的是迁移后的 schema，不改变前提。
            pragma = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            if pragma != 0:
                failures.append(f"迁移等价连接的 foreign_keys 应为 0，实际为 {pragma}")
            for table, required in expected.items():
                rows = connection.exec_driver_sql(f"PRAGMA foreign_key_list({table})").all()
                actual = {(row[3], row[2]) for row in rows}
                missing = required - actual
                if missing:
                    failures.append(f"{table} 缺少外键引用：{sorted(missing)}")
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
            if violations:
                failures.append(f"foreign_key_check 发现违规：{violations}")
    finally:
        engine.dispose()
    return failures


def render_postgres_sql() -> str:
    """按 PostgreSQL 方言离线渲染整条迁移链，返回渲染出的 SQL 文本。

    直接使用 alembic 的 EnvironmentContext，而不是 `alembic upgrade --sql`：
    后者会加载 alembic/env.py，从而按运行时地址创建异步引擎并要求安装
    PostgreSQL 异步驱动；本函数只需要方言本身。
    """
    from alembic.config import Config
    from alembic.runtime.environment import EnvironmentContext
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    script = ScriptDirectory.from_config(config)
    buffer = io.StringIO()

    with EnvironmentContext(
        config, script, fn=lambda rev, _ctx: script._upgrade_revs("head", rev),
        as_sql=True, starting_rev="base", destination_rev="head", output_buffer=buffer,
    ) as environment:
        environment.configure(
            url=POSTGRES_URL, literal_binds=True, dialect_opts={"paramstyle": "named"},
        )
        with environment.begin_transaction():
            environment.run_migrations()
    return buffer.getvalue()


def main() -> int:
    """逐项执行检查并返回进程退出码，0 表示迁移可在两种数据库上重放。"""
    sys.path.insert(0, str(BACKEND_DIR))
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite+aiosqlite:///{(Path(tmp) / 'migrate-check.db').as_posix()}"
        upgraded = run_alembic(url, "upgrade", "head")
        if upgraded.returncode != 0:
            print("[fail] SQLite upgrade head")
            print(upgraded.stdout + upgraded.stderr)
            return 1
        print("[ok] SQLite upgrade head")

        diffs = compare_with_metadata(Path(tmp) / "migrate-check.db")
        if diffs:
            failures.append("[fail] 迁移结果与 ORM metadata 不一致：")
            failures.extend(f"    {diff}" for diff in diffs)
        else:
            print("[ok] 迁移结果与 ORM metadata 一致")

        foreign_key_failures = check_sqlite_foreign_keys(Path(tmp) / "migrate-check.db")
        if foreign_key_failures:
            failures.append("[fail] SQLite 外键完整性：")
            failures.extend(f"    {failure}" for failure in foreign_key_failures)
        else:
            print("[ok] SQLite batch 重建后的外键完整")

        downgraded = run_alembic(url, "downgrade", "base")
        if downgraded.returncode != 0:
            failures.append("[fail] SQLite downgrade base：")
            failures.append(downgraded.stdout + downgraded.stderr)
        else:
            print("[ok] SQLite downgrade base")

    try:
        rendered = render_postgres_sql()
    except Exception as exc:  # 渲染期异常即代表迁移含非可移植写法
        failures.append(f"[fail] PostgreSQL 离线 SQL 渲染：{exc!r}")
    else:
        print(f"[ok] PostgreSQL 离线 SQL 渲染（{len(rendered.splitlines())} 行）")

    for line in failures:
        print(line)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
