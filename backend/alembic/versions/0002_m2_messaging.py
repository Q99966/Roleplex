"""M2 消息闭环：会话事件游标、消息幂等键、生成状态与事件持久化

Revision ID: 0002_m2_messaging
Revises: 0001_initial

会话新增 revision/event_seq 作为乐观锁与事件序号来源；消息新增客户端幂等键；
新增 generations / event_log / queue_jobs 支撑停止生成、断线恢复和单进程会话队列。

SQLite 不支持在已有表上直接加约束，因此列与约束变更统一走 batch 模式，
保证同一份迁移在 SQLite 与 PostgreSQL 上都能重放。
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_m2_messaging"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("event_seq", sa.Integer(), nullable=False, server_default="0"))

    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("client_message_id", sa.String(128)))
        # 同一会话同一发送者的客户端消息 ID 唯一，重复投递直接命中已有消息。
        batch.create_unique_constraint(
            "uq_message_client_key", ["conversation_id", "sender_id", "client_message_id"]
        )
    op.create_index("ix_messages_conversation_id_id", "messages", ["conversation_id", "id"])
    op.create_index("ix_messages_conversation_pinned", "messages", ["conversation_id", "pinned"])

    op.create_table(
        "generations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assistant_message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("stream_epoch", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(64)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("stop_requested_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_generations_conversation_status", "generations", ["conversation_id", "status"])
    # 事件先落库再广播，(conversation_id, event_seq) 唯一保证客户端可幂等回放。
    op.create_table(
        "event_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("stream_epoch", sa.String(128)),
        sa.Column("generation_id", sa.Integer(), sa.ForeignKey("generations.id", ondelete="SET NULL")),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("delta_seq", sa.Integer()),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", "event_seq", name="uq_event_conversation_seq"),
    )
    op.create_index("ix_event_conversation_seq", "event_log", ["conversation_id", "event_seq"])

    op.create_table(
        "queue_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation_id", sa.Integer(), sa.ForeignKey("generations.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_jobs_conversation_status", "queue_jobs", ["conversation_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_jobs_conversation_status", table_name="queue_jobs")
    op.drop_table("queue_jobs")
    op.drop_index("ix_event_conversation_seq", table_name="event_log")
    op.drop_table("event_log")
    op.drop_index("ix_generations_conversation_status", table_name="generations")
    op.drop_table("generations")
    op.drop_index("ix_messages_conversation_pinned", table_name="messages")
    op.drop_index("ix_messages_conversation_id_id", table_name="messages")
    with op.batch_alter_table("messages") as batch:
        batch.drop_constraint("uq_message_client_key", type_="unique")
        batch.drop_column("client_message_id")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("event_seq")
        batch.drop_column("revision")
