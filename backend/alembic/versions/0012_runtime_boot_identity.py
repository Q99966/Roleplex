"""记录运行实例所属内核启动身份，主机重启后可确认旧实例已不可能存活。"""
from alembic import op
import sqlalchemy as sa

revision = '0012_runtime_boot_identity'
down_revision = '0011_cleanup_failure_links'
branch_labels = None
depends_on = None


def upgrade():
    """旧记录不补造宿主启动身份。"""
    op.add_column('runtime_entries', sa.Column('host_boot_id', sa.String(64)))


def downgrade():
    """移除本次新增的宿主启动证明。"""
    with op.batch_alter_table('runtime_entries') as batch:
        batch.drop_column('host_boot_id')
