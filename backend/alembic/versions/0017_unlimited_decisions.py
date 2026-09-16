"""明确不限决策模式，保留旧配置和快照，并扩展计数存储。"""
from alembic import op, context
import sqlalchemy as sa

revision='0017_unlimited_decisions'
down_revision='0016_usage_history_index'
branch_labels=None
depends_on=None

FIELDS={
    'instance_settings': [('decision_limit',True)],
    'workflow_budgets': [('decision_limit',True),('used_decisions',False)],
    'agent_executions': [('decision_count',False)],
    'model_call_usage': [('call_index',False)],
}


def upgrade():
    """null 为明确不限；旧行数值不变，不重置运行中预算。"""
    for table,fields in FIELDS.items():
        with op.batch_alter_table(table) as batch:
            for name,nullable in fields:
                batch.alter_column(name,existing_type=sa.Integer(),type_=sa.BigInteger(),
                    existing_nullable=False,nullable=nullable)


def downgrade():
    """不兼容旧版的数据必须先显式处理，禁止偷偷将不限改为有限。"""
    if not context.is_offline_mode():
        for table,fields in FIELDS.items():
            for name,_ in fields:
                column=sa.column(name,sa.BigInteger())
                source=sa.table(table,column)
                invalid=sa.select(sa.func.count()).select_from(source).where(
                    sa.or_(column.is_(None),column>(256 if name=='decision_limit' else 2**31-1),
                        column<(1 if name=='decision_limit' else 0)))
                if op.get_bind().scalar(invalid):
                    raise RuntimeError('DECISION_DOWNGRADE_REQUIRES_LEGACY_VALUES')
    for table,fields in reversed(list(FIELDS.items())):
        with op.batch_alter_table(table) as batch:
            for name,nullable in fields:
                batch.alter_column(name,existing_type=sa.BigInteger(),type_=sa.Integer(),
                    existing_nullable=nullable,nullable=False)
