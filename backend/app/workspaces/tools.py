"""W1a 工作区 LangChain 工具适配与每次调用二次授权。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.tools import FAILED_OUTPUT_PREFIX, REJECTED_OUTPUT_PREFIX, guard_tools
from ..db import SessionLocal
from ..models import (
    AgentExecution,
    Conversation,
    ExecutionWorkspace,
    Generation,
    Role,
    User,
    WorkspaceBinding,
)
from .files import WorkspaceFileError, WorkspaceFileService
from .paths import WorkspacePathError
from .service import binding_root

WORKSPACE_FILE_TOOLS = ("workspace_list", "workspace_read", "workspace_write")
WORKSPACE_TOOL_POLICY_VERSION = 1
WORKSPACE_TOOL_DESCRIPTIONS = {
    "workspace_list": "列出当前 execution 已绑定工作区内的目录；path 只能是相对路径。",
    "workspace_read": "读取当前 execution 已绑定工作区内的 UTF-8 文件，并取得 sha256 供后续安全更新。",
    "workspace_write": "在工作区新建 UTF-8 文件，或携带 workspace_read 返回的 expected_sha256 原子更新。",
}
_LEASE_LOCKS: dict[int, asyncio.Lock] = {}


class WorkspaceListInput(BaseModel):
    """目录列表工具输入。"""

    path: str = Field(default=".", max_length=1024)
    after_name: str | None = Field(default=None, max_length=255)
    limit: int = Field(default=200, ge=1, le=200)


class WorkspaceReadInput(BaseModel):
    """UTF-8 文件分块读取工具输入。"""

    path: str = Field(min_length=1, max_length=1024)
    offset_bytes: int = Field(default=0, ge=0)
    max_bytes: int = Field(default=65_536, ge=1, le=65_536)


class WorkspaceWriteInput(BaseModel):
    """UTF-8 文件乐观写入工具输入。"""

    path: str = Field(min_length=1, max_length=1024)
    content: str
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


async def _create_lease(
    session: AsyncSession,
    *,
    execution_id: str,
    conversation_id: int,
    role: Role,
    triggered_by_user_id: int | None,
    allow_dangerous: bool,
) -> tuple[ExecutionWorkspace, WorkspaceBinding] | None:
    """校验 W1a 暴露矩阵并原子创建 ready lease；不满足时隐藏全部工具。"""
    enabled = tuple(name for name in WORKSPACE_FILE_TOOLS if name in (role.builtin_tools_json or []))
    if not enabled or not allow_dangerous or triggered_by_user_id is None:
        return None
    conversation = await session.get(Conversation, conversation_id)
    user = await session.get(User, triggered_by_user_id)
    execution = await session.scalar(select(AgentExecution).where(
        AgentExecution.execution_id == execution_id,
    ))
    if (
        conversation is None
        or conversation.type != "single"
        or conversation.workspace_binding_id is None
        or user is None
        or not user.is_owner
        or role.created_by != user.id
        or execution is None
        or execution.status != "running"
        or execution.conversation_id != conversation.id
        or execution.role_id != role.id
    ):
        return None
    binding = await session.scalar(select(WorkspaceBinding).where(
        WorkspaceBinding.id == conversation.workspace_binding_id,
        WorkspaceBinding.created_by == user.id,
        WorkspaceBinding.active.is_(True),
        WorkspaceBinding.file_tools_enabled.is_(True),
    ))
    if binding is None:
        return None
    try:
        binding_root(binding)
    except WorkspacePathError:
        return None

    lock = _LEASE_LOCKS.setdefault(binding.id, asyncio.Lock())
    async with lock:
        existing = await session.scalar(select(ExecutionWorkspace).where(
            ExecutionWorkspace.execution_id == execution_id,
        ))
        if existing is not None:
            return (existing, binding) if existing.status == "ready" else None
        busy = await session.scalar(select(ExecutionWorkspace.id).where(
            ExecutionWorkspace.workspace_binding_id == binding.id,
            ExecutionWorkspace.status == "ready",
        ))
        if busy is not None:
            return None
        lease = ExecutionWorkspace(
            execution_id=execution_id,
            workspace_binding_id=binding.id,
            workspace_kind="managed_directory",
            root_path_snapshot=binding.root_path,
            status="ready",
            error_code=None,
            created_at=datetime.now(timezone.utc),
        )
        session.add(lease)
        await session.commit()
        await session.refresh(lease)
        return lease, binding


async def _authorized_service(
    *,
    execution_id: str,
    conversation_id: int,
    role_id: int,
    triggered_by_user_id: int,
    workspace_binding_id: int,
    root_path_snapshot: str,
    tool_name: str,
) -> WorkspaceFileService | None:
    """每次工具调用重新校验身份、绑定、能力和 lease 快照。"""
    async with SessionLocal() as session:
        execution = await session.scalar(select(AgentExecution).where(
            AgentExecution.execution_id == execution_id,
            AgentExecution.conversation_id == conversation_id,
            AgentExecution.role_id == role_id,
            AgentExecution.status == "running",
        ))
        generation = await session.get(Generation, execution.generation_id) if execution is not None else None
        conversation = await session.get(Conversation, conversation_id)
        role = await session.get(Role, role_id)
        user = await session.get(User, triggered_by_user_id)
        lease = await session.scalar(select(ExecutionWorkspace).where(
            ExecutionWorkspace.execution_id == execution_id,
            ExecutionWorkspace.workspace_binding_id == workspace_binding_id,
            ExecutionWorkspace.status == "ready",
        ))
        binding = await session.scalar(select(WorkspaceBinding).where(
            WorkspaceBinding.id == workspace_binding_id,
            WorkspaceBinding.created_by == triggered_by_user_id,
            WorkspaceBinding.active.is_(True),
            WorkspaceBinding.file_tools_enabled.is_(True),
        ))
        if (
            execution is None
            or conversation is None
            or conversation.type != "single"
            or conversation.deleted_at is not None
            or conversation.workspace_binding_id != workspace_binding_id
            or role is None
            or not role.active
            or role.deleted_at is not None
            or role.created_by != triggered_by_user_id
            or tool_name not in (role.builtin_tools_json or [])
            or user is None
            or not user.is_owner
            or lease is None
            or lease.root_path_snapshot != root_path_snapshot
            or binding is None
            or binding.root_path != root_path_snapshot
            or generation is None
            or generation.status != "running"
            or generation.stop_requested_at is not None
        ):
            return None
        try:
            root = binding_root(binding)
        except WorkspacePathError:
            return None
        return WorkspaceFileService(root=root, execution_id=execution_id)


def _error_result(code: str) -> str:
    """返回给模型的结构化文件失败，不泄露路径或宿主异常。"""
    body = json.dumps({"ok": False, "error_code": code}, separators=(",", ":"))
    return f"{FAILED_OUTPUT_PREFIX} {body}"


async def create_workspace_tools(
    session: AsyncSession,
    *,
    execution_id: str,
    conversation_id: int,
    role: Role,
    triggered_by_user_id: int | None,
    allow_dangerous: bool,
) -> list[BaseTool]:
    """为本次 execution 创建受 lease 和二次授权保护的 W1a 工具。"""
    created = await _create_lease(
        session,
        execution_id=execution_id,
        conversation_id=conversation_id,
        role=role,
        triggered_by_user_id=triggered_by_user_id,
        allow_dangerous=allow_dangerous,
    )
    if created is None or triggered_by_user_id is None:
        return []
    lease, _binding = created

    async def service(tool_name: str) -> WorkspaceFileService | None:
        """按本 execution 与具体工具名重新授权。"""
        return await _authorized_service(
            execution_id=execution_id,
            conversation_id=conversation_id,
            role_id=role.id,
            triggered_by_user_id=triggered_by_user_id,
            workspace_binding_id=lease.workspace_binding_id,
            root_path_snapshot=lease.root_path_snapshot,
            tool_name=tool_name,
        )

    async def workspace_list(path: str = ".", after_name: str | None = None, limit: int = 200) -> str:
        """列出绑定工作区内一个目录页，不跟随符号链接。"""
        authorized = await service("workspace_list")
        if authorized is None:
            return f"{REJECTED_OUTPUT_PREFIX} WORKSPACE_TOOL_NOT_AVAILABLE"
        try:
            return authorized.json_result(await authorized.list(path, after_name=after_name, limit=limit))
        except WorkspaceFileError as exc:
            return _error_result(exc.code)

    async def workspace_read(path: str, offset_bytes: int = 0, max_bytes: int = 65_536) -> str:
        """读取绑定工作区内 UTF-8 普通文件的一段并返回全文件 hash。"""
        authorized = await service("workspace_read")
        if authorized is None:
            return f"{REJECTED_OUTPUT_PREFIX} WORKSPACE_TOOL_NOT_AVAILABLE"
        try:
            return authorized.json_result(
                await authorized.read(path, offset_bytes=offset_bytes, max_bytes=max_bytes)
            )
        except WorkspaceFileError as exc:
            return _error_result(exc.code)

    async def workspace_write(path: str, content: str, expected_sha256: str | None = None) -> str:
        """exclusive 新建或按 expected hash 原子替换 UTF-8 文件。"""
        authorized = await service("workspace_write")
        if authorized is None:
            return f"{REJECTED_OUTPUT_PREFIX} WORKSPACE_TOOL_NOT_AVAILABLE"
        try:
            return authorized.json_result(
                await authorized.write(path, content, expected_sha256=expected_sha256)
            )
        except WorkspaceFileError as exc:
            return _error_result(exc.code)

    raw: dict[str, BaseTool] = {
        "workspace_list": StructuredTool.from_function(
            coroutine=workspace_list,
            name="workspace_list",
            description=WORKSPACE_TOOL_DESCRIPTIONS["workspace_list"],
            args_schema=WorkspaceListInput,
        ),
        "workspace_read": StructuredTool.from_function(
            coroutine=workspace_read,
            name="workspace_read",
            description=WORKSPACE_TOOL_DESCRIPTIONS["workspace_read"],
            args_schema=WorkspaceReadInput,
        ),
        "workspace_write": StructuredTool.from_function(
            coroutine=workspace_write,
            name="workspace_write",
            description=WORKSPACE_TOOL_DESCRIPTIONS["workspace_write"],
            args_schema=WorkspaceWriteInput,
        ),
    }
    selected = [raw[name] for name in WORKSPACE_FILE_TOOLS if name in (role.builtin_tools_json or [])]
    return guard_tools(selected, allow_dangerous=allow_dangerous)


async def workspace_tool_policy(
    session: AsyncSession,
    *,
    conversation: Conversation,
    role: Role,
    triggered_by_user_id: int | None,
) -> dict[str, object]:
    """返回 ContextBuilder 应计入预算和 hash 的实际 W1a 暴露策略。"""
    exposed = [name for name in WORKSPACE_FILE_TOOLS if name in (role.builtin_tools_json or [])]
    if (
        not exposed
        or triggered_by_user_id != role.created_by
        or conversation.type != "single"
        or conversation.workspace_binding_id is None
    ):
        return {"version": WORKSPACE_TOOL_POLICY_VERSION, "exposed_tools": []}
    user = await session.get(User, triggered_by_user_id)
    binding = await session.scalar(select(WorkspaceBinding).where(
        WorkspaceBinding.id == conversation.workspace_binding_id,
        WorkspaceBinding.created_by == triggered_by_user_id,
        WorkspaceBinding.active.is_(True),
        WorkspaceBinding.file_tools_enabled.is_(True),
    ))
    if user is None or not user.is_owner or binding is None:
        return {"version": WORKSPACE_TOOL_POLICY_VERSION, "exposed_tools": []}
    try:
        binding_root(binding)
    except WorkspacePathError:
        return {"version": WORKSPACE_TOOL_POLICY_VERSION, "exposed_tools": []}
    return {
        "version": WORKSPACE_TOOL_POLICY_VERSION,
        "workspace_binding_id": binding.id,
        "workspace_kind": binding.workspace_kind,
        "exposed_tools": [
            {"name": name, "description": WORKSPACE_TOOL_DESCRIPTIONS[name]}
            for name in WORKSPACE_FILE_TOOLS if name in exposed
        ],
    }


async def retain_execution_workspace(execution_id: str) -> None:
    """将 execution 的 managed directory lease 幂等收口为 retained。"""
    async with SessionLocal() as session:
        lease = await session.scalar(select(ExecutionWorkspace).where(
            ExecutionWorkspace.execution_id == execution_id,
            ExecutionWorkspace.status == "ready",
        ))
        if lease is None:
            return
        lease.status = "retained"
        lease.ended_at = datetime.now(timezone.utc)
        await session.commit()
