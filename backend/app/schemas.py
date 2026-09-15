from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class RegisterRequest(BaseModel):
    """注册请求；密码只作为写入参数接收，不会回显。

    密码规则不写在这里：长度和字符类别由 `security.passwords` 统一判定，
    以便注册和改密返回同一个稳定错误码，而不是笼统的参数校验错。
    此处的 `max_length` 只是防止超大请求体的外层护栏，正常不合规密码
    会先被策略拦下。
    """

    username: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    password: SecretStr = Field(max_length=256)
    nickname: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    """改密请求；校验旧密码后写入新密码并撤销全部旧 Token。"""

    current_password: SecretStr = Field(max_length=256)
    new_password: SecretStr = Field(max_length=256)


class LoginRequest(BaseModel):
    """用于签发绑定 Token 版本的访问 Token 的凭据请求。"""

    username: str
    password: SecretStr


class UserResponse(BaseModel):
    """安全的公开用户资料，不包含密码和 Token 状态。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    nickname: str
    avatar: str | None
    is_owner: bool


class TokenResponse(BaseModel):
    """包含 Bearer Token 和公开用户资料的认证响应。

    `password_reset_required` 显式回传待改密状态，使前端不必解析 JWT：
    为 true 时除改密和查看本人资料外的接口都会被拒绝，客户端应直接进入重置流程。
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserResponse
    password_reset_required: bool = False


class ModelConfigCreate(BaseModel):
    """Owner 专用的模型厂商配置请求；API Key 只允许写入。"""

    name: str = Field(min_length=1, max_length=128)
    provider_type: Literal["anthropic", "openai_compatible"]
    base_url: str | None = Field(default=None, max_length=512)
    api_key: SecretStr
    capability_overrides: dict[str, Any] = Field(default_factory=dict)


