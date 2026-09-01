"""persist per-repository initialization steps

Revision ID: 0003_job_steps
Revises: 0002_embedding_metadata
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_job_steps"
down_revision = "0002_embedding_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("job_steps"):
        return
    op.create_table(
        "job_steps",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=True),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "repository_id",
            "stage",
            name="uq_job_step_job_repository_stage",
        ),
    )
    op.create_index("ix_job_steps_job_id", "job_steps", ["job_id"], unique=False)
    op.create_index("ix_job_steps_repository_id", "job_steps", ["repository_id"], unique=False)
    op.create_index("ix_job_steps_status", "job_steps", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_job_steps_status", table_name="job_steps")
    op.drop_index("ix_job_steps_repository_id", table_name="job_steps")
    op.drop_index("ix_job_steps_job_id", table_name="job_steps")
    op.drop_table("job_steps")
