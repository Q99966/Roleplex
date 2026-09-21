"""工作区文件/命令工具适配、能力开关与逐次/逐项授权。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Annotated, Literal

from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
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
from .replacements import ReplacementInput, MAX_REPLACEMENTS, validate_edit_shape
from .catalog import WORKSPACE_FILE_TOOLS, WORKSPACE_MUTATION_TOOLS
from .paths import WorkspacePathError
from .service import binding_root
from .commands import WorkspaceCommandError, WorkspaceCommandService
from .batch_read import ReadItemInput
from .batch_mutation import WriteItemInput, EditItemInput
from .access import authorized_member, authorized_execution
from .service_query import create_status_tool, ServiceStatusInput
from .diagnostics import AccessDecision, AccessRejected, denied, mutation_blocker

SERVICE_TOOLS = ('workspace_start_service', 'workspace_service_status', 'workspace_service_logs', 'workspace_stop_service')
WORKSPACE_TOOLS = (*WORKSPACE_FILE_TOOLS, 'workspace_run_command', 'workspace_run_shell', *SERVICE_TOOLS)
WORKSPACE_TOOL_POLICY_VERSION = 20
WORKSPACE_TOOL_DESCRIPTIONS = {
    'workspace_list': '查看工作区目录下的文件和子目录。返回条目名与类型；结果未列完时使用 next_after_name 继续，不递归读取正文。例：{"path":"src","limit":50}。',
    'workspace_read': '读取已知路径的 UTF-8 文件，可按行、按字节或用 items 批量读取。成功结果包含全文件 sha256，供后续编辑或续读校验版本；版本变化时重新读取，不拼接不同版本。未读完按返回的next_line或next_offset续读。例：{"path":"src/game.js","start_line":120,"end_line":180}。',
    'workspace_search': '定位文件或文本所在位置。文本搜索区分大小写，返回路径、行号、少量上下文及文件 sha256；文件名搜索使用通配符；默认排除敏感路径、链接、依赖和构建目录。多个关键词优先合并 queries；| 和 & 是普通字符，不是运算符。结果 partial 表示范围未查完，不能断言没有其他匹配。例：{"path":"src","queries":["score","reset"],"match":"any"}。',
    'workspace_write': '创建文件或用完整正文替换已有文件，自动创建缺失父目录；文件失败可能留下空目录。新建不传 expected_sha256；覆盖前先读取，将返回的真实 sha256 填入 expected_sha256，不能猜测或省略来强行覆盖。例：{"path":"src/hello.txt","content":"hello"}。',
    'workspace_edit': '精确修改已有 UTF-8 文件，避免重写整份正文。先读取并传回真实 expected_sha256。单处用 old_text/new_text，多处用 replacements；所有旧片段在同一原始版本中唯一匹配且不得重叠，全部校验通过后提交一次。版本冲突重新读取，匹配失败调整片段，不模糊猜测。例如取得 hash 后，可用 replacements 同时把 score = 0 改为 score = 1、speed = 2 改为 speed = 3。',
    'workspace_run_command': '运行只读的固定命令 pwd/list/read/count；只接受结构化参数，不执行 Shell 语法或任意程序。例：{"command":"count","args":{"path":"src/game.js"}}。',
    'workspace_run_shell': '执行安装、构建、测试等一次性 Shell 脚本，每次须经 Owner 批准。脚本可修改文件或访问网络，不提供文件 diff。调用结束会清理其进程，不能用来保活后台服务；拒绝后不要换脚本规避审批。例：{"script":"node --version"}。',
    'workspace_start_service': '启动回答结束后仍需运行的 HTTP 开发服务，每次须经 Owner 批准。脚本以前台方式运行并监听 127.0.0.1 的指定端口；等待 HTTP 健康检查成功后返回 runtime_id。不要用后台符号、tmux 或 Docker 绕过托管。脚本可能修改文件；启动失败或停止服务不回滚文件修改。',
    'workspace_service_status': '查看本会话的托管服务。用空参数 {} 找回尚未结束或清理待确认的服务 runtime_id；也可传真实 runtime_id 查单个实例。列表 has_more 时用 next_cursor 续页。查询失败不等于没有服务；本工具不会启动或停止服务，也不扫描主机进程。',
    'workspace_service_logs': '读取本会话指定托管服务的 stdout/stderr 日志。runtime_id 从服务启动或状态查询结果取得，after 用返回的游标续读；日志有保留上限，注意结果中的缺口。返回内容会进入当前模型上下文。',
    'workspace_stop_service': '停止本会话指定托管服务并确认进程回收。runtime_id 从服务启动或状态查询结果取得，不接受 PID 或端口，不停止其他会话服务；停止进程不代表文件修改被撤销。',
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
    if name in WORKSPACE_MUTATION_TOOLS:
        description += (' 单文件 path 形式与 items 批次形式互斥。批次不设固定项数或总JSON字节上限，单文件限制仍生效；先预检全批，执行失败即停，不回滚已提交文件。'
            '结果未知先核对，不能整批盲目重放。服务占用导致拒绝时先协调停服并确认回收，不擅停其他会话服务或换脚本绕过保护。'
            '工具会在内部排队；同一次等待保留参数，不必重复发送代码。')
    if name in {'workspace_read', 'workspace_search'}:
        from ..config import settings
        description += (f' 当前内容额度 {settings.workspace_read_content_bytes} 字节，完整结果最多64 KiB；'
                        f'扫描上限：单文件 {settings.workspace_scan_file_bytes} 字节、总量 {settings.workspace_scan_total_bytes} 字节、{settings.workspace_scan_seconds} 秒。')
    if name == 'workspace_read':
        description += (' 参数 JSON 最多16 KiB，批量按实际返回内容共享额度。行模式遇 line_too_long 时，'
                        '用返回的 start_offset 改为字节读取，不混传行/字节参数。')
    if name == 'workspace_write' and edit_available:
        description += ' 局部修改优先使用本轮已提供的 workspace_edit。'
    if name == 'workspace_edit':
        description += ' 每文件全部 old_text/new_text 合计最多64 KiB，最终文件最多1 MiB。'
    if name == 'workspace_run_shell':
        from .shell import shell_configuration
        try:
            config = shell_configuration()
        except WorkspaceCommandError:
            return description
        description += (f" 当前解释器 {config['shell_kind']}，执行超时 {config['timeout_seconds']} 秒，"
                        f"输出最多 {config['output_bytes']} 字节。")
    return description


def _enabled_tools(role: Role, binding: WorkspaceBinding, conversation_type: str = 'single') -> list[str]:
    """组合角色与工作区的独立文件/命令开关。

    Args:
        role：本 execution 的当前角色。
        binding：当前 World 的工作区记录。
        conversation_type：群聊仅取原生文件工具，不扩大命令或服务权限。
    """
    from .shell import shell_configuration
    try:
        shell_configuration()
        shell_available = True
    except WorkspaceCommandError:
        shell_available = False
    return [name for name in WORKSPACE_TOOLS if (conversation_type == 'single' or name in WORKSPACE_FILE_TOOLS) and name != 'workspace_service_status' and name in (role.builtin_tools_json or []) and (
        (binding.services_enabled and shell_available) if name in SERVICE_TOOLS else (binding.shell_enabled and shell_available) if name == 'workspace_run_shell'
        else binding.basic_commands_enabled if name == 'workspace_run_command' else binding.file_tools_enabled)]


class ServiceStartInput(BaseModel):
    """受限前台服务请求，不接收目录、环境或运行身份。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    script: str = Field(min_length=1, max_length=65536, description="前台服务启动脚本，UTF-8 最多65536字节；运行目录由绑定工作区决定。")
    port: int = Field(ge=1024, le=65535, description="服务实际监听的端口，必须绑定127.0.0.1；应与脚本中的端口一致。")
    health_path: str = Field(default='/', pattern=r'^/(?:[^/\\?#\x00-\x20][^\\?#\x00-\x20]*)?$', max_length=512, description="健康检查的相对URL路径，默认/；不要传完整网址或查询参数。")
    lifetime_seconds: int = Field(default=7200, ge=1, le=28800, description="服务最长存续秒数，默认7200，最多28800；到期由平台回收。")


