"""运行实例和回收事实；来源身份保留快照，资源删除不抹掉审计链。"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class RuntimeGate(Base):
    """World 内配额和范围门槛的短事务串行化点，不覆盖进程运行期。"""
    __tablename__ = 'runtime_gate'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    closing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RuntimeEntry(Base):
    """顶层命令/服务身份；PID 必须和出生身份一起使用。"""
    __tablename__ = 'runtime_entries'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # 审计快照保留；当前授权关联随物理删除断开，防止整数 ID 复用重新授予权限。
    conversation_ref_id: Mapped[int | None] = mapped_column(Integer,
        ForeignKey('conversations.id', name='fk_runtime_conversation_ref', ondelete='SET NULL'))
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chain_id: Mapped[str | None] = mapped_column(String(64))
    role_id: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    process_instance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pid: Mapped[int | None] = mapped_column(Integer)
    birth: Mapped[str | None] = mapped_column(String(64))
    port: Mapped[int | None] = mapped_column(Integer)
    leased_port: Mapped[int | None] = mapped_column(Integer, unique=True)
    approval_id: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    health_code: Mapped[int | None] = mapped_column(Integer)
    health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    log_encrypted: Mapped[str | None] = mapped_column(Text)
    log_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint('execution_id', 'tool_call_id', name='uq_runtime_execution_call'),
        Index('ix_runtime_conversation_state', 'conversation_id', 'state'),
        Index('ix_runtime_workspace_state', 'workspace_id', 'state'),
    )


class CleanupOperation(Base):
    """先持久化范围和清单，再执行停止；未完成操作不可冒充成功。"""
    __tablename__ = 'runtime_cleanup_operations'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    process_instance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index('ix_cleanup_scope_state', 'scope', 'scope_id', 'state'),)


class CleanupItem(Base):
    """冻结清单条目与逐项回收结果；汇总引用条目而非重复业务事实。"""
    __tablename__ = 'runtime_cleanup_items'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint('operation_id', 'runtime_id', name='uq_cleanup_target'),)
