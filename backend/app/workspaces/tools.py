"""W1a/W1b 工作区工具适配、独立能力开关与每次调用二次授权。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, ConfigDict
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
from .commands import WorkspaceCommandError, WorkspaceCommandService

WORKSPACE_FILE_TOOLS = ("workspace_list", "workspace_read", "workspace_write")
WORKSPACE_TOOLS = (*WORKSPACE_FILE_TOOLS, 'workspace_run_command', 'workspace_run_shell')
WORKSPACE_TOOL_POLICY_VERSION = 3
WORKSPACE_TOOL_DESCRIPTIONS = {
    "workspace_list": "列出当前 execution 已绑定工作区内的目录；path 只能是相对路径。",
    "workspace_read": "读取当前 execution 已绑定工作区内的 UTF-8 文件，并取得 sha256 供后续安全更新。",
    "workspace_write": "在工作区新建 UTF-8 文件，或携带 workspace_read 返回的 expected_sha256 原子更新。",
    "workspace_run_command": "在绑定工作区运行固定命令 pwd/list/read/count；args 仅接受相对 path，不接受 Shell 或任意 argv。",
    "workspace_run_shell": "请求执行 Shell 脚本，必须等待 Owner 对本次脚本批准；只接受 script，不允许 cwd、环境或审批参数。",
}
_LEASE_LOCKS: dict[int, asyncio.Lock] = {}
_COMMAND_CALL_LOCKS: dict[int, asyncio.Lock] = {}


def _tool_description(name: str) -> str:
    """使模型工具 schema 与 ContextBuilder 的策略 hash 使用同一能力描述。

    Args:
        name：实际内置工具名。
    """
    description = WORKSPACE_TOOL_DESCRIPTIONS[name]
    if name == 'workspace_run_shell':
        from .shell import shell_configuration
        try:
            config = shell_configuration()
        except WorkspaceCommandError:
            return description
        description += (f" 当前解释器为 {config['shell_kind']}；脚本最多 65536 UTF-8 字节，"
                        f"执行超时 {config['timeout_seconds']} 秒，输出保留 {config['output_bytes']} 字节。")
    return description


def _enabled_tools(role: Role, binding: WorkspaceBinding) -> list[str]:
    """组合角色与工作区的独立文件/命令开关。

    Args:
        role：本 execution 的当前角色。
        binding：当前 World 的工作区记录。
    """
    from .shell import shell_configuration
    try:
        shell_configuration()
        shell_available = True
    except WorkspaceCommandError:
        shell_available = False
    return [name for name in WORKSPACE_TOOLS if name in (role.builtin_tools_json or []) and (
        (binding.shell_enabled and shell_available) if name == 'workspace_run_shell'
        else binding.basic_commands_enabled if name == 'workspace_run_command' else binding.file_tools_enabled)]


class WorkspaceShellInput(BaseModel):
    """模型仅能提交脚本；真实调用身份由防腐层上下文提供。"""

    model_config = ConfigDict(extra='forbid')
    script: str = Field(min_length=1, max_length=65536)


class WorkspaceCommandInput(BaseModel):
    """结构化命令输入；命令专用字段由执行层校验，错误不回显原始参数。"""

    model_config = ConfigDict(extra='forbid')
    command: str = Field(min_length=1, max_length=32)
    args: dict = Field(default_factory=dict)


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
    """校验文件/命令暴露矩阵并创建 ready lease。

    Args:
        session：创建 lease 的短事务会话。
        execution_id：本轮持久 execution。
        conversation_id：本轮 single 会话。
        role：当前执行角色。
        triggered_by_user_id：调度器绑定的触发者。
        allow_dangerous：链路是否获准使用 dangerous 工具。
    """
    enabled = tuple(name for name in WORKSPACE_TOOLS if name in (role.builtin_tools_json or []))
    if not enabled or not role.active or role.deleted_at is not None or not allow_dangerous or triggered_by_user_id is None:
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
    ))
    if binding is None or not _enabled_tools(role, binding):
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
    """每次工具调用重新校验身份、绑定、独立能力和 lease 快照。

    Args:
        execution_id：本轮持久 execution。
        conversation_id：本轮会话 ID。
        role_id：执行角色 ID。
        triggered_by_user_id：调度器绑定的原始触发者。
        workspace_binding_id：lease 绑定的当前 World 工作区。
        root_path_snapshot：创建工具时捕获的规范根，不接受模型覆盖。
        tool_name：用于重新检查角色与工作区开关的工具名。
    """
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
            or tool_name not in _enabled_tools(role, binding)
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
        if str(root) != root_path_snapshot:
            return None
        return WorkspaceFileService(root=root, execution_id=execution_id)


def _error_result(code: str) -> str:
    """返回给模型的结构化文件失败，不泄露路径或宿主异常。"""
    body = json.dumps({"ok": False, "error_code": code}, separators=(",", ":"))
    return f"{FAILED_OUTPUT_PREFIX} {body}"


def _command_result(result: dict) -> str:
    """区分命令政策拒绝与实际进程失败，不把拒绝标成执行异常。

    Args:
        result：命令服务结果或稳定错误对象。
    """
    body = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
    code = result.get('error_code')
    if not code:
        return body
    prefix = FAILED_OUTPUT_PREFIX if code in {'COMMAND_FAILED', 'COMMAND_TIMEOUT'} else REJECTED_OUTPUT_PREFIX
    return f'{prefix} {body}'


async def create_workspace_tools(
    session: AsyncSession,
    *,
    execution_id: str,
    conversation_id: int,
    role: Role,
    triggered_by_user_id: int | None,
    allow_dangerous: bool,
) -> list[BaseTool]:
    """为本次 execution 创建受 lease 和二次授权保护的文件/命令工具。

    Args:
        session：创建 lease 的短事务会话。
        execution_id：本轮持久 execution。
        conversation_id：本轮 single 会话。
        role：当前执行角色。
        triggered_by_user_id：调度器绑定的触发者。
        allow_dangerous：链路是否允许 dangerous 工具。
    """
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

    async def workspace_run_command(command: str, args: dict | None = None) -> str:
        """在获得串行槽后重新鉴权并执行固定命令。

        Args:
            command：服务端登记的命令 ID。
            args：只允许该命令的专用参数。
        """
        async with _COMMAND_CALL_LOCKS.setdefault(lease.workspace_binding_id, asyncio.Lock()):
            authorized = await service('workspace_run_command')
            if authorized is None:
                return f'{REJECTED_OUTPUT_PREFIX} WORKSPACE_TOOL_NOT_AVAILABLE'
            try:
                result = await WorkspaceCommandService(
                    root=authorized.root, execution_id=execution_id,
                ).run(command, args if args is not None else {})
                return _command_result(result)
            except WorkspaceCommandError as exc:
                return _command_result({'ok': False, 'error_code': exc.code})

    async def workspace_run_shell(script: str) -> str:
        """串行等待本次批准，执行前仍复核租用和身份。

        Args:
            script：模型脚本，不能覆盖宿主执行参数。
        """
        from ..agent.tool_context import tool_call_id
        from .approvals import request_and_run
        async with _COMMAND_CALL_LOCKS.setdefault(lease.workspace_binding_id, asyncio.Lock()):
            try:
                return _command_result(await request_and_run(script=script, execution_id=execution_id,
                    conversation_id=conversation_id, role_id=role.id, owner_id=triggered_by_user_id,
                    workspace_binding_id=lease.workspace_binding_id, root_path=lease.root_path_snapshot,
                    tool_call_id=tool_call_id.get()))
            except WorkspaceCommandError as exc:
                return _command_result({'ok': False, 'error_code': exc.code})

    raw: dict[str, BaseTool] = {
        'workspace_run_shell': StructuredTool.from_function(coroutine=workspace_run_shell, name='workspace_run_shell',
            description=_tool_description('workspace_run_shell'), args_schema=WorkspaceShellInput,
            handle_validation_error=lambda _error: _command_result({'ok': False, 'error_code': 'SHELL_ARGUMENT_INVALID'})),
        'workspace_run_command': StructuredTool.from_function(
            coroutine=workspace_run_command, name='workspace_run_command',
            description=WORKSPACE_TOOL_DESCRIPTIONS['workspace_run_command'], args_schema=WorkspaceCommandInput,
            handle_validation_error=lambda _error: _command_result({'ok': False, 'error_code': 'COMMAND_ARGUMENT_INVALID'}),
        ),
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
    selected = [raw[name] for name in _enabled_tools(role, _binding)]
    return guard_tools(selected, allow_dangerous=allow_dangerous)


async def workspace_tool_policy(
    session: AsyncSession,
    *,
    conversation: Conversation,
    role: Role,
    triggered_by_user_id: int | None,
) -> dict[str, object]:
    """返回 ContextBuilder 应计入预算和 hash 的实际文件/命令暴露策略。

    Args:
        session：只读策略查询会话。
        conversation：本次上下文所属会话。
        role：当前角色配置。
        triggered_by_user_id：本轮触发者，必须为当前 Owner。
    """
    exposed = [name for name in WORKSPACE_TOOLS if name in (role.builtin_tools_json or [])]
    if (
        not exposed
        or not role.active
        or role.deleted_at is not None
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
    ))
    if user is None or not user.is_owner or binding is None:
        return {"version": WORKSPACE_TOOL_POLICY_VERSION, "exposed_tools": []}
    exposed = _enabled_tools(role, binding)
    try:
        binding_root(binding)
    except WorkspacePathError:
        return {"version": WORKSPACE_TOOL_POLICY_VERSION, "exposed_tools": []}
    return {
        "version": WORKSPACE_TOOL_POLICY_VERSION,
        "workspace_binding_id": binding.id,
        "workspace_kind": binding.workspace_kind,
        "exposed_tools": [
            {"name": name, "description": _tool_description(name)}
            for name in WORKSPACE_TOOLS if name in exposed
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
