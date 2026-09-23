from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from ..db import get_session
from ..config import settings
from ..models import ModelConfig, Role, User
from ..schemas import RoleCreate, RoleResponse
from ..security.tokens import get_current_user, require_owner

router = APIRouter(prefix="/api/roles", tags=["roles"])


def to_response(role: Role) -> RoleResponse:
    """将 Owner 拥有的 ORM 角色转换为公开 API 表示。

    墓碑角色同样返回：客户端渲染历史消息时需要按 id 查出原名称与头像，
    通过 `deleted_at` 区分并做降级展示，而不是显示成匿名 Agent。
    """
    return RoleResponse(
        id=role.id, name=role.name, managed_kind=role.managed_kind, avatar=role.avatar, description=role.description,
        tags=role.tags_json or [], system_prompt=role.system_prompt, revision=role.revision,
        model_config_id=role.model_config_id, model_name=role.model_name,
        context_window_tokens=role.context_window_tokens,
        context_window_ceiling_tokens=settings.max_context_tokens,
        effective_context_window_tokens=min(role.context_window_tokens, settings.max_context_tokens),
        params=role.params_json or {}, skills=role.skills_json or [],
        builtin_tools=role.builtin_tools_json or [], mcp_servers=role.mcp_servers_json or [],
        active=role.active, deleted_at=role.deleted_at,
        created_at=role.created_at, updated_at=role.updated_at,
    )


async def editable_role(session: AsyncSession, role_id: int, owner_id: int) -> Role:
    """解析可编辑的角色；墓碑与他人角色一律按不存在处理。

    Args:
        session：请求级数据库会话。
        role_id：目标角色 id。
        owner_id：当前 Owner 的用户 id，用于资源隔离。

    Raises:
        HTTPException：404 `ROLE_NOT_FOUND`，不区分"不存在"、"非本人"和"已删除"。
    """
    role = await session.scalar(select(Role).where(
        Role.id == role_id, Role.created_by == owner_id, Role.deleted_at.is_(None),
    ))
    if not role:
        raise HTTPException(status_code=404, detail="ROLE_NOT_FOUND")
    if role.managed_kind:
        raise HTTPException(409, 'ROLE_MANAGED_IDENTITY')
    return role


async def owned_model_config(session: AsyncSession, owner_id: int, config_id: int) -> ModelConfig:
    """解析模型配置，并强制执行同一 Owner 的资源隔离。"""
    config = await session.scalar(select(ModelConfig).where(ModelConfig.id == config_id, ModelConfig.created_by == owner_id))
    if not config:
        raise HTTPException(status_code=422, detail="MODEL_CONFIG_NOT_FOUND")
    return config


async def flush_role(session: AsyncSession):
    """约束竞争返回安全业务码，避免 SQL 异常连同角色 Prompt 进入日志。"""
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, 'ROLE_WRITE_CONFLICT') from None


@router.get("", response_model=list[RoleResponse])
async def list_roles(user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_session)], include_managed: bool = False):
    """仅列出当前认证用户拥有的角色。"""
    roles = (await session.scalars(select(Role).where(Role.created_by == user.id,
        True if include_managed else Role.managed_kind.is_(None)).order_by(Role.updated_at.desc()))).all()
    return [to_response(role) for role in roles]


@router.post("", response_model=RoleResponse, status_code=201)
async def create_role(payload: RoleCreate, user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """校验模型配置归属后，创建由 Owner 管理的角色。"""
    await owned_model_config(session, user.id, payload.model_config_id)
    now = datetime.now(timezone.utc)
    role = Role(
        created_by=user.id, name=payload.name, avatar=payload.avatar, description=payload.description,
        tags_json=payload.tags, system_prompt=payload.system_prompt, model_config_id=payload.model_config_id,
        model_name=payload.model_name, context_window_tokens=payload.context_window_tokens,
        params_json=payload.params, skills_json=payload.skills,
        builtin_tools_json=payload.builtin_tools, mcp_servers_json=payload.mcp_servers,
        mcp_tools_cache_json=[], active=payload.active if payload.active is not None else True, created_at=now, updated_at=now,
    )
    session.add(role)
    await flush_role(session)
    await session.commit()
    await session.refresh(role)
    return to_response(role)


@router.get("/{role_id}", response_model=RoleResponse)
async def get_role(role_id: int, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_session)]):
    """仅当角色属于请求用户时返回角色信息。"""
    role = await session.scalar(select(Role).where(Role.id == role_id, Role.created_by == user.id))
    if not role:
        raise HTTPException(status_code=404, detail="ROLE_NOT_FOUND")
    return to_response(role)


