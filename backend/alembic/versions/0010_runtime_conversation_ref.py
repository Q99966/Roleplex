"""将运行实例的授权关联与不可变会话来源快照分开。"""
from alembic import op
import sqlalchemy as sa

revision = '0010_runtime_conversation_ref'
down_revision = '0009_runtime_services'
branch_labels = None
depends_on = None


def upgrade():
    """旧来源仅在 execution 仍能证明属于当前会话时建立授权关联。"""
    with op.batch_alter_table('runtime_entries') as batch:
        batch.add_column(sa.Column('conversation_ref_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_runtime_conversation_ref', 'conversations', ['conversation_ref_id'], ['id'], ondelete='SET NULL')
    entries = sa.table('runtime_entries', sa.column('conversation_id'), sa.column('conversation_ref_id'), sa.column('execution_id'))
    executions = sa.table('agent_executions', sa.column('execution_id'), sa.column('conversation_id'))
    op.execute(entries.update().where(sa.exists(sa.select(executions.c.execution_id).where(
        executions.c.execution_id == entries.c.execution_id, executions.c.conversation_id == entries.c.conversation_id)))
        .values(conversation_ref_id=entries.c.conversation_id))


def downgrade():
    """只移除授权引用，不删除原始审计快照。"""
    with op.batch_alter_table('runtime_entries') as batch:
        batch.drop_constraint('fk_runtime_conversation_ref', type_='foreignkey')
        batch.drop_column('conversation_ref_id')
