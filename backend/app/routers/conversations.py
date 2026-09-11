from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Conversation, ConversationMember, Role, User
from ..realtime import store as event_store
from ..schemas import (
    ConversationCreate,
    ConversationMembersUpdate,
    ConversationResponse,
    ConversationWorkspaceUpdate,
)
from ..security.tokens import get_current_user, require_owner
from ..workspaces.service import available_workspace

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


async def require_member(session: AsyncSession, conversation_id: int, user_id: int) -> ConversationMember:
    """要求请求者是会话成员，并将无权访问或已进回收站的会话隐藏为不存在。"""
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


async def response(session: AsyncSession, conversation: Conversation, member: ConversationMember) -> ConversationResponse:
    """将共享会话状态、角色成员和请求用户的个人偏好合并返回。

    角色成员过滤掉墓碑：已删除的角色不应作为孤儿项出现在成员列表里，
    它只在历史消息的发送者展示中出现。
    """
    role_members = (await session.scalars(
        select(ConversationMember.member_id)
        .join(Role, Role.id == ConversationMember.member_id)
        .where(
            ConversationMember.conversation_id == conversation.id,
            ConversationMember.member_type == "role",
            Role.deleted_at.is_(None),
        )
        .order_by(ConversationMember.id.asc())
    )).all()
    return ConversationResponse(
        id=conversation.id, type=conversation.type, title=conversation.title,
        orchestrator_enabled=conversation.orchestrator_enabled,
        orchestrator_role_id=conversation.orchestrator_role_id,
        workspace_binding_id=conversation.workspace_binding_id,
        role_ids=list(role_members),
        revision=conversation.revision,
        last_message_at=conversation.last_message_at,
        pinned=member.pinned, archived=member.archived,
        deleted_at=conversation.deleted_at,
    )


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_session)]):
    """按个人置顶状态和活跃时间排序，列出用户可见的会话。

    已进回收站的会话不出现在这里，需要通过回收站接口查看与恢复。
    """
    rows = (await session.execute(
        select(Conversation, ConversationMember)
        .join(ConversationMember, ConversationMember.conversation_id == Conversation.id)
        .where(
            ConversationMember.member_type == "user", ConversationMember.member_id == user.id,
            Conversation.deleted_at.is_(None),
        )
        .order_by(ConversationMember.pinned.desc(), Conversation.last_message_at.desc(), Conversation.created_at.desc())
    )).all()
    return [await response(session, conversation, member) for conversation, member in rows]


@router.get("/deleted", response_model=list[ConversationResponse])
async def list_deleted_conversations(user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_session)]):
    """列出回收站中的会话，按删除时间从新到旧排序。

    路由必须声明在 `/{conversation_id}` 之前，否则 `deleted` 会被当作会话 id 解析。
    响应中的 `deleted_at` 供客户端计算剩余保留天数。
    """
    rows = (await session.execute(
        select(Conversation, ConversationMember)
        .join(ConversationMember, ConversationMember.conversation_id == Conversation.id)
        .where(
            ConversationMember.member_type == "user", ConversationMember.member_id == user.id,
            Conversation.deleted_at.is_not(None),
        )
        .order_by(Conversation.deleted_at.desc())
    )).all()
    return [await response(session, conversation, member) for conversation, member in rows]


