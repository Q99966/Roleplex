"""固定世界管理者的专用角色身份；业务配置在 Owner 就绪后幂等衔接。"""
from alembic import op
import sqlalchemy as sa

revision = '0032_world_manager_identity'
down_revision = '0031_world_memories'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('roles') as batch:
        batch.add_column(sa.Column('managed_kind', sa.String(32), nullable=True))
        batch.create_unique_constraint('uq_role_owner_managed', ['created_by', 'managed_kind'])


def downgrade():
    # 旧版将系统岗位解释为可随意替换的普通角色，不能悄悄回退业务身份。
    if not op.get_context().as_sql and op.get_bind().execute(sa.text(
        "SELECT 1 FROM roles WHERE managed_kind IS NOT NULL LIMIT 1")).first():
        raise RuntimeError('WORLD_MANAGER_DOWNGRADE_REQUIRES_EXPORT')
    with op.batch_alter_table('roles') as batch:
        batch.drop_constraint('uq_role_owner_managed', type_='unique')
        batch.drop_column('managed_kind')