class ServiceIdInput(BaseModel):
    """按不可猜测资源 ID 查询/停止，绝不接受裸 PID。"""
    model_config = ConfigDict(extra='forbid')
    runtime_id: str = Field(pattern=r'^[a-f0-9]{32}$', description="服务启动或状态查询返回的真实runtime_id，不是PID；不要编造。")


class ServiceLogInput(ServiceIdInput):
    """有界日志游标。"""
    after: int = Field(default=0, ge=0, le=2**63 - 1, description="上次读取返回的next_seq；首次省略或传0。")


class WorkspaceShellInput(BaseModel):
    """模型仅能提交脚本；真实调用身份由防腐层上下文提供。"""

    model_config = ConfigDict(extra='forbid')
    script: str = Field(min_length=1, max_length=65536, description="一次性脚本，UTF-8最多65536字节；不要传cwd、环境或审批字段。")


class WorkspaceCommandInput(BaseModel):
    """结构化命令输入；命令专用字段由执行层校验，错误不回显原始参数。"""

    model_config = ConfigDict(extra='forbid')
    command: str = Field(min_length=1, max_length=32, description="固定命令：pwd查看目录、list列条目、read读取文本、count统计文件行数。")
    args: dict = Field(default_factory=dict, description="命令参数对象；pwd传{}，其他命令传path相对路径；不支持任意命令行参数。")