@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(payload: ConversationCreate, user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """创建会话，并且只绑定 Owner 拥有且处于启用状态的角色。"""
    if payload.type == "single" and len(payload.role_ids) != 1:
        raise HTTPException(status_code=422, detail="SINGLE_CHAT_REQUIRES_ONE_ROLE")
    if payload.type == "group" and len(payload.role_ids) < 2:
        raise HTTPException(status_code=422, detail="GROUP_CHAT_REQUIRES_MULTIPLE_ROLES")
    if payload.type != "single" and payload.workspace_binding_id is not None:
        raise HTTPException(status_code=422, detail="SINGLE_CHAT_REQUIRED")
    if not payload.role_ids:
        raise HTTPException(status_code=422, detail="ROLE_REQUIRED")
    if len(payload.role_ids) != len(set(payload.role_ids)):
        raise HTTPException(status_code=422, detail="ROLE_NOT_AVAILABLE")
    roles = (await session.scalars(select(Role).where(
        Role.id.in_(payload.role_ids),
        Role.created_by == user.id,
        Role.active.is_(True),
        Role.deleted_at.is_(None),
    ))).all()
    if len(roles) != len(payload.role_ids):
        raise HTTPException(status_code=422, detail="ROLE_NOT_AVAILABLE")
    roles_by_id = {role.id: role for role in roles}
    if payload.orchestrator_role_id and payload.orchestrator_role_id not in {role.id for role in roles}:
        raise HTTPException(status_code=422, detail="ORCHESTRATOR_MUST_BE_MEMBER")
    if payload.workspace_binding_id is not None:
        await available_workspace(session, payload.workspace_binding_id, user.id)
    now = datetime.now(timezone.utc)
    conversation = Conversation(
        type=payload.type, title=payload.title, created_by=user.id,
        orchestrator_enabled=payload.orchestrator_enabled,
        orchestrator_role_id=payload.orchestrator_role_id,
        workspace_binding_id=payload.workspace_binding_id,
        created_at=now,
    )
    session.add(conversation)
    await session.flush()
    session.add(ConversationMember(conversation_id=conversation.id, member_type="user", member_id=user.id, joined_at=now))
    for role_id in payload.role_ids:
        session.add(ConversationMember(
            conversation_id=conversation.id, member_type="role", member_id=roles_by_id[role_id].id, joined_at=now,
        ))
    await session.commit()
    await session.refresh(conversation)
    member = await require_member(session, conversation.id, user.id)
    return await response(session, conversation, member)


@router.put("/{conversation_id}/workspace", response_model=ConversationResponse)
async def update_conversation_workspace(
    conversation_id: int,
    payload: ConversationWorkspaceUpdate,
    user: Annotated[User, Depends(require_owner)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """按 revision 为 Owner 的 single 会话绑定或解绑当前 World 工作区。"""
    conversation = await _owned_conversation(session, conversation_id, user.id)
    if conversation.deleted_at is not None:
        raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND")
    if conversation.type != "single":
        raise HTTPException(status_code=422, detail="SINGLE_CHAT_REQUIRED")
    if conversation.revision != payload.expected_revision:
        raise HTTPException(409, 'CONVERSATION_REVISION_CONFLICT')
    if payload.workspace_binding_id is not None:
        await available_workspace(session, payload.workspace_binding_id, user.id)
    from ..runtime.manager import manager
    from ..runtime.registry import quota_view
    changed = conversation.workspace_binding_id != payload.workspace_binding_id
    if changed and (await quota_view('conversation', conversation_id))['used'] and not payload.confirm_cleanup:
        raise HTTPException(409, 'RUNTIME_CLEANUP_CONFIRM_REQUIRED')
    owner_id = user.id
    await session.rollback()
    async def apply_binding():
        """门槛保持关闭期间提交绑定版本，再释放资源变更权限。"""
        next_revision = payload.expected_revision + 1
        updated = await session.scalar(update(Conversation).where(Conversation.id == conversation_id,
            Conversation.revision == payload.expected_revision).values(workspace_binding_id=payload.workspace_binding_id,
            revision=next_revision).returning(Conversation.revision))
        if updated is None:
            await session.rollback()
            raise HTTPException(409, 'CONVERSATION_REVISION_CONFLICT')
        pending = await event_store.append_event(session, conversation_id, 'conversation_updated',
            {'workspace_binding_id': payload.workspace_binding_id, 'revision': next_revision}, revision=next_revision)
        await session.commit()
        await event_store.publish_events(pending)
        current = await _owned_conversation(session, conversation_id, owner_id)
        member = await require_member(session, conversation_id, owner_id)
        return await response(session, current, member)
    if changed:
        async with manager.cleanup_scope('conversation', conversation_id, 'workspace_rebind', owner_id,
            resource_revision=payload.expected_revision, confirmation=payload.confirm_cleanup):
            return await apply_binding()
    async with manager.cleanup_lock:
        return await apply_binding()


@router.put("/{conversation_id}/members", response_model=ConversationResponse)
async def update_group_members(
    conversation_id: int,
    payload: ConversationMembersUpdate,
    user: Annotated[User, Depends(require_owner)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """按请求顺序替换群聊角色成员，并用 revision 阻止静默覆盖。

    Args:
        conversation_id：目标群聊 ID。
        payload：新角色顺序和调用方读取到的会话 revision。
        user：当前世界 Owner。
        session：请求级数据库会话。
    """
    conversation = await _owned_conversation(session, conversation_id, user.id)
    if conversation.deleted_at is not None:
        raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND")
    if conversation.type != "group":
        raise HTTPException(status_code=422, detail="GROUP_CHAT_REQUIRED")
    if len(payload.role_ids) < 2:
        raise HTTPException(status_code=422, detail="GROUP_CHAT_REQUIRES_MULTIPLE_ROLES")
    if len(payload.role_ids) != len(set(payload.role_ids)):
        raise HTTPException(status_code=422, detail="ROLE_NOT_AVAILABLE")
    roles = (await session.scalars(select(Role).where(
        Role.id.in_(payload.role_ids),
        Role.created_by == user.id,
        Role.active.is_(True),
        Role.deleted_at.is_(None),
    ))).all()
    if len(roles) != len(payload.role_ids):
        raise HTTPException(status_code=422, detail="ROLE_NOT_AVAILABLE")

    next_revision = payload.expected_revision + 1
    updated = await session.scalar(
        update(Conversation)
        .where(Conversation.id == conversation_id, Conversation.revision == payload.expected_revision)
        .values(
            revision=next_revision,
            orchestrator_enabled=(
                conversation.orchestrator_enabled
                and conversation.orchestrator_role_id in set(payload.role_ids)
            ),
            orchestrator_role_id=(
                conversation.orchestrator_role_id
                if conversation.orchestrator_role_id in set(payload.role_ids)
                else None
            ),
        )
        .returning(Conversation.revision)
    )
    if updated is None:
        await session.rollback()
        raise HTTPException(status_code=409, detail="CONVERSATION_REVISION_CONFLICT")

    await session.execute(delete(ConversationMember).where(
        ConversationMember.conversation_id == conversation_id,
        ConversationMember.member_type == "role",
    ))
    now = datetime.now(timezone.utc)
    for role_id in payload.role_ids:
        session.add(ConversationMember(
            conversation_id=conversation_id,
            member_type="role",
            member_id=role_id,
            joined_at=now,
        ))
    pending = await event_store.append_event(
        session,
        conversation_id,
        "member_updated",
        {"role_ids": payload.role_ids, "revision": next_revision},
        revision=next_revision,
    )
    await session.commit()
    await event_store.publish_events(pending)
    await session.refresh(conversation)
    member = await require_member(session, conversation_id, user.id)
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


async def _owned_conversation(session: AsyncSession, conversation_id: int, owner_id: int) -> Conversation:
    """解析由该 Owner 创建的会话，含已进回收站的会话。

    删除与恢复都要能操作回收站中的会话，因此这里不按 `deleted_at` 过滤；
    不存在或非本人创建一律返回 404，不泄露会话是否存在。

    Args:
        session：请求级数据库会话。
        conversation_id：目标会话 id。
        owner_id：当前 Owner 的用户 id。

    Raises:
        HTTPException：404 `CONVERSATION_NOT_FOUND`。
    """
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.created_by != owner_id:
        raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND")
    return conversation


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: int, user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)], confirm_cleanup: bool = False):
    """把会话移入回收站：只写删除时间，数据保留到保留期结束。

    会话立即从列表消失，但消息、成员和事件都还在，保留期内可以完整恢复；
    真正的级联删除由启动清理执行（见 `services/retention.py`）。
    重复删除按已在回收站处理，返回成功而不是报错。
    """
    conversation = await _owned_conversation(session, conversation_id, user.id)
    if conversation.deleted_at is None:
        from ..runtime.manager import manager
        from ..runtime.registry import quota_view
        if (await quota_view('conversation', conversation_id))['used'] and not confirm_cleanup:
            raise HTTPException(409, 'RUNTIME_CLEANUP_CONFIRM_REQUIRED')
        owner_id = user.id
        await session.rollback()
        async with manager.cleanup_scope('conversation', conversation_id, 'conversation_delete', owner_id, confirmation=confirm_cleanup):
            conversation = await _owned_conversation(session, conversation_id, owner_id)
            conversation.deleted_at = datetime.now(timezone.utc)
            await session.commit()


@router.post("/{conversation_id}/restore", response_model=ConversationResponse)
async def restore_conversation(conversation_id: int, user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """把会话从回收站恢复回列表。

    只清除删除时间；保留期内数据从未被物理删除，因此恢复不需要重建任何内容。
    已被启动清理物理删除的会话查不到，按 404 处理。

    Raises:
        HTTPException：404 `CONVERSATION_NOT_FOUND` 会话不存在、非本人或已过保留期被清理。
    """
    conversation = await _owned_conversation(session, conversation_id, user.id)
    conversation.deleted_at = None
    await session.commit()
    member = await require_member(session, conversation_id, user.id)
    return await response(session, conversation, member)
