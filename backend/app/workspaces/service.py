"""Workspace Binding 可用性、归属与会话绑定共享规则。"""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Conversation, ExecutionWorkspace, WorkspaceBinding
from .paths import WorkspacePathError, resolve_workspace_root


def binding_root(binding: WorkspaceBinding) -> Path:
    """重新 canonicalize Owner 登记的绝对根，不能因数据库有值就跳过复核。"""
    return resolve_workspace_root(binding.root_path)


async def binding_availability(session: AsyncSession, binding: WorkspaceBinding) -> str:
    """返回当前可展示的 available/unavailable/busy/disabled 状态。"""
    if not binding.active:
        return "disabled"
    try:
        root = binding_root(binding)
    except WorkspacePathError:
        return "unavailable"
    if not root.is_dir():
        return "unavailable"
    busy = await session.scalar(
        select(func.count()).select_from(ExecutionWorkspace).where(
            ExecutionWorkspace.workspace_binding_id == binding.id,
            ExecutionWorkspace.status == "ready",
        )
    )
    return "busy" if busy else "available"


async def owned_workspace(
    session: AsyncSession, workspace_id: int, owner_id: int,
) -> WorkspaceBinding:
    """解析当前 Owner 工作区，不存在或越权统一隐藏为 404。"""
    binding = await session.scalar(select(WorkspaceBinding).where(
        WorkspaceBinding.id == workspace_id,
        WorkspaceBinding.created_by == owner_id,
    ))
    if binding is None:
        raise HTTPException(status_code=404, detail="WORKSPACE_NOT_FOUND")
    return binding


async def available_workspace(
    session: AsyncSession, workspace_id: int, owner_id: int,
) -> WorkspaceBinding:
    """解析可以绑定新 single 会话的工作区。"""
    binding = await owned_workspace(session, workspace_id, owner_id)
    availability = await binding_availability(session, binding)
    if availability == "busy":
        raise HTTPException(status_code=409, detail="WORKSPACE_BUSY")
    if availability != "available":
        raise HTTPException(status_code=409, detail="WORKSPACE_UNAVAILABLE")
    return binding


async def conversation_binding_count(session: AsyncSession, workspace_id: int) -> int:
    """返回仍引用该 binding 的会话数量，用于 Owner 安全摘要。"""
    value = await session.scalar(
        select(func.count()).select_from(Conversation).where(
            Conversation.workspace_binding_id == workspace_id,
        )
    )
    return int(value or 0)
