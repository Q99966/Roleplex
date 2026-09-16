"""会话串行工作流定义、运行快照与节点尝试。"""
from alembic import op
import sqlalchemy as sa
revision = '0019_conversation_workflows'
down_revision = '0018_file_effects'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('workflow_definitions',
        sa.Column('id', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False, primary_key=False),
        sa.Column('name', sa.String(length=128), nullable=False, primary_key=False),
        sa.Column('revision', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('graph', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, primary_key=False))
    op.create_index('ix_workflow_definition_conversation', 'workflow_definitions', ['conversation_id'])
    op.create_table('workflow_runs',
        sa.Column('id', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False, primary_key=False),
        sa.Column('definition_id', sa.String(length=64), sa.ForeignKey('workflow_definitions.id', ondelete='CASCADE'), nullable=False, primary_key=False),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, primary_key=False),
        sa.Column('definition_revision', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('snapshot', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('workspace_binding_id', sa.Integer(), nullable=True, primary_key=False),
        sa.Column('workspace_root', sa.Text(), nullable=True, primary_key=False),
        sa.Column('chain_id', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('trigger_message_id', sa.Integer(), sa.ForeignKey('messages.id', ondelete='SET NULL'), nullable=True, primary_key=False),
        sa.Column('request_key', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('request_digest', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('input_text', sa.Text(), nullable=False, primary_key=False),
        sa.Column('status', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('revision', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('cursor', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('selected', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('rerun_downstream', sa.Boolean(), nullable=False, primary_key=False),
        sa.Column('error_code', sa.String(length=128), nullable=True, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.UniqueConstraint('chain_id', name=None),
        sa.UniqueConstraint('conversation_id', 'request_key', name='uq_workflow_start_request'))
    op.create_index('ix_workflow_run_conversation_status', 'workflow_runs', ['conversation_id', 'status'])
    op.create_table('workflow_attempts',
        sa.Column('id', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('run_id', sa.String(length=64), sa.ForeignKey('workflow_runs.id', ondelete='CASCADE'), nullable=False, primary_key=False),
        sa.Column('node_id', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('number', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('status', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('upstream_ids', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('retry_source_id', sa.String(length=64), nullable=True, primary_key=False),
        sa.Column('instruction', sa.Text(), nullable=False, primary_key=False),
        sa.Column('input_message_id', sa.Integer(), sa.ForeignKey('messages.id', ondelete='SET NULL'), nullable=True, primary_key=False),
        sa.Column('generation_id', sa.Integer(), sa.ForeignKey('generations.id', ondelete='SET NULL'), nullable=True, primary_key=False),
        sa.Column('execution_id', sa.String(length=64), sa.ForeignKey('agent_executions.execution_id', ondelete='SET NULL'), nullable=True, primary_key=False),
        sa.Column('error_code', sa.String(length=128), nullable=True, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.UniqueConstraint('generation_id', name=None),
        sa.UniqueConstraint('execution_id', name=None),
        sa.UniqueConstraint('run_id', 'node_id', 'number', name='uq_workflow_node_attempt'))

def downgrade():
    op.drop_table('workflow_attempts')
    op.drop_table('workflow_runs')
    op.drop_table('workflow_definitions')
