"""逐次审批状态机：短事务决定，唯一宿主等待，不从 approved 行重放执行。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from contextvars import copy_context
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import SessionLocal, with_locked_retry
from ..models import AgentExecution, ToolApprovalRequest, WorkspaceBinding
from ..realtime import store as event_store
from .commands import WorkspaceCommandError
from .shell import shell_configuration, validate_script, run_shell

APPROVAL_SECONDS = 300
_waiters: dict[tuple[str, str], asyncio.Event] = {}
logger = logging.getLogger('roleplex.approvals')


def _utc(value: datetime) -> datetime:
    """统一 SQLite 无时区值与 PostgreSQL 的带时区值。

    Args:
        value：数据库或宿主产生的 UTC 时间点。
    """
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _cipher() -> Fernet:
    """按用途隔离派生 World 审批密钥，不与模型 Key/工具详情互换密文。"""
    material = b'roleplex-shell-approval-v1\0' + settings.resolved_api_key_secret().encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def _serialized(request: dict) -> bytes:
    """生成只用于密文与摘要的规范请求表示。

    Args:
        request：宿主构造的冻结请求，不得直接进入日志。
    """
    return json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def decrypt_request(row: ToolApprovalRequest) -> dict:
    """解密并验证密文未被跨 execution、工作区或调用替换。

    Args:
        row：已完成 Owner/会话归属授权的审批记录。
    """
    try:
        raw = _cipher().decrypt(row.request_encrypted.encode())
        request = json.loads(raw)
        if hashlib.sha256(raw).hexdigest() != row.request_digest or any(request[key] != getattr(row, key)
            for key in ('execution_id', 'workspace_binding_id', 'tool_call_id', 'tool_name')):
            raise ValueError()
        return request
    except (InvalidToken, ValueError, TypeError, KeyError):
        raise WorkspaceCommandError('SHELL_APPROVAL_MISMATCH') from None


async def _authorized(request: dict) -> bool:
    """在创建、决定和启动三个边界复核权限及冻结配置。

    Args:
        request：已经校验身份的冻结请求。
    """
    from .tools import _authorized_service
    service = await _authorized_service(execution_id=request['execution_id'], conversation_id=request['conversation_id'],
        role_id=request['role_id'], triggered_by_user_id=request['owner_id'], workspace_binding_id=request['workspace_binding_id'],
        root_path_snapshot=request['root_path'], tool_name='workspace_run_shell')
    try:
        current = shell_configuration()
    except WorkspaceCommandError:
        return False
    return service is not None and all(request[key] == value for key, value in current.items())


def _log(row: ToolApprovalRequest, request: dict, *, reason: str | None = None) -> None:
    """只写允许的关联字段与摘要，不写加密前的业务正文。

    Args:
        row：已提交的审批事实。
        request：只提取链路身份，不能整体序列化。
        reason：终态安全原因；创建时为空。
    """
    fields = {key: request[key] for key in ('execution_id', 'conversation_id', 'generation_id', 'chain_id', 'role_id', 'tool_call_id')}
    fields.update(approval_id=row.id, request_digest=row.request_digest)
    if reason:
        fields.update(status='success' if row.status == 'approved' else 'cancelled' if reason in {'cancelled', 'restart'} else 'rejected',
            approval_status=row.status, reason=reason,
            duration_ms=max(0, int((_utc(row.resolved_at) - _utc(row.requested_at)).total_seconds() * 1000)))
    logger.info('tool.approval_resolved' if reason else 'tool.approval_requested', extra=fields)


async def resolve_approval(approval_id: int, *, decision: str, digest: str | None = None,
                           owner_id: int | None = None, reason: str = 'owner_decision') -> ToolApprovalRequest:
    """原子竞争一次审批决定，重复请求返回已有终态。

    Args:
        approval_id：由已授权路由或宿主任务解析的审批 ID。
        decision：approve/reject 或宿主使用的 expire。
        digest：Owner 页面看到的请求摘要，宿主过期为空。
        owner_id：已通过 Owner 认证及归属校验的用户，宿主为空。
        reason：登记的过期/取消/重启原因。
    """
    async def transition():
        """在独立短事务内比较并交换状态，事件和状态原子提交。"""
        async with SessionLocal() as session:
            row = await session.get(ToolApprovalRequest, approval_id)
            if row is None:
                raise HTTPException(404, 'SHELL_APPROVAL_NOT_FOUND')
            if digest is not None and digest != row.request_digest:
                raise HTTPException(409, 'SHELL_APPROVAL_MISMATCH')
            if row.status != 'pending':
                # 路由已复核 Owner/会话归属；既有决定不因绑定后续删除而再次解密或执行。
                return row, {}, None, None
            if decision == 'expire':
                # 重启/取消必须能收口损坏密文；不为过期决定解密脚本。
                execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == row.execution_id))
                request = {'execution_id': row.execution_id, 'tool_call_id': row.tool_call_id,
                    'conversation_id': execution.conversation_id, 'generation_id': execution.generation_id,
                    'chain_id': execution.chain_id, 'role_id': execution.role_id}
            else:
                request = decrypt_request(row)
            if owner_id is not None and request['owner_id'] != owner_id:
                raise HTTPException(404, 'SHELL_APPROVAL_NOT_FOUND')
            now = datetime.now(timezone.utc)
            expired = _utc(row.expires_at) <= now
            no_waiter = (row.execution_id, row.tool_call_id) not in _waiters
            target = 'expired' if decision == 'expire' or expired or no_waiter else 'approved' if decision == 'approve' else 'rejected'
            resolved_reason = reason if decision == 'expire' else 'expired' if expired else 'restart' if no_waiter else reason
            if target == 'approved' and not await _authorized(request):
                await session.refresh(row)
                if row.status != 'pending':
                    return row, request, None, None
                raise HTTPException(409, 'WORKSPACE_TOOL_NOT_AVAILABLE')
            # 授权查询也可能跨过截止时间；CAS 使用真正决定时刻，不沿用请求到达时刻。
            now = datetime.now(timezone.utc)
            if target in {'approved', 'rejected'} and _utc(row.expires_at) <= now:
                target, resolved_reason = 'expired', 'expired'
            conditions = [ToolApprovalRequest.id == approval_id, ToolApprovalRequest.status == 'pending']
            if target in {'approved', 'rejected'}:
                conditions.append(ToolApprovalRequest.expires_at > now)
            result = await session.execute(update(ToolApprovalRequest).where(*conditions).values(
                status=target, resolved_at=now, resolved_by=owner_id if target != 'expired' else None,
            ).execution_options(synchronize_session=False))
            if not result.rowcount:
                await session.refresh(row)
                return row, request, None, None
            await session.refresh(row)
            event = await event_store.append_event(session, request['conversation_id'], 'approval_changed',
                {'approval_id': row.id, 'status': row.status}, generation_id=request['generation_id'])
            await session.commit()
            return row, request, event, resolved_reason
    row, request, event, resolved_reason = await with_locked_retry(transition)
    if event is not None:
        await event_store.publish_events(event)
        _log(row, request, reason=resolved_reason)
        waiter = _waiters.get((row.execution_id, row.tool_call_id))
        if waiter:
            waiter.set()
    return row


async def request_and_run(*, script: str, execution_id: str, conversation_id: int, role_id: int,
                          owner_id: int, workspace_binding_id: int, root_path: str, tool_call_id: str | None) -> dict:
    """唯一宿主创建 pending，等待决定，批准后再次授权并执行一次。

    Args:
        script：模型脚本，只进密文和 stdin。
        execution_id：调度器提供的持久身份。
        conversation_id：当前 single 会话。
        role_id：执行角色。
        owner_id：原始触发 Owner。
        workspace_binding_id：execution 捕获的绑定。
        root_path：execution 的规范根快照。
        tool_call_id：防腐层注入的实际调用身份，不接受模型提供。
    """
    validate_script(script)
    if not tool_call_id:
        raise WorkspaceCommandError('SHELL_ARGUMENT_INVALID')
    key = (execution_id, tool_call_id)
    if key in _waiters:
        raise WorkspaceCommandError('SHELL_REQUEST_CONFLICT')
    waiter = _waiters[key] = asyncio.Event()
    row = None
    try:
        request = dict(script=script, execution_id=execution_id, conversation_id=conversation_id, role_id=role_id,
            owner_id=owner_id, workspace_binding_id=workspace_binding_id, root_path=root_path,
            tool_call_id=tool_call_id, tool_name='workspace_run_shell', **shell_configuration())
        if not await _authorized(request):
            raise WorkspaceCommandError('WORKSPACE_TOOL_NOT_AVAILABLE')
        async def create():
            """先加密后持久化，唯一约束禁止一个宿主身份再次申请/消费。"""
            async with SessionLocal() as session:
                execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == execution_id))
                request.update(generation_id=execution.generation_id, chain_id=execution.chain_id)
                raw = _serialized(request)
                now = datetime.now(timezone.utc)
                pending = ToolApprovalRequest(execution_id=execution_id, workspace_binding_id=workspace_binding_id,
                    tool_call_id=tool_call_id, tool_name='workspace_run_shell', request_encrypted=_cipher().encrypt(raw).decode(),
                    request_digest=hashlib.sha256(raw).hexdigest(), status='pending', requested_at=now,
                    expires_at=now + timedelta(seconds=APPROVAL_SECONDS))
                session.add(pending)
                await session.flush()
                event = await event_store.append_event(session, conversation_id, 'approval_changed',
                    {'approval_id': pending.id, 'status': 'pending'}, generation_id=execution.generation_id)
                await session.commit()
                return pending, event
        try:
            # 提交与句柄交接不能被取消切断，否则会留下已提交但宿主不知道 ID 的 pending。
            creating = asyncio.create_task(with_locked_retry(create), context=copy_context())
            cancelled = False
            while not creating.done():
                try:
                    await asyncio.shield(creating)
                except asyncio.CancelledError:
                    cancelled = True
            row, event = creating.result()
        except IntegrityError:
            raise WorkspaceCommandError('SHELL_REQUEST_CONFLICT') from None
        _log(row, request)
        await event_store.publish_events(event)
        if cancelled:
            raise asyncio.CancelledError
        try:
            await asyncio.wait_for(waiter.wait(), max(0, (_utc(row.expires_at) - datetime.now(timezone.utc)).total_seconds()))
        except TimeoutError:
            await resolve_approval(row.id, decision='expire', reason='expired')
        async with SessionLocal() as session:
            row = await session.get(ToolApprovalRequest, row.id)
            request = decrypt_request(row)
        if row.status != 'approved':
            raise WorkspaceCommandError('SHELL_REJECTED' if row.status == 'rejected' else 'SHELL_APPROVAL_EXPIRED')
        if not await _authorized(request):
            raise WorkspaceCommandError('WORKSPACE_TOOL_NOT_AVAILABLE')
        return await run_shell(request)
    finally:
        if row is not None:
            async def close_pending():
                """取消/结束时关闭仍 pending 的审批，不改变已经决定的终态。"""
                await resolve_approval(row.id, decision='expire', reason='cancelled')
            cleanup = asyncio.create_task(close_pending(), context=copy_context())
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            _waiters.pop(key, None)
            cleanup.result()
            if cancelled:
                raise asyncio.CancelledError
        else:
            _waiters.pop(key, None)


async def recover_approvals() -> None:
    """启动时关闭所有遗留 pending；approved 也不由恢复过程执行。"""
    async with SessionLocal() as session:
        ids = list((await session.scalars(select(ToolApprovalRequest.id).where(ToolApprovalRequest.status == 'pending'))).all())
    for approval_id in ids:
        await resolve_approval(approval_id, decision='expire', reason='restart')


async def pending_payload(row: ToolApprovalRequest) -> dict:
    """构造 Owner-only 视图，不向客户端返回可执行文件路径。

    Args:
        row：已检查会话归属且仍未过期的记录。
    """
    request = decrypt_request(row)
    async with SessionLocal() as session:
        binding = await session.get(WorkspaceBinding, row.workspace_binding_id)
    return {key: request[key] for key in ('execution_id', 'tool_call_id', 'tool_name', 'workspace_binding_id',
        'root_path', 'script', 'shell_kind', 'timeout_seconds', 'output_bytes')} | {
        'id': row.id, 'request_digest': row.request_digest, 'status': row.status,
        'requested_at': _utc(row.requested_at).isoformat(), 'expires_at': _utc(row.expires_at).isoformat(),
        'world_name': settings.world_name, 'workspace_name': binding.display_name if binding else '不可用工作区',
    }
