"""世界任务及子链根预算；每个群仍保留独立 chain。"""
from alembic import op
import sqlalchemy as sa

revision = '0030_world_tasks'
down_revision = '0029_world_orchestrator'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('workflow_budgets') as batch:
        batch.add_column(sa.Column('root_chain_id', sa.String(64), nullable=True))
        batch.create_foreign_key('fk_workflow_budget_root', 'workflow_budgets', ['root_chain_id'], ['chain_id'], ondelete='RESTRICT')
    op.create_table('world_tasks',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('trigger_message_id', sa.Integer(), sa.ForeignKey('messages.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('root_execution_id', sa.String(64), sa.ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), nullable=False),
        sa.Column('chain_id', sa.String(64), sa.ForeignKey('workflow_budgets.chain_id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.Column('appointment_revision', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(160), nullable=False), sa.Column('scope_json', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False), sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True), sa.Column('error_code', sa.String(64), nullable=True),
        sa.Column('feedback_hash', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('world_task_children',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('task_id', sa.String(64), sa.ForeignKey('world_tasks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('parent_execution_id', sa.String(64), sa.ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), nullable=False),
        sa.Column('request_key', sa.String(128), nullable=False), sa.Column('request_digest', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(24), nullable=False),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='SET NULL'), nullable=True),
        sa.Column('coordination_id', sa.String(64), sa.ForeignKey('coordination_sessions.id', ondelete='SET NULL'), nullable=True, unique=True),
        sa.Column('chain_id', sa.String(64), sa.ForeignKey('workflow_budgets.chain_id', ondelete='SET NULL'), nullable=True, unique=True),
        sa.Column('status', sa.String(24), nullable=False), sa.Column('reference_json', sa.JSON(), nullable=False),
        sa.Column('error_code', sa.String(64), nullable=True), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('task_id', 'request_key', name='uq_world_task_child_request'))
    with op.batch_alter_table('world_coordination_grants') as batch:
        batch.add_column(sa.Column('task_id', sa.String(64), nullable=True))
        batch.create_foreign_key('fk_world_grant_task', 'world_tasks', ['task_id'], ['id'], ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('world_coordination_grants') as batch:
        batch.drop_constraint('fk_world_grant_task', type_='foreignkey')
        batch.drop_column('task_id')
    op.drop_table('world_task_children')
    op.drop_table('world_tasks')
    with op.batch_alter_table('workflow_budgets') as batch:
        batch.drop_constraint('fk_workflow_budget_root', type_='foreignkey')
        batch.drop_column('root_chain_id')
