"""世界岗位记忆与原始来源版本；不按任职角色复制一套记忆。"""
from alembic import op
import sqlalchemy as sa

revision = '0031_world_memories'
down_revision = '0030_world_tasks'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('world_memories',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('category', sa.String(24), nullable=False), sa.Column('text', sa.Text(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False), sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('origin', sa.String(24), nullable=False), sa.Column('source_message_id', sa.Integer(), nullable=True),
        sa.Column('source_revision', sa.Integer(), nullable=True), sa.Column('source_role_id', sa.Integer(), nullable=True),
        sa.Column('request_key', sa.String(128), nullable=False), sa.Column('request_digest', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('owner_id', 'request_key', name='uq_world_memory_request'))
    op.create_table('world_memory_versions',
        sa.Column('memory_id', sa.String(64), sa.ForeignKey('world_memories.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('revision', sa.Integer(), primary_key=True), sa.Column('snapshot_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('world_memory_versions')
    op.drop_table('world_memories')