@router.put("/{role_id}", response_model=RoleResponse)
async def update_role(role_id: int, payload: RoleCreate, user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """替换 Owner 拥有的角色，同时保持模型配置隔离。"""
    from ..workflows import service
    # 撤权与派发共用短控制边界，防止旧状态覆盖撤销标记。
    async with service.control_lock:
        role = await editable_role(session, role_id, user.id)
        if payload.expected_revision is not None and payload.expected_revision != role.revision:
            raise HTTPException(409, 'ROLE_REVISION_CONFLICT')
        await owned_model_config(session, user.id, payload.model_config_id)
        if payload.active is not None: role.active = payload.active
        role.name = payload.name
        role.avatar = payload.avatar
        role.description = payload.description
        role.tags_json = payload.tags
        role.system_prompt = payload.system_prompt
        role.model_config_id = payload.model_config_id
        role.model_name = payload.model_name
        role.context_window_tokens = payload.context_window_tokens
        role.params_json = payload.params
        # 兼容没有这些编辑器的客户端；只有显式字段（包括 []）才替换现有配置。
        if 'skills' in payload.model_fields_set:
            role.skills_json = payload.skills
        role.builtin_tools_json = payload.builtin_tools
        if 'mcp_servers' in payload.model_fields_set:
            role.mcp_servers_json = payload.mcp_servers
        role.revision += 1
        role.updated_at = datetime.now(timezone.utc)
        await flush_role(session)
        pending = []
        world_revoked = None
        if payload.active is False:
            from ..workflows.coordination import revoke_role_runs
            pending = await revoke_role_runs(session, role_id)
            from ..world_orchestrator.service import invalidate_role_in_session
            world_revoked = await invalidate_role_in_session(session, role_id)
        await session.commit()
    from ..realtime.store import publish_events
    if pending: await publish_events(*pending)
    from ..world_orchestrator.service import finish_revocation
    await finish_revocation(world_revoked)
    await session.refresh(role)
    return to_response(role)


@router.delete("/{role_id}", status_code=204)
async def delete_role(role_id: int, user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """把角色置为墓碑：保留身份信息，清除全部可用配置。

    不做物理删除——消息只按 `sender_id` 记录发送者，角色行一旦消失，历史里
    就再也查不出"谁说的"。因此保留 id、名称、头像与删除时间，清空系统提示词、
    模型绑定、技能与 MCP 配置，并置为停用，使它不能再被选进会话或触发生成。

    角色重名约束是"只约束未删除角色"的部分唯一索引，所以墓碑保留原名的同时
    不会挡住立刻新建同名角色。
    """
    from ..workflows import service
    # 撤权与派发共用短控制边界，防止旧状态覆盖撤销标记。
    async with service.control_lock:
        role = await editable_role(session, role_id, user.id)
        role.deleted_at = datetime.now(timezone.utc)
        role.updated_at = role.deleted_at
        role.active = False
        # 清除全部可用配置：墓碑只保留身份，不保留任何能驱动模型或工具的内容。
        role.system_prompt = ""
        role.model_config_id = None
        role.model_name = ""
        role.context_window_tokens = 200_000
        role.description = None
        role.tags_json = []
        role.params_json = {}
        role.skills_json = []
        role.builtin_tools_json = []
        role.mcp_servers_json = []
        role.mcp_tools_cache_json = []
        role.revision += 1
        from ..workflows.coordination import revoke_role_runs
        pending = await revoke_role_runs(session, role_id)
        from ..world_orchestrator.service import invalidate_role_in_session
        world_revoked = await invalidate_role_in_session(session, role_id)
        await session.commit()
    from ..realtime.store import publish_events
    if pending: await publish_events(*pending)
    from ..world_orchestrator.service import finish_revocation
    await finish_revocation(world_revoked)
