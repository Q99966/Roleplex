"""W1d 三层配额、顶层运行实例及可追溯回收清单。"""
from alembic import op
import sqlalchemy as sa

revision = '0009_runtime_services'
down_revision = '0008_shell_approvals'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为新旧资源建立持久默认值，并创建与资源删除独立的运行审计。"""
    for table, limit in [('instance_settings', 20), ('workspace_bindings', 5), ('conversations', 3)]:
        op.add_column(table, sa.Column('process_limit', sa.Integer(), nullable=False, server_default=str(limit)))
        op.add_column(table, sa.Column('process_limit_version', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('workspace_bindings', sa.Column('services_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table('runtime_gate', sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sequence', sa.BigInteger(), nullable=False), sa.Column('closing', sa.Boolean(), nullable=False))
    op.bulk_insert(sa.table('runtime_gate', sa.column('id', sa.Integer()), sa.column('sequence', sa.BigInteger()),
        sa.column('closing', sa.Boolean())), [{'id': 1, 'sequence': 0, 'closing': False}])
    op.create_table('runtime_entries',
        sa.Column('id', sa.String(64), primary_key=True), sa.Column('sequence', sa.BigInteger(), nullable=False),
        *[sa.Column(name, sa.Integer(), nullable=False) for name in ('owner_id', 'conversation_id', 'workspace_id', 'role_id')],
        sa.Column('execution_id', sa.String(64), nullable=False), sa.Column('chain_id', sa.String(64)), sa.Column('tool_call_id', sa.String(128), nullable=False),
        sa.Column('tool_name', sa.String(64), nullable=False), sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('state', sa.String(32), nullable=False), sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('process_instance_id', sa.String(64), nullable=False), sa.Column('pid', sa.Integer()), sa.Column('birth', sa.String(64)),
        sa.Column('port', sa.Integer()), sa.Column('leased_port', sa.Integer(), unique=True), sa.Column('approval_id', sa.Integer()),
        sa.Column('error_code', sa.String(64)), sa.Column('exit_code', sa.Integer()), sa.Column('health_code', sa.Integer()),
        sa.Column('health_checked_at', sa.DateTime(timezone=True)), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        *[sa.Column(name, sa.DateTime(timezone=True)) for name in ('started_at', 'expires_at', 'ended_at', 'log_expires_at')],
        sa.Column('log_encrypted', sa.Text()), sa.UniqueConstraint('execution_id', 'tool_call_id', name='uq_runtime_execution_call'))
    op.create_index('ix_runtime_conversation_state', 'runtime_entries', ['conversation_id', 'state'])
    op.create_index('ix_runtime_workspace_state', 'runtime_entries', ['workspace_id', 'state'])
    op.create_table('runtime_cleanup_operations', sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('scope', sa.String(16), nullable=False), sa.Column('scope_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(64), nullable=False), sa.Column('actor_id', sa.Integer()),
        sa.Column('process_instance_id', sa.String(64), nullable=False), sa.Column('state', sa.String(32), nullable=False),
        sa.Column('target_count', sa.Integer(), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True)))
    op.create_index('ix_cleanup_scope_state', 'runtime_cleanup_operations', ['scope', 'scope_id', 'state'])
    op.create_table('runtime_cleanup_items', sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('operation_id', sa.String(64), nullable=False), sa.Column('runtime_id', sa.String(64), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False), sa.Column('state', sa.String(32), nullable=False),
        sa.Column('outcome', sa.String(64)), sa.Column('error_code', sa.String(64)),
        sa.Column('started_at', sa.DateTime(timezone=True)), sa.Column('ended_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('operation_id', 'runtime_id', name='uq_cleanup_target'))


def downgrade() -> None:
    """删除新增运行时结构，保留已有业务资源。"""
    for table in ('runtime_cleanup_items', 'runtime_cleanup_operations', 'runtime_entries', 'runtime_gate'):
        op.drop_table(table)
    with op.batch_alter_table('workspace_bindings') as batch:
        batch.drop_column('services_enabled')
    for table in ('conversations', 'workspace_bindings', 'instance_settings'):
        with op.batch_alter_table(table) as batch:
            batch.drop_column('process_limit_version')
            batch.drop_column('process_limit')
