from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
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
    process_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    process_limit_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decision_limit: Mapped[int | None] = mapped_column(BigInteger().evaluates_none(), nullable=True, default=8)
    budget_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    platform_prompt_override: Mapped[str | None] = mapped_column(Text)
    world_prompt: Mapped[str] = mapped_column(Text, nullable=False, default='', server_default='')
    prompt_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    prompt_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
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
    process_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    process_limit_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="single")
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default='', server_default='')
    prompt_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    prompt_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    orchestrator_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
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

    __table_args__ = (Index("ix_generations_conversation_status", "conversation_id", "status"),
        Index("ix_generations_assistant_message", "assistant_message_id"))


class WorkflowBudget(Base):
    """同一用户消息链的冻结预算，角色发言不会重置额度。"""
    __tablename__ = 'workflow_budgets'
    chain_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    # 独立上下文维护请求没有聊天消息；它仍使用唯一 chain 和世界预算快照。
    trigger_message_id: Mapped[int | None] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'), nullable=True, unique=True)
    decision_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    used_decisions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    configuration_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
    usage_tracked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    decision_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    task_text: Mapped[str | None] = mapped_column(Text)
    context_hint_text: Mapped[str | None] = mapped_column(Text)
    # 只存本次采用的配置版本、指纹和工具身份，正文仍由其业务配置持有。
    context_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
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
        Index("ix_agent_executions_conversation_role", "conversation_id", "role_id", "id"),
    )


class ModelCallUsage(Base):
    """一次框架模型调用的观测记录，不包含正文、凭据或 HTTP 重试推算。"""
    __tablename__ = 'model_call_usage'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), nullable=False)
    call_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='started')
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cache_hit_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cache_write_tokens: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_estimate_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint('execution_id','call_index',name='uq_model_call_usage_execution_index'),)


class ConversationContext(Base):
    """每个会话唯一的持久材料版本；不按角色复制，也不保存 Provider 用量。"""
    __tablename__ = 'conversation_contexts'
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    summary_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default='0')
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConversationContextEntry(Base):
    """原消息的共享投影及来源版本；正文仅在终态或来源修订时更新。

    pending/excluded 保留原因和来源，不将未完成回复伪装成稳定历史。
    ORM 消息所有者的事务同时维护本表；原消息仍是来源事实。
    """
    __tablename__ = 'conversation_context_entries'
    message_id: Mapped[int] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversation_contexts.conversation_id', ondelete='CASCADE'), nullable=False)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_status: Mapped[str] = mapped_column(String(32), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sender_id: Mapped[int | None] = mapped_column(Integer)
    chain_id: Mapped[str | None] = mapped_column(String(64))
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    search_text: Mapped[str | None] = mapped_column(Text)
    execution_facts_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False, default='', server_default='')
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default='0')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index('ix_context_entries_conversation_message', 'conversation_id', 'message_id'),)


class ContextCompression(Base):
    """一次幂等的上下文维护请求；复用 execution/generation/chain，不伪造聊天消息。"""
    __tablename__ = 'context_compressions'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    role_id: Mapped[int | None] = mapped_column(ForeignKey('roles.id', ondelete='SET NULL'))
    execution_id: Mapped[str] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), nullable=False, unique=True)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 可空唯一值确保同一会话只有一个在途维护请求；终态释放，历史记录不删除。
    active_conversation_id: Mapped[int | None] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), unique=True)
    source_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_summary_id: Mapped[str | None] = mapped_column(String(64))
    base_summary_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    through_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    keep_recent: Mapped[int] = mapped_column(Integer, nullable=False)
    target_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    model_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens_estimate: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_tokens_estimate: Mapped[int | None] = mapped_column(BigInteger)
    completed_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default='queued')
    status: Mapped[str] = mapped_column(String(32), nullable=False, default='queued')
    error_code: Mapped[str | None] = mapped_column(String(64))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint('conversation_id', 'owner_id', 'request_key', name='uq_context_compression_request'),
        Index('ix_context_compressions_conversation_created', 'conversation_id', 'created_at'))


class ContextCompressionSource(Base):
    """冻结的来源身份；消息删除后仍保留 ID，以便明确判定旧摘要失效。"""
    __tablename__ = 'context_compression_sources'
    compression_id: Mapped[str] = mapped_column(ForeignKey('context_compressions.id', ondelete='CASCADE'), primary_key=True)
    message_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_status: Mapped[str] = mapped_column(String(16), nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ContextSummary(Base):
    """校验后发布的不可变摘要；活动指针可回退，原消息及后来追加的尾部保持原样。"""
    __tablename__ = 'context_summaries'
    id: Mapped[str] = mapped_column(ForeignKey('context_compressions.id', ondelete='CASCADE'), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    active_conversation_id: Mapped[int | None] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), unique=True)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MemoryReference(Base):
    """工具已读取的来源凭据；不保存查询词、原文或另一套 Trace。"""
    __tablename__ = 'memory_references'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    characters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index('ix_memory_references_execution', 'execution_id', 'created_at'),)


class WorkspaceBinding(Base):
    """当前 World Owner 从前端登记的主机绝对工作目录。"""

    __tablename__ = "workspace_bindings"
    process_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    process_limit_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    services_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

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


class ToolApprovalRequest(Base):
    """Owner 逐次 Shell 审批；只保存加密请求，终态不授予重放权限。"""

    __tablename__ = 'tool_approval_requests'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), nullable=False)
    workspace_binding_id: Mapped[int | None] = mapped_column(ForeignKey('workspace_bindings.id', ondelete='SET NULL'))
    tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(256), nullable=False)
    request_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    __table_args__ = (
        UniqueConstraint('execution_id', 'tool_call_id', name='uq_approval_execution_call'),
        Index('ix_approval_status_expires', 'status', 'expires_at'),
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


class ToolExecutionDetail(Base):
    """Owner 私有执行详情；原始内容只以当前 World 密文保存。"""

    __tablename__ = 'tool_execution_details'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'), nullable=False)
    execution_id: Mapped[str] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), nullable=False)
    call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_encrypted: Mapped[str | None] = mapped_column(Text)
    output_encrypted: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint('message_id', 'call_id', name='uq_tool_detail_message_call'),
        Index('ix_tool_detail_expires_at', 'expires_at'),
    )


# 迁移和运行时共用同一 metadata 入口；不在业务路径执行 create_all。
from .runtime.models import CleanupItem, CleanupOperation, RuntimeEntry, RuntimeGate  # noqa: E402,F401


class FileEffect(Base):
    """单文件操作边界的加密事实，写前和提交后更新同一身份。"""
    __tablename__='file_effects'
    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey('agent_executions.execution_id',ondelete='CASCADE'),nullable=False)
    call_id: Mapped[str] = mapped_column(String(128),nullable=False)
    item_index: Mapped[int] = mapped_column(Integer,nullable=False)
    payload_encrypted: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False)
    __table_args__=(UniqueConstraint('execution_id','call_id','item_index',name='uq_file_effect_call_item'),
        Index('ix_file_effect_execution_id','execution_id','id'))

# 统一 Alembic metadata 入口；工作流只关联原执行记录。
from .workflows.models import WorkflowDefinition, WorkflowRun, WorkflowAttempt, WorkflowActivation, ExecutionAllocation  # noqa: E402,F401

from .workflows.models import CoordinationSession, WorkflowGraphRevision
from .workflows.models import WorkflowFeedback, WorkflowFeedbackEvent
