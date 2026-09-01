"""allow longer live job stage text

Revision ID: 0006_job_stage_detail
Revises: 0005_remove_repo_webhook
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_job_stage_detail"
down_revision = "0005_remove_repo_webhook"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "jobs",
        "current_stage",
        existing_type=sa.String(length=64),
        type_=sa.Text(),
        existing_nullable=False,
        existing_server_default="",
    )


def downgrade() -> None:
    op.alter_column(
        "jobs",
        "current_stage",
        existing_type=sa.Text(),
        type_=sa.String(length=64),
        existing_nullable=False,
        existing_server_default="",
    )