class ModelConfigResponse(BaseModel):
    """模型厂商配置响应，只返回脱敏后的 Key 信息。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    provider_type: str
    base_url: str | None
    api_key_hint: str
    capability_overrides: dict[str, Any]
    created_at: datetime


class RoleCreate(BaseModel):
    """包含提示词、技能、工具和 MCP 元数据的 Agent 定义请求。"""

    name: str = Field(min_length=1, max_length=128)
    avatar: str | None = None
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    system_prompt: str = Field(min_length=1, max_length=100_000)
    model_config_id: int
    model_name: str = Field(min_length=1, max_length=128)
    context_window_tokens: int = Field(default=200_000, ge=4_096, le=2_000_000)
    params: dict[str, Any] = Field(default_factory=dict)
    skills: list[dict[str, str]] = Field(default_factory=list)
    builtin_tools: list[str] = Field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def output_must_fit_context_window(self) -> "RoleCreate":
        """拒绝最大输出已经占满整个上下文窗口的角色配置。"""
        max_tokens = self.params.get("max_tokens")
        if isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and max_tokens >= self.context_window_tokens:
            raise ValueError("max_tokens 必须小于 context_window_tokens")
        return self


class RoleResponse(BaseModel):
    """返回给角色所属用户的公开 Agent 定义。

    `deleted_at` 非空表示墓碑：身份信息保留供历史消息展示，配置已被清空、
    不能再选进会话或触发生成，`model_config_id` 也随之为空。
    """

    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    avatar: str | None
    description: str | None
    tags: list[str]
    system_prompt: str
    model_config_id: int | None
    model_name: str
    context_window_tokens: int
    context_window_ceiling_tokens: int
    effective_context_window_tokens: int
    params: dict[str, Any]
    skills: list[dict[str, Any]]
    builtin_tools: list[str]
    mcp_servers: list[dict[str, Any]]
    active: bool
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ConversationCreate(BaseModel):
    """会话创建请求；角色成员规则由路由层继续校验。"""

    type: Literal["single", "group"] = "single"
    title: str = Field(min_length=1, max_length=256)
    role_ids: list[int] = Field(default_factory=list, max_length=50)
    orchestrator_enabled: bool = False
    orchestrator_role_id: int | None = None
    workspace_binding_id: int | None = None


class ConversationResponse(BaseModel):
    """合并共享会话状态、角色成员和请求用户个人偏好的响应。

    `deleted_at` 非空表示会话在回收站中：不出现在普通列表里，保留期内可恢复。
    `role_ids` 不包含已删除的角色，避免成员列表出现指向墓碑的孤儿项。
    """

    id: int
    type: str
    title: str
    orchestrator_enabled: bool
    orchestrator_role_id: int | None
    workspace_binding_id: int | None
    role_ids: list[int] = Field(default_factory=list)
    revision: int
    last_message_at: datetime | None
    pinned: bool
    archived: bool
    deleted_at: datetime | None = None


class ConversationMembersUpdate(BaseModel):
    """Owner 更新群聊角色成员时使用的乐观锁请求。"""

    role_ids: list[int] = Field(max_length=50)
    expected_revision: int = Field(ge=0)


class ConversationWorkspaceUpdate(BaseModel):
    """Owner 为 single 会话绑定或解绑当前 World 工作区的乐观锁请求。"""

    workspace_binding_id: int | None
    expected_revision: int = Field(ge=0)
    confirm_cleanup: bool = False


class WorkspaceCreate(BaseModel):
    """Owner 登记主机绝对目录或创建精确空目录的请求。"""

    display_name: str = Field(min_length=1, max_length=128)
    root_path: str = Field(min_length=1, max_length=2048)
    create_directory: bool = False
    acknowledge_existing_content: bool = False


class WorkspaceUpdate(BaseModel):
    """调整生命周期和工具能力；停用时回收运行实例需显式确认。"""

    active: bool | None = None
    file_tools_enabled: bool | None = None
    basic_commands_enabled: bool | None = None
    shell_enabled: bool | None = None
    confirm_cleanup: bool = False


class WorkspaceResponse(BaseModel):
    """只向当前 World Owner 返回的 Workspace Binding 表示。"""

    id: int
    display_name: str
    root_path: str
    workspace_kind: str
    file_tools_enabled: bool
    basic_commands_enabled: bool
    shell_enabled: bool
    active: bool
    availability: Literal["available", "unavailable", "busy", "disabled"]
    last_validated_at: datetime | None
    bound_conversation_count: int
    created_at: datetime
    updated_at: datetime


class Part(BaseModel):
    """可扩展的消息 part 信封，保留未知字段以支持向前兼容。"""

    type: str
    text: str | None = None
    model_config = ConfigDict(extra="allow")


# 仅由服务器写入消息元数据，正常结束由已有 status=done 表达。
StopReason = Literal['user_cancelled', 'graph_budget', 'provider_failed', 'protocol_error', 'interrupted', 'context_rejected']


class MessageCreate(BaseModel):
    """包含提及、回复和可选客户端幂等信息的消息请求。"""

    parts: list[Part] = Field(min_length=1)
    mentions: list[int | Literal["all"]] = Field(default_factory=list)
    reply_to_id: int | None = None
    client_message_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode='after')
    def reject_server_facts(self):
        """服务器摘要不能由消息发送接口伪造。"""
        if any(part.type == 'execution_summary' for part in self.parts):
            from pydantic_core import PydanticCustomError
            raise PydanticCustomError('server_part_forbidden', '服务器执行摘要不能由客户端提交')
        return self


class EventEnvelope(BaseModel):
    """可恢复会话事件使用的版本化事件结构。"""

    stream_epoch: str
    event_seq: int
    type: str
    conversation_id: int
    payload: dict[str, Any]


class HistoryWindowMetadata(BaseModel):
    """完整消息窗口的共享元数据；字节数计入实际传输信封。"""

    has_more: bool = False
    next_cursor: str | None = None
    oversized: bool = False
    page_bytes: int = Field(default=0, ge=0)


class ShellApprovalDecision(BaseModel):
    """只允许决定当前所见请求，拒绝覆写脚本或执行上下文。"""

    model_config = ConfigDict(extra='forbid')
    decision: Literal['approve', 'reject']
    request_digest: str = Field(pattern=r'^[0-9a-f]{64}$')


class ToolCaptureView(BaseModel):
    """Owner 私有正文与原始字节/截断信息；校验失败不得回显输入。"""

    model_config = ConfigDict(hide_input_in_errors=True)
    text: str
    bytes: int = Field(ge=0)
    truncated: bool


class BatchReadResultView(BaseModel):
    """一项实际读取的版本与有界片段，不代表统一快照。"""
    model_config = ConfigDict(hide_input_in_errors=True, extra='forbid')
    text: str = Field(max_length=65536)
    bytes: int = Field(ge=0, le=65536)
    eof: bool
    next_offset: int = Field(ge=0, le=2**63 - 1)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class LineReadResultView(BaseModel):
    """完整行范围结果；版本与扫描量不等同于返回正文大小。"""
    model_config = ConfigDict(hide_input_in_errors=True, extra='forbid')
    mode: Literal['lines']
    text: str = Field(max_length=65536)
    bytes: int = Field(ge=0, le=65536)
    start_line: int = Field(ge=1)
    start_offset: int | None = Field(default=None, ge=0)
    end_line: int | None = Field(ge=1)
    next_line: int = Field(ge=1)
    eof: bool
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    scanned_bytes: int = Field(ge=0, le=64 * 1024 * 1024)
    limited_reason: Literal['line_too_long', 'content_budget', 'json_budget'] | None


class SearchContextView(BaseModel):
    """只供 Owner/模型查看的有界行片段。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    line_number: int = Field(ge=1)
    text: str = Field(max_length=512)
    truncated: bool


