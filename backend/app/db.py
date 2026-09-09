from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

logger = logging.getLogger("roleplex.db")


class Base(DeclarativeBase):
    """所有 SQLAlchemy 模型共用的基类。"""


engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args={"timeout": 30} if settings.database_url.startswith("sqlite") else {},
)


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def now_utc() -> datetime:
    """返回用于持久化记录的带时区 UTC 时间。"""
    return datetime.now(timezone.utc)


async def get_session() -> AsyncIterator[AsyncSession]:
    """提供一个请求范围内的异步数据库会话，并在请求结束后关闭。"""
    async with SessionLocal() as session:
        yield session


async def with_locked_retry(operation, attempts: int = 4):
    """SQLite 报告临时锁定时，重试短事务写操作。

    Args:
        operation：自行管理事务生命周期的异步可调用对象。
        attempts：最大尝试次数，包含第一次调用。

    Returns:
        `operation` 返回的值。

    Raises:
        Exception：异常不是锁定错误，或重试次数耗尽时抛出原始异常。
    """
    for attempt in range(attempts):
        try:
            return await operation()
        except Exception as exc:
            if "locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            await asyncio.sleep(0.05 * (2**attempt))


async def init_db() -> None:
    """执行数据库迁移、初始化单例状态，并恢复中断的工作。"""
    from . import models  # noqa: F401

    await run_migrations()
    await ensure_instance_settings()
    await recover_interrupted_messages()


def _alembic_config():
    """构造只指向本项目迁移目录的 Alembic 配置。

    不加载 alembic.ini：该文件的日志配置会覆盖应用自身的日志设置，
    且其中的数据库地址只适用于命令行使用；运行时地址由 alembic/env.py
    从应用配置读取。
    """
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    return config


async def run_migrations() -> None:
    """把数据库升级到最新迁移版本。

    schema 只由迁移创建，不再在启动时按 ORM metadata 建表：否则新库不会记录
    迁移版本，已有库也拿不到新增字段，schema 会在开发过程中静默漂移。
    迁移在工作线程中执行，因为 alembic 的在线迁移会自行启动事件循环。
    """
    from alembic import command
    from alembic.util.exc import CommandError
    from .worlds.compatibility import WorldRequiresNewerRoleplex

    try:
        await asyncio.to_thread(command.upgrade, _alembic_config(), "head")
    except CommandError as exc:
        if "Can't locate revision identified by" in str(exc):
            raise WorldRequiresNewerRoleplex(
                "这个世界来自更新版本的 Roleplex，请升级软件后再打开；世界数据没有被修改。"
            ) from None
        raise


async def ensure_instance_settings() -> None:
    """确保用于原子认领首个 Owner 的单例记录存在。"""
    from .models import InstanceSettings

    async with SessionLocal() as session:
        if await session.get(InstanceSettings, 1) is None:
            session.add(InstanceSettings(id=1, owner_user_id=None, created_at=now_utc()))
            await session.commit()


async def recover_interrupted_messages() -> None:
    """恢复未完成消息；遗留命令卡只标记执行中断，不猜测进程退出结果。"""
    from sqlalchemy import select
    from .models import Message

    async with SessionLocal() as session:
        messages = (await session.scalars(select(Message).where(
            Message.status.in_(['pending', 'generating']),
        ))).all()
        for message in messages:
            message.status = 'interrupted'
            parts = [dict(part) for part in message.parts_json]
            changed = False
            for part in parts:
                if part.get('type') == 'tool_call' and part.get('tool_name') == 'workspace_run_command' and part.get('status') == 'running':
                    part.update(status='failed', error_code='EXECUTION_INTERRUPTED', exit_code=None)
                    part.pop('command_status', None)
                    changed = True
            if changed:
                message.parts_json = parts
                message.revision += 1
        await session.commit()


async def close_db() -> None:
    """应用关闭时释放数据库连接池。"""
    await engine.dispose()
