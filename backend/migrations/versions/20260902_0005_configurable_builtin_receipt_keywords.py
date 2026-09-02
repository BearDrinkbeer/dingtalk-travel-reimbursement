"""Make built-in receipt classification keywords configurable.

Revision ID: 20260902_0005
Revises: 20260902_0004
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0005"
down_revision: str | None = "20260902_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BUILTIN_KEYWORDS = (
    ("酒店", "lodging"),
    ("宾馆", "lodging"),
    ("住宿", "lodging"),
    ("房费", "lodging"),
    ("客房", "lodging"),
    ("航空", "airfare"),
    ("飞机", "airfare"),
    ("民航", "airfare"),
    ("铁路", "rail_fare"),
    ("火车", "rail_fare"),
    ("高铁", "rail_fare"),
    ("动车", "rail_fare"),
    ("出租汽车", "local_transport"),
    ("出租车", "local_transport"),
    ("公路客运", "local_transport"),
    ("道路旅客运输", "local_transport"),
    ("汽车客运", "local_transport"),
    ("客运服务费", "local_transport"),
)


def upgrade() -> None:
    op.add_column(
        "receipt_keyword_mappings",
        sa.Column("is_builtin", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "receipt_keyword_mappings",
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )

    connection = op.get_bind()
    existing = set(
        connection.execute(
            sa.text("SELECT normalized_keyword FROM receipt_keyword_mappings")
        ).scalars()
    )
    table = sa.table(
        "receipt_keyword_mappings",
        sa.column("keyword", sa.String()),
        sa.column("normalized_keyword", sa.String()),
        sa.column("category_id", sa.String()),
        sa.column("is_builtin", sa.Boolean()),
        sa.column("enabled", sa.Boolean()),
    )
    rows = [
        {
            "keyword": keyword,
            "normalized_keyword": keyword.casefold(),
            "category_id": category_id,
            "is_builtin": True,
            "enabled": True,
        }
        for keyword, category_id in _BUILTIN_KEYWORDS
        if keyword.casefold() not in existing
    ]
    if rows:
        op.bulk_insert(table, rows)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM receipt_keyword_mappings WHERE is_builtin = 1"))
    op.drop_column("receipt_keyword_mappings", "enabled")
    op.drop_column("receipt_keyword_mappings", "is_builtin")
