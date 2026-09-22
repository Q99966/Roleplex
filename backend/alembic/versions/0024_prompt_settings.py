"""World/会话提示词、角色配置版本和执行输入来源记录。"""
from alembic import op
import sqlalchemy as sa

revision = '0024_prompt_settings'
down_revision = '0023_workflow_feedback'
branch_labels = None
depends_on = None


def upgrade():
    """旧世界继承平台默认，角色、会话和执行内容保持原样。"""
    op.add_column('instance_settings', sa.Column('platform_prompt_override', sa.Text(), nullable=True))
    op.add_column('instance_settings', sa.Column('world_prompt', sa.Text(), nullable=False, server_default=''))
    op.add_column('instance_settings', sa.Column('prompt_revision', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('instance_settings', sa.Column('prompt_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('conversations', sa.Column('system_prompt', sa.Text(), nullable=False, server_default=''))
    op.add_column('conversations', sa.Column('prompt_revision', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('conversations', sa.Column('prompt_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('roles', sa.Column('revision', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('agent_executions', sa.Column('context_snapshot_json', sa.JSON(), nullable=True))


def downgrade():
    """仅移除本批配置列，不重建原消息、角色或执行记录。"""
    op.drop_column('agent_executions', 'context_snapshot_json')
    op.drop_column('roles', 'revision')
    for table, columns in [('conversations', ['prompt_updated_at', 'prompt_revision', 'system_prompt']),
                           ('instance_settings', ['prompt_updated_at', 'prompt_revision', 'world_prompt', 'platform_prompt_override'])]:
        for column in columns:
            op.drop_column(table, column)
