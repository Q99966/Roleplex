"""类型配置与幂等初始化；世界身份仍由 manifest 持有。"""
from alembic import op
import sqlalchemy as sa

revision = '0028_world_types'
down_revision = '0027_context_policy'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('world_type_state',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('configuration_json', sa.JSON(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('initialized_type', sa.String(64), nullable=True),
        sa.Column('initialized_version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('resources_json', sa.JSON(), nullable=False),
        sa.Column('required_fields_json', sa.JSON(), nullable=False),
        sa.Column('error_code', sa.String(64), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('world_type_state')
