"""为角色旧回复的执行关联核对增加索引，不修改历史数据。"""
from alembic import op

revision='0016_usage_history_index'
down_revision='0015_model_call_usage'
branch_labels=None
depends_on=None


def upgrade():
    """避免累计查询按每条旧回复反复扫描生成表。"""
    op.create_index('ix_generations_assistant_message','generations',['assistant_message_id'])


def downgrade():
    """删除新增查询索引。"""
    op.drop_index('ix_generations_assistant_message',table_name='generations')
