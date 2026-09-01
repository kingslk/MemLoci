"""query log recall fields for MCP iteration

Revision ID: 0007_query_log_recall
Revises: 0006_job_stage_detail
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_query_log_recall"
down_revision = "0006_job_stage_detail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_query_logs",
        sa.Column("recall_mode", sa.String(length=32), server_default="", nullable=False),
    )
    op.add_column(
        "agent_query_logs",
        sa.Column("primary_switched", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "agent_query_logs",
        sa.Column("returned_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_agent_query_logs_project_id", "agent_query_logs", ["project_id"])
    op.create_index("ix_agent_query_logs_recall_mode", "agent_query_logs", ["recall_mode"])


def downgrade() -> None:
    op.drop_index("ix_agent_query_logs_recall_mode", table_name="agent_query_logs")
    op.drop_index("ix_agent_query_logs_project_id", table_name="agent_query_logs")
    op.drop_column("agent_query_logs", "returned_count")
    op.drop_column("agent_query_logs", "primary_switched")
    op.drop_column("agent_query_logs", "recall_mode")
