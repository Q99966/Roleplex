"""流程定义、冻结运行与节点尝试；不复制工具正文或另建 Trace。"""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from ..db import Base


class WorkflowDefinition(Base):
    """可编辑定义；运行持有独立快照，不随新版改变。"""
    __tablename__ = 'workflow_definitions'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    graph: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index('ix_workflow_definition_conversation', 'conversation_id'),)


class WorkflowRun(Base):
    """一次流程的控制状态；selected 仅选择当前尝试，旧尝试始终保留。"""
    __tablename__ = 'workflow_runs'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    definition_id: Mapped[str] = mapped_column(ForeignKey('workflow_definitions.id', ondelete='CASCADE'), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    definition_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    # 历史身份快照不随资源删除 SET NULL，否则会把解绑误认成同一运行环境。
    workspace_binding_id: Mapped[int | None] = mapped_column(Integer)
    workspace_root: Mapped[str | None] = mapped_column(Text)
    chain_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    trigger_message_id: Mapped[int | None] = mapped_column(ForeignKey('messages.id', ondelete='SET NULL'))
    request_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    cursor: Mapped[int] = mapped_column(Integer, nullable=False)
    selected: Mapped[dict] = mapped_column(JSON, nullable=False)
    rerun_downstream: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint('conversation_id', 'request_key', name='uq_workflow_start_request'),
        Index('ix_workflow_run_conversation_status', 'conversation_id', 'status'))


class WorkflowAttempt(Base):
    """一个节点的一次尝试，关联准确输入、上游版本和原始执行身份。"""
    __tablename__ = 'workflow_attempts'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('workflow_runs.id', ondelete='CASCADE'), nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    upstream_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    retry_source_id: Mapped[str | None] = mapped_column(String(64))
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    input_message_id: Mapped[int | None] = mapped_column(ForeignKey('messages.id', ondelete='SET NULL'))
    generation_id: Mapped[int | None] = mapped_column(ForeignKey('generations.id', ondelete='SET NULL'), unique=True)
    execution_id: Mapped[str | None] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='SET NULL'), unique=True)
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint('run_id', 'node_id', 'number', name='uq_workflow_node_attempt'),)
