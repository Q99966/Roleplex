"""自动压缩策略与原执行链上的维护子任务；不复制私有工具正文。"""
from alembic import op
import sqlalchemy as sa

revision = '0027_context_policy'
down_revision = '0026_context_compaction_memory'
branch_labels = None
depends_on = None


def upgrade():
    for table in ['instance_settings', 'conversations']:
        op.add_column(table, sa.Column('context_policy_json', sa.JSON(), nullable=True))
        op.add_column(table, sa.Column('context_policy_revision', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('context_compressions', sa.Column('trigger', sa.String(16), nullable=False, server_default='manual'))
    op.add_column('context_compressions', sa.Column('scope', sa.String(16), nullable=False, server_default='conversation'))
    op.add_column('context_compressions', sa.Column('runtime_json', sa.JSON(), nullable=True))


def downgrade():
    for column in ['runtime_json', 'scope', 'trigger']:
        op.drop_column('context_compressions', column)
    for table in ['conversations', 'instance_settings']:
        op.drop_column(table, 'context_policy_revision')
        op.drop_column(table, 'context_policy_json')
