"""record embedding provider metadata

Revision ID: 0002_embedding_metadata
Revises: 0001_initial
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_embedding_metadata"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in ("evidence", "code_files", "memories"):
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "embedding_provider" not in columns:
            op.add_column(
                table,
                sa.Column(
                    "embedding_provider",
                    sa.String(length=100),
                    nullable=False,
                    server_default="hash",
                ),
            )
        if "embedding_model" not in columns:
            op.add_column(
                table,
                sa.Column(
                    "embedding_model",
                    sa.String(length=200),
                    nullable=False,
                    server_default="hash-384",
                ),
            )
        if "embedding_dimensions" not in columns:
            op.add_column(
                table,
                sa.Column(
                    "embedding_dimensions",
                    sa.Integer(),
                    nullable=False,
                    server_default="384",
                ),
            )
        if "embedding_version" not in columns:
            op.add_column(
                table,
                sa.Column(
                    "embedding_version",
                    sa.String(length=100),
                    nullable=False,
                    server_default="v1",
                ),
            )


def downgrade() -> None:
    for table in ("memories", "code_files", "evidence"):
        op.drop_column(table, "embedding_version")
        op.drop_column(table, "embedding_dimensions")
        op.drop_column(table, "embedding_model")
        op.drop_column(table, "embedding_provider")
