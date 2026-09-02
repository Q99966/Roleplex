"""E0 持久 Agent execution 身份

Revision ID: 0005_agent_executions
Revises: 0004_role_context_window

新增 agent_executions，规范化 generation 对应的 execution ID、角色、chain、
执行类型和终态。M4b 父子关系与 dispatch 字段本迁移只建立结构，不开放行为。
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_agent_executions"
down_revision = "0004_role_context_window"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 execution 表、父子/归属外键和诊断索引。"""
    op.create_table(
        "agent_executions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("execution_id", sa.String(64), nullable=False),
        sa.Column(
            "parent_execution_id",
            sa.String(64),
            sa.ForeignKey("agent_executions.execution_id", ondelete="CASCADE"),
        ),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "generation_id",
            sa.Integer(),
            sa.ForeignKey("generations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chain_id", sa.String(64), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="SET NULL")),
        sa.Column("execution_kind", sa.String(16), nullable=False),
        sa.Column("dispatch_order", sa.Integer()),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("task_text", sa.Text()),
        sa.Column("context_hint_text", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("execution_id", name="uq_agent_executions_execution_id"),
        sa.UniqueConstraint("generation_id", name="uq_agent_executions_generation"),
        sa.UniqueConstraint(
            "parent_execution_id", "dispatch_order", "attempt",
            name="uq_agent_execution_dispatch_attempt",
        ),
    )
    op.create_index(
        "ix_agent_executions_conversation_status",
        "agent_executions",
        ["conversation_id", "status"],
    )
    op.create_index(
        "ix_agent_executions_parent_order_attempt",
        "agent_executions",
        ["parent_execution_id", "dispatch_order", "attempt"],
    )
    op.create_index("ix_agent_executions_chain_id", "agent_executions", ["chain_id"])


def downgrade() -> None:
    """移除 execution 表及其索引。"""
    op.drop_index("ix_agent_executions_chain_id", table_name="agent_executions")
    op.drop_index("ix_agent_executions_parent_order_attempt", table_name="agent_executions")
    op.drop_index("ix_agent_executions_conversation_status", table_name="agent_executions")
    op.drop_table("agent_executions")