class SearchMatchView(BaseModel):
    """搜索命中，文件名匹配不得伪造行号或完整版本。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    path: str = Field(max_length=1024)
    line_number: int | None = Field(ge=1)
    text: str | None = Field(max_length=512)
    truncated: bool = False
    context_before: list[SearchContextView] = Field(max_length=3)
    context_after: list[SearchContextView] = Field(max_length=3)
    sha256: str | None = Field(pattern=r'^[a-f0-9]{64}$')
    version_confirmed: bool
    matched_queries: list[int] = Field(default_factory=list, max_length=8)


class SearchIssueView(BaseModel):
    """受限扫描的原因，长路径可省略但不伪造截断后的目标身份。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    reason: str = Field(max_length=80)
    path: str | None = Field(max_length=1024)


class SearchResultView(BaseModel):
    """完整 JSON 再经 64 KiB 校验的搜索结果。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    version: Literal[1]
    status: Literal['complete', 'partial']
    matches: list[SearchMatchView] = Field(max_length=200)
    issues: list[SearchIssueView] = Field(max_length=8)
    truncated: bool
    scanned_files: int = Field(ge=0, le=500)
    scanned_bytes: int = Field(ge=0, le=256 * 1024 * 1024 + 1)
    visited_entries: int = Field(ge=0, le=10001)


class BatchReadItemView(BaseModel):
    """本次调用中的稳定文件节点，失败/未开始没有伪造读取结果。"""
    model_config = ConfigDict(hide_input_in_errors=True, extra='forbid')
    id: str = Field(pattern=r'^item-[0-7]$')
    operation: Literal['read']
    path: str = Field(max_length=1024)
    status: Literal['pending', 'running', 'success', 'failed', 'rejected', 'cancelled', 'not_executed', 'budget_exhausted']
    error_code: str | None = Field(max_length=80)
    output_limited: bool
    result: BatchReadResultView | LineReadResultView | None


class BatchReadDetailView(BaseModel):
    """Owner 读取批次；父身份仍是外层原始 message/call，不新增 execution。"""
    model_config = ConfigDict(hide_input_in_errors=True, extra='forbid')
    version: Literal[1]
    status: Literal['running', 'success', 'partial', 'failed', 'cancelled', 'rejected']
    error_code: str | None = Field(max_length=80)
    items: list[BatchReadItemView] = Field(min_length=1, max_length=8)


class WriteBlockerView(BaseModel):
    """有状态查询权限时，最多披露本会话的必要服务身份。"""
    model_config = ConfigDict(hide_input_in_errors=True, extra='forbid')
    runtime_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    state: str = Field(max_length=32)


class WriteDiagnosticView(BaseModel):
    """只描述本次被拒绝的写入项，不代表批次之前没有提交。"""
    model_config = ConfigDict(hide_input_in_errors=True, extra='forbid')
    version: Literal[1]
    reason: Literal['service_active', 'service_stopping', 'cleanup_required', 'scope_closing', 'scope_cleanup',
        'capability_changed', 'binding_changed', 'lease_unavailable', 'workspace_unavailable']
    scope: Literal['world', 'workspace', 'conversation', 'tool']
    executed: Literal[False]
    message: str = Field(max_length=300)
    next_steps: list[str] = Field(max_length=3)
    recommended_tool: Literal['workspace_service_status'] | None
    services: list[WriteBlockerView] | None = Field(max_length=3)
    services_truncated: bool
    other_sessions_blocking: bool | None


class FileDiffLineView(BaseModel):
    """私有差异的一行；不适用的行号显式为 null。"""
    model_config = ConfigDict(hide_input_in_errors=True, extra='forbid')
    kind: Literal['context', 'insert', 'delete']
    old_line: int | None = Field(ge=1)
    new_line: int | None = Field(ge=1)
    text: str = Field(max_length=65536)
    ending: Literal['lf', 'crlf', 'none']


class FileDiffHunkView(BaseModel):
    """项目自己的有界差异块，不依赖第三方组件字段。"""
    model_config = ConfigDict(hide_input_in_errors=True, extra='forbid')
    old_start: int = Field(ge=1)
    old_lines: int = Field(ge=0)
    new_start: int = Field(ge=1)
    new_lines: int = Field(ge=0)
    lines: list[FileDiffLineView] = Field(max_length=1000)


class FileChangeView(BaseModel):
    """一次确认提交的文件节点；统计未知时不得回退为零。"""
    model_config = ConfigDict(hide_input_in_errors=True, extra='forbid')
    id: Literal['file-0']
    path: str = Field(max_length=4096)
    operation: Literal['created', 'modified', 'unchanged']
    applied: bool | None
    before_sha256: str | None = Field(pattern=r'^[a-f0-9]{64}$')
    after_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    before_bytes: int = Field(ge=0)
    after_bytes: int = Field(ge=0)
    added: int | None = Field(ge=0)
    removed: int | None = Field(ge=0)
    hunks: list[FileDiffHunkView] = Field(max_length=1000)


class WriteDetailView(BaseModel):
    """D Owner 私有变更扩展；D 只产生一个文件节点。"""
    model_config = ConfigDict(hide_input_in_errors=True, extra='forbid')
    version: Literal[1]
    availability: Literal['recorded', 'partial', 'unavailable', 'not_executed', 'result_unconfirmed', 'pending', 'not_recorded']
    reason: Literal['input_budget', 'line_budget', 'queue_full', 'queue_timeout', 'compute_timeout', 'cancelled', 'capture_failed', 'not_text', 'shutdown'] | None
    files: list[FileChangeView] = Field(max_length=1)
    created_parent_count: int | None = Field(default=None, ge=0, le=19, strict=True)


class BatchWriteResultView(BaseModel):
    """已确认提交的精简结果，不包含原文或 diff。"""
    model_config = ConfigDict(hide_input_in_errors=True, extra='forbid')
    created: bool
    bytes: int = Field(ge=0, le=1024 * 1024)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class BatchMutationItemView(BaseModel):
    """批次内的修改事实与原 D 差异对象，不建立独立 Agent execution。"""
    model_config = ConfigDict(hide_input_in_errors=True, extra='forbid')
    id: str = Field(pattern=r'^item-[0-7]$')
    path: str = Field(max_length=1024)
    operation: Literal['write', 'edit']
    status: Literal['not_executed', 'running', 'success', 'failed', 'result_unconfirmed']
    applied: bool | None
    created_parent_count: int | None = Field(default=None, ge=0, le=19, strict=True)
    error_code: str | None = Field(max_length=80)
    result: BatchWriteResultView | None
    write: WriteDetailView | None
    diagnostic: WriteDiagnosticView | None = None


class WriteWaitView(BaseModel):
    """写入等待失败的固定阶段和原因，不含参数或路径。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    phase: Literal['queue', 'lock']
    reason: Literal['queue_full', 'queue_bytes', 'queue_timeout', 'lock_timeout', 'closed']


class BatchMutationDetailView(BaseModel):
    """Owner 私有修改批次：整批字节与行数在响应边界再次核验。"""
    model_config = ConfigDict(hide_input_in_errors=True, extra='forbid')
    version: Literal[1]
    status: Literal['running', 'success', 'partial', 'failed', 'rejected', 'cancelled', 'result_unconfirmed']
    error_code: str | None = Field(max_length=80)
    items: list[BatchMutationItemView] = Field(min_length=1, max_length=8)
    wait_diagnostic: WriteWaitView | None = None


class ShellDetailView(BaseModel):
    """Shell 详情 wire contract，未知执行信息保持 null，不从总耗时推算。"""

    model_config = ConfigDict(hide_input_in_errors=True)
    script: ToolCaptureView | None
    approval_status: Literal['pending', 'approved', 'rejected', 'expired'] | None
    approval_wait_ms: int | None = Field(ge=0)
    execution_duration_ms: int | None = Field(ge=0)
    stdout: ToolCaptureView | None
    stderr: ToolCaptureView | None
    output_availability: Literal['recorded', 'pending', 'not_executed', 'not_recorded']
    execution_status: Literal['exited', 'timed_out', 'cancelled', 'not_executed', 'unavailable'] | None
    exit_code: int | None
