"""create the v1 domain schema

Revision ID: 0001_initial
Revises:
"""

from alembic import op

from packages.common import models  # noqa: F401
from packages.common.db import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Initial schema is generated from the single source of truth: SQLAlchemy models."""

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    """The first migration is reversible for local development and test databases."""

    Base.metadata.drop_all(bind=op.get_bind())
