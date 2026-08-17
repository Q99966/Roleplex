from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Conversation, ConversationMember, Generation, Message, Role
from ..agent.fake_provider import stream_fake_reply
from . import event_store

logger = logging.getLogger("roleplex.chat")

# 运行中的生成任务；停止生成通过取消对应任务实现。
_running: dict[int, asyncio.Task[None]] = {}

# 流式内容先保存在内存，按节流间隔落库，避免逐片段写事务。
_PERSIST_INTERVAL_SECONDS = 1.0


def message_payload(message: Message) -> dict:
    """把消息 ORM 记录转换为公开事件和 REST 响应共用的结构。"""
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sender_type": message.sender_type,
        "sender_id": message.sender_id,
        "parts_json": message.parts_json,
        "status": message.status,
        "revision": message.revision,
        "chain_id": message.chain_id,
        "created_at": message.created_at.isoformat(),
    }


async def resolve_reply_role(session, conversation_id: int) -> Role | None:
    """返回该会话中负责回复的角色；当前单聊只取唯一角色成员。"""
    role_id = await session.scalar(
        select(ConversationMember.member_id)
        .where(ConversationMember.conversation_id == conversation_id, ConversationMember.member_type == "role")
        .order_by(ConversationMember.id.asc())
    )
    if role_id is None:
        return None
    return await session.get(Role, role_id)


def start_generation(generation_id: int, conversation_id: int, prompt: str) -> None:
    """为一次生成登记后台任务，使停止生成可以取消它。"""
    task = asyncio.create_task(_run_generation(generation_id, conversation_id, prompt))
    _running[generation_id] = task
    task.add_done_callback(lambda _t: _running.pop(generation_id, None))


async def request_stop(generation_id: int) -> bool:
    """请求停止一次生成；返回是否存在正在运行的任务。"""
    task = _running.get(generation_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


async def _finalize(generation_id: int, status: str, text: str, error_code: str | None = None) -> None:
    """把生成的终态和已缓冲内容写入数据库并广播事件。"""
    async with SessionLocal() as session:
        generation = await session.get(Generation, generation_id)
        if generation is None or generation.status in {"completed", "stopped", "failed"}:
            return
        message = await session.get(Message, generation.assistant_message_id) if generation.assistant_message_id else None
        generation.status = status
        generation.error_code = error_code
        generation.ended_at = datetime.now(timezone.utc)
        events_to_publish = []
        if message is not None:
            message.parts_json = [{"type": "text", "text": text}]
            message.status = {"completed": "done", "stopped": "stopped", "failed": "error"}[status]
            message.revision += 1
            events_to_publish.append(
                await event_store.append_event(
                    session,
                    generation.conversation_id,
                    "message_done",
                    {"message": message_payload(message), "error_code": error_code},
                    revision=message.revision,
                    generation_id=generation.id,
                )
            )
        await session.commit()
    await event_store.publish_events(*events_to_publish)


async def _run_generation(generation_id: int, conversation_id: int, prompt: str) -> None:
    """执行一次确定性 fake 生成，并按事件协议广播增量与终态。

    Args:
        generation_id：生成记录标识，同时用于停止生成。
        conversation_id：所属会话。
        prompt：触发本次生成的用户文本。
    """
    accumulated = ""
    delta_seq = 0
    try:
        async with SessionLocal() as session:
            generation = await session.get(Generation, generation_id)
            if generation is None:
                return
            role = await resolve_reply_role(session, conversation_id)
            assistant = Message(
                conversation_id=conversation_id,
                sender_type="role",
                sender_id=role.id if role else None,
                parts_json=[{"type": "text", "text": ""}],
                status="generating",
                revision=0,
                chain_id=generation.run_id,
                created_at=datetime.now(timezone.utc),
            )
            session.add(assistant)
            await session.flush()
            generation.assistant_message_id = assistant.id
            generation.status = "running"
            generation.started_at = datetime.now(timezone.utc)
            created_event = await event_store.append_event(
                session,
                conversation_id,
                "message_created",
                {"message": message_payload(assistant)},
                generation_id=generation.id,
            )
            await session.commit()
            assistant_id = assistant.id
        await event_store.publish_events(created_event)
        logger.info(
            "generation.started",
            extra={"conversation_id": conversation_id, "generation_id": generation_id, "message_id": assistant_id},
        )

        last_persist = asyncio.get_running_loop().time()
        async for chunk in stream_fake_reply(prompt):
            accumulated += chunk
            delta_seq += 1
            now = asyncio.get_running_loop().time()
            should_persist = now - last_persist >= _PERSIST_INTERVAL_SECONDS
            async with SessionLocal() as session:
                message = await session.get(Message, assistant_id)
                if message is None:
                    return
                if should_persist:
                    message.parts_json = [{"type": "text", "text": accumulated}]
                    last_persist = now
                message.revision += 1
                delta_event = await event_store.append_event(
                    session,
                    conversation_id,
                    "message_delta",
                    {"message_id": assistant_id, "text": chunk},
                    revision=message.revision,
                    delta_seq=delta_seq,
                    generation_id=generation_id,
                )
                await session.commit()
            await event_store.publish_events(delta_event)

        await _finalize(generation_id, "completed", accumulated)
        logger.info(
            "generation.completed",
            extra={"conversation_id": conversation_id, "generation_id": generation_id, "delta_count": delta_seq},
        )
    except asyncio.CancelledError:
        # 用户主动停止属于预期结果，按 stopped 落库而不是未处理异常。
        await _finalize(generation_id, "stopped", accumulated)
        logger.info(
            "generation.stopped",
            extra={"conversation_id": conversation_id, "generation_id": generation_id, "delta_count": delta_seq},
        )
        raise
    except Exception:
        await _finalize(generation_id, "failed", accumulated, error_code="PROVIDER_ERROR")
        logger.exception(
            "generation.failed",
            extra={"conversation_id": conversation_id, "generation_id": generation_id},
        )


async def build_snapshot(session, conversation_id: int) -> dict:
    """构建断线恢复使用的完整会话快照。"""
    conversation = await session.get(Conversation, conversation_id)
    messages = (await session.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id.asc())
    )).all()
    active = await session.scalar(
        select(Generation)
        .where(Generation.conversation_id == conversation_id, Generation.status.in_(["queued", "running"]))
        .order_by(Generation.id.desc())
    )
    return {
        "conversation_id": conversation_id,
        "event_seq": conversation.event_seq if conversation else 0,
        "messages": [message_payload(message) for message in messages],
        "active_generation_id": active.id if active else None,
    }


def new_run_id() -> str:
    """生成一次 Agent 执行的链路标识。"""
    return uuid.uuid4().hex
