"""drop unused memory_relations; sync table/column comments

Revision ID: 0008_drop_relations_comments
Revises: 0007_query_log_recall
"""

import sqlalchemy as sa
from alembic import op

from packages.common import models  # noqa: F401
from packages.common.db import Base

revision = "0008_drop_relations_comments"
down_revision = "0007_query_log_recall"
branch_labels = None
depends_on = None


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _apply_comments(bind) -> None:
    """把 SQLAlchemy comment= 写成 PostgreSQL COMMENT ON。COMMENT 字面量不能绑参数。"""

    for table in Base.metadata.sorted_tables:
        if table.comment:
            bind.execute(sa.text(f"COMMENT ON TABLE {_quote(table.name)} IS {_sql_str(table.comment)}"))
        for column in table.columns:
            if not column.comment:
                continue
            bind.execute(
                sa.text(
                    "COMMENT ON COLUMN "
                    f"{_quote(table.name)}.{_quote(column.name)} IS {_sql_str(column.comment)}"
                )
            )


def upgrade() -> None:
    op.drop_table("memory_relations")
    _apply_comments(op.get_bind())


def downgrade() -> None:
    op.create_table(
        "memory_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_memory_id",
            sa.Integer(),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_memory_id",
            sa.Integer(),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0.5", nullable=False),
    )
