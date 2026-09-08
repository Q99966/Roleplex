"""W1a 当前 World 工作区与 execution lease

Revision ID: 0006_world_workspaces
Revises: 0005_agent_executions

新增 Workspace Binding、execution workspace，并让 single 会话可空绑定当前 World
中的 managed directory。本迁移不增加命令、Shell、Git 或 worktree 字段。
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_world_workspaces"
down_revision = "0005_agent_executions"
branch_labels = None
depends_on = None

_ACTIVE_WORKSPACE = sa.text("active IS TRUE")


def upgrade() -> None:
    """创建工作区表并为会话增加可空绑定。"""
    op.create_table(
        "workspace_bindings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("root_path", sa.String(2048), nullable=False),
        sa.Column("workspace_kind", sa.String(32), nullable=False),
        sa.Column("file_tools_enabled", sa.Boolean(), nullable=False),
        sa.Column("basic_commands_enabled", sa.Boolean(), nullable=False),
        sa.Column("shell_enabled", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("root_path", name="uq_workspace_root_path"),
    )
    op.create_index(
        "uq_workspace_owner_name_active", "workspace_bindings", ["created_by", "display_name"],
        unique=True, sqlite_where=_ACTIVE_WORKSPACE, postgresql_where=_ACTIVE_WORKSPACE,
    )
    op.create_index(
        "ix_workspace_bindings_owner_active", "workspace_bindings", ["created_by", "active"],
    )

    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("workspace_binding_id", sa.Integer()))
        batch.create_foreign_key(
            "fk_conversations_workspace_binding_id", "workspace_bindings",
            ["workspace_binding_id"], ["id"], ondelete="SET NULL",
        )

    op.create_table(
        "execution_workspaces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "execution_id", sa.String(64),
            sa.ForeignKey("agent_executions.execution_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "workspace_binding_id", sa.Integer(),
            sa.ForeignKey("workspace_bindings.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("workspace_kind", sa.String(32), nullable=False),
        sa.Column("root_path_snapshot", sa.String(2048), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("cleaned_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("execution_id", name="uq_execution_workspaces_execution_id"),
    )
    op.create_index(
        "ix_execution_workspaces_binding_status", "execution_workspaces", ["workspace_binding_id", "status"],
    )
    with op.batch_alter_table("tool_calls") as batch:
        batch.add_column(sa.Column("execution_id", sa.String(64)))
        batch.add_column(sa.Column("workspace_binding_id", sa.Integer()))
        batch.create_foreign_key(
            "fk_tool_calls_execution_id", "agent_executions", ["execution_id"], ["execution_id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_tool_calls_workspace_binding_id", "workspace_bindings", ["workspace_binding_id"], ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_tool_calls_execution_id", "tool_calls", ["execution_id"])


def downgrade() -> None:
    """移除 W1a 工作区表和会话绑定。"""
    op.drop_index("ix_tool_calls_execution_id", table_name="tool_calls")
    with op.batch_alter_table("tool_calls") as batch:
        batch.drop_constraint("fk_tool_calls_workspace_binding_id", type_="foreignkey")
        batch.drop_constraint("fk_tool_calls_execution_id", type_="foreignkey")
        batch.drop_column("workspace_binding_id")
        batch.drop_column("execution_id")
    op.drop_index("ix_execution_workspaces_binding_status", table_name="execution_workspaces")
    op.drop_table("execution_workspaces")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_constraint("fk_conversations_workspace_binding_id", type_="foreignkey")
        batch.drop_column("workspace_binding_id")
    op.drop_index("ix_workspace_bindings_owner_active", table_name="workspace_bindings")
    op.drop_index("uq_workspace_owner_name_active", table_name="workspace_bindings")
    op.drop_table("workspace_bindings")
