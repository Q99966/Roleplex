from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import now_utc
from ..models import Conversation, EventLog
from . import events
from .events import DomainEvent


async def append_event(
    session: AsyncSession,
    conversation_id: int,
    event_type: str,
    payload: dict[str, Any],
    *,
    revision: int = 0,
    delta_seq: int | None = None,
    generation_id: int | None = None,
) -> DomainEvent:
    """在当前事务内分配会话事件序号并写入事件日志。

    调用方必须在提交事务之后再调用 `publish_events`；先广播后落库会让客户端
    看到无法通过事件日志恢复的事件。

    Args:
        session：调用方持有的数据库会话，本函数不提交事务。
        conversation_id：事件所属会话。
        event_type：稳定的领域事件名称。
        payload：事件负载，必须只包含可公开的字段。
        revision：事件对应的会话或消息版本。
        delta_seq：流式增量序号，非增量事件为 None。
        generation_id：关联的生成记录，非生成事件为 None。

    Returns:
        已分配序号但尚未广播的领域事件。
    """
    result = await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(event_seq=Conversation.event_seq + 1)
        .returning(Conversation.event_seq)
    )
    event_seq = result.scalar_one()
    epoch = events.current_epoch()
    session.add(
        EventLog(
            conversation_id=conversation_id,
            event_seq=event_seq,
            event_type=event_type,
            stream_epoch=epoch,
            generation_id=generation_id,
            revision=revision,
            delta_seq=delta_seq,
            payload_json=payload,
            created_at=now_utc(),
        )
    )
    return DomainEvent(
        stream_epoch=epoch,
        event_seq=event_seq,
        conversation_id=conversation_id,
        type=event_type,
        payload=payload,
        revision=revision,
        delta_seq=delta_seq,
        generation_id=generation_id,
    )


async def publish_events(*pending: DomainEvent) -> None:
    """事务提交后把事件广播给在线订阅者。"""
    if events.hub is None:
        return
    for event in pending:
        await events.hub.publish(event)


async def read_backlog(session: AsyncSession, conversation_id: int, after_event_seq: int, limit: int = 500) -> list[DomainEvent]:
    """读取断线期间的持久化事件，用于重连回放。

    Args:
        session：数据库会话。
        conversation_id：目标会话。
        after_event_seq：客户端已经应用的最后一个事件序号。
        limit：单次回放的最大事件数，超过时调用方应改用快照。
    """
    rows = (await session.scalars(
        select(EventLog)
        .where(EventLog.conversation_id == conversation_id, EventLog.event_seq > after_event_seq)
        .order_by(EventLog.event_seq.asc())
        .limit(limit)
    )).all()
    return [
        DomainEvent(
            stream_epoch=row.stream_epoch or events.current_epoch(),
            event_seq=row.event_seq,
            conversation_id=row.conversation_id,
            type=row.event_type,
            payload=row.payload_json or {},
            revision=row.revision,
            delta_seq=row.delta_seq,
            generation_id=row.generation_id,
        )
        for row in rows
    ]


async def latest_event_seq(session: AsyncSession, conversation_id: int) -> int:
    """返回会话当前已分配的最大事件序号。"""
    conversation = await session.get(Conversation, conversation_id)
    return conversation.event_seq if conversation else 0
