"""工具时间线的 Owner 加密详情，不改写历史消息。"""
from alembic import op
import sqlalchemy as sa

revision = '0007_tool_execution_details'
down_revision = '0006_world_workspaces'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建按消息调用唯一定位的加密详情表。"""
    op.create_table('tool_execution_details',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('message_id', sa.Integer(), sa.ForeignKey('messages.id', ondelete='CASCADE'), nullable=False),
        sa.Column('execution_id', sa.String(64), sa.ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), nullable=False),
        sa.Column('call_id', sa.String(128), nullable=False),
        sa.Column('tool_name', sa.String(256), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('input_encrypted', sa.Text()), sa.Column('output_encrypted', sa.Text()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True)),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('message_id', 'call_id', name='uq_tool_detail_message_call'),
    )
    op.create_index('ix_tool_detail_expires_at', 'tool_execution_details', ['expires_at'])


def downgrade() -> None:
    """删除私有详情表，不改变公开消息。"""
    op.drop_index('ix_tool_detail_expires_at', table_name='tool_execution_details')
    op.drop_table('tool_execution_details')
