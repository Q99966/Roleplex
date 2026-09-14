"""工作区文件/命令工具适配、能力开关与逐次/逐项授权。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Annotated, Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.tools import FAILED_OUTPUT_PREFIX, REJECTED_OUTPUT_PREFIX, guard_tools
from ..db import SessionLocal
from ..models import (
    AgentExecution,
    Conversation,
    ExecutionWorkspace,
    Role,
    User,
    WorkspaceBinding,
)
from .files import WorkspaceFileError, WorkspaceFileService, MAX_EDIT_BYTES
from .catalog import WORKSPACE_FILE_TOOLS, WORKSPACE_MUTATION_TOOLS
from .paths import WorkspacePathError
from .service import binding_root
from .commands import WorkspaceCommandError, WorkspaceCommandService
from .batch_read import ReadItemInput
from .batch_mutation import WriteItemInput, EditItemInput
from .access import authorized_member, authorized_execution
from .service_query import create_status_tool
from .diagnostics import AccessDecision, AccessRejected, denied, mutation_blocker

SERVICE_TOOLS = ('workspace_start_service', 'workspace_service_status', 'workspace_service_logs', 'workspace_stop_service')
WORKSPACE_TOOLS = (*WORKSPACE_FILE_TOOLS, 'workspace_run_command', 'workspace_run_shell', *SERVICE_TOOLS)
WORKSPACE_TOOL_POLICY_VERSION = 15
WORKSPACE_TOOL_DESCRIPTIONS = {
    "workspace_list": "列出当前 execution 已绑定工作区内的目录；path 只能是相对路径。",
    'workspace_read': '读取绑定工作区的 UTF-8 文件。可用 path+start_line/end_line 按行读取（从1开始、含两端，默认200行、最多2000行）；与 offset_bytes/max_bytes 字节模式互斥。兼容旧 path 字节形式，默认最多65536字节，返回原五字段。items 一次最多8项，各项默认65536字节，按实际返回量分享主机内容预算；不因申请值相加拒绝。参数JSON最多16 KiB，完整结果最多64 KiB，预算未覆盖项保留状态。所有成功读取给出全文件sha256；可传 expected_sha256 校验搜索/续读版本，不拼接不同版本。行模式只返回完整行，line_too_long时可用返回的start_offset作为offset_bytes改用字节模式。读取支持更大文件的有界扫描，写入上限仍为1 MiB。繁忙时工具内部有界排队，不需要查询队列。',
    'workspace_search': '在绑定工作区定位文件和代码。query为区分大小写的字面文本，mode=text（默认）返回相对路径、匹配行号、少量上下文和确认后的全文件sha256；mode=files使用文件名fnmatch模式（如*.py），不提供内容版本。path默认根目录，可缩小到子目录/文件；limit默认100最多200，context_lines默认1最多3。系统敏感路径、链接、依赖缓存和构建目录排除。扫描/结果达到预算时status=partial，不等于全工作区无匹配。text可用queries数组（1..8词，每词1..256字符）替代query，match=any表示OR，all要求同一行含全部词；多个已知关键词合并一次扫描，matched_queries返回从0开始的命中词索引，|和&仍是字面字符。已知小文件可直接读取；未知位置先搜索，再用workspace_read按行读取；读取传已知expected_sha256，变化后重新定位。不是Shell、正则或语义索引，不获得额外资源权限。',

    "workspace_write": "普通源码创建或整文件替换优先使用本工具；自动创建工作区内缺失的父目录，不必先用Shell建目录，拒绝链接/敏感路径和非目录祖先。批量预检不创建目录，实际写入阶段才创建；失败可能保留已创建空目录。更新携带 workspace_read 返回的全文件 expected_sha256。同工作区有未结束服务时须先协调停服、确认回收后编辑，再重新申请启动；不擅停其他会话服务，不自动换脚本规避拒绝。这不构成文件写入隔离。",
    'workspace_edit': '已有 UTF-8 文件的局部修改优先使用本工具。提供 path、非空且唯一匹配的 old_text、new_text 和最近读取取得的全文件 expected_sha256；两片段合计最多 64 KiB。不做正则、模糊或全部替换，不能猜 hash；版本冲突重新读取，匹配多处时提供更精确上下文。可用空 new_text 删除片段但不删除文件。服务占用时先协调停服再编辑并重新审批启动，不擅停其他会话服务，不换脚本规避拒绝。',
    "workspace_run_command": "在绑定工作区运行固定命令 pwd/list/read/count；args 仅接受相对 path，不接受 Shell 或任意 argv。",
    "workspace_run_shell": "执行一次性复杂命令、安装、测试、构建、格式化或代码生成，须等待 Owner 对本次实际脚本批准。只接受 script，不允许 cwd、环境或审批参数。脚本可能修改文件、访问网络和影响服务，不是只读能力，也未采集文件 diff。运行服务时仍可申请，受独立权限、配额与清理门槛约束；不得移用批准或自动换工具规避拒绝；调用结束清理进程，不用于偷偷保活。",
    'workspace_start_service': '请求 Owner 批准实际脚本并托管前台 HTTP 开发服务；可包含必要准备操作，但普通源码编辑优先原生文件工具，不仅为减少调用次数塞进启动脚本。必须绑定 127.0.0.1 指定端口，不用后台符号/tmux/Docker。等待真实 HTTP ready 后返回资源 ID，回答结束后仍运行。脚本可修改文件、访问网络，未采集文件 diff；启动失败或回收成功不代表副作用回滚。独立逐次审批，不移用 Shell 批准，不自动换入口规避拒绝。',
    'workspace_service_status': '不传参数即可找回当前会话尚未结束或清理待确认的常驻服务 runtime_id；默认每页50项，limit最大100，has_more时用next_cursor作为cursor续页。列表不是同一时刻快照，服务状态可能变化。传runtime_id查询指定实例并兼容原四字段，不能同时传cursor/limit，也不要传null ID。不扫描全机，不占进程名额，不启动或停止；只查本会话登记，工作区停用或无可写租用也可查询。缺失、撤权或查询失败不是空列表；可通过既有权限允许的停止工具或Owner /ps处理，查询不授予停止权限。',
    'workspace_service_logs': '读取当前会话服务的有界私有双流日志及游标缺口，内容会发给当前模型。',
    'workspace_stop_service': '停止当前会话指定的托管实例并确认回收，不按 PID 或端口杀进程。',
}
_LEASE_LOCKS: dict[int, asyncio.Lock] = {}
_COMMAND_CALL_LOCKS: dict[int, asyncio.Lock] = {}


def _tool_description(name: str, *, edit_available: bool = False) -> str:
    """使模型工具 schema 与 ContextBuilder 的策略 hash 使用同一能力描述。

    Args:
        name：实际内置工具名。
        edit_available：本次实际工具集合是否包含 edit，禁止向模型推荐未暴露工具。
    """
    description = WORKSPACE_TOOL_DESCRIPTIONS[name]
    if name in {'workspace_read', 'workspace_search'}:
        from ..config import settings
        description += (f' 当前主机正文额度 {settings.workspace_read_content_bytes} 字节，单文件扫描上限 '
                        f'{settings.workspace_scan_file_bytes} 字节，单次扫描总量 {settings.workspace_scan_total_bytes} 字节，'
                        f'扫描时限 {settings.workspace_scan_seconds} 秒。')
    if name == 'workspace_write' and edit_available:
        description += ' 局部修改可优先使用本轮已启用的 workspace_edit，避免重传整份文件。'
    if name in WORKSPACE_MUTATION_TOOLS:
        description += (' 支持 items 数组（1..8项），与顶层单文件参数严格互斥；沿用本工具单项字段，整批 JSON 输入最多256 KiB。'
            '先检查全批目标/版本再顺序执行，任何执行失败停止后续项，不自动回滚。重复目标拒绝，结果逐项区分成功、失败、未执行或未确认。'
            '只重试失败/未执行项并重新读取 hash，未确认项先核查；不整批盲目重放，不承诺跨文件事务或跨调用 exactly-once。')
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
    return [name for name in WORKSPACE_TOOLS if name != 'workspace_service_status' and name in (role.builtin_tools_json or []) and (
        (binding.services_enabled and shell_available) if name in SERVICE_TOOLS else (binding.shell_enabled and shell_available) if name == 'workspace_run_shell'
        else binding.basic_commands_enabled if name == 'workspace_run_command' else binding.file_tools_enabled)]


class ServiceStartInput(BaseModel):
    """受限前台服务请求，不接收目录、环境或运行身份。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    script: str = Field(min_length=1, max_length=65536)
    port: int = Field(ge=1024, le=65535)
    health_path: str = Field(default='/', pattern=r'^/(?:[^/\\?#\x00-\x20][^\\?#\x00-\x20]*)?$', max_length=512)
    lifetime_seconds: int = Field(default=7200, ge=1, le=28800)


