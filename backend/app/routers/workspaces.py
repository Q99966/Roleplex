"""当前 World Owner 工作区登记、复核与能力管理接口。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models import Conversation, ExecutionWorkspace, User, WorkspaceBinding
from ..realtime import store as event_store
from ..schemas import WorkspaceCreate, WorkspaceResponse, WorkspaceUpdate
from ..security.tokens import require_owner
from ..workspaces.catalog import WORKSPACE_FILE_TOOLS
from ..workspaces.paths import WorkspacePathError, resolve_workspace_root
from ..workspaces.service import (
    binding_availability,
    binding_root,
    conversation_binding_count,
    owned_workspace,
)

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def _path_http_error(exc: WorkspacePathError, *, directory: bool = False) -> HTTPException:
    """把路径层稳定错误转换为公开 HTTP 分类。"""
    code = exc.code
    if directory and code in {"WORKSPACE_FILE_NOT_FOUND", "WORKSPACE_ROOT_NOT_AVAILABLE"}:
        return HTTPException(status_code=404, detail="WORKSPACE_DIRECTORY_NOT_FOUND")
    status = 409 if code == "WORKSPACE_ROOT_NOT_AVAILABLE" else 422
    return HTTPException(status_code=status, detail=code)


async def _response(session: AsyncSession, binding: WorkspaceBinding) -> WorkspaceResponse:
    """构造只由 Owner 接口返回、包含规范绝对根的工作区响应。"""
    return WorkspaceResponse(
        id=binding.id,
        display_name=binding.display_name,
        root_path=binding.root_path,
        workspace_kind=binding.workspace_kind,
        file_tools_enabled=binding.file_tools_enabled,
        basic_commands_enabled=binding.basic_commands_enabled,
        shell_enabled=binding.shell_enabled,
        active=binding.active,
        availability=await binding_availability(session, binding),
        last_validated_at=binding.last_validated_at,
        bound_conversation_count=await conversation_binding_count(session, binding.id),
        created_at=binding.created_at,
        updated_at=binding.updated_at,
    )


@router.get("/capabilities")
async def capabilities(_owner: Annotated[User, Depends(require_owner)]) -> dict:
    """列出当前 World 的 W1a 能力；Workspace 根由 Owner 逐条配置。"""
    from ..workspaces.shell import shell_configuration
    from ..workspaces.commands import WorkspaceCommandError
    try:
        shell = shell_configuration()
    except WorkspaceCommandError:
        shell = None
    return {
        "world_name": settings.world_name,
        "workspace_kinds": ["managed_directory"],
        "file_tools": list(WORKSPACE_FILE_TOOLS),
        "basic_commands_available": True,
        "shell_available": shell is not None,
        "shell_kind": shell['shell_kind'] if shell else None,
        "shell_approval_mode": 'per_call',
        "shell_timeout_seconds": settings.workspace_command_timeout_seconds,
        "shell_output_bytes": settings.workspace_command_output_bytes,
    }


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    user: Annotated[User, Depends(require_owner)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """按稳定 ID 列出当前 World Owner 登记的全部工作区。"""
    bindings = (await session.scalars(select(WorkspaceBinding).where(
        WorkspaceBinding.created_by == user.id,
    ).order_by(WorkspaceBinding.id.asc()))).all()
    return [await _response(session, binding) for binding in bindings]


@router.post("", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    payload: WorkspaceCreate,
    user: Annotated[User, Depends(require_owner)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """登记 Owner 输入的既有绝对目录，或创建一个精确空目录。"""
    try:
        target = resolve_workspace_root(payload.root_path, require_exists=not payload.create_directory)
    except WorkspacePathError as exc:
        raise _path_http_error(exc, directory=True) from None
    canonical_root = str(target)

    duplicate_name = await session.scalar(select(WorkspaceBinding.id).where(
        WorkspaceBinding.created_by == user.id,
        WorkspaceBinding.display_name == payload.display_name,
        WorkspaceBinding.active.is_(True),
    ))
    if duplicate_name is not None:
        raise HTTPException(status_code=409, detail="WORKSPACE_NAME_CONFLICT")
    duplicate_path = await session.scalar(select(WorkspaceBinding.id).where(
        WorkspaceBinding.root_path == canonical_root,
    ))
    if duplicate_path is not None:
        raise HTTPException(status_code=409, detail="WORKSPACE_PATH_CONFLICT")

    if payload.create_directory:
        if target.exists() or target.is_symlink():
            raise HTTPException(status_code=409, detail="WORKSPACE_DIRECTORY_EXISTS")
        if not target.parent.is_dir():
            raise HTTPException(status_code=404, detail="WORKSPACE_DIRECTORY_NOT_FOUND")
        try:
            target.mkdir()
        except FileExistsError:
            raise HTTPException(status_code=409, detail="WORKSPACE_DIRECTORY_EXISTS") from None
    else:
        if not payload.acknowledge_existing_content:
            raise HTTPException(status_code=422, detail="WORKSPACE_EXISTING_CONTENT_ACK_REQUIRED")
        if not target.exists() or not target.is_dir() or target.is_symlink():
            raise HTTPException(status_code=404, detail="WORKSPACE_DIRECTORY_NOT_FOUND")

    now = datetime.now(timezone.utc)
    binding = WorkspaceBinding(
        created_by=user.id,
        display_name=payload.display_name,
        root_path=canonical_root,
        workspace_kind="managed_directory",
        file_tools_enabled=False,
        basic_commands_enabled=False,
        shell_enabled=False,
        active=True,
        last_validated_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(binding)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # 并发创建无法可靠从约束名跨数据库分类；重新读取后返回稳定冲突。
        if await session.scalar(select(WorkspaceBinding.id).where(
            WorkspaceBinding.root_path == canonical_root,
        )) is not None:
            raise HTTPException(status_code=409, detail="WORKSPACE_PATH_CONFLICT") from None
        raise HTTPException(status_code=409, detail="WORKSPACE_NAME_CONFLICT") from None
    await session.refresh(binding)
    return await _response(session, binding)


@router.post("/{workspace_id}/validate", response_model=WorkspaceResponse)
async def validate_workspace(
    workspace_id: int,
    user: Annotated[User, Depends(require_owner)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """重新解析 binding 目录并记录最近成功复核时间。"""
    binding = await owned_workspace(session, workspace_id, user.id)
    try:
        root = binding_root(binding)
    except WorkspacePathError as exc:
        raise _path_http_error(exc) from None
    if not root.is_dir():
        raise HTTPException(status_code=409, detail="WORKSPACE_UNAVAILABLE")
    binding.last_validated_at = datetime.now(timezone.utc)
    binding.updated_at = binding.last_validated_at
    await session.commit()
    return await _response(session, binding)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: int,
    payload: WorkspaceUpdate,
    user: Annotated[User, Depends(require_owner)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """启用/禁用 binding、文件与结构化命令能力。

    Args:
        workspace_id：当前 World 的绑定 ID。
        payload：Owner 提交的能力变更。
        user：已通过 Owner 鉴权的用户。
        session：本次短事务数据库会话。
    """
    from ..runtime.manager import manager
    from ..runtime.registry import quota_view
    owner_id = user.id
    await owned_workspace(session, workspace_id, owner_id)
    if payload.active is False and (await quota_view('workspace', workspace_id))['used'] and not payload.confirm_cleanup:
        raise HTTPException(409, 'RUNTIME_CLEANUP_CONFIRM_REQUIRED')
    await session.rollback()
    async def apply_update():
        """在停止门槛内应用停用；其他开关仍是独立能力，不隐式停止服务。"""
        binding = await owned_workspace(session, workspace_id, owner_id)
        if payload.active is not None:
            if payload.active:
                try:
                    binding_root(binding)
                except WorkspacePathError as exc:
                    raise _path_http_error(exc) from None
            binding.active = payload.active
        if payload.file_tools_enabled is not None:
            binding.file_tools_enabled = payload.file_tools_enabled
        if payload.basic_commands_enabled is not None:
            binding.basic_commands_enabled = payload.basic_commands_enabled
        if payload.shell_enabled is not None:
            if payload.shell_enabled:
                from ..workspaces.shell import shell_configuration
                from ..workspaces.commands import WorkspaceCommandError
                try:
                    shell_configuration()
                except WorkspaceCommandError as exc:
                    raise HTTPException(409, exc.code) from None
            binding.shell_enabled = payload.shell_enabled
        binding.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return await _response(session, binding)
    if payload.active is False:
        async with manager.cleanup_scope('workspace', workspace_id, 'workspace_disabled', owner_id, confirmation=payload.confirm_cleanup):
            return await apply_update()
    async with manager.cleanup_lock:
        return await apply_update()


@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(
    workspace_id: int,
    user: Annotated[User, Depends(require_owner)],
    session: Annotated[AsyncSession, Depends(get_session)],
    confirm_cleanup: bool = False,
):
    """只解除数据库登记；物理目录及其内容永不由本端点删除。"""
    from ..runtime.manager import manager
    from ..runtime.registry import quota_view
    owner_id = user.id
    await owned_workspace(session, workspace_id, owner_id)
    if (await quota_view('workspace', workspace_id))['used'] and not confirm_cleanup:
        raise HTTPException(409, 'RUNTIME_CLEANUP_CONFIRM_REQUIRED')
    await session.rollback()
    async with manager.cleanup_scope('workspace', workspace_id, 'workspace_delete', owner_id, confirmation=confirm_cleanup):
        active_lease = await session.scalar(select(ExecutionWorkspace.id).where(
            ExecutionWorkspace.workspace_binding_id == workspace_id, ExecutionWorkspace.status == 'ready'))
        if active_lease is not None:
            raise HTTPException(409, 'WORKSPACE_BUSY')
        conversations = (await session.scalars(select(Conversation).where(
            Conversation.workspace_binding_id == workspace_id).order_by(Conversation.id))).all()
        pending_events = []
        for conversation in conversations:
            conversation.workspace_binding_id = None
            conversation.revision += 1
            pending_events.append(await event_store.append_event(session, conversation.id, 'conversation_updated',
                {'workspace_binding_id': None, 'revision': conversation.revision}, revision=conversation.revision))
        await session.execute(delete(WorkspaceBinding).where(WorkspaceBinding.id == workspace_id))
        await session.commit()
        await event_store.publish_events(*pending_events)
