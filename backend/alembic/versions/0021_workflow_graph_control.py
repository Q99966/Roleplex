"""独立协调会话、显式管理授权与不可变图修订。"""
from alembic import op
import sqlalchemy as sa
revision = '0021_workflow_graph_control'
down_revision = '0020_group_orchestration'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('coordination_sessions',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role_id', sa.Integer(), sa.ForeignKey('roles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('appointment_revision', sa.Integer(), nullable=False),
        sa.Column('definition_id', sa.String(length=64), nullable=False),
        sa.Column('run_id', sa.String(length=64), sa.ForeignKey('workflow_runs.id', ondelete='CASCADE')),
        sa.Column('started_run_id', sa.String(length=64)),
        sa.Column('execution_id', sa.String(64), sa.ForeignKey('agent_executions.execution_id', ondelete='SET NULL'), unique=True),
        sa.Column('chain_id', sa.String(length=64), nullable=False),
        sa.Column('trigger_message_id', sa.Integer(), sa.ForeignKey('messages.id', ondelete='SET NULL')),
        sa.Column('mode', sa.String(length=24), nullable=False),
        sa.Column('goal', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('request_key', sa.String(length=64), nullable=False),
        sa.Column('request_digest', sa.String(length=64), nullable=False),
        sa.Column('constraints_json', sa.JSON(), nullable=False),
        sa.Column('workspace_binding_id', sa.Integer()),
        sa.Column('error_code', sa.String(length=128)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('conversation_id', 'request_key', name='uq_coordination_request'),
    )
    op.create_index('ix_coordination_conversation_status', 'coordination_sessions', ['conversation_id', 'status'])
    op.create_table('workflow_graph_revisions',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('definition_id', sa.String(length=64), sa.ForeignKey('workflow_definitions.id', ondelete='CASCADE')),
        sa.Column('run_id', sa.String(length=64), sa.ForeignKey('workflow_runs.id', ondelete='CASCADE')),
        sa.Column('target_key', sa.String(length=80), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('graph', sa.JSON(), nullable=False),
        sa.Column('mutation_key', sa.String(length=64)),
        sa.Column('request_digest', sa.String(length=64)),
        sa.Column('source_execution_id', sa.String(64), sa.ForeignKey('agent_executions.execution_id', ondelete='SET NULL')),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('changes_json', sa.JSON(), nullable=False),
        sa.Column('legacy', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('target_key', 'mutation_key', name='uq_workflow_graph_mutation'),
        sa.UniqueConstraint('target_key', 'number', name='uq_workflow_graph_number'),
    )
    for table in ['workflow_runs', 'workflow_activations', 'workflow_attempts']:
        op.add_column(table, sa.Column('graph_revision', sa.Integer()))
    op.add_column('workflow_attempts', sa.Column('node_snapshot_json', sa.JSON()))
    with op.batch_alter_table('execution_allocations') as batch:
        batch.alter_column('attempt_id', existing_type=sa.String(64), nullable=True)
        batch.add_column(sa.Column('coordination_session_id', sa.String(64)))
        batch.add_column(sa.Column('control_tools_json', sa.JSON(), nullable=False, server_default='[]'))
        batch.create_foreign_key('fk_allocation_coordination', 'coordination_sessions', ['coordination_session_id'], ['id'], ondelete='CASCADE')
    # 仅迁移已知旧 phase 的能力，未知职责默认无权。新执行按实际需求发放。
    allocations = sa.table('execution_allocations', sa.column('attempt_id', sa.String()), sa.column('control_tools_json', sa.JSON()))
    attempts = sa.table('workflow_attempts', sa.column('id', sa.String()), sa.column('phase', sa.String()))
    for phase, name in [('plan', 'workflow_plan'), ('summary', 'workflow_summary'), ('judge', 'workflow_result'), ('work', 'workflow_result')]:
        op.execute(allocations.update().where(allocations.c.attempt_id.in_(sa.select(attempts.c.id).where(attempts.c.phase == phase))).values(control_tools_json=sa.type_coerce(sa.literal_column("'[\"" + name + "\"]'"), sa.JSON())))


def downgrade():
    # 新协调授权没有旧 attempt 归属，只删除这类授权记录，保留原 execution 事实。
    op.execute(sa.text('DELETE FROM execution_allocations WHERE attempt_id IS NULL'))
    with op.batch_alter_table('execution_allocations') as batch:
        batch.drop_constraint('fk_allocation_coordination', type_='foreignkey')
        batch.drop_column('control_tools_json')
        batch.drop_column('coordination_session_id')
        batch.alter_column('attempt_id', existing_type=sa.String(64), nullable=False)
    for table in ['workflow_runs', 'workflow_activations', 'workflow_attempts']:
        with op.batch_alter_table(table) as batch:
            batch.drop_column('graph_revision')
    with op.batch_alter_table('workflow_attempts') as batch:
        batch.drop_column('node_snapshot_json')
    op.drop_table('workflow_graph_revisions')
    op.drop_table('coordination_sessions')
