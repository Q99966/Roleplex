"""角色级模型上下文窗口

Revision ID: 0004_role_context_window
Revises: 0003_delete_semantics

同一份 Provider Key/base URL 可以被多个角色绑定到不同模型，因此上下文窗口属于角色选择的具体模型，
不能放在 Provider 配置上，也不能混入会透传给厂商的 params_json。已有开发角色统一回填 200K；真实能力
仍由 Owner 按所用模型调整。

SQLite 继续使用 batch 模式，保持后续表重建和 PostgreSQL 离线 SQL 的同一迁移路径。
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_role_context_window"
down_revision = "0003_delete_semantics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加非空角色上下文窗口并回填 200K。"""
    with op.batch_alter_table("roles") as batch:
        batch.add_column(
            sa.Column("context_window_tokens", sa.Integer(), nullable=False, server_default="200000")
        )


def downgrade() -> None:
    """移除角色上下文窗口配置。"""
    with op.batch_alter_table("roles") as batch:
        batch.drop_column("context_window_tokens")
