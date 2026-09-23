"""广播批次与执行输入分离，公开消息来源使用既有版本化 meta_json。"""
from alembic import op
import sqlalchemy as sa

revision = '0033_workflow_communication'
down_revision = '0032_world_manager_identity'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('workflow_dispatch_batches',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('run_id', sa.String(64), sa.ForeignKey('workflow_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('message_id', sa.Integer(), sa.ForeignKey('messages.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('members_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('execution_inputs',
        sa.Column('execution_id', sa.String(64), sa.ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), primary_key=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('message_id', sa.Integer(), sa.ForeignKey('messages.id', ondelete='SET NULL'), nullable=True),
        sa.Column('batch_id', sa.String(64), sa.ForeignKey('workflow_dispatch_batches.id', ondelete='SET NULL'), nullable=True),
        sa.Column('text', sa.Text(), nullable=False), sa.Column('source_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    if not op.get_context().as_sql and op.get_bind().execute(sa.text('SELECT 1 FROM execution_inputs LIMIT 1')).first():
        raise RuntimeError('EXECUTION_INPUT_DOWNGRADE_REQUIRES_EXPORT')
    op.drop_table('execution_inputs')
    op.drop_table('workflow_dispatch_batches')
