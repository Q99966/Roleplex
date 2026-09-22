"""主动压缩、不可变来源与检索引用；维护请求复用现有预算。"""
from alembic import op
import sqlalchemy as sa

revision = '0026_context_compaction_memory'
down_revision = '0025_conversation_context'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('workflow_budgets') as batch:
        batch.alter_column('trigger_message_id', existing_type=sa.Integer(), nullable=True)
    op.add_column('conversation_context_entries', sa.Column('search_text', sa.Text(), nullable=True))
    op.add_column('conversation_context_entries', sa.Column('execution_facts_json', sa.JSON(), nullable=True))
    op.add_column('conversation_contexts', sa.Column('summary_revision', sa.BigInteger(), nullable=False, server_default='0'))
    op.add_column('conversation_context_entries', sa.Column('text_hash', sa.String(64), nullable=False, server_default=''))
    op.add_column('conversation_context_entries', sa.Column('projection_version', sa.Integer(), nullable=False, server_default='0'))
    op.create_table('context_compressions',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role_id', sa.Integer(), sa.ForeignKey('roles.id', ondelete='SET NULL'), nullable=True),
        sa.Column('execution_id', sa.String(64), sa.ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('request_key', sa.String(128), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('active_conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=True, unique=True),
        sa.Column('source_revision', sa.BigInteger(), nullable=False),
        sa.Column('base_summary_id', sa.String(64), nullable=True),
        sa.Column('base_summary_revision', sa.BigInteger(), nullable=False),
        sa.Column('through_message_id', sa.Integer(), nullable=False),
        sa.Column('keep_recent', sa.Integer(), nullable=False),
        sa.Column('target_tokens', sa.Integer(), nullable=False),
        sa.Column('instructions', sa.Text(), nullable=False),
        sa.Column('prompt_snapshot_json', sa.JSON(), nullable=False),
        sa.Column('model_snapshot_json', sa.JSON(), nullable=False),
        sa.Column('source_count', sa.Integer(), nullable=False),
        sa.Column('input_tokens_estimate', sa.BigInteger(), nullable=False),
        sa.Column('output_tokens_estimate', sa.BigInteger(), nullable=True),
        sa.Column('completed_calls', sa.Integer(), nullable=False),
        sa.Column('phase', sa.String(32), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('error_code', sa.String(64), nullable=True),
        sa.Column('cancel_requested', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('conversation_id', 'owner_id', 'request_key', name='uq_context_compression_request'))
    op.create_index('ix_context_compressions_conversation_created', 'context_compressions', ['conversation_id', 'created_at'])
    op.create_table('context_compression_sources',
        sa.Column('compression_id', sa.String(64), sa.ForeignKey('context_compressions.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('message_id', sa.Integer(), primary_key=True),
        sa.Column('source_revision', sa.Integer(), nullable=False),
        sa.Column('source_status', sa.String(16), nullable=False),
        sa.Column('text_hash', sa.String(64), nullable=False))
    op.create_table('context_summaries',
        sa.Column('id', sa.String(64), sa.ForeignKey('context_compressions.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('active_conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=True, unique=True),
        sa.Column('content_json', sa.JSON(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('text_bytes', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('memory_references',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('execution_id', sa.String(64), sa.ForeignKey('agent_executions.execution_id', ondelete='CASCADE'), nullable=False),
        sa.Column('tool_call_id', sa.String(64), nullable=False),
        sa.Column('source_kind', sa.String(16), nullable=False),
        sa.Column('source_id', sa.String(64), nullable=False),
        sa.Column('source_revision', sa.Integer(), nullable=False),
        sa.Column('reference', sa.Text(), nullable=False),
        sa.Column('action', sa.String(16), nullable=False),
        sa.Column('offset', sa.Integer(), nullable=False),
        sa.Column('characters', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_memory_references_execution', 'memory_references', ['execution_id', 'created_at'])


def downgrade():
    op.drop_index('ix_memory_references_execution', table_name='memory_references')
    op.drop_table('memory_references')
    op.drop_table('context_summaries')
    op.drop_table('context_compression_sources')
    op.drop_index('ix_context_compressions_conversation_created', table_name='context_compressions')
    op.drop_table('context_compressions')
    op.drop_column('conversation_contexts', 'summary_revision')
    for name in ['projection_version', 'text_hash', 'execution_facts_json', 'search_text']:
        op.drop_column('conversation_context_entries', name)
    # 降级无法保留没有消息来源的维护额度；普通聊天链和历史消息不变。
    budgets = sa.table('workflow_budgets', sa.column('trigger_message_id'))
    op.execute(budgets.delete().where(budgets.c.trigger_message_id.is_(None)))
    with op.batch_alter_table('workflow_budgets') as batch:
        batch.alter_column('trigger_message_id', existing_type=sa.Integer(), nullable=False)
