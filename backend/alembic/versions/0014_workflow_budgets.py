"""World 默认决策额度、消息链快照与 execution 幂等计数。"""
from alembic import op
import sqlalchemy as sa

revision = '0014_workflow_budgets'
down_revision = '0013_runtime_recovery_receipts'
branch_labels = None
depends_on = None


def upgrade():
    """旧任务不补造授权快照；恢复时缺失快照须停止并由用户新发任务。"""
    op.add_column('instance_settings', sa.Column('decision_limit', sa.Integer(), nullable=False, server_default='8'))
    op.add_column('instance_settings', sa.Column('budget_revision', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('agent_executions', sa.Column('decision_count', sa.Integer(), nullable=False, server_default='0'))
    op.create_table('workflow_budgets',
        sa.Column('chain_id', sa.String(64), primary_key=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('trigger_message_id', sa.Integer(), sa.ForeignKey('messages.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('decision_limit', sa.Integer(), nullable=False),
        sa.Column('used_decisions', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('configuration_revision', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    """删除预算数据，按原迁移机制还原表结构。"""
    op.drop_table('workflow_budgets')
    with op.batch_alter_table('agent_executions') as batch:
        batch.drop_column('decision_count')
    with op.batch_alter_table('instance_settings') as batch:
        batch.drop_column('budget_revision')
        batch.drop_column('decision_limit')
