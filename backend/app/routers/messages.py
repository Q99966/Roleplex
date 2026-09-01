from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..realtime.events import current_epoch
from ..models import Conversation, ConversationMember, Generation, Message, QueueJob, Role, User
from ..config.logging import current_request_id, set_log_context
from ..schemas import MessageCreate
from ..security.tokens import get_current_user
from ..realtime import store as event_store
from ..services import chat
from ..scheduling import conversation_scheduler

logger = logging.getLogger("roleplex.messages")
router = APIRouter(prefix="/api/conversations", tags=["messages"])


async def require_member(session: AsyncSession, conversation_id: int, user_id: int) -> ConversationMember:
    """要求请求者是会话成员；无权访问或已进回收站的会话一律按不存在处理。

    回收站中的会话对消息链路等同于不存在：不能读历史、不能发言、不能触发生成，
    否则被删除的会话仍会产生新消息和新事件。
    """
    member = await session.scalar(
        select(ConversationMember)
        .join(Conversation, Conversation.id == ConversationMember.conversation_id)
        .where(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.member_type == "user",
            ConversationMember.member_id == user_id,
            Conversation.deleted_at.is_(None),
        )
    )
    if not member:
        raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND")
    return member


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """返回会话历史及当前事件序号，供前端建立事件游标。"""
    await require_member(session, conversation_id, user.id)
    snapshot = await chat.build_snapshot(session, conversation_id)
    return {
        "items": snapshot["messages"],
        "event_seq": snapshot["event_seq"],
        "stream_epoch": current_epoch(),
        "active_generation_id": snapshot["active_generation_id"],
        "active_generation_ids": snapshot["active_generation_ids"],
    }


