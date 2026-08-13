from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

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
    """创建本地数据表、初始化单例状态，并恢复中断的工作。"""
    from . import models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await ensure_instance_settings()
    await recover_interrupted_messages()


async def ensure_instance_settings() -> None:
    """确保用于原子认领首个 Owner 的单例记录存在。"""
    from .models import InstanceSettings

    async with SessionLocal() as session:
        if await session.get(InstanceSettings, 1) is None:
            session.add(InstanceSettings(id=1, owner_user_id=None, created_at=now_utc()))
            await session.commit()


async def recover_interrupted_messages() -> None:
    """将进程重启遗留的未完成生成标记为 interrupted。"""
    from sqlalchemy import update
    from .models import Message

    async with SessionLocal() as session:
        await session.execute(
            update(Message)
            .where(Message.status.in_(["pending", "generating"]))
            .values(status="interrupted")
        )
        await session.commit()


async def close_db() -> None:
    """应用关闭时释放数据库连接池。"""
    await engine.dispose()
