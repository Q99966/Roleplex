from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def json_dict() -> dict[str, Any]:
    """为可变 ORM 字段返回全新的 JSON 对象默认值。"""
    return {}


def json_list() -> list[Any]:
    """为可变 ORM 字段返回全新的 JSON 数组默认值。"""
    return []


class InstanceSettings(Base):
    """用于分配和保存 Owner 的实例单例状态。"""
    __tablename__ = "instance_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class User(Base):
    """本地账号；Owner 状态和 Token 版本由服务端控制。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str] = mapped_column(String(128), nullable=False)
    avatar: Mapped[str | None] = mapped_column(String(512))
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelConfig(Base):
    """Owner 范围内的模型厂商配置，Key 材料以加密形式保存。"""

    __tablename__ = "model_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512))
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    capability_overrides_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=json_dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Role(Base):
    """Owner 创建的 Agent 定义及其序列化能力配置。

    删除采用墓碑：保留 id、名称、头像与删除时间，清除系统提示词、模型绑定、
    技能与 MCP 配置，使历史消息永远能显示"谁说的"，同时不再残留可用配置。
    """

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    avatar: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    tags_json: Mapped[list[str]] = mapped_column(JSON, default=json_list, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # 墓碑需要清除模型绑定，因此允许为空；仍在使用的角色由服务层保证非空。
    model_config_id: Mapped[int | None] = mapped_column(ForeignKey("model_configs.id", ondelete="RESTRICT"))
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # 属于角色实际选择的模型，不放入会透传给 Provider 的 params_json。
    context_window_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=200_000)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=json_dict, nullable=False)
    skills_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=json_list, nullable=False)
    builtin_tools_json: Mapped[list[str]] = mapped_column(JSON, default=json_list, nullable=False)
    mcp_servers_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=json_list, nullable=False)
    mcp_tools_cache_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=json_list, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # 只对未删除的角色约束重名：墓碑保留原名用于历史展示，同时允许立刻新建同名角色。
        # 部分索引在 SQLite 与 PostgreSQL 上都支持，不依赖单一数据库的专属特性。
        Index(
            "uq_role_owner_name_active", "created_by", "name", unique=True,
            sqlite_where=text("deleted_at IS NULL"), postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class Conversation(Base):
    """共享聊天元数据；个人置顶和归档状态保存在成员记录中。

    删除采用回收站：只写入 `deleted_at`，会话立即从列表消失但数据仍在，
    保留期内可以恢复；超过保留期后由启动清理真正级联删除。
    """

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="single")
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    orchestrator_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    orchestrator_role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id", ondelete="SET NULL"))
    workspace_binding_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspace_bindings.id", ondelete="SET NULL")
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # 启动清理按删除时间扫描过期会话，列表查询按该列过滤未删除会话。
        Index("ix_conversations_deleted_at", "deleted_at"),
    )


class ConversationMember(Base):
    """支持用户和角色的多态成员记录，并保存用户级偏好。"""

    __tablename__ = "conversation_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    member_type: Mapped[str] = mapped_column(String(16), nullable=False)
    member_id: Mapped[int] = mapped_column(Integer, nullable=False)
    last_read_message_id: Mapped[int | None] = mapped_column(Integer)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("conversation_id", "member_type", "member_id", name="uq_conversation_member"),
        Index("ix_members_conversation", "conversation_id"),
    )


class Invite(Base):
    """为后续 Guest 入群流程预留的有限次邀请状态。"""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Message(Base):
    """带有序号、版本和生成状态的会话消息。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_id: Mapped[int | None] = mapped_column(Integer)
    reply_to_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    client_message_id: Mapped[str | None] = mapped_column(String(128))
    mentions_json: Mapped[list[Any]] = mapped_column(JSON, default=json_list, nullable=False)
    parts_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=json_list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="done")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    chain_id: Mapped[str | None] = mapped_column(String(64))
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=json_dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_messages_conversation_id_id", "conversation_id", "id"),
        Index("ix_messages_conversation_pinned", "conversation_id", "pinned"),
        UniqueConstraint("conversation_id", "sender_id", "client_message_id", name="uq_message_client_key"),
    )


class Generation(Base):
    """一次 Agent 生成的状态和 epoch；用于停止、恢复和链路关联。"""

    __tablename__ = "generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    assistant_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    stream_epoch: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    run_id: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_generations_conversation_status", "conversation_id", "status"),)


