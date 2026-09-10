from __future__ import annotations

import asyncio
import logging
import re
import uuid
from contextvars import copy_context

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..config import settings
from ..config.logging import log_context, set_log_context
from ..db import SessionLocal
from ..models import Conversation, ConversationMember, User
from ..security.tokens import token_requires_password_reset
from ..services import chat
from ..services.history import build_history_window
from . import events as events_module
from . import store as event_store
from .events import current_epoch

# logger 名称属于既有排障查询条件，文件移动不应改变可观测字段。
logger = logging.getLogger("roleplex.ws")
router = APIRouter()

# 首帧认证必须在有限时间内完成，避免未认证连接长期占用资源。
_AUTH_TIMEOUT_SECONDS = 10.0

# backlog 超过该数量时改为下发完整快照，避免重连时回放过长历史。
_BACKLOG_LIMIT = 300
_SUBSCRIPTION_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


class _AccessDenied(Exception):
    """发送前授权失效的稳定内部信号，不包含凭据或用户原文。"""

    def __init__(self, code: str):
        """记录显式策略码，不从异常文本推导错误语义。

        Args:
            code：执行层固定的认证或资源拒绝码。
        """
        super().__init__(code)
        self.code = code


def _tag(payload: dict, conversation_id: int, subscription_id: str | None) -> dict:
    """仅为协商了订阅身份的客户端附加控制归属。

    Args:
        payload：旧版公开帧。
        conversation_id：本次订阅所属会话。
        subscription_id：客户端有界操作标识；None 保持旧帧格式。
    """
    return {**payload, 'conversation_id': conversation_id, 'subscription_id': subscription_id} if subscription_id else payload


