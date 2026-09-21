"""节点反馈与追加式处置记录，协调请求保留其来源与本次自动处理授权。"""
from alembic import op
import sqlalchemy as sa

revision = '0023_workflow_feedback'
down_revision = '0022_activation_graph_versions'
branch_labels = None
depends_on = None


def upgrade():
    """用通用类型建立反馈来源、处置版本及请求幂等约束。"""
    op.add_column('coordination_sessions', sa.Column('feedback_ids_json', sa.JSON(), nullable=False, server_default='[]'))
    op.add_column('coordination_sessions', sa.Column('feedback_mode', sa.String(16), nullable=False, server_default='manual'))
    op.create_table('workflow_feedback',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('run_id', sa.String(64), sa.ForeignKey('workflow_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('attempt_id', sa.String(64), sa.ForeignKey('workflow_attempts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('actor_execution_id', sa.String(64), sa.ForeignKey('agent_executions.execution_id', ondelete='SET NULL')),
        sa.Column('source_role_id', sa.Integer(), sa.ForeignKey('roles.id', ondelete='SET NULL')),
        sa.Column('graph_revision', sa.Integer()),
        sa.Column('source_result_revision', sa.Integer()),
        sa.Column('node_id', sa.String(64), nullable=False),
        sa.Column('category', sa.String(24), nullable=False),
        sa.Column('summary', sa.String(240), nullable=False),
        sa.Column('details', sa.Text(), nullable=False),
        sa.Column('blocking', sa.Boolean(), nullable=False),
        sa.Column('requested_tools', sa.JSON(), nullable=False),
        sa.Column('capability_check', sa.JSON(), nullable=False),
        sa.Column('suggested_role_id', sa.Integer(), sa.ForeignKey('roles.id', ondelete='SET NULL')),
        sa.Column('handler_role_id', sa.Integer(), sa.ForeignKey('roles.id', ondelete='SET NULL')),
        sa.Column('handler_node_ids', sa.JSON(), nullable=False),
        sa.Column('handler_activation_ids', sa.JSON(), nullable=False),
        sa.Column('verification_attempt_id', sa.String(64), sa.ForeignKey('workflow_attempts.id', ondelete='SET NULL')),
        sa.Column('coordination_session_id', sa.String(64), sa.ForeignKey('coordination_sessions.id', ondelete='SET NULL')),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('last_dispatch_revision', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('coordination_requested', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('request_key', sa.String(64), nullable=False),
        sa.Column('request_digest', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('attempt_id', 'request_key', name='uq_workflow_feedback_request'))
    op.create_index('ix_workflow_feedback_run_status', 'workflow_feedback', ['run_id', 'status'])
    op.create_table('workflow_feedback_events',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('feedback_id', sa.String(64), sa.ForeignKey('workflow_feedback.id', ondelete='CASCADE'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(24), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('actor_execution_id', sa.String(64), sa.ForeignKey('agent_executions.execution_id', ondelete='SET NULL')),
        sa.Column('request_key', sa.String(64), nullable=False),
        sa.Column('request_digest', sa.String(64), nullable=False),
        sa.Column('data_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('feedback_id', 'revision', name='uq_feedback_event_revision'),
        sa.UniqueConstraint('feedback_id', 'request_key', name='uq_feedback_event_request'))


def downgrade():
    """按依赖逆序移除本阶段 schema。"""
    op.drop_table('workflow_feedback_events')
    op.drop_index('ix_workflow_feedback_run_status', table_name='workflow_feedback')
    op.drop_table('workflow_feedback')
    op.drop_column('coordination_sessions', 'feedback_mode')
    op.drop_column('coordination_sessions', 'feedback_ids_json')
