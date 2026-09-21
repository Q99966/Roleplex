"""按图版本区分同节点同轮的激活，保留移除/重新添加前的意图。"""
from alembic import op
import sqlalchemy as sa
revision='0022_activation_graph_versions'
down_revision='0021_workflow_graph_control'
branch_labels=None
depends_on=None


def upgrade():
    with op.batch_alter_table('workflow_activations') as batch:
        batch.drop_constraint('uq_workflow_activation_round',type_='unique')
        batch.create_unique_constraint('uq_workflow_activation_graph_round',['run_id','node_id','iteration','graph_revision'])


def downgrade():
    if not op.get_context().as_sql:
        table=sa.table('workflow_activations',sa.column('run_id'),sa.column('node_id'),sa.column('iteration'))
        duplicate=op.get_bind().execute(sa.select(table.c.run_id).group_by(table.c.run_id,table.c.node_id,table.c.iteration).having(sa.func.count()>1).limit(1)).first()
        if duplicate: raise RuntimeError('WORKFLOW_GRAPH_DOWNGRADE_REQUIRES_HISTORY_PRESERVATION')
    with op.batch_alter_table('workflow_activations') as batch:
        batch.drop_constraint('uq_workflow_activation_graph_round',type_='unique')
        batch.create_unique_constraint('uq_workflow_activation_round',['run_id','node_id','iteration'])
