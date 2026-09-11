"""回收批次持久化失败原因及后续核查关系，机器日志故障时仍可追查。"""
from alembic import op
import sqlalchemy as sa

revision = '0011_cleanup_failure_links'
down_revision = '0010_runtime_conversation_ref'
branch_labels = None
depends_on = None


def upgrade():
    """增加可空审计字段；旧批次不推测失败原因或关联关系。"""
    op.add_column('runtime_cleanup_operations', sa.Column('error_code', sa.String(64)))
    op.add_column('runtime_cleanup_operations', sa.Column('superseded_by_id', sa.String(64)))


def downgrade():
    """只移除本次新增审计字段。"""
    with op.batch_alter_table('runtime_cleanup_operations') as batch:
        batch.drop_column('superseded_by_id')
        batch.drop_column('error_code')
