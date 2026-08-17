from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Conversation, ConversationMember, Role, User
from ..schemas import ConversationCreate, ConversationResponse
from ..security import get_current_user, require_owner

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


async def require_member(session: AsyncSession, conversation_id: int, user_id: int) -> ConversationMember:
    """要求请求者是会话成员，并将无权访问的会话隐藏为不存在。"""
    member = await session.scalar(select(ConversationMember).where(
        ConversationMember.conversation_id == conversation_id,
        ConversationMember.member_type == "user",
        ConversationMember.member_id == user_id,
    ))
    if not member:
        raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND")
    return member


async def response(session: AsyncSession, conversation: Conversation, member: ConversationMember) -> ConversationResponse:
    """将共享会话状态、角色成员和请求用户的个人偏好合并返回。"""
    role_members = (await session.scalars(select(ConversationMember.member_id).where(
        ConversationMember.conversation_id == conversation.id,
        ConversationMember.member_type == "role",
    ))).all()
    return ConversationResponse(
        id=conversation.id, type=conversation.type, title=conversation.title,
        orchestrator_enabled=conversation.orchestrator_enabled,
        orchestrator_role_id=conversation.orchestrator_role_id,
        role_ids=list(role_members),
        last_message_at=conversation.last_message_at,
        pinned=member.pinned, archived=member.archived,
    )


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_session)]):
    """按个人置顶状态和活跃时间排序，列出用户可见的会话。"""
    rows = (await session.execute(
        select(Conversation, ConversationMember)
        .join(ConversationMember, ConversationMember.conversation_id == Conversation.id)
        .where(ConversationMember.member_type == "user", ConversationMember.member_id == user.id)
        .order_by(ConversationMember.pinned.desc(), Conversation.last_message_at.desc(), Conversation.created_at.desc())
    )).all()
    return [await response(session, conversation, member) for conversation, member in rows]


@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(payload: ConversationCreate, user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """创建会话，并且只绑定 Owner 拥有且处于启用状态的角色。"""
    if payload.type == "single" and len(payload.role_ids) != 1:
        raise HTTPException(status_code=422, detail="SINGLE_CHAT_REQUIRES_ONE_ROLE")
    if not payload.role_ids:
        raise HTTPException(status_code=422, detail="ROLE_REQUIRED")
    roles = (await session.scalars(select(Role).where(Role.id.in_(payload.role_ids), Role.created_by == user.id, Role.active.is_(True)))).all()
    if len(roles) != len(set(payload.role_ids)):
        raise HTTPException(status_code=422, detail="ROLE_NOT_AVAILABLE")
    if payload.orchestrator_role_id and payload.orchestrator_role_id not in {role.id for role in roles}:
        raise HTTPException(status_code=422, detail="ORCHESTRATOR_MUST_BE_MEMBER")
    now = datetime.now(timezone.utc)
    conversation = Conversation(
        type=payload.type, title=payload.title, created_by=user.id,
        orchestrator_enabled=payload.orchestrator_enabled,
        orchestrator_role_id=payload.orchestrator_role_id, created_at=now,
    )
    session.add(conversation)
    await session.flush()
    session.add(ConversationMember(conversation_id=conversation.id, member_type="user", member_id=user.id, joined_at=now))
    for role in roles:
        session.add(ConversationMember(conversation_id=conversation.id, member_type="role", member_id=role.id, joined_at=now))
    await session.commit()
    await session.refresh(conversation)
    member = await require_member(session, conversation.id, user.id)
    return await response(session, conversation, member)


@router.patch("/{conversation_id}/preferences", response_model=ConversationResponse)
async def update_preferences(
    conversation_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    pinned: bool | None = None,
    archived: bool | None = None,
):
    """更新用户级会话偏好，不修改共享会话元数据。"""
    member = await require_member(session, conversation_id, user.id)
    if pinned is not None:
        member.pinned = pinned
    if archived is not None:
        member.archived = archived
    conversation = await session.get(Conversation, conversation_id)
    await session.commit()
    return await response(session, conversation, member)


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: int, user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """删除当前认证 Owner 创建的会话。"""
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.created_by != user.id:
        raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND")
    await session.delete(conversation)
    await session.commit()