class ServiceIdInput(BaseModel):
    """按不可猜测资源 ID 查询/停止，绝不接受裸 PID。"""
    model_config = ConfigDict(extra='forbid')
    runtime_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class ServiceLogInput(ServiceIdInput):
    """有界日志游标。"""
    after: int = Field(default=0, ge=0, le=2**63 - 1)


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
    """统一单文件/批量读取；旧路径参数兼容，items 不与顶层单项字段混用。"""

    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    path: str | None = Field(default=None, min_length=1, max_length=1024)
    offset_bytes: int = Field(default=0, ge=0)
    max_bytes: int = Field(default=65_536, ge=1, le=65_536)
    items: list[ReadItemInput] | None = Field(default=None, min_length=1, max_length=8)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    expected_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='before')
    @classmethod
    def exclusive_mode(cls, value):
        """Args:
            value：原始工具参数；按键是否出现校验，不能用 null/默认值规避互斥。
        """
        if not isinstance(value, dict):
            raise ValueError('WORKSPACE_READ_ARGUMENT_INVALID')
        if 'items' in value:
            if value['items'] is None or any(key in value for key in ('path', 'offset_bytes', 'max_bytes', 'start_line', 'end_line', 'expected_sha256')):
                raise ValueError('WORKSPACE_READ_ARGUMENT_INVALID')
        elif value.get('path') is None:
            raise ValueError('WORKSPACE_READ_ARGUMENT_INVALID')
        else:
            ReadItemInput.model_validate(value)
        return value


