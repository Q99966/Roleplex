from __future__ import annotations

import asyncio
import logging
import uuid

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from . import events as events_module
from .config import settings
from .db import SessionLocal
from .events import current_epoch
from .models import Conversation, ConversationMember, User
from .logging_config import log_context, set_log_context
from .security import token_requires_password_reset
from .services import chat, event_store

logger = logging.getLogger("roleplex.ws")
router = APIRouter()

# 首帧认证必须在有限时间内完成，避免未认证连接长期占用资源。
_AUTH_TIMEOUT_SECONDS = 10.0

# backlog 超过该数量时改为下发完整快照，避免重连时回放过长历史。
_BACKLOG_LIMIT = 300


async def _authenticate(token: str) -> User | None:
    """校验首帧 Token 并返回用户；Token 无效、已撤销或待改密时返回 None。

    待改密的 Token 在这里一并拒绝，与 REST 侧的默认拒绝保持一致：
    否则弱口令账号虽然进不了 REST 接口，却仍能订阅会话事件流。
    """
    try:
        payload = jwt.decode(token, settings.resolved_jwt_secret(), algorithms=[settings.jwt_algorithm])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
    if token_requires_password_reset(payload):
        return None
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None or user.token_version != payload.get("ver"):
            return None
        return user


async def _is_member(conversation_id: int, user_id: int) -> bool:
    """检查用户是否为该会话成员，用于订阅前的资源级授权。

    回收站中的会话按不存在处理，与 REST 侧保持一致：已删除的会话不应还能订阅事件流。
    """
    async with SessionLocal() as session:
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
        return member is not None


async def _close_policy_violation(websocket: WebSocket) -> None:
    """以策略违规关闭连接；对端已经断开时静默返回。

    连接断开后 ASGI 层会拒绝一切后续帧（包括关闭帧）并抛 RuntimeError。
    客户端在认证前刷新页面或切换会话都会走到这里，属于预期竞态而不是故障。
    """
    try:
        await websocket.close(code=1008)
    except RuntimeError:
        return


@router.websocket("/api/ws")
async def conversation_stream(websocket: WebSocket) -> None:
    """会话事件流：首帧认证后按事件序号恢复，再进入实时推送。

    协议要求 Token 只出现在首帧消息体中，不允许放在查询参数或日志里。
    """
    connection_id = uuid.uuid4().hex
    peer = getattr(websocket, "client", None)
    client = f"{peer.host}:{peer.port}" if peer else None
    with log_context(request_id=f"ws-{connection_id}", ws_connection_id=connection_id):
        await _serve_conversation_stream(websocket, client)


