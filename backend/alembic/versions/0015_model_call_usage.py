"""持久化模型调用用量；旧执行标记未采集，不从日志回填。"""
from alembic import op
import sqlalchemy as sa

revision='0015_model_call_usage'
down_revision='0014_workflow_budgets'
branch_labels=None
depends_on=None


def upgrade():
    """新增只读观测数据，不调整执行预算。"""
    op.add_column('agent_executions',sa.Column('usage_tracked',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.create_index('ix_agent_executions_conversation_role','agent_executions',['conversation_id','role_id','id'])
    op.create_table('model_call_usage',
        sa.Column('id',sa.Integer(),primary_key=True,autoincrement=True),
        sa.Column('execution_id',sa.String(64),sa.ForeignKey('agent_executions.execution_id',ondelete='CASCADE'),nullable=False),
        sa.Column('call_index',sa.Integer(),nullable=False),
        sa.Column('provider_mode',sa.String(16),nullable=False),
        sa.Column('model_name',sa.String(128),nullable=False),
        sa.Column('status',sa.String(16),nullable=False,server_default='started'),
        sa.Column('input_tokens',sa.BigInteger()),sa.Column('output_tokens',sa.BigInteger()),
        sa.Column('cache_hit_tokens',sa.BigInteger()),sa.Column('cache_write_tokens',sa.BigInteger()),
        sa.Column('duration_ms',sa.BigInteger()),
        sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('execution_id','call_index',name='uq_model_call_usage_execution_index'))


def downgrade():
    """移除观测数据，不修改执行本身。"""
    op.drop_table('model_call_usage')
    op.drop_index('ix_agent_executions_conversation_role',table_name='agent_executions')
    with op.batch_alter_table('agent_executions') as batch:
        batch.drop_column('usage_tracked')
