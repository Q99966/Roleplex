"""Owner 会话进程与三层配置接口，不按 PID 开放主机进程控制。"""
from typing import Annotated, Literal
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import AgentExecution, Conversation, Generation, Message, ToolApprovalRequest, User, WorkspaceBinding
from ..security import require_owner
from ..runtime import logs, registry
from ..runtime.manager import manager, supported
from ..runtime.models import RuntimeEntry
from ..workspaces.approvals import decrypt_request
from ..workspaces.commands import WorkspaceCommandError
from .messages import require_member

router = APIRouter(prefix='/api', tags=['runtime'])


class RuntimeConfiguration(BaseModel):
    """显式范围和版本化限额；不能通过输入调整另一个 World。"""
    model_config = ConfigDict(extra='forbid')
    scope: Literal['world', 'workspace', 'conversation']
    scope_id: int = Field(ge=0, strict=True)
    limit: int = Field(ge=1, le=2**31 - 1, strict=True)
    expected_revision: int = Field(ge=0, strict=True)
    services_enabled: bool | None = None
    confirm_cleanup: bool = False


async def authorize_scope(session, owner_id: int, scope: str, scope_id: int):
    """Args:
        session：请求只读会话。
        owner_id：已认证 Owner。
        scope：目标层级。
        scope_id：资源身份。
    """
    if scope == 'world' and scope_id == 0:
        return
    if scope == 'conversation':
        await require_member(session, scope_id, owner_id)
        row = await session.get(Conversation, scope_id)
    elif scope == 'workspace':
        row = await session.get(WorkspaceBinding, scope_id)
    else:
        raise HTTPException(422, 'RUNTIME_SCOPE_INVALID')
    if row is None or row.created_by != owner_id:
        raise HTTPException(404, 'RUNTIME_NOT_FOUND')


