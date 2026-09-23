"""公开广播与私有执行输入关联；沿用原 execution/run 身份。"""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from ..db import Base


class WorkflowDispatchBatch(Base):
    __tablename__ = 'workflow_dispatch_batches'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('workflow_runs.id', ondelete='CASCADE'), nullable=False)
    message_id: Mapped[int] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'), nullable=False, unique=True)
    members_json: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExecutionInput(Base):
    __tablename__ = 'execution_inputs'
    execution_id: Mapped[str] = mapped_column(ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    message_id: Mapped[int | None] = mapped_column(ForeignKey('messages.id', ondelete='SET NULL'))
    batch_id: Mapped[str | None] = mapped_column(ForeignKey('workflow_dispatch_batches.id', ondelete='SET NULL'))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
