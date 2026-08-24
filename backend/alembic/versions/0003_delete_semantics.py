"""删除语义：角色墓碑与会话回收站

Revision ID: 0003_delete_semantics
Revises: 0002_m2_messaging

角色删除改为墓碑：新增 deleted_at，并把 model_config_id 放开为可空，
以便清除模型绑定的同时保留 id、名称与头像，让历史消息永远能显示"谁说的"。
角色重名约束从整表唯一改为"只约束未删除角色"的部分唯一索引：墓碑保留原名
用于历史展示，同时不再挡住新建同名角色。部分索引在 SQLite 与 PostgreSQL
上都支持，不依赖单一数据库的专属特性。

会话删除改为回收站：新增 deleted_at 与其索引，删除只写时间戳，
超过保留期后由启动清理真正级联删除。

SQLite 不支持在已有表上直接改列与删约束，因此统一走 batch 模式，
保证同一份迁移在 SQLite 与 PostgreSQL 上都能重放。
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_delete_semantics"
down_revision = "0002_m2_messaging"
branch_labels = None
depends_on = None

# 部分唯一索引的条件，升级与降级共用，避免两处写法漂移。
_ACTIVE_ROLE = sa.text("deleted_at IS NULL")


def upgrade() -> None:
    with op.batch_alter_table("roles") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True)))
        # 墓碑要清除模型绑定，因此该外键列必须允许为空。
        batch.alter_column("model_config_id", existing_type=sa.Integer(), nullable=True)
        batch.drop_constraint("uq_role_owner_name", type_="unique")
    op.create_index(
        "uq_role_owner_name_active", "roles", ["created_by", "name"], unique=True,
        sqlite_where=_ACTIVE_ROLE, postgresql_where=_ACTIVE_ROLE,
    )

    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.create_index("ix_conversations_deleted_at", "conversations", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_conversations_deleted_at", table_name="conversations")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("deleted_at")

    op.drop_index("uq_role_owner_name_active", table_name="roles")
    with op.batch_alter_table("roles") as batch:
        batch.create_unique_constraint("uq_role_owner_name", ["created_by", "name"])
        # 回退前墓碑角色的 model_config_id 为空，恢复非空约束会失败；
        # 因此降级只在没有墓碑数据时可用，与开发阶段重建数据库的纪律一致。
        batch.alter_column("model_config_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("deleted_at")