@router.get('/runtime/config')
async def get_config(scope: Literal['world', 'workspace', 'conversation'], scope_id: int,
    response: Response, user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """读取已授权范围的实际配置与占用。

    Args:
        scope：配置层级。
        scope_id：当前 World 中的资源身份。
        response：no-store 边界。
        user：Owner 身份。
        session：归属查询会话。
    """
    response.headers['Cache-Control'] = 'no-store'
    await authorize_scope(session, user.id, scope, scope_id)
    return {**await registry.quota_view(scope, scope_id), 'services_supported': supported()}


@router.put('/runtime/config')
async def put_config(payload: RuntimeConfiguration, response: Response, user: Annotated[User, Depends(require_owner)],
                     session: Annotated[AsyncSession, Depends(get_session)]):
    """配置修改串行化；关闭服务能力时必须确认并逐项回收。

    Args:
        payload：当前所见版本与新配置。
        response：禁止缓存。
        user：认证 Owner。
        session：只用于归属查询。
    """
    response.headers['Cache-Control'] = 'no-store'
    owner_id = user.id
    await authorize_scope(session, owner_id, payload.scope, payload.scope_id)
    if payload.services_enabled is True and not supported():
        raise HTTPException(409, 'RUNTIME_NOT_SUPPORTED')
    before = await registry.quota_view(payload.scope, payload.scope_id)
    if before['revision'] != payload.expected_revision:
        raise HTTPException(409, 'RUNTIME_REVISION_CONFLICT')
    await session.rollback()
    arguments = dict(limit=payload.limit, expected_revision=payload.expected_revision, actor_id=owner_id, services_enabled=payload.services_enabled)
    if payload.services_enabled is False and before.get('services_enabled'):
        if not payload.confirm_cleanup:
            raise HTTPException(409, 'RUNTIME_CLEANUP_CONFIRM_REQUIRED')
        async with manager.cleanup_scope(payload.scope, payload.scope_id, 'service_capability_disabled', owner_id,
                                         expected_revision=payload.expected_revision):
            return {**await registry.configure(payload.scope, payload.scope_id, **arguments), 'services_supported': supported()}
    async with manager.cleanup_lock:
        return {**await registry.configure(payload.scope, payload.scope_id, **arguments), 'services_supported': supported()}


def summary(row: RuntimeEntry) -> dict:
    """只返回该 Owner 有权管理的安全运行信息，不含脚本和根。

    Args:
        row：已验证归属的实例。
    """
    result = {key: getattr(row, key) for key in ('id', 'sequence', 'conversation_id', 'workspace_id', 'execution_id', 'role_id',
        'tool_name', 'kind', 'state', 'revision', 'pid', 'port', 'error_code', 'exit_code', 'health_code',
        'created_at', 'started_at', 'expires_at', 'ended_at', 'health_checked_at')}
    return {key: (value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.isoformat())
        if isinstance(value, datetime) else value for key, value in result.items()}


@router.get('/runtime/cleanup-preview')
async def cleanup_preview(scope: Literal['world', 'workspace', 'conversation'], scope_id: int, response: Response,
    user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """资源变更前显示影响清单；真正停止时仍以冻结的最新范围为准。

    Args:
        scope：Owner 拟变更的范围。
        scope_id：资源身份。
        response：禁止缓存。
        user：Owner。
        session：只读会话。
    """
    response.headers['Cache-Control'] = 'no-store'
    await authorize_scope(session, user.id, scope, scope_id)
    rows = list((await session.scalars(select(RuntimeEntry).where(registry.scope_filter(scope, scope_id),
        RuntimeEntry.state.in_(registry.ACTIVE)).order_by(RuntimeEntry.sequence.desc()))).all())
    return {'items': [summary(row) for row in rows]}


async def owned_entry(session, owner_id: int, conversation_id: int, runtime_id: str):
    """Args:
        session：请求查询会话。
        owner_id：已认证 Owner。
        conversation_id：当前会话。
        runtime_id：不可猜测的资源 ID。
    """
    await require_member(session, conversation_id, owner_id)
    row = await session.get(RuntimeEntry, runtime_id)
    if row is None or row.owner_id != owner_id or row.conversation_ref_id != conversation_id:
        raise HTTPException(404, 'RUNTIME_NOT_FOUND')
    return row


@router.get('/conversations/{conversation_id}/processes')
async def list_processes(conversation_id: int, response: Response, user: Annotated[User, Depends(require_owner)],
                         session: Annotated[AsyncSession, Depends(get_session)]):
    """列出本会话的登记，不扫描操作系统或其他会话。

    Args:
        conversation_id：当前会话。
        response：禁止 HTTP 缓存。
        user：认证 Owner。
        session：请求数据库会话。
    """
    response.headers['Cache-Control'] = 'no-store'
    await require_member(session, conversation_id, user.id)
    active = list((await session.scalars(select(RuntimeEntry).where(RuntimeEntry.conversation_ref_id == conversation_id,
        RuntimeEntry.owner_id == user.id, RuntimeEntry.state.in_(registry.ACTIVE)).order_by(RuntimeEntry.sequence.desc()))).all())
    recent = list((await session.scalars(select(RuntimeEntry).where(RuntimeEntry.conversation_ref_id == conversation_id,
        RuntimeEntry.owner_id == user.id, RuntimeEntry.state.in_(registry.TERMINAL)).order_by(RuntimeEntry.sequence.desc()).limit(50))).all())
    return {'items': [summary(row) for row in [*active, *recent]], 'services_supported': supported()}


@router.get('/conversations/{conversation_id}/processes/{runtime_id}')
async def get_process(conversation_id: int, runtime_id: str, response: Response, user: Annotated[User, Depends(require_owner)],
                      session: Annotated[AsyncSession, Depends(get_session)]):
    """按原审批读取冻结脚本，不推测当前工作区等于历史 cwd。

    Args:
        conversation_id：当前会话。
        runtime_id：资源身份。
        response：禁止缓存。
        user：认证 Owner。
        session：归属查询会话。
    """
    response.headers['Cache-Control'] = 'no-store'
    row = await owned_entry(session, user.id, conversation_id, runtime_id)
    approval = await session.scalar(select(ToolApprovalRequest).where(ToolApprovalRequest.execution_id == row.execution_id,
        ToolApprovalRequest.tool_call_id == row.tool_call_id, ToolApprovalRequest.tool_name == row.tool_name))
    try:
        request = decrypt_request(approval) if approval else None
    except WorkspaceCommandError:
        request = None
    message_id = await session.scalar(select(Generation.assistant_message_id).join(AgentExecution,
        AgentExecution.generation_id == Generation.id).where(AgentExecution.execution_id == row.execution_id,
        Generation.conversation_id == conversation_id))
    message = await session.get(Message, message_id) if message_id else None
    part = next((part for part in message.parts_json if part.get('type') == 'tool_call' and part.get('call_id') == row.tool_call_id), None) if message else None
    return {**summary(row), 'request': {key: request[key] for key in ('script', 'root_path', 'shell_kind', 'port', 'health_path', 'lifetime_seconds') if key in request} if request else None,
        'command_detail': {'message_id': message_id, 'part': part} if row.kind == 'command' and part else None}


@router.get('/conversations/{conversation_id}/processes/{runtime_id}/logs')
async def get_logs(conversation_id: int, runtime_id: str, response: Response, user: Annotated[User, Depends(require_owner)],
                   session: Annotated[AsyncSession, Depends(get_session)], after: int = Query(default=0, ge=0, le=2**63 - 1)):
    """读取内存环形窗口或终态密文尾部，不进入正式日志。

    Args:
        conversation_id：当前会话。
        runtime_id：资源身份。
        response：禁止缓存。
        user：Owner。
        session：只读授权会话。
        after：观察序号。
    """
    response.headers['Cache-Control'] = 'no-store'
    row = await owned_entry(session, user.id, conversation_id, runtime_id)
    host = manager.hosts.get(runtime_id)
    return host.ring.page(after) if host else logs.archived_page(row, after)


@router.post('/conversations/{conversation_id}/processes/{runtime_id}/stop')
async def stop_process(conversation_id: int, runtime_id: str, response: Response, user: Annotated[User, Depends(require_owner)],
                       session: Annotated[AsyncSession, Depends(get_session)]):
    """停止一个准确归属的实例，重复请求不重复操作。

    Args:
        conversation_id：当前会话。
        runtime_id：资源身份，不接受 PID。
        response：禁止缓存。
        user：当前 Owner。
        session：授权会话。
    """
    response.headers['Cache-Control'] = 'no-store'
    await owned_entry(session, user.id, conversation_id, runtime_id)
    await session.rollback()
    return summary(await manager.stop_one(runtime_id))
