"""运行实例绑定监管器一次性回收证明；旧数据不补造证明。"""
from alembic import op
import sqlalchemy as sa

revision = '0013_runtime_recovery_receipts'
down_revision = '0012_runtime_boot_identity'
branch_labels = None
depends_on = None


def upgrade():
    """仅保存不可逆令牌哈希，原令牌不进入业务数据库或日志。"""
    op.add_column('runtime_entries', sa.Column('recovery_token_hash', sa.String(64)))


def downgrade():
    """移除新增证明绑定字段。"""
    with op.batch_alter_table('runtime_entries') as batch:
        batch.drop_column('recovery_token_hash')