async def _serve_conversation_stream(websocket: WebSocket, client: str | None) -> None:
    """在已建立日志上下文中处理认证、订阅、恢复与连接回收。"""
    await websocket.accept()
    logger.info("ws.accepted", extra={"client": client})
    try:
        first_frame = await asyncio.wait_for(websocket.receive_json(), timeout=_AUTH_TIMEOUT_SECONDS)
    except WebSocketDisconnect:
        # 对端在认证前主动断开：连接已经不存在，不需要也不能再发关闭帧。
        logger.info("ws.closed_before_auth", extra={"client": client})
        return
    except (asyncio.TimeoutError, ValueError):
        logger.warning("ws.auth_rejected", extra={"reason": "timeout_or_invalid_json", "client": client})
        await _close_policy_violation(websocket)
        return

    if first_frame.get("type") != "auth" or not isinstance(first_frame.get("token"), str):
        logger.warning("ws.auth_rejected", extra={"reason": "invalid_auth_frame", "client": client})
        await _close_policy_violation(websocket)
        return
    user = await _authenticate(first_frame["token"])
    if user is None:
        logger.warning("ws.auth_rejected", extra={"reason": "invalid_credentials", "client": client})
        await _close_policy_violation(websocket)
        return
    set_log_context(user_id=user.id)
    logger.info("ws.authenticated", extra={"client": client})
    await websocket.send_json({"type": "auth_ok", "stream_epoch": current_epoch()})

    queue: asyncio.Queue | None = None
    conversation_id: int | None = None
    pump: asyncio.Task[None] | None = None
    send_lock = asyncio.Lock()
    try:
        while True:
            frame = await websocket.receive_json()
            action = frame.get("type")

            if action == "subscribe":
                new_id = frame.get("conversation_id")
                if not isinstance(new_id, int) or not await _is_member(new_id, user.id):
                    logger.warning("ws.subscription_rejected", extra={"requested_conversation_id": new_id})
                    async with send_lock:
                        await websocket.send_json({"type": "error", "payload": {"code": "CONVERSATION_NOT_FOUND"}})
                    continue
                if pump is not None:
                    pump.cancel()
                if queue is not None and conversation_id is not None:
                    await events_module.hub.unsubscribe(conversation_id, queue)
                conversation_id = new_id
                set_log_context(conversation_id=conversation_id)
                # 先注册实时订阅，再读取 backlog，保证注册和回放之间不丢事件。
                queue = await events_module.hub.subscribe(conversation_id)
                async with send_lock:
                    recovery = await _send_recovery(websocket, conversation_id, frame)
                logger.info("ws.subscribed", extra=recovery)
                pump = asyncio.create_task(_pump(websocket, queue, send_lock))

            elif action == "ping":
                async with send_lock:
                    await websocket.send_json({"type": "pong"})

            elif action == "close":
                break
    except WebSocketDisconnect:
        pass
    except RuntimeError as exc:
        # 发送过程中对端断开：ASGI 层会拒绝后续帧，与断开事件是同一件事，按预期结束。
        logger.debug("ws.send_after_disconnect", extra={"conversation_id": conversation_id, "detail": str(exc)})
    except Exception:
        logger.exception("ws.stream_failed", extra={"conversation_id": conversation_id})
    finally:
        if pump is not None:
            pump.cancel()
        if queue is not None and conversation_id is not None:
            await events_module.hub.unsubscribe(conversation_id, queue)
        logger.info("ws.disconnected", extra={"client": client})


async def _send_recovery(websocket: WebSocket, conversation_id: int, frame: dict) -> dict:
    """按客户端游标发送 backlog 或完整快照。"""
    after_seq = frame.get("after_event_seq")
    after_seq = after_seq if isinstance(after_seq, int) and after_seq >= 0 else 0
    client_epoch = frame.get("stream_epoch")
    epoch = current_epoch()

    async with SessionLocal() as session:
        latest = await event_store.latest_event_seq(session, conversation_id)
        needs_snapshot = client_epoch != epoch or latest - after_seq > _BACKLOG_LIMIT
        if needs_snapshot:
            snapshot = await chat.build_snapshot(session, conversation_id)
            await websocket.send_json({"type": "snapshot", "stream_epoch": epoch, "payload": snapshot})
            return {
                "recovery_mode": "snapshot",
                "stream_epoch": epoch,
                "after_event_seq": after_seq,
                "latest_event_seq": latest,
            }
        backlog = await event_store.read_backlog(session, conversation_id, after_seq, _BACKLOG_LIMIT)

    await websocket.send_json({
        "type": "subscribed",
        "stream_epoch": epoch,
        "conversation_id": conversation_id,
        "event_seq": latest,
    })
    for event in backlog:
        await websocket.send_json(event.as_dict())
    return {
        "recovery_mode": "backlog",
        "stream_epoch": epoch,
        "after_event_seq": after_seq,
        "latest_event_seq": latest,
        "backlog_count": len(backlog),
    }


async def _pump(websocket: WebSocket, queue: asyncio.Queue, send_lock: asyncio.Lock) -> None:
    """持续把订阅队列中的实时事件推送给客户端，直到订阅被取消。"""
    try:
        while True:
            event = await queue.get()
            async with send_lock:
                await websocket.send_json(event.as_dict())
    except asyncio.CancelledError:
        # 订阅切换或连接关闭属于预期取消。
        raise
    except (WebSocketDisconnect, RuntimeError):
        return
