"""删除保留期与过期清理。

会话删除进回收站：只写 `deleted_at`，数据仍在，保留期内可以恢复；
超过保留期后才真正级联删除。清理只在服务启动时执行一次，不起常驻定时任务——
Roleplex 是随开随关的单机工具，启动清理已覆盖绝大多数场景；而 `asyncio.sleep`
走单调时钟，Windows 休眠期间不推进，定时任务会持续漂移。过期数据留在盘上
不影响任何功能，下次启动即清。

角色删除不走这里：角色采用墓碑，永不物理删除，否则历史消息会失去发送者。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DATA_DIR
from ..db import SessionLocal
from ..models import Attachment, Conversation, Message

logger = logging.getLogger("roleplex.retention")

# 已删除会话的保留天数；超过后由启动清理物理删除。
RETENTION_DAYS = 7


def retention_cutoff(now: datetime | None = None) -> datetime:
    """返回保留期分界时间：早于该时间被删除的会话应当清理。

    Args:
        now：当前时间，测试可注入以构造过期数据；默认取 UTC 现在。
    """
    return (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)


def _resolve_attachment_path(stored: str) -> Path:
    """把附件的存储路径解析为磁盘绝对路径。

    Args:
        stored：附件表中记录的路径，可能是相对于数据目录的相对路径。
    """
    path = Path(stored)
    return path if path.is_absolute() else DATA_DIR / path


async def purge_expired_conversations(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """物理删除超过保留期的已删除会话及其级联数据。

    消息、成员、事件日志、生成记录、队列、产物与工具审计都以 `ON DELETE CASCADE`
    指向会话，因此删除会话行即可带走它们；附件另有磁盘文件，必须在删除数据库行
    之前取出路径，否则记录消失后就找不到要删的文件了。

    Args:
        session：请求或启动期的数据库会话；调用方负责提交。
        now：当前时间，测试可注入。

    Returns:
        `{"conversations": 会话数, "files": 已删除的附件文件数}`，用于日志与测试断言。
    """
    cutoff = retention_cutoff(now)
    expired = (await session.scalars(
        select(Conversation.id).where(Conversation.deleted_at.is_not(None), Conversation.deleted_at < cutoff)
    )).all()
    if not expired:
        return {"conversations": 0, "files": 0}

    # 先取附件磁盘路径：数据库行被级联删除后就无从得知该删哪些文件。
    paths = (await session.scalars(
        select(Attachment.path)
        .join(Message, Message.id == Attachment.message_id)
        .where(Message.conversation_id.in_(expired))
    )).all()

    await session.execute(delete(Conversation).where(Conversation.id.in_(expired)))
    await session.commit()

    removed = 0
    for stored in paths:
        try:
            _resolve_attachment_path(stored).unlink(missing_ok=True)
            removed += 1
        except OSError:
            # 文件被占用或权限不足时留给下次启动清理，不能让清理失败影响启动。
            logger.warning("retention.attachment_unlink_failed", extra={"attachment_path": stored})
    return {"conversations": len(expired), "files": removed}


async def purge_expired_on_startup() -> dict[str, int]:
    """服务启动时清理一次过期会话，失败不阻断启动。

    Returns:
        本次清理的统计；失败时返回全零，具体错误已记录日志。
    """
    try:
        async with SessionLocal() as session:
            result = await purge_expired_conversations(session)
    except Exception:
        # 清理属于后台维护，任何失败都不应该让服务起不来。
        logger.exception("retention.purge_failed")
        return {"conversations": 0, "files": 0}
    if result["conversations"]:
        logger.info(
            "retention.purged",
            extra={"purged_conversations": result["conversations"], "purged_files": result["files"], "retention_days": RETENTION_DAYS},
        )
    return result
