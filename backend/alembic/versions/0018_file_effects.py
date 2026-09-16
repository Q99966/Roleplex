"""保存文件提交边界证据，不复制源码或工具详情。"""
from alembic import op
import sqlalchemy as sa
revision='0018_file_effects'
down_revision='0017_unlimited_decisions'
branch_labels=None
depends_on=None


def upgrade():
    """旧调用不补造记录，新增表只接收真实执行边界。"""
    op.create_table('file_effects',
        sa.Column('id',sa.Integer(),primary_key=True,autoincrement=True),
        sa.Column('execution_id',sa.String(64),sa.ForeignKey('agent_executions.execution_id',ondelete='CASCADE'),nullable=False),
        sa.Column('call_id',sa.String(128),nullable=False),
        sa.Column('item_index',sa.Integer(),nullable=False),
        sa.Column('payload_encrypted',sa.Text()),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('execution_id','call_id','item_index',name='uq_file_effect_call_item'))
    op.create_index('ix_file_effect_execution_id','file_effects',['execution_id','id'])


def downgrade():
    """移除边界证据，保留原有工具详情和实际文件。"""
    op.drop_table('file_effects')