async def _send_checked(websocket: WebSocket, payload: dict, conversation_id: int, subscription_id: str | None, token: str | None) -> None:
    """恢复与实时发送前复核认证及资源权限。

    Args:
        websocket：已首帧认证的连接。
        payload：安全公开帧。
        conversation_id：订阅会话。
        subscription_id：本次操作身份。
        token：仅驻留本宿主任务的原始凭据；测试内部调用可省略。
    """
    if token is not None:
        current = await _authenticate(token)
        if current is None:
            raise _AccessDenied('AUTH_INVALID')
        if not await _is_member(conversation_id, current.id):
            raise _AccessDenied('CONVERSATION_NOT_FOUND')
    await websocket.send_json(_tag(payload, conversation_id, subscription_id))


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

    if not isinstance(first_frame, dict) or first_frame.get("type") != "auth" or not isinstance(first_frame.get("token"), str):
        logger.warning("ws.auth_rejected", extra={"reason": "invalid_auth_frame", "client": client})
        await _close_policy_violation(websocket)
        return
    token = first_frame['token']
    user = await _authenticate(token)
    if user is None:
        logger.warning("ws.auth_rejected", extra={"reason": "invalid_credentials", "client": client})
        await _close_policy_violation(websocket)
        return
    set_log_context(user_id=user.id)
    logger.info("ws.authenticated", extra={"client": client})
    await websocket.send_json({"type": "auth_ok", "stream_epoch": current_epoch(), 'capabilities': ['subscription_control_v1', 'history_window_v1']})

    queue: asyncio.Queue | None = None
    conversation_id: int | None = None
    pump: asyncio.Task[None] | None = None
    send_lock = asyncio.Lock()
    subscription_id: str | None = None

    async def detach() -> None:
        """等待旧 pump 结束再注销队列，防止旧发送任务延续到新订阅。"""
        nonlocal pump, queue, conversation_id, subscription_id
        if pump is not None:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            pump = None
        if queue is not None and conversation_id is not None:
            await events_module.hub.unsubscribe(conversation_id, queue)
        queue, conversation_id, subscription_id = None, None, None
        set_log_context(conversation_id=None)

    try:
        while True:
            frame = await websocket.receive_json()
            if await _authenticate(token) is None:
                logger.warning('ws.auth_rejected', extra={'reason': 'credentials_changed', 'client': client})
                async with send_lock:
                    await websocket.send_json({'type': 'error', 'payload': {'code': 'AUTH_INVALID'}})
                    await _close_policy_violation(websocket)
                return
            if not isinstance(frame, dict):
                async with send_lock:
                    await websocket.send_json({'type': 'error', 'payload': {'code': 'VALIDATION_ERROR'}})
                continue
            action = frame.get("type")
            requested_sid = frame.get('subscription_id')
            if requested_sid is not None and (not isinstance(requested_sid, str) or not _SUBSCRIPTION_ID.fullmatch(requested_sid)):
                async with send_lock:
                    await websocket.send_json({'type': 'error', 'payload': {'code': 'VALIDATION_ERROR'}})
                continue

            if action == "subscribe":
                if frame.get('history_window') not in (None, 'recent'):
                    async with send_lock:
                        await websocket.send_json({'type': 'error', 'payload': {'code': 'VALIDATION_ERROR'}})
                    continue
                new_id = frame.get("conversation_id")
                if type(new_id) is not int or new_id <= 0 or (requested_sid and 'after_event_seq' in frame and (
                    type(frame['after_event_seq']) is not int or frame['after_event_seq'] < 0
                )):
                    async with send_lock:
                        await websocket.send_json({'type': 'error', 'payload': {'code': 'VALIDATION_ERROR'}})
                    continue
                if not await _is_member(new_id, user.id):
                    logger.warning("ws.subscription_rejected", extra={"requested_conversation_id": new_id if type(new_id) is int else None})
                    async with send_lock:
                        await websocket.send_json(_tag({"type": "error", "payload": {"code": "CONVERSATION_NOT_FOUND"}}, new_id, requested_sid))
                    continue
                await detach()
                conversation_id = new_id
                subscription_id = requested_sid
                set_log_context(conversation_id=conversation_id)
                # 先注册实时订阅，再读取 backlog，保证注册和回放之间不丢事件。
                queue = await events_module.hub.subscribe(conversation_id)
                try:
                    async with send_lock:
                        recovery = await _send_recovery(websocket, conversation_id, frame, token)
                except _AccessDenied as exc:
                    await detach()
                    logger.warning('ws.subscription_rejected', extra={'conversation_id': new_id, 'error_code': exc.code})
                    await websocket.send_json(_tag({'type': 'error', 'payload': {'code': exc.code}}, new_id, requested_sid))
                    if exc.code == 'AUTH_INVALID':
                        await _close_policy_violation(websocket)
                        return
                    continue
                logger.info("ws.subscribed", extra=recovery)
                pump = asyncio.create_task(_pump(websocket, queue, send_lock, conversation_id, subscription_id, token), context=copy_context())

            elif action == 'unsubscribe':
                removed = queue is not None and (requested_sid is None or requested_sid == subscription_id)
                previous_id = conversation_id
                if removed:
                    await detach()
                    logger.info('ws.unsubscribed', extra={'conversation_id': previous_id})
                async with send_lock:
                    await websocket.send_json({'type': 'unsubscribed', 'removed': removed,
                        **({'subscription_id': requested_sid} if requested_sid else {})})

            elif action == "ping":
                if conversation_id is not None and not await _is_member(conversation_id, user.id):
                    previous_id, previous_sid = conversation_id, subscription_id
                    await detach()
                    logger.warning('ws.subscription_rejected', extra={'conversation_id': previous_id, 'error_code': 'CONVERSATION_NOT_FOUND'})
                    await websocket.send_json(_tag({'type': 'error', 'payload': {'code': 'CONVERSATION_NOT_FOUND'}}, previous_id, previous_sid))
                async with send_lock:
                    await websocket.send_json({"type": "pong"})

            elif action == "close":
                break
            else:
                async with send_lock:
                    await websocket.send_json({'type': 'error', 'payload': {'code': 'VALIDATION_ERROR'}})
    except WebSocketDisconnect:
        pass
    except RuntimeError as exc:
        # 发送过程中对端断开：ASGI 层会拒绝后续帧，与断开事件是同一件事，按预期结束。
        logger.debug("ws.send_after_disconnect", extra={"conversation_id": conversation_id, "detail": str(exc)})
    except Exception:
        logger.exception("ws.stream_failed", extra={"conversation_id": conversation_id})
    finally:
        await detach()
        logger.info("ws.disconnected", extra={"client": client})