class WorkspaceSearchInput(BaseModel):
    """搜索参数只表达绑定根内的定位任务，不接受执行身份或任意命令。"""
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)
    query: str | None = Field(default=None, min_length=1, max_length=256)
    queries: list[Annotated[str, Field(min_length=1, max_length=256)]] | None = Field(default=None, min_length=1, max_length=8)
    match: Literal['any', 'all'] = 'any'
    mode: Literal['text', 'files'] = 'text'
    path: str = Field(default='.', max_length=1024)
    limit: int = Field(default=100, ge=1, le=200)
    context_lines: int = Field(default=1, ge=0, le=3)


class WorkspaceWriteInput(BaseModel):
    """单文件或批量创建/替换，两种参数形式互斥。"""

    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    path: str | None = Field(default=None, min_length=1, max_length=1024)
    content: str | None = None
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    items: list[WriteItemInput] | None = Field(default=None, min_length=1, max_length=8)

    @model_validator(mode='before')
    @classmethod
    def exclusive_mode(cls, value):
        """Args:
            value：按原始键存在性拒绝混合模式，包含 null 和默认值。
        """
        _mutation_mode(value, ('path', 'content', 'expected_sha256'), ('path', 'content'))
        return value


class WorkspaceEditInput(BaseModel):
    """单文件精确替换输入；字节合计在文件执行层复核，不接受额外参数。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    path: str | None = Field(default=None, min_length=1, max_length=1024)
    old_text: str | None = Field(default=None, min_length=1, max_length=MAX_EDIT_BYTES)
    new_text: str | None = Field(default=None, max_length=MAX_EDIT_BYTES)
    expected_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    items: list[EditItemInput] | None = Field(default=None, min_length=1, max_length=8)

    @model_validator(mode='before')
    @classmethod
    def exclusive_mode(cls, value):
        """Args:
            value：原始编辑参数，items 与全部旧字段互斥。
        """
        fields = ('path', 'old_text', 'new_text', 'expected_sha256')
        _mutation_mode(value, fields, fields)
        return value


def _mutation_mode(value: dict, fields: tuple, required: tuple) -> None:
    """统一写/编辑的输入形式判断，不改变各字段的具体校验规则。

    Args:
        value：原始模型参数。
        fields：全部旧单文件字段。
        required：旧形式必填且不可为 null 的字段。
    """
    if not isinstance(value, dict):
        raise ValueError('Invalid mutation shape')
    if 'items' in value:
        if value['items'] is None or any(field in value for field in fields):
            raise ValueError('Mixed mutation modes')
    elif any(value.get(field) is None for field in required):
        raise ValueError('Missing mutation fields')


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
    enabled = tuple(name for name in WORKSPACE_TOOLS if name != 'workspace_service_status' and name in (role.builtin_tools_json or []))
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


async def _service_decision(
    *,
    execution_id: str,
    conversation_id: int,
    role_id: int,
    triggered_by_user_id: int,
    workspace_binding_id: int,
    root_path_snapshot: str,
    tool_name: str,
    query_available: bool = False,
) -> AccessDecision:
    """每次调用校验实际执行条件；原生修改额外返回经授权的拒绝事实。

    Args:
        execution_id：本轮持久 execution。
        conversation_id：本轮会话 ID。
        role_id：执行角色 ID。
        triggered_by_user_id：调度器绑定的原始触发者。
        workspace_binding_id：lease 绑定的当前 World 工作区。
        root_path_snapshot：创建工具时捕获的规范根，不接受模型覆盖。
        tool_name：用于重新检查角色与工作区开关的工具名。
        query_available：原生修改的本轮实际工具集中是否有状态查询，不能由模型指定。
    """
    mutation = tool_name in WORKSPACE_MUTATION_TOOLS

    def reject(code: str, scope: str) -> AccessDecision:
        """Args:
            code：具体可用性原因。
            scope：已授权条件的影响范围；非修改调用保持原通用拒绝。
        """
        return denied(code, scope) if mutation else AccessDecision()

    async with SessionLocal() as session:
        actor = await authorized_execution(session, execution_id=execution_id, conversation_id=conversation_id,
            role_id=role_id, user_id=triggered_by_user_id, tool_name=tool_name, require_tool=not mutation)
        if actor is None:
            return AccessDecision()
        conversation, role = actor
        lease = await session.scalar(select(ExecutionWorkspace).where(
            ExecutionWorkspace.execution_id == execution_id,
            ExecutionWorkspace.workspace_binding_id == workspace_binding_id,
            ExecutionWorkspace.status == "ready",
        ))
        binding = await session.scalar(select(WorkspaceBinding).where(
            WorkspaceBinding.id == workspace_binding_id,
            WorkspaceBinding.created_by == triggered_by_user_id,
        ))
        if binding is None:
            return AccessDecision()
        if tool_name not in (role.builtin_tools_json or []):
            return reject('WORKSPACE_TOOL_CAPABILITY_CHANGED', 'tool')
        if conversation.workspace_binding_id != workspace_binding_id or binding.root_path != root_path_snapshot:
            return reject('WORKSPACE_BINDING_CHANGED', 'workspace')
        if lease is None or lease.root_path_snapshot != root_path_snapshot:
            return reject('WORKSPACE_LEASE_UNAVAILABLE', 'workspace')
        if not binding.active or tool_name not in _enabled_tools(role, binding):
            return reject('WORKSPACE_TOOL_CAPABILITY_CHANGED', 'workspace')
        if mutation:
            blocker = await mutation_blocker(session, workspace_id=workspace_binding_id, conversation_id=conversation_id,
                owner_id=triggered_by_user_id, query_available=query_available and 'workspace_service_status' in (role.builtin_tools_json or []))
            if blocker is not None:
                return blocker
        if tool_name == 'workspace_run_shell':
            from ..runtime.models import RuntimeEntry
            # 运行中的服务不替代 Shell 的逐次审批；仅未确认回收仍阻止新的任意脚本。
            # 原生写工具暂保留占用限制，不将其宣传为对 Shell/服务自身写文件的隔离。
            if await session.scalar(select(RuntimeEntry.id).where(RuntimeEntry.workspace_id == workspace_binding_id,
                RuntimeEntry.kind == 'service', RuntimeEntry.state == 'cleanup_required').limit(1)):
                return AccessDecision()
        if not mutation:
            from ..runtime.models import RuntimeGate, CleanupOperation
            from sqlalchemy import or_
            if await session.scalar(select(RuntimeGate.closing).where(RuntimeGate.id == 1)):
                return AccessDecision()
            if await session.scalar(select(CleanupOperation.id).where(CleanupOperation.state.in_(('running', 'prepared', 'failed')),
                or_(CleanupOperation.scope == 'world', (CleanupOperation.scope == 'conversation') & (CleanupOperation.scope_id == conversation_id),
                    (CleanupOperation.scope == 'workspace') & (CleanupOperation.scope_id == workspace_binding_id))).limit(1)):
                return AccessDecision()
        try:
            root = binding_root(binding)
        except WorkspacePathError:
            return reject('WORKSPACE_UNAVAILABLE', 'workspace')
        if str(root) != root_path_snapshot:
            return reject('WORKSPACE_BINDING_CHANGED', 'workspace')
        return AccessDecision(service=WorkspaceFileService(root=root, execution_id=execution_id), error_code='')


async def _authorized_service(**identity) -> WorkspaceFileService | None:
    """保留其他工具/审批调用方的 service-or-None 接口，不扩大其策略。

    Args:
        identity：宿主绑定的身份、工具和租用快照；由 _service_decision 的显式参数约束。
    """
    return (await _service_decision(**identity)).service


def _read_rejected(code: str) -> bool:
    """区分预检/授权/准入拒绝与真实读取失败，保留旧文件错误的终态。

    Args:
        code：已登记的读取/搜索错误码。
    """
    return code in {'WORKSPACE_READ_ARGUMENT_INVALID', 'WORKSPACE_SEARCH_ARGUMENT_INVALID',
        'WORKSPACE_BATCH_ARGUMENT_INVALID', 'WORKSPACE_BATCH_INPUT_TOO_LARGE', 'WORKSPACE_TOOL_NOT_AVAILABLE',
        'WORKSPACE_SCAN_BUSY', 'WORKSPACE_SCAN_QUEUE_TIMEOUT', 'WORKSPACE_SCAN_CLOSED',
        'WORKSPACE_READ_BUDGET_EXHAUSTED', 'WORKSPACE_FILE_REVISION_CONFLICT'}


def _error_result(code: str, *, rejected: bool = False, diagnostic: dict | None = None, details: dict | None = None) -> str:
    """返回给模型的结构化文件失败，不泄露路径或宿主异常。

    Args:
        code：固定错误码。
        rejected：原生文件工具的预期输入/匹配拒绝；默认保持旧工具响应语义。
        diagnostic：已鉴权且有界的本项拒绝事实，仅用于模型与 Owner 详情。
        details：执行层生成的安全预算、阶段或父目录创建计数。
    """
    body = json.dumps({"ok": False, "error_code": code, **({'diagnostic': diagnostic} if diagnostic is not None else {}),
        **({'details': details} if details is not None else {})}, ensure_ascii=False, separators=(",", ":"))
    return f"{REJECTED_OUTPUT_PREFIX if rejected else FAILED_OUTPUT_PREFIX} {body}"


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
    """创建文件/命令工具及独立只读状态工具；查询不依赖可写 lease。

    Args:
        session：创建 lease 的短事务会话。
        execution_id：本轮持久 execution。
        conversation_id：本轮 single 会话。
        role：当前执行角色。
        triggered_by_user_id：调度器绑定的触发者。
        allow_dangerous：链路是否允许 dangerous 工具。
    """
    query_tools = []
    if 'workspace_service_status' in (role.builtin_tools_json or []) and allow_dangerous and triggered_by_user_id is not None and await authorized_execution(session,
        execution_id=execution_id, conversation_id=conversation_id, role_id=role.id,
        user_id=triggered_by_user_id, tool_name='workspace_service_status') is not None:
        query_tools.append(create_status_tool(execution_id=execution_id, conversation_id=conversation_id,
            role_id=role.id, user_id=triggered_by_user_id, description=_tool_description('workspace_service_status')))
    created = await _create_lease(
        session,
        execution_id=execution_id,
        conversation_id=conversation_id,
        role=role,
        triggered_by_user_id=triggered_by_user_id,
        allow_dangerous=allow_dangerous,
    )
    if created is None or triggered_by_user_id is None:
        return guard_tools(query_tools, allow_dangerous=allow_dangerous)
    lease, _binding = created

    def runtime_identity(name: str) -> dict:
        """Args:
            name：正在调用的固定工具名，身份来自宿主而非模型。
        """
        from ..agent.tool_context import tool_call_id
        return dict(owner_id=triggered_by_user_id, conversation_id=conversation_id, workspace_id=lease.workspace_binding_id,
            execution_id=execution_id, role_id=role.id, tool_call_id=tool_call_id.get(), tool_name=name)

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

    async def mutation_service(tool_name: str) -> WorkspaceFileService:
        """Args:
            tool_name：原生 write/edit；失败以类型化写前拒绝交给单项/批次共用处理。
        """
        decision = await _service_decision(execution_id=execution_id, conversation_id=conversation_id, role_id=role.id,
            triggered_by_user_id=triggered_by_user_id, workspace_binding_id=lease.workspace_binding_id,
            root_path_snapshot=lease.root_path_snapshot, tool_name=tool_name, query_available=bool(query_tools))
        if decision.service is None:
            raise AccessRejected(decision)
        return decision.service

    async def workspace_list(path: str = ".", after_name: str | None = None, limit: int = 200) -> str:
        """列出绑定工作区内一个目录页，不跟随符号链接。"""
        authorized = await service("workspace_list")
        if authorized is None:
            return f"{REJECTED_OUTPUT_PREFIX} WORKSPACE_TOOL_NOT_AVAILABLE"
        try:
            return authorized.json_result(await authorized.list(path, after_name=after_name, limit=limit))
        except WorkspaceFileError as exc:
            return _error_result(exc.code)

    async def workspace_read(path: str | None = None, offset_bytes: int = 0, max_bytes: int = 65536, items: list | None = None,
                             start_line: int | None = None, end_line: int | None = None, expected_sha256: str | None = None) -> str:
        """先获得扫描准入再授权，保留旧单项格式并支持按行读取。

        Args:
            path：单文件相对路径，与 items 互斥。
            offset_bytes：旧字节起点。
            max_bytes：字节返回期望上限。
            items：逐文件参数，允许每项选择一种读取模式。
            start_line：行模式起点。
            end_line：包含的结束行。
            expected_sha256：可选完整版本约束。
        """
        from .scan_admission import admitted
        try:
            if items is not None:
                items = [item.model_dump(exclude_unset=True) if isinstance(item, BaseModel) else item for item in items]
            size = len(json.dumps({'path': path, 'items': items}, ensure_ascii=False).encode())
            if size > 16384:
                raise WorkspaceFileError('WORKSPACE_BATCH_INPUT_TOO_LARGE',
                    {'phase': 'precheck', 'actual': size, 'limit': 16384, 'unit': 'utf8_bytes'})
            # 批次先登记未开始节点，再由 read_many 取得同一准入；排队取消也有准确的私有状态。
            if items is not None:
                return await read_items(items)
            async with admitted(size):
                authorized = await service('workspace_read')
                if authorized is None:
                    raise WorkspaceFileError('WORKSPACE_TOOL_NOT_AVAILABLE')
                if start_line is not None:
                    result = json.dumps(await authorized.read_lines(path, start_line=start_line, end_line=end_line,
                        expected_sha256=expected_sha256), ensure_ascii=False, separators=(',', ':'))
                else:
                    result = authorized.json_result(await authorized.read(path, offset_bytes=offset_bytes, max_bytes=max_bytes,
                        expected_sha256=expected_sha256))
                if await service('workspace_read') is None:
                    raise WorkspaceFileError('WORKSPACE_TOOL_NOT_AVAILABLE')
                return result
        except UnicodeError:
            return _error_result('WORKSPACE_READ_ARGUMENT_INVALID', rejected=True)
        except WorkspaceFileError as exc:
            return _error_result(exc.code, rejected=_read_rejected(exc.code), details=exc.details)

    async def workspace_search(query: str | None = None, mode: str = 'text', path: str = '.', limit: int = 100, context_lines: int = 1, queries: list[str] | None = None, match: str = 'any') -> str:
        """获得准入后重新校验权限，搜索原文只进入模型与 Owner 详情。

        Args:
            query：单个字面内容或文件名模式。
            queries：多个字面词，与 query 互斥。
            match：any/all，同一行的匹配条件。
            mode：text/files。
            path：绑定根内扫描范围。
            limit：匹配数量上限。
            context_lines：命中前后上下文行数。
        """
        from .scan_admission import admitted
        try:
            async with admitted(len(json.dumps({'query': query, 'queries': queries, 'match': match, 'path': path}, ensure_ascii=False).encode())):
                authorized = await service('workspace_search')
                if authorized is None:
                    raise WorkspaceFileError('WORKSPACE_TOOL_NOT_AVAILABLE')
                result = await authorized.search(query=query, queries=queries, match=match, mode=mode, path=path, limit=limit, context_lines=context_lines,
                                                authorize=lambda: service('workspace_search'))
                if await service('workspace_search') is None:
                    raise WorkspaceFileError('WORKSPACE_TOOL_NOT_AVAILABLE')
                return json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        except UnicodeError:
            return _error_result('WORKSPACE_SEARCH_ARGUMENT_INVALID', rejected=True)
        except WorkspaceFileError as exc:
            return _error_result(exc.code, rejected=_read_rejected(exc.code), details=exc.details)

    async def mutate_file(tool_name: str, path: str, arguments: dict) -> str:
        """write/edit 共用授权、私有采集和锁外计算，不接受模型指定工具名。

        Args:
            tool_name：内置包装函数绑定的写操作名称。
            path：模型请求路径，执行层再解析。
            arguments：包装函数从固定 schema 构造的业务参数。
        """
        from ..agent.write_capture import begin_write_capture, WriteReceipt
        receipt = begin_write_capture(path) or WriteReceipt(path)
        try:
            async with _COMMAND_CALL_LOCKS.setdefault(lease.workspace_binding_id, asyncio.Lock()):
                authorized = await mutation_service(tool_name)
                operation = authorized.write if tool_name == 'workspace_write' else authorized.edit
                result = await operation(path, **arguments, capture_applied=receipt.applied,
                    **({'capture_parent_created': receipt.parent_created} if tool_name == 'workspace_write' else {}))
                result_value = json.loads(authorized.json_result(result))
                if tool_name == 'workspace_write':
                    result_value['created_parent_count'] = receipt.created_parent_count
                output = json.dumps(result_value, ensure_ascii=False)
            # 库计算或排队绝不能延长文件/工作区写锁的持有时间。
            if receipt:
                await receipt.finish(output)
            return output
        except WorkspaceFileError as exc:
            diagnostic = exc.diagnostic if isinstance(exc, AccessRejected) else None
            if receipt:
                receipt.not_executed()
                receipt.diagnostic = diagnostic
            return _error_result(exc.code, rejected=tool_name == 'workspace_edit' or isinstance(exc, AccessRejected), diagnostic=diagnostic,
                details={'created_parent_count': receipt.created_parent_count} if tool_name == 'workspace_write' else None)
        finally:
            if receipt:
                receipt.release()

    async def read_items(items: list) -> str:
        """Args:
            items：模型逐文件请求，批次身份与权限从当前宿主取得。
        """
        from .batch_read import validate_items, ReadBatchReceipt, read_many
        from ..agent.write_capture import write_capture_scope
        from ..agent.tool_context import tool_call_id
        try:
            values = validate_items(items)
            scope, call_id = write_capture_scope.get(), tool_call_id.get()
            receipt = scope.begin_read_batch(call_id, values) if scope and call_id else None
            receipt = receipt or ReadBatchReceipt(values)
            result = await read_many(values, authorize=lambda: service('workspace_read'), receipt=receipt)
            if await service('workspace_read') is None:
                raise WorkspaceFileError('WORKSPACE_TOOL_NOT_AVAILABLE')
            status = receipt.value['status']
            return result if status == 'success' else f'{REJECTED_OUTPUT_PREFIX if status == "rejected" else FAILED_OUTPUT_PREFIX} {result}'
        except WorkspaceFileError as exc:
            return _error_result(exc.code, rejected=True, details=exc.details)

    async def mutate_items(tool_name: str, items: list) -> str:
        """Args:
            tool_name：宿主绑定的原 write/edit 工具。
            items：模型多文件参数，不接受调度/归属身份。
        """
        from .batch_mutation import validate_items, BatchMutationReceipt, mutate_many
        from ..agent.write_capture import write_capture_scope
        from ..agent.tool_context import tool_call_id
        operation = 'write' if tool_name == 'workspace_write' else 'edit'
        try:
            values = validate_items(operation, items)
            scope, call_id = write_capture_scope.get(), tool_call_id.get()
            receipt = scope.begin_mutation_batch(call_id, operation, values) if scope and call_id else None
            receipt = receipt or BatchMutationReceipt(operation, values)
            result = await mutate_many(operation, values, authorize=lambda: mutation_service(tool_name),
                lock=_COMMAND_CALL_LOCKS.setdefault(lease.workspace_binding_id, asyncio.Lock()), receipt=receipt)
            status = receipt.value['status']
            return result if status == 'success' else f'{REJECTED_OUTPUT_PREFIX if status == "rejected" else FAILED_OUTPUT_PREFIX} {result}'
        except WorkspaceFileError as exc:
            return _error_result(exc.code, rejected=True, details=exc.details)

    async def workspace_write(path: str | None = None, content: str | None = None, expected_sha256: str | None = None, items: list | None = None) -> str:
        """新建或整文件替换。

        Args:
            path：工作区相对路径。
            content：完整新内容。
            expected_sha256：更新所需的完整旧 hash。
            items：批次形式，与所有顶层单项参数互斥。
        """
        if items is not None:
            return await mutate_items('workspace_write', items)
        return await mutate_file('workspace_write', path, {'content': content, 'expected_sha256': expected_sha256})

    async def workspace_edit(path: str | None = None, old_text: str | None = None, new_text: str | None = None,
                             expected_sha256: str | None = None, items: list | None = None) -> str:
        """对已有文件做一次唯一字面替换。

        Args:
            path：工作区相对路径。
            old_text：唯一旧片段。
            new_text：替换片段。
            expected_sha256：读取取得的完整文件 hash。
            items：逐文件编辑参数，与旧形式严格互斥。
        """
        if items is not None:
            return await mutate_items('workspace_edit', items)
        return await mutate_file('workspace_edit', path, {'old_text': old_text, 'new_text': new_text, 'expected_sha256': expected_sha256})

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
                from ..runtime.manager import manager
                async with manager.command(**runtime_identity('workspace_run_command')):
                    result = await WorkspaceCommandService(root=authorized.root, execution_id=execution_id).run(command, args if args is not None else {})
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
                from ..runtime.manager import manager
                async with manager.command(**runtime_identity('workspace_run_shell')):
                    return _command_result(await request_and_run(script=script, execution_id=execution_id,
                        conversation_id=conversation_id, role_id=role.id, owner_id=triggered_by_user_id,
                        workspace_binding_id=lease.workspace_binding_id, root_path=lease.root_path_snapshot,
                        tool_call_id=tool_call_id.get()))
            except WorkspaceCommandError as exc:
                return _command_result({'ok': False, 'error_code': exc.code})

    async def workspace_start_service(script: str, port: int, health_path: str = '/', lifetime_seconds: int = 7200) -> str:
        """Args:
            script：审批的前台脚本。
            port：声明端口。
            health_path：受限本机探针路径。
            lifetime_seconds：不超过主机限制的寿命。
        """
        from ..runtime.manager import manager
        from ..runtime.registry import RuntimeRejected
        try:
            async with _COMMAND_CALL_LOCKS.setdefault(lease.workspace_binding_id, asyncio.Lock()):
                if await service('workspace_start_service') is None:
                    return _command_result({'error_code': 'WORKSPACE_TOOL_NOT_AVAILABLE'})
                identity = {**runtime_identity('workspace_start_service'), 'root_path': lease.root_path_snapshot}
                return json.dumps(await manager.start_service(identity=identity, script=script, port=port,
                    health_path=health_path, lifetime_seconds=lifetime_seconds))
        except (RuntimeRejected, WorkspaceCommandError) as exc:
            return _command_result({'error_code': exc.code})

    async def runtime_access(runtime_id: str, name: str):
        """Args:
            runtime_id：模型引用的资源身份。
            name：查询/日志/停止的实际工具名。
        """
        from ..runtime import registry
        if await service(name) is None:
            raise registry.RuntimeRejected('WORKSPACE_TOOL_NOT_AVAILABLE')
        row = await registry.get(runtime_id)
        if row is None or row.owner_id != triggered_by_user_id or row.conversation_ref_id != conversation_id:
            raise registry.RuntimeRejected('RUNTIME_NOT_FOUND')
        return row

    async def workspace_service_logs(runtime_id: str, after: int = 0) -> str:
        """Args:
            runtime_id：当前会话运行实例。
            after：已读取的观察序号。
        """
        from ..runtime import registry, logs
        from ..runtime.manager import manager
        try:
            row = await runtime_access(runtime_id, 'workspace_service_logs')
            host = manager.hosts.get(runtime_id)
            return json.dumps(host.ring.page(after) if host else logs.archived_page(row, after), ensure_ascii=False)
        except registry.RuntimeRejected as exc:
            return _command_result({'error_code': exc.code})

    async def workspace_stop_service(runtime_id: str) -> str:
        """Args:
            runtime_id：当前会话托管实例，不接受 PID。
        """
        from ..runtime.registry import RuntimeRejected
        from ..runtime.manager import manager
        try:
            await runtime_access(runtime_id, 'workspace_stop_service')
            row = await manager.stop_one(runtime_id, 'agent_stop')
            return json.dumps({'runtime_id': row.id, 'state': row.state})
        except RuntimeRejected as exc:
            return _command_result({'error_code': exc.code})

    raw: dict[str, BaseTool] = {
        'workspace_search': StructuredTool.from_function(coroutine=workspace_search, name='workspace_search',
            description=_tool_description('workspace_search'), args_schema=WorkspaceSearchInput,
            handle_validation_error=lambda _error: _error_result('WORKSPACE_SEARCH_ARGUMENT_INVALID', rejected=True)),
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
            description=_tool_description("workspace_read"),
            args_schema=WorkspaceReadInput,
            handle_validation_error=lambda _error: _error_result('WORKSPACE_READ_ARGUMENT_INVALID', rejected=True),
        ),
        "workspace_write": StructuredTool.from_function(
            coroutine=workspace_write,
            name="workspace_write",
            description=_tool_description('workspace_write', edit_available='workspace_edit' in _enabled_tools(role, _binding)),
            args_schema=WorkspaceWriteInput,
            handle_validation_error=lambda _error: _error_result('WORKSPACE_WRITE_ARGUMENT_INVALID', rejected=True),
        ),
        'workspace_edit': StructuredTool.from_function(
            coroutine=workspace_edit, name='workspace_edit', description=_tool_description('workspace_edit'),
            args_schema=WorkspaceEditInput,
            handle_validation_error=lambda _error: _error_result('WORKSPACE_EDIT_ARGUMENT_INVALID', rejected=True),
        ),
    }
    for name, function, schema in [('workspace_start_service', workspace_start_service, ServiceStartInput),
        ('workspace_service_logs', workspace_service_logs, ServiceLogInput),
        ('workspace_stop_service', workspace_stop_service, ServiceIdInput)]:
        raw[name] = StructuredTool.from_function(coroutine=function, name=name, description=WORKSPACE_TOOL_DESCRIPTIONS[name],
            args_schema=schema, handle_validation_error=lambda _error: _command_result({'error_code': 'RUNTIME_ARGUMENT_INVALID'}))
    raw.update({tool.name: tool for tool in query_tools})
    enabled = {*_enabled_tools(role, _binding), *(tool.name for tool in query_tools)}
    selected = [raw[name] for name in WORKSPACE_TOOLS if name in enabled]
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
    ):
        return {"version": WORKSPACE_TOOL_POLICY_VERSION, "exposed_tools": []}
    user = await session.get(User, triggered_by_user_id)
    binding = await session.scalar(select(WorkspaceBinding).where(
        WorkspaceBinding.id == conversation.workspace_binding_id,
        WorkspaceBinding.created_by == triggered_by_user_id,
        WorkspaceBinding.active.is_(True),
    ))
    if user is None or not user.is_owner:
        return {"version": WORKSPACE_TOOL_POLICY_VERSION, "exposed_tools": []}
    exposed = []
    if binding is not None:
        try:
            binding_root(binding)
            exposed = _enabled_tools(role, binding)
        except WorkspacePathError:
            pass
    if await authorized_member(session, conversation_id=conversation.id, role_id=role.id,
        user_id=triggered_by_user_id, tool_name='workspace_service_status') is not None:
        exposed.append('workspace_service_status')
    return {
        "version": WORKSPACE_TOOL_POLICY_VERSION,
        **({"workspace_binding_id": binding.id, "workspace_kind": binding.workspace_kind} if binding else {}),
        "exposed_tools": [
            {"name": name, "description": _tool_description(name, edit_available='workspace_edit' in exposed)}
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
