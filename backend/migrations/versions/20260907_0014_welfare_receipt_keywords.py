"""Seed company-requested delivery and bedding classification hints once.

Revision ID: 20260907_0014
Revises: 20260906_0013
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_0014"
down_revision: str | None = "20260906_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEYWORDS = ("收派服务", "床品", "床笠", "床单", "被套")


def upgrade() -> None:
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
    )
    rows = [
        {"keyword": keyword, "normalized_keyword": keyword, "category_id": "employee_welfare"}
        for keyword in _KEYWORDS
        if keyword not in existing
    ]
    if rows:
        op.bulk_insert(table, rows)


def downgrade() -> None:
    # These are editable business settings, not schema. Keep them on downgrade
    # rather than deleting a pre-existing or subsequently edited user rule.
    pass