async def _send_recovery(websocket: WebSocket, conversation_id: int, frame: dict, token: str | None = None) -> dict:
    """按游标恢复，数据发送完毕后才确认同步水位。

    Args:
        websocket：已认证连接。
        conversation_id：已授权的目标会话。
        frame：已经验证操作身份的订阅请求。
        token：发送前复核的认证凭据，不写日志。
    """
    after_seq = frame.get("after_event_seq")
    after_seq = after_seq if isinstance(after_seq, int) and after_seq >= 0 else 0
    client_epoch = frame.get("stream_epoch")
    epoch = current_epoch()
    sid = frame.get('subscription_id')

    async with SessionLocal() as session:
        latest = await event_store.latest_event_seq(session, conversation_id)
        needs_snapshot = client_epoch != epoch or latest - after_seq > _BACKLOG_LIMIT or after_seq > latest
        if needs_snapshot:
            snapshot = (await build_history_window(session, conversation_id, snapshot=True, subscription_id=sid)
                        if frame.get('history_window') == 'recent' else await chat.build_snapshot(session, conversation_id))
            await _send_checked(websocket, {"type": "snapshot", "stream_epoch": epoch, "payload": snapshot}, conversation_id, sid, token)
            if sid:
                await _send_checked(websocket, {'type': 'sync_complete', 'stream_epoch': epoch,
                    'through_event_seq': snapshot['event_seq']}, conversation_id, sid, token)
            return {
                "recovery_mode": "snapshot",
                "stream_epoch": epoch,
                "after_event_seq": after_seq,
                "latest_event_seq": latest,
            }
        backlog = await event_store.read_backlog(session, conversation_id, after_seq, _BACKLOG_LIMIT)

    await _send_checked(websocket, {
        "type": "subscribed",
        "stream_epoch": epoch,
        "conversation_id": conversation_id,
        "event_seq": latest,
    }, conversation_id, sid, token)
    for event in backlog:
        await _send_checked(websocket, event.as_dict(), conversation_id, sid, token)
    if sid:
        await _send_checked(websocket, {'type': 'sync_complete', 'stream_epoch': epoch,
            'through_event_seq': backlog[-1].event_seq if backlog else after_seq}, conversation_id, sid, token)
    return {
        "recovery_mode": "backlog",
        "stream_epoch": epoch,
        "after_event_seq": after_seq,
        "latest_event_seq": latest,
        "backlog_count": len(backlog),
    }


async def _pump(websocket: WebSocket, queue: asyncio.Queue, send_lock: asyncio.Lock,
                conversation_id: int, subscription_id: str | None, token: str) -> None:
    """只推送本订阅的事件，并持续复核访问边界。

    Args:
        websocket：当前物理连接。
        queue：该会话独占订阅队列。
        send_lock：同一连接的发送顺序锁。
        conversation_id：本任务绑定会话。
        subscription_id：固定的操作标识，不随外部选择漂移。
        token：本连接首帧凭据。
    """
    try:
        while True:
            event = await queue.get()
            if event.conversation_id != conversation_id:
                continue
            async with send_lock:
                await _send_checked(websocket, event.as_dict(), conversation_id, subscription_id, token)
    except _AccessDenied as exc:
        logger.warning('ws.auth_rejected' if exc.code == 'AUTH_INVALID' else 'ws.subscription_rejected',
                       extra={'conversation_id': conversation_id, 'error_code': exc.code})
        async with send_lock:
            await websocket.send_json(_tag({'type': 'error', 'payload': {'code': exc.code}}, conversation_id, subscription_id))
            if exc.code == 'AUTH_INVALID':
                await _close_policy_violation(websocket)
    except asyncio.CancelledError:
        # 订阅切换或连接关闭属于预期取消。
        raise
    except (WebSocketDisconnect, RuntimeError):
        return
