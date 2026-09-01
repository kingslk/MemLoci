"""store a webhook secret digest per repository

Revision ID: 0004_repository_webhook_secret
Revises: 0003_job_steps
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_repository_webhook_secret"
down_revision = "0003_job_steps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("repositories")}
    if "webhook_secret_hash" not in columns:
        op.add_column(
            "repositories",
            sa.Column("webhook_secret_hash", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("repositories")}
    if "webhook_secret_hash" in columns:
        op.drop_column("repositories", "webhook_secret_hash")