class WorkspaceListInput(BaseModel):
    """目录列表工具输入。"""

    path: str = Field(default=".", max_length=1024, description="工作区相对目录，默认根目录。")
    after_name: str | None = Field(default=None, max_length=255, description="上一页返回的next_after_name；首次省略。")
    limit: int = Field(default=200, ge=1, le=200, description="本次最多返回的条目数，默认200。")


class WorkspaceReadInput(BaseModel):
    """统一单文件/批量读取；旧路径参数兼容，items 不与顶层单项字段混用。"""

    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    path: str | None = Field(default=None, min_length=1, max_length=1024, description="单文件相对路径；与items不能同时出现。")
    offset_bytes: int = Field(default=0, ge=0, description="字节模式的起始偏移，从0开始；不能与行范围混传。")
    max_bytes: int = Field(default=65_536, ge=1, le=65_536, description="字节模式最多返回的UTF-8字节数，必须为整数；行模式不要传此字段。")
    items: list[ReadItemInput] | None = Field(default=None, min_length=1, max_length=8, description="一次读取多个文件或范围，最多8项；使用时省略所有顶层单文件字段，包括null。")
    start_line: int | None = Field(default=None, ge=1, description="行模式起始行，从1开始；不能同时传offset_bytes/max_bytes。")
    end_line: int | None = Field(default=None, ge=1, description="行模式结束行，包含本行且不小于start_line；省略时默认读200行，最多2000行。")
    expected_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$', description="可选的已知全文件sha256；来自此前读取或文本搜索，版本不同则拒绝。")

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
    query: str | None = Field(default=None, min_length=1, max_length=256, description="单个字面关键词；mode=files时为文件名通配模式，如*.js。与queries互斥。")
    queries: list[Annotated[str, Field(min_length=1, max_length=256)]] | None = Field(default=None, min_length=1, max_length=8, description="仅文本模式支持的多个字面关键词，最多8个、每个最多256字符；与query互斥。")
    match: Literal['any', 'all'] = Field(default='any', description="queries匹配方式：any任一关键词命中，all要求同一行含全部词。")
    mode: Literal['text', 'files'] = Field(default='text', description="text搜索文本内容；files匹配文件名且不返回内容版本。")
    path: str = Field(default='.', max_length=1024, description="搜索范围：工作区相对目录或文件，默认根目录。")
    limit: int = Field(default=100, ge=1, le=200, description="最多返回的匹配结果数，默认100；达到上限时结果可能不完整。")
    context_lines: int = Field(default=1, ge=0, le=3, description="每条文本命中附带的前后上下文行数，默认1。")


class WorkspaceWriteInput(BaseModel):
    """单文件或批量创建/替换，两种参数形式互斥。"""

    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    path: str | None = Field(default=None, min_length=1, max_length=1024, description="单文件相对路径；缺失父目录会自动创建，与items互斥。")
    content: str | None = Field(default=None, description="完整UTF-8文件正文，最终文件最多1 MiB；单文件模式必填，可为空字符串。")
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$", description="新建时省略；覆盖已有文件时必填最近读取返回的真实sha256。")
    items: list[WriteItemInput] | None = Field(default=None, min_length=1, description="非空独立文件写入数组，无固定项数或整批JSON字节上限；与全部顶层单文件字段互斥，包括null。")

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
    path: str | None = Field(default=None, min_length=1, max_length=1024, description="已有文件的相对路径；与items互斥。")
    old_text: str | None = Field(default=None, min_length=1, max_length=MAX_EDIT_BYTES, description="单处修改的非空旧片段，必须唯一匹配；使用replacements时省略。")
    new_text: str | None = Field(default=None, max_length=MAX_EDIT_BYTES, description="单处修改的新片段；空字符串表示删除旧片段，不删除文件。")
    expected_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$', description="单文件编辑必填：最近读取返回的全文件sha256，不能猜测。")
    replacements: list[ReplacementInput] | None = Field(default=None, min_length=1, max_length=MAX_REPLACEMENTS, description="同文件1..32处替换，针对原始版本匹配；与old_text/new_text互斥。例如[{\"old_text\":\"score = 0\",\"new_text\":\"score = 1\"}]。")
    items: list[EditItemInput] | None = Field(default=None, min_length=1, description="非空独立文件编辑数组，无固定项数或整批JSON字节上限，每项可用单片段或replacements；与全部顶层单文件字段互斥，包括null。")

    @model_validator(mode='before')
    @classmethod
    def exclusive_mode(cls, value):
        """Args:
            value：原始编辑参数，items 与全部旧字段互斥。
        """
        fields = ('path', 'old_text', 'new_text', 'expected_sha256', 'replacements')
        _mutation_mode(value, fields, ('path', 'expected_sha256'))
        if 'items' not in value:
            validate_edit_shape(value)
        return value


