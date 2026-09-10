"""Owner-only Shell 审批；共享事件不携带脚本。"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import AgentExecution, ToolApprovalRequest, User
from ..schemas import ShellApprovalDecision
from ..security import require_owner
from ..workspaces.approvals import pending_payload, resolve_approval
from ..workspaces.commands import WorkspaceCommandError
from .messages import require_member

router = APIRouter(prefix='/api/conversations', tags=['shell-approvals'])


@router.get('/{conversation_id}/tool-approvals')
async def list_approvals(conversation_id: int, response: Response, user: Annotated[User, Depends(require_owner)],
                         session: Annotated[AsyncSession, Depends(get_session)]):
    """读取当前会话未过期审批，不向 Guest 或历史缓存暴露脚本。

    Args:
        conversation_id：Owner 当前查看会话。
        response：设置禁止缓存。
        user：有效 Owner 身份。
        session：请求级只读会话。
    """
    response.headers['Cache-Control'] = 'no-store'
    await require_member(session, conversation_id, user.id)
    rows = (await session.scalars(select(ToolApprovalRequest).join(AgentExecution,
        AgentExecution.execution_id == ToolApprovalRequest.execution_id).where(
        AgentExecution.conversation_id == conversation_id, ToolApprovalRequest.status == 'pending',
        ToolApprovalRequest.expires_at > datetime.now(timezone.utc),
    ).order_by(ToolApprovalRequest.id))).all()
    try:
        return [await pending_payload(row) for row in rows]
    except WorkspaceCommandError as exc:
        raise HTTPException(409, exc.code) from None


@router.post('/{conversation_id}/tool-approvals/{approval_id}/decision')
async def decide_approval(conversation_id: int, approval_id: int, payload: ShellApprovalDecision, response: Response,
                           user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """先验证资源归属，再通过独立短事务决定；浏览器不能修改执行参数。

    Args:
        conversation_id：当前会话。
        approval_id：该会话审批。
        payload：决定与所见请求摘要。
        response：禁止浏览器缓存。
        user：当前 World Owner。
        session：用于资源归属的只读会话。
    """
    response.headers['Cache-Control'] = 'no-store'
    await require_member(session, conversation_id, user.id)
    row = await session.scalar(select(ToolApprovalRequest).join(AgentExecution,
        AgentExecution.execution_id == ToolApprovalRequest.execution_id).where(
        ToolApprovalRequest.id == approval_id, AgentExecution.conversation_id == conversation_id))
    if row is None:
        raise HTTPException(404, 'SHELL_APPROVAL_NOT_FOUND')
    # 不让权限读取事务跨过独立的审批写事务。
    owner_id = user.id
    await session.rollback()
    try:
        decided = await resolve_approval(approval_id, decision=payload.decision, digest=payload.request_digest, owner_id=owner_id)
    except WorkspaceCommandError as exc:
        raise HTTPException(409, exc.code) from None
    return {'id': decided.id, 'status': decided.status}
