"""持久会话上下文与每次模型调用的输入估算。"""
from alembic import op
import sqlalchemy as sa

revision = '0025_conversation_context'
down_revision = '0024_prompt_settings'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('conversation_contexts',
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('revision', sa.BigInteger(), nullable=False),
        sa.Column('projection_version', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    # 先建立所有旧会话的身份，正文由启动时的有界、可重入回填处理。
    conversations = sa.table('conversations', sa.column('id'), sa.column('created_at'))
    contexts = sa.table('conversation_contexts', sa.column('conversation_id'), sa.column('revision'),
        sa.column('projection_version'), sa.column('updated_at'))
    op.execute(contexts.insert().from_select(['conversation_id', 'revision', 'projection_version', 'updated_at'],
        sa.select(conversations.c.id, sa.literal(0), sa.literal(1), conversations.c.created_at)))
    op.create_table('conversation_context_entries',
        sa.Column('message_id', sa.Integer(), sa.ForeignKey('messages.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversation_contexts.conversation_id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_revision', sa.Integer(), nullable=False),
        sa.Column('source_status', sa.String(32), nullable=False),
        sa.Column('sender_type', sa.String(32), nullable=False),
        sa.Column('sender_id', sa.Integer(), nullable=True),
        sa.Column('chain_id', sa.String(64), nullable=True),
        sa.Column('pinned', sa.Boolean(), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('reason', sa.String(32), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('text_bytes', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_context_entries_conversation_message', 'conversation_context_entries', ['conversation_id', 'message_id'])
    op.add_column('model_call_usage', sa.Column('input_estimate_json', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('model_call_usage', 'input_estimate_json')
    op.drop_index('ix_context_entries_conversation_message', table_name='conversation_context_entries')
    op.drop_table('conversation_context_entries')
    op.drop_table('conversation_contexts')
