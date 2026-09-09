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


class WorkspaceCreate(BaseModel):
    """Owner 登记主机绝对目录或创建精确空目录的请求。"""

    display_name: str = Field(min_length=1, max_length=128)
    root_path: str = Field(min_length=1, max_length=2048)
    create_directory: bool = False
    acknowledge_existing_content: bool = False


class WorkspaceUpdate(BaseModel):
    """调整生命周期、原生文件与 W1b 结构化命令能力；Shell 仍未开放。"""

    active: bool | None = None
    file_tools_enabled: bool | None = None
    basic_commands_enabled: bool | None = None


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


class MessageCreate(BaseModel):
    """包含提及、回复和可选客户端幂等信息的消息请求。"""

    parts: list[Part] = Field(min_length=1)
    mentions: list[int | Literal["all"]] = Field(default_factory=list)
    reply_to_id: int | None = None
    client_message_id: str | None = Field(default=None, max_length=128)


class EventEnvelope(BaseModel):
    """可恢复会话事件使用的版本化事件结构。"""

    stream_epoch: str
    event_seq: int
    type: str
    conversation_id: int
    payload: dict[str, Any]
