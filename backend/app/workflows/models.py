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
    """一次流程的控制状态；v1 使用 selected，v2 使用独立激活，旧尝试始终保留。"""
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
    runtime_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')
    state_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default='{}')
    graph_revision: Mapped[int | None] = mapped_column(Integer)
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
    activation_id: Mapped[str | None] = mapped_column(ForeignKey('workflow_activations.id', ondelete='CASCADE'))
    graph_revision: Mapped[int | None] = mapped_column(Integer)
    node_snapshot_json: Mapped[dict | None] = mapped_column(JSON)
    phase: Mapped[str] = mapped_column(String(24), nullable=False, default='work', server_default='work')
    result_json: Mapped[dict | None] = mapped_column(JSON)
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


class WorkflowActivation(Base):
    """精确的节点激活；同图同轮重试共享身份，跨图修订与历史轮次独立保留。"""
    __tablename__ = 'workflow_activations'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('workflow_runs.id', ondelete='CASCADE'), nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    loop_id: Mapped[str | None] = mapped_column(String(64))
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    graph_revision: Mapped[int | None] = mapped_column(Integer)
    dependencies_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default='[]')
    selected_attempt_id: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint('run_id', 'node_id', 'iteration', 'graph_revision', name='uq_workflow_activation_graph_round'),
        Index('ix_workflow_activation_run_status', 'run_id', 'status'))


class ExecutionAllocation(Base):
    """任务工具与资源分配，工具工厂/上下文/每次调用复用同一记录。"""
    __tablename__ = 'execution_allocations'
    execution_id: Mapped[str] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), primary_key=True)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey('workflow_attempts.id', ondelete='CASCADE'), unique=True)
    coordination_session_id: Mapped[str | None] = mapped_column(ForeignKey('coordination_sessions.id', ondelete='CASCADE'))
    control_tools_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default='[]')
    tools_json: Mapped[list] = mapped_column(JSON, nullable=False)
    workspace_binding_id: Mapped[int | None] = mapped_column(Integer)
    resource_root: Mapped[str | None] = mapped_column(Text)
    authority_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    waiting_mode: Mapped[str | None] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CoordinationSession(Base):
    """Owner 的一次显式规划/执行/重规划请求；可先于定义和运行存在。"""
    __tablename__ = 'coordination_sessions'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey('roles.id', ondelete='CASCADE'), nullable=False)
    appointment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    # 新草稿槽位不要求预先建假定义；提交时才创建对应 definition。
    definition_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str | None] = mapped_column(ForeignKey('workflow_runs.id', ondelete='CASCADE'))
    started_run_id: Mapped[str | None] = mapped_column(String(64))
    execution_id: Mapped[str | None] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='SET NULL'), unique=True)
    chain_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_message_id: Mapped[int | None] = mapped_column(ForeignKey('messages.id', ondelete='SET NULL'))
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    request_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    constraints_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    feedback_ids_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default='[]')
    feedback_mode: Mapped[str] = mapped_column(String(16), nullable=False, default='manual', server_default='manual')
    workspace_binding_id: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint('conversation_id', 'request_key', name='uq_coordination_request'),
        Index('ix_coordination_conversation_status', 'conversation_id', 'status'))


class WorkflowGraphRevision(Base):
    """不可变图内容和持久修改身份；适用状态区分已采用、未来轮次和未采用。"""
    __tablename__ = 'workflow_graph_revisions'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    definition_id: Mapped[str | None] = mapped_column(ForeignKey('workflow_definitions.id', ondelete='CASCADE'))
    run_id: Mapped[str | None] = mapped_column(ForeignKey('workflow_runs.id', ondelete='CASCADE'))
    target_key: Mapped[str] = mapped_column(String(80), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    graph: Mapped[dict] = mapped_column(JSON, nullable=False)
    mutation_key: Mapped[str | None] = mapped_column(String(64))
    request_digest: Mapped[str | None] = mapped_column(String(64))
    source_execution_id: Mapped[str | None] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='SET NULL'))
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    changes_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default='0')
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint('target_key', 'number', name='uq_workflow_graph_number'),
        UniqueConstraint('target_key', 'mutation_key', name='uq_workflow_graph_mutation'),)


class WorkflowFeedback(Base):
    """来源不可改写的节点意见；处置状态与实际执行终态分别维护。"""
    __tablename__ = 'workflow_feedback'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('workflow_runs.id', ondelete='CASCADE'), nullable=False)
    attempt_id: Mapped[str] = mapped_column(ForeignKey('workflow_attempts.id', ondelete='CASCADE'), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    actor_execution_id: Mapped[str | None] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='SET NULL'))
    source_role_id: Mapped[int | None] = mapped_column(ForeignKey('roles.id', ondelete='SET NULL'))
    graph_revision: Mapped[int | None] = mapped_column(Integer)
    source_result_revision: Mapped[int | None] = mapped_column(Integer)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    summary: Mapped[str] = mapped_column(String(240), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False)
    requested_tools: Mapped[list] = mapped_column(JSON, nullable=False)
    capability_check: Mapped[dict] = mapped_column(JSON, nullable=False)
    suggested_role_id: Mapped[int | None] = mapped_column(ForeignKey('roles.id', ondelete='SET NULL'))
    handler_role_id: Mapped[int | None] = mapped_column(ForeignKey('roles.id', ondelete='SET NULL'))
    handler_node_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    handler_activation_ids: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    verification_attempt_id: Mapped[str | None] = mapped_column(ForeignKey('workflow_attempts.id', ondelete='SET NULL'))
    coordination_session_id: Mapped[str | None] = mapped_column(ForeignKey('coordination_sessions.id', ondelete='SET NULL'))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    last_dispatch_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    coordination_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default='0')
    request_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint('attempt_id', 'request_key', name='uq_workflow_feedback_request'),
                     Index('ix_workflow_feedback_run_status', 'run_id', 'status'))


class WorkflowFeedbackEvent(Base):
    """追加的反馈处置事实，原意见、人工核验与模型处置可独立追查。"""
    __tablename__ = 'workflow_feedback_events'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    feedback_id: Mapped[str] = mapped_column(ForeignKey('workflow_feedback.id', ondelete='CASCADE'), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    actor_execution_id: Mapped[str | None] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='SET NULL'))
    request_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    data_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint('feedback_id', 'revision', name='uq_feedback_event_revision'),
                     UniqueConstraint('feedback_id', 'request_key', name='uq_feedback_event_request'))
