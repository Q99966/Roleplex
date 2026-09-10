"""W1c 加密 Shell 逐次审批，不引入仓库或 worktree。"""
from alembic import op
import sqlalchemy as sa

revision = '0008_shell_approvals'
down_revision = '0007_tool_execution_details'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建具有唯一调用身份和过期索引的审批表。"""
    op.create_table('tool_approval_requests',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('execution_id', sa.String(64), sa.ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), nullable=False),
        sa.Column('workspace_binding_id', sa.Integer(), sa.ForeignKey('workspace_bindings.id', ondelete='SET NULL')),
        sa.Column('tool_call_id', sa.String(128), nullable=False),
        sa.Column('tool_name', sa.String(256), nullable=False),
        sa.Column('request_encrypted', sa.Text(), nullable=False),
        sa.Column('request_digest', sa.String(64), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True)),
        sa.Column('resolved_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.UniqueConstraint('execution_id', 'tool_call_id', name='uq_approval_execution_call'),
    )
    op.create_index('ix_approval_status_expires', 'tool_approval_requests', ['status', 'expires_at'])


def downgrade() -> None:
    """移除审批表，不改变已有执行和工作区。"""
    op.drop_index('ix_approval_status_expires', table_name='tool_approval_requests')
    op.drop_table('tool_approval_requests')