@router.post("/{conversation_id}/messages", status_code=202)
async def send_message(
    conversation_id: int,
    payload: MessageCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """持久化用户消息并排队一次生成；重复的客户端消息标识按幂等处理。"""
    await require_member(session, conversation_id, user.id)
    text = next((part.text or "" for part in payload.parts if part.type == "text"), "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="TEXT_PART_REQUIRED")

    if payload.client_message_id:
        existing = await session.scalar(select(Message).where(
            Message.conversation_id == conversation_id,
            Message.sender_id == user.id,
            Message.client_message_id == payload.client_message_id,
        ))
        if existing:
            generation_ids = list((await session.scalars(
                select(Generation.id)
                .where(
                    Generation.conversation_id == conversation_id,
                    Generation.run_id == existing.chain_id,
                )
                .order_by(Generation.id.asc())
            )).all()) if existing.chain_id else []
            return {
                "message": chat.message_payload(existing),
                "duplicate": True,
                "generation_id": generation_ids[0] if generation_ids else None,
                "generation_ids": generation_ids,
            }

    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND")
    target_roles = await _resolve_target_roles(session, conversation, payload.mentions)

    now = datetime.now(timezone.utc)
    run_id = chat.new_run_id()
    message = Message(
        conversation_id=conversation_id,
        sender_type="user",
        sender_id=user.id,
        client_message_id=payload.client_message_id,
        reply_to_id=payload.reply_to_id,
        mentions_json=[m for m in payload.mentions],
        parts_json=[part.model_dump() for part in payload.parts],
        status="done",
        revision=0,
        chain_id=run_id,
        created_at=now,
    )
    session.add(message)
    await session.flush()

    jobs: list[QueueJob] = []
    generations: list[Generation] = []
    for role in target_roles:
        generation = Generation(
            conversation_id=conversation_id,
            stream_epoch=current_epoch(),
            status="queued",
            run_id=run_id,
        )
        session.add(generation)
        await session.flush()
        execution_id = chat.new_run_id()
        job = QueueJob(
            conversation_id=conversation_id,
            generation_id=generation.id,
            status="queued",
            payload_json={
                "current_message_id": message.id,
                "target_role_id": role.id,
                "triggered_by_user_id": user.id,
                "execution_id": execution_id,
                "chain_id": run_id,
                "allow_dangerous": user.is_owner,
                "execution_kind": "single" if conversation.type == "single" else "group_role",
                "request_id": current_request_id(),
            },
            attempts=0,
            cancel_requested=False,
            created_at=now,
        )
        session.add(job)
        generations.append(generation)
        jobs.append(job)

    conversation.last_message_at = now

    created_event = await event_store.append_event(
        session,
        conversation_id,
        "message_created",
        {"message": chat.message_payload(message)},
        revision=message.revision,
    )
    await session.commit()
    await session.refresh(message)
    for generation, job in zip(generations, jobs, strict=True):
        await session.refresh(generation)
        await session.refresh(job)
    await event_store.publish_events(created_event)

    for job in jobs:
        logger.info(
            "generation.created",
            extra={
                "conversation_id": conversation_id,
                "generation_id": job.generation_id,
                "chain_id": run_id,
                "execution_id": job.payload_json["execution_id"],
                "role_id": job.payload_json["target_role_id"],
            },
        )
        await conversation_scheduler.enqueue(conversation_id, job.id)

    set_log_context(
        user_id=user.id,
        conversation_id=conversation_id,
        message_id=message.id,
        generation_id=generations[0].id if generations else None,
        chain_id=run_id,
    )
    generation_ids = [generation.id for generation in generations]
    logger.info(
        "message.queued",
        extra={
            "conversation_id": conversation_id,
            "message_id": message.id,
            "generation_id": generation_ids[0] if generation_ids else None,
            "generation_ids": generation_ids,
            "chain_id": run_id,
            "stream_epoch": created_event.stream_epoch,
            "event_seq": created_event.event_seq,
            "message_revision": created_event.revision,
        },
    )
    return {
        "message": chat.message_payload(message),
        "generation_id": generation_ids[0] if generation_ids else None,
        "generation_ids": generation_ids,
        "duplicate": False,
    }


@router.post("/{conversation_id}/stop", status_code=202)
async def stop_generation(
    conversation_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """停止当前会话正在运行的生成；没有运行任务时按幂等成功返回。"""
    await require_member(session, conversation_id, user.id)
    generation = await session.scalar(
        select(Generation)
        .where(Generation.conversation_id == conversation_id, Generation.status.in_(["queued", "running"]))
        .order_by(case((Generation.status == "running", 0), else_=1), Generation.id.desc())
    )
    if generation is None:
        return {"stopped": False, "generation_id": None, "generation_ids": []}
    generation_ids = await conversation_scheduler.stop_chain(conversation_id, generation.run_id or "")
    stopped = bool(generation_ids)
    logger.info(
        "generation.stop_requested",
        extra={
            "conversation_id": conversation_id,
            "generation_id": generation.id,
            "generation_ids": generation_ids,
            "was_running": stopped,
        },
    )
    return {"stopped": stopped, "generation_id": generation.id, "generation_ids": generation_ids}


async def _resolve_target_roles(
    session: AsyncSession,
    conversation: Conversation,
    mentions: list[int | str],
) -> list[Role]:
    """按单聊或群聊触发规则解析稳定目标角色顺序。

    Args:
        session：请求级数据库会话。
        conversation：已经过成员鉴权的目标会话。
        mentions：客户端提交的角色 ID 或 `all`。

    Returns:
        已去重并按实际执行顺序排列的可用角色。

    Raises:
        HTTPException：角色不可用或展开后超过 chain 上限。
    """
    rows = (await session.execute(
        select(Role, ConversationMember)
        .join(
            ConversationMember,
            (ConversationMember.member_id == Role.id) & (ConversationMember.member_type == "role"),
        )
        .where(
            ConversationMember.conversation_id == conversation.id,
            Role.created_by == conversation.created_by,
        )
        .order_by(ConversationMember.id.asc())
    )).all()
    member_roles = [role for role, _member in rows]
    available = {
        role.id: role
        for role in member_roles
        if role.active and role.deleted_at is None
    }
    if conversation.type == "single":
        if len(member_roles) != 1 or len(available) != 1:
            raise HTTPException(status_code=422, detail="CONVERSATION_HAS_NO_ROLE")
        return [next(iter(available.values()))]
    if not mentions:
        return []

    if "all" in mentions:
        if len(available) != len(member_roles):
            raise HTTPException(status_code=422, detail="ROLE_NOT_AVAILABLE")
        targets = member_roles
    else:
        targets = []
        seen: set[int] = set()
        for raw in mentions:
            if not isinstance(raw, int) or raw in seen:
                continue
            role = available.get(raw)
            if role is None:
                raise HTTPException(status_code=422, detail="ROLE_NOT_AVAILABLE")
            seen.add(raw)
            targets.append(role)
    if len(targets) > 20:
        raise HTTPException(status_code=422, detail="CHAIN_LIMIT_EXCEEDED")
    return targets
