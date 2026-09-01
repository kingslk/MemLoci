"""remove the unused repository webhook secret digest

Revision ID: 0005_remove_repo_webhook
Revises: 0004_repository_webhook_secret
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_remove_repo_webhook"
down_revision = "0004_repository_webhook_secret"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("repositories", "webhook_secret_hash")


def downgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("webhook_secret_hash", sa.String(length=64), nullable=True),
    )
