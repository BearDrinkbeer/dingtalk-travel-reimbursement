"""Add administrator-managed receipt category keyword mappings.

Revision ID: 20260902_0004
Revises: 20260902_0003
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0004"
down_revision: str | None = "20260902_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "receipt_keyword_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("keyword", sa.String(length=100), nullable=False),
        sa.Column("normalized_keyword", sa.String(length=100), nullable=False),
        sa.Column("category_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_receipt_keyword_mappings_normalized_keyword",
        "receipt_keyword_mappings",
        ["normalized_keyword"],
        unique=True,
    )
    op.create_index(
        "ix_receipt_keyword_mappings_category_id",
        "receipt_keyword_mappings",
        ["category_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_receipt_keyword_mappings_category_id",
        table_name="receipt_keyword_mappings",
    )
    op.drop_index(
        "ix_receipt_keyword_mappings_normalized_keyword",
        table_name="receipt_keyword_mappings",
    )
    op.drop_table("receipt_keyword_mappings")
