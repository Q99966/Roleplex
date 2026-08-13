from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import ModelConfig, Role, User
from ..schemas import RoleCreate, RoleResponse
from ..security import get_current_user, require_owner

router = APIRouter(prefix="/api/roles", tags=["roles"])


def to_response(role: Role) -> RoleResponse:
    """将 Owner 拥有的 ORM 角色转换为公开 API 表示。"""
    return RoleResponse(
        id=role.id, name=role.name, avatar=role.avatar, description=role.description,
        tags=role.tags_json or [], system_prompt=role.system_prompt,
        model_config_id=role.model_config_id, model_name=role.model_name,
        params=role.params_json or {}, skills=role.skills_json or [],
        builtin_tools=role.builtin_tools_json or [], mcp_servers=role.mcp_servers_json or [],
        active=role.active, created_at=role.created_at, updated_at=role.updated_at,
    )


async def owned_model_config(session: AsyncSession, owner_id: int, config_id: int) -> ModelConfig:
    """解析模型配置，并强制执行同一 Owner 的资源隔离。"""
    config = await session.scalar(select(ModelConfig).where(ModelConfig.id == config_id, ModelConfig.created_by == owner_id))
    if not config:
        raise HTTPException(status_code=422, detail="MODEL_CONFIG_NOT_FOUND")
    return config


@router.get("", response_model=list[RoleResponse])
async def list_roles(user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_session)]):
    """仅列出当前认证用户拥有的角色。"""
    roles = (await session.scalars(select(Role).where(Role.created_by == user.id).order_by(Role.updated_at.desc()))).all()
    return [to_response(role) for role in roles]


@router.post("", response_model=RoleResponse, status_code=201)
async def create_role(payload: RoleCreate, user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """校验模型配置归属后，创建由 Owner 管理的角色。"""
    await owned_model_config(session, user.id, payload.model_config_id)
    now = datetime.now(timezone.utc)
    role = Role(
        created_by=user.id, name=payload.name, avatar=payload.avatar, description=payload.description,
        tags_json=payload.tags, system_prompt=payload.system_prompt, model_config_id=payload.model_config_id,
        model_name=payload.model_name, params_json=payload.params, skills_json=payload.skills,
        builtin_tools_json=payload.builtin_tools, mcp_servers_json=payload.mcp_servers,
        mcp_tools_cache_json=[], active=True, created_at=now, updated_at=now,
    )
    session.add(role)
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
    role = await session.scalar(select(Role).where(Role.id == role_id, Role.created_by == user.id))
    if not role:
        raise HTTPException(status_code=404, detail="ROLE_NOT_FOUND")
    await owned_model_config(session, user.id, payload.model_config_id)
    role.name = payload.name
    role.avatar = payload.avatar
    role.description = payload.description
    role.tags_json = payload.tags
    role.system_prompt = payload.system_prompt
    role.model_config_id = payload.model_config_id
    role.model_name = payload.model_name
    role.params_json = payload.params
    role.skills_json = payload.skills
    role.builtin_tools_json = payload.builtin_tools
    role.mcp_servers_json = payload.mcp_servers
    role.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(role)
    return to_response(role)


@router.delete("/{role_id}", status_code=204)
async def delete_role(role_id: int, user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """删除 Owner 拥有的角色；数据库约束保护仍被引用的配置。"""
    role = await session.scalar(select(Role).where(Role.id == role_id, Role.created_by == user.id))
    if not role:
        raise HTTPException(status_code=404, detail="ROLE_NOT_FOUND")
    await session.delete(role)
    await session.commit()
