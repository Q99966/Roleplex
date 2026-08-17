from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..events import current_epoch
from ..models import Conversation, ConversationMember, Generation, Message, User
from ..schemas import MessageCreate
from ..security import get_current_user
from ..services import chat, event_store

logger = logging.getLogger("roleplex.messages")
router = APIRouter(prefix="/api/conversations", tags=["messages"])


async def require_member(session: AsyncSession, conversation_id: int, user_id: int) -> ConversationMember:
    """要求请求者是会话成员；无权访问的会话一律按不存在处理。"""
    member = await session.scalar(select(ConversationMember).where(
        ConversationMember.conversation_id == conversation_id,
        ConversationMember.member_type == "user",
        ConversationMember.member_id == user_id,
    ))
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
            return {"message": chat.message_payload(existing), "duplicate": True, "generation_id": None}

    role = await chat.resolve_reply_role(session, conversation_id)
    if role is None:
        raise HTTPException(status_code=422, detail="CONVERSATION_HAS_NO_ROLE")

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

    generation = Generation(
        conversation_id=conversation_id,
        stream_epoch=current_epoch(),
        status="queued",
        run_id=run_id,
    )
    session.add(generation)
    await session.flush()

    conversation = await session.get(Conversation, conversation_id)
    if conversation is not None:
        conversation.last_message_at = now
        conversation.revision += 1

    created_event = await event_store.append_event(
        session,
        conversation_id,
        "message_created",
        {"message": chat.message_payload(message)},
        revision=message.revision,
    )
    await session.commit()
    await session.refresh(message)
    await session.refresh(generation)
    await event_store.publish_events(created_event)

    chat.start_generation(generation.id, conversation_id, text)
    logger.info(
        "message.queued",
        extra={"conversation_id": conversation_id, "generation_id": generation.id, "chain_id": run_id},
    )
    return {"message": chat.message_payload(message), "generation_id": generation.id, "duplicate": False}


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
        .order_by(Generation.id.desc())
    )
    if generation is None:
        return {"stopped": False, "generation_id": None}
    generation.stop_requested_at = datetime.now(timezone.utc)
    await session.commit()
    stopped = await chat.request_stop(generation.id)
    logger.info(
        "generation.stop_requested",
        extra={"conversation_id": conversation_id, "generation_id": generation.id, "was_running": stopped},
    )
    return {"stopped": stopped, "generation_id": generation.id}
