from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class RegisterRequest(BaseModel):
    """注册请求；密码只作为写入参数接收，不会回显。"""

    username: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    password: SecretStr = Field(min_length=8, max_length=256)
    nickname: str = Field(min_length=1, max_length=128)


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
    """包含 Bearer Token 和公开用户资料的认证响应。"""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserResponse


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
    params: dict[str, Any] = Field(default_factory=dict)
    skills: list[dict[str, str]] = Field(default_factory=list)
    builtin_tools: list[str] = Field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)


class RoleResponse(BaseModel):
    """返回给角色所属用户的公开 Agent 定义。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    avatar: str | None
    description: str | None
    tags: list[str]
    system_prompt: str
    model_config_id: int
    model_name: str
    params: dict[str, Any]
    skills: list[dict[str, Any]]
    builtin_tools: list[str]
    mcp_servers: list[dict[str, Any]]
    active: bool
    created_at: datetime
    updated_at: datetime


class ConversationCreate(BaseModel):
    """会话创建请求；角色成员规则由路由层继续校验。"""

    type: Literal["single", "group"] = "single"
    title: str = Field(min_length=1, max_length=256)
    role_ids: list[int] = Field(default_factory=list, max_length=50)
    orchestrator_enabled: bool = False
    orchestrator_role_id: int | None = None


class ConversationResponse(BaseModel):
    """合并共享会话状态和请求用户个人偏好的响应。"""

    id: int
    type: str
    title: str
    orchestrator_enabled: bool
    orchestrator_role_id: int | None
    last_message_at: datetime | None
    pinned: bool
    archived: bool


class Part(BaseModel):
    """可扩展的消息 part 信封，保留未知字段以支持向前兼容。"""

    type: str
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
