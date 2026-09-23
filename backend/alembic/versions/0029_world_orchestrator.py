"""世界任命、用途隔离和持久授权凭据。"""
from alembic import op
import sqlalchemy as sa

revision = '0029_world_orchestrator'
down_revision = '0028_world_types'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('conversations', sa.Column('purpose', sa.String(24), nullable=False, server_default='chat'))
    op.create_table('world_orchestrator',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('role_id', sa.Integer(), sa.ForeignKey('roles.id', ondelete='SET NULL'), nullable=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='SET NULL'), nullable=True, unique=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('world_coordination_grants',
        sa.Column('execution_id', sa.String(64), sa.ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), primary_key=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.Column('appointment_revision', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('world_coordination_grants')
    op.drop_table('world_orchestrator')
    # 退回旧代码前协调会话不能成为普通会话可枚举的成员资料。
    op.execute("DELETE FROM conversation_members WHERE conversation_id IN (SELECT id FROM conversations WHERE purpose = 'world_coord')")
    op.drop_column('conversations', 'purpose')
