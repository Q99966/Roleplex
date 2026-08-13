"""create initial Roleplex schema

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Runtime uses Base.metadata.create_all during the bootstrap phase; this
    # migration is the reproducible schema contract for future deployments.
    from app.db import Base
    from app import models  # noqa: F401
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    from app.db import Base
    from app import models  # noqa: F401
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
