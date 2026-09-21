"""群协调任命版本、节点激活与每执行工具分配。"""
from alembic import op
import sqlalchemy as sa
revision = '0020_group_orchestration'
down_revision = '0019_conversation_workflows'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('conversations', sa.Column('orchestrator_revision', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('workflow_runs', sa.Column('runtime_version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('workflow_runs', sa.Column('state_json', sa.JSON(), nullable=False, server_default='{}'))
    op.create_table('workflow_activations',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('run_id', sa.String(64), sa.ForeignKey('workflow_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('node_id', sa.String(64), nullable=False),
        sa.Column('loop_id', sa.String(64)),
        sa.Column('iteration', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('dependencies_json', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('selected_attempt_id', sa.String(64)),
        sa.Column('error_code', sa.String(128)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('run_id', 'node_id', 'iteration', name='uq_workflow_activation_round'))
    op.create_index('ix_workflow_activation_run_status', 'workflow_activations', ['run_id', 'status'])
    with op.batch_alter_table('workflow_attempts') as batch:
        batch.add_column(sa.Column('activation_id', sa.String(64)))
        batch.add_column(sa.Column('phase', sa.String(24), nullable=False, server_default='work'))
        batch.add_column(sa.Column('result_json', sa.JSON()))
        batch.create_foreign_key('fk_workflow_attempt_activation', 'workflow_activations', ['activation_id'], ['id'], ondelete='CASCADE')
    op.create_table('execution_allocations',
        sa.Column('execution_id', sa.String(64), sa.ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), primary_key=True),
        sa.Column('attempt_id', sa.String(64), sa.ForeignKey('workflow_attempts.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('tools_json', sa.JSON(), nullable=False),
        sa.Column('workspace_binding_id', sa.Integer()),
        sa.Column('resource_root', sa.Text()),
        sa.Column('authority_json', sa.JSON(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('waiting_mode', sa.String(24)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('execution_allocations')
    with op.batch_alter_table('workflow_attempts') as batch:
        batch.drop_constraint('fk_workflow_attempt_activation', type_='foreignkey')
        batch.drop_column('result_json')
        batch.drop_column('phase')
        batch.drop_column('activation_id')
    op.drop_table('workflow_activations')
    with op.batch_alter_table('workflow_runs') as batch:
        batch.drop_column('state_json')
        batch.drop_column('runtime_version')
    with op.batch_alter_table('conversations') as batch:
        batch.drop_column('orchestrator_revision')