class AgentExecution(Base):
    """一次 Agent 实际执行的持久身份、归属和终态。

    `generations` 继续拥有消息流生命周期；本表负责 execution ID、角色、chain、
    execution kind 和后续 M4b 父子关系，队列 JSON 与日志不得替代本表。
    """

    __tablename__ = "agent_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_executions.execution_id", ondelete="CASCADE")
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("generations.id", ondelete="CASCADE"), nullable=False
    )
    chain_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id", ondelete="SET NULL"))
    execution_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    dispatch_order: Mapped[int | None] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    task_text: Mapped[str | None] = mapped_column(Text)
    context_hint_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("execution_id", name="uq_agent_executions_execution_id"),
        UniqueConstraint("generation_id", name="uq_agent_executions_generation"),
        UniqueConstraint(
            "parent_execution_id", "dispatch_order", "attempt",
            name="uq_agent_execution_dispatch_attempt",
        ),
        Index("ix_agent_executions_conversation_status", "conversation_id", "status"),
        Index(
            "ix_agent_executions_parent_order_attempt",
            "parent_execution_id", "dispatch_order", "attempt",
        ),
        Index("ix_agent_executions_chain_id", "chain_id"),
    )


class WorkspaceBinding(Base):
    """当前 World Owner 从前端登记的主机绝对工作目录。"""

    __tablename__ = "workspace_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    root_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    workspace_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="managed_directory")
    file_tools_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    basic_commands_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shell_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_workspace_owner_name_active", "created_by", "display_name", unique=True,
            sqlite_where=text("active IS TRUE"), postgresql_where=text("active IS TRUE"),
        ),
        UniqueConstraint("root_path", name="uq_workspace_root_path"),
        Index("ix_workspace_bindings_owner_active", "created_by", "active"),
    )


class ExecutionWorkspace(Base):
    """一次 Agent execution 对 managed directory 的持久租用快照。"""

    __tablename__ = "execution_workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("agent_executions.execution_id", ondelete="CASCADE"), nullable=False
    )
    workspace_binding_id: Mapped[int] = mapped_column(
        ForeignKey("workspace_bindings.id", ondelete="CASCADE"), nullable=False
    )
    workspace_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    root_path_snapshot: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("execution_id", name="uq_execution_workspaces_execution_id"),
        Index("ix_execution_workspaces_binding_status", "workspace_binding_id", "status"),
    )


class EventLog(Base):
    """持久化会话事件；实时 EventHub 只负责提交后的进程内广播。"""

    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    stream_epoch: Mapped[str | None] = mapped_column(String(128))
    generation_id: Mapped[int | None] = mapped_column(ForeignKey("generations.id", ondelete="SET NULL"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    delta_seq: Mapped[int | None] = mapped_column(Integer)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=json_dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("conversation_id", "event_seq", name="uq_event_conversation_seq"),
        Index("ix_event_conversation_seq", "conversation_id", "event_seq"),
    )


class QueueJob(Base):
    """单进程会话队列的持久化唤醒任务；execution 身份另存规范化表。"""

    __tablename__ = "queue_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    generation_id: Mapped[int | None] = mapped_column(ForeignKey("generations.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=json_dict, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_jobs_conversation_status", "conversation_id", "status"),)


class Artifact(Base):
    """会话产物的身份记录，其版本作为不可变引用。"""

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    language: Mapped[str | None] = mapped_column(String(64))
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ArtifactVersion(Base):
    """由消息 part 引用的不可变产物内容快照。"""

    __tablename__ = "artifact_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("artifact_id", "version", name="uq_artifact_version"),)


class Attachment(Base):
    """上传文件的元数据；存储路径由服务端生成。"""

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)


class ToolCall(Base):
    """记录工具授权、执行过程和结果的审计记录。"""

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id", ondelete="SET NULL"))
    triggered_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_executions.execution_id", ondelete="SET NULL")
    )
    workspace_binding_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspace_bindings.id", ondelete="SET NULL")
    )
    tool_name: Mapped[str] = mapped_column(String(256), nullable=False)
    args_summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_tool_calls_conversation_id_id", "conversation_id", "id"),
        Index("ix_tool_calls_execution_id", "execution_id"),
    )