# 模型暴露与上下文预算共用同一份参数 schema，字段说明移动后也不能漏算。
WORKSPACE_TOOL_SCHEMAS = {
    'workspace_list': WorkspaceListInput, 'workspace_read': WorkspaceReadInput, 'workspace_search': WorkspaceSearchInput,
    'workspace_write': WorkspaceWriteInput, 'workspace_edit': WorkspaceEditInput,
    'workspace_run_command': WorkspaceCommandInput, 'workspace_run_shell': WorkspaceShellInput,
    'workspace_start_service': ServiceStartInput, 'workspace_service_status': ServiceStatusInput,
    'workspace_service_logs': ServiceLogInput, 'workspace_stop_service': ServiceIdInput,
}


def _tool_parameters(name: str) -> dict:
    """获取模型实际使用的参数结构，包括展开后的嵌套字段说明。

    Args:
        name：已登记的工作区工具名。
    """
    definition = StructuredTool(name=name, description=_tool_description(name), args_schema=WORKSPACE_TOOL_SCHEMAS[name])
    return convert_to_openai_tool(definition)['function']['parameters']


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
        conversation_id：本轮单聊或串行群聊会话。
        role：当前执行角色。
        triggered_by_user_id：调度器绑定的触发者。
        allow_dangerous：链路是否获准使用 dangerous 工具。
    """
    from ..workflows.allocations import tools_for
    allocated = await tools_for(session, execution_id)
    enabled = tuple(name for name in WORKSPACE_TOOLS if name != 'workspace_service_status' and name in (role.builtin_tools_json or []) and (allocated is None or name in allocated))
    if not enabled or not role.active or role.deleted_at is not None or not allow_dangerous or triggered_by_user_id is None:
        return None
    conversation = await session.get(Conversation, conversation_id)
    user = await session.get(User, triggered_by_user_id)
    execution = await session.scalar(select(AgentExecution).where(
        AgentExecution.execution_id == execution_id,
    ))
    if (
        conversation is None
        or conversation.type not in ("single", "group")
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
    if binding is None or not _enabled_tools(role, binding, conversation.type):
        return None
    # 工具暴露前也复核双方成员、执行类型和取消状态；实际调用仍再次检查。
    enabled = [name for name in _enabled_tools(role, binding, conversation.type) if allocated is None or name in allocated]
    if not enabled:
        return None
    if await authorized_execution(session, execution_id=execution_id, conversation_id=conversation_id,
        role_id=role.id, user_id=user.id, tool_name=enabled[0]) is None:
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
        # lease 只保存授权根快照；真实读写占用在文件操作入口协调。
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
        if not binding.active or tool_name not in _enabled_tools(role, binding, conversation.type):
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
        async def recheck():
            decision = await _service_decision(execution_id=execution_id, conversation_id=conversation_id,
                role_id=role_id, triggered_by_user_id=triggered_by_user_id, workspace_binding_id=workspace_binding_id,
                root_path_snapshot=root_path_snapshot, tool_name=tool_name, query_available=query_available)
            if decision.service is None:
                raise AccessRejected(decision) if mutation else WorkspaceFileError('WORKSPACE_TOOL_NOT_AVAILABLE')
        return AccessDecision(service=WorkspaceFileService(root=root, execution_id=execution_id, authorize=recheck), error_code='')


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
        conversation_id：本轮单聊或串行群聊会话。
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
    conversation = await session.get(Conversation, conversation_id)

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
        from .write_admission import admitted, locked
        try:
            async with admitted(len(json.dumps({'path': path, **arguments}, ensure_ascii=False).encode())):
                async with locked(_COMMAND_CALL_LOCKS.setdefault(lease.workspace_binding_id, asyncio.Lock())):
                    authorized = await mutation_service(tool_name)
                    operation = authorized.write if tool_name == 'workspace_write' else authorized.edit
                    result = await operation(path, **arguments, capture_applied=receipt.applied, _recheck=lambda: mutation_service(tool_name),
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
                details={**(exc.details or {}), **({'created_parent_count': receipt.created_parent_count} if tool_name == 'workspace_write' else {})})
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
                             expected_sha256: str | None = None, items: list | None = None, replacements: list[dict] | None = None) -> str:
        """对已有文件的一个或多个唯一片段校验后提交一次替换。

        Args:
            path：工作区相对路径。
            replacements：同一原始版本的多个非重叠替换，与旧字段互斥。
            old_text：唯一旧片段。
            new_text：替换片段。
            expected_sha256：读取取得的完整文件 hash。
            items：逐文件编辑参数，与旧形式严格互斥。
        """
        if items is not None:
            return await mutate_items('workspace_edit', items)
        # LangChain 对嵌套 schema 返回模型实例，显式转换为执行层的普通字段。
        if replacements is not None:
            replacements = [pair.model_dump() if isinstance(pair, ReplacementInput) else pair for pair in replacements]
        arguments = {'replacements': replacements} if replacements is not None else {'old_text': old_text, 'new_text': new_text}
        return await mutate_file('workspace_edit', path, {**arguments, 'expected_sha256': expected_sha256})

    async def workspace_run_command(command: str, args: dict | None = None) -> str:
        """在获得串行槽后重新鉴权并执行固定命令。

        Args:
            command：服务端登记的命令 ID。
            args：只允许该命令的专用参数。
        """
        from .resource_admission import OperationLock
        async with OperationLock(_COMMAND_CALL_LOCKS.setdefault(lease.workspace_binding_id, asyncio.Lock()),
            lease.root_path_snapshot, execution_id, mode='read'):
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
        """审批等待不占写锁，批准后持原锁复核租用和身份再执行。

        Args:
            script：模型脚本，不能覆盖宿主执行参数。
        """
        from ..agent.tool_context import tool_call_id
        from .approvals import request_and_run
        from .resource_admission import OperationLock
        try:
            from ..runtime.manager import manager
            async with manager.command(**runtime_identity('workspace_run_shell')):
                return _command_result(await request_and_run(script=script, execution_id=execution_id,
                    conversation_id=conversation_id, role_id=role.id, owner_id=triggered_by_user_id,
                    workspace_binding_id=lease.workspace_binding_id, root_path=lease.root_path_snapshot,
                    tool_call_id=tool_call_id.get(),
                    execution_lock=OperationLock(_COMMAND_CALL_LOCKS.setdefault(lease.workspace_binding_id, asyncio.Lock()),
                        lease.root_path_snapshot, execution_id)))
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
            description=_tool_description('workspace_write', edit_available='workspace_edit' in _enabled_tools(role, _binding, conversation.type)),
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
    enabled = {*_enabled_tools(role, _binding, conversation.type), *(tool.name for tool in query_tools)}
    from ..workflows.allocations import tools_for
    allocated = await tools_for(session, execution_id)
    selected = [raw[name] for name in WORKSPACE_TOOLS if name in enabled and (allocated is None or name in allocated)]
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
        or conversation.type not in ("single", "group")
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
            exposed = _enabled_tools(role, binding, conversation.type)
            if exposed and await authorized_member(session, conversation_id=conversation.id, role_id=role.id,
                user_id=triggered_by_user_id, tool_name=exposed[0]) is None:
                exposed = []
        except WorkspacePathError:
            pass
    if await authorized_member(session, conversation_id=conversation.id, role_id=role.id,
        user_id=triggered_by_user_id, tool_name='workspace_service_status') is not None:
        exposed.append('workspace_service_status')
    return {
        "version": WORKSPACE_TOOL_POLICY_VERSION,
        **({"workspace_binding_id": binding.id, "workspace_kind": binding.workspace_kind} if binding else {}),
        "exposed_tools": [
            {"name": name, "description": _tool_description(name, edit_available='workspace_edit' in exposed),
             "parameters": _tool_parameters(name)}
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
