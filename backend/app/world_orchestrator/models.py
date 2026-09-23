"""世界任命与每次执行的授权凭据；消息、工具及用量复用既有表。"""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ..db import Base


class WorldOrchestrator(Base):
    __tablename__ = 'world_orchestrator'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    role_id: Mapped[int | None] = mapped_column(ForeignKey('roles.id', ondelete='SET NULL'))
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey('conversations.id', ondelete='SET NULL'), unique=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorldCoordinationGrant(Base):
    __tablename__ = 'world_coordination_grants'
    execution_id: Mapped[str] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    role_id: Mapped[int] = mapped_column(Integer, nullable=False)
    appointment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey('world_tasks.id', ondelete='SET NULL'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorldTask(Base):
    """世界目标及其准确子任务关系，不替代已有执行/工作流事实。"""
    __tablename__ = 'world_tasks'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    trigger_message_id: Mapped[int] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'), nullable=False, unique=True)
    root_execution_id: Mapped[str] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), nullable=False)
    chain_id: Mapped[str] = mapped_column(ForeignKey('workflow_budgets.chain_id', ondelete='CASCADE'), nullable=False, unique=True)
    role_id: Mapped[int] = mapped_column(Integer, nullable=False)
    appointment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    scope_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default='queued')
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    feedback_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorldTaskChild(Base):
    __tablename__ = 'world_task_children'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('world_tasks.id', ondelete='CASCADE'), nullable=False)
    parent_execution_id: Mapped[str] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey('conversations.id', ondelete='SET NULL'))
    coordination_id: Mapped[str | None] = mapped_column(ForeignKey('coordination_sessions.id', ondelete='SET NULL'), unique=True)
    chain_id: Mapped[str | None] = mapped_column(ForeignKey('workflow_budgets.chain_id', ondelete='SET NULL'), unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default='pending_dispatch')
    reference_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint('task_id', 'request_key', name='uq_world_task_child_request'),)


class WorldMemory(Base):
    """有来源和版本的世界约定；普通角色历史检索不直接读取此表。"""
    __tablename__ = 'world_memories'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default='active')
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    source_message_id: Mapped[int | None] = mapped_column(Integer)
    source_revision: Mapped[int | None] = mapped_column(Integer)
    source_role_id: Mapped[int | None] = mapped_column(Integer)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint('owner_id', 'request_key', name='uq_world_memory_request'),)


class WorldMemoryVersion(Base):
    __tablename__ = 'world_memory_versions'
    memory_id: Mapped[str] = mapped_column(ForeignKey('world_memories.id', ondelete='CASCADE'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
