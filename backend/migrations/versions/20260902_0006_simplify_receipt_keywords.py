"""Remove receipt keyword source and enabled state.

Revision ID: 20260902_0006
Revises: 20260902_0005
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0006"
down_revision: str | None = "20260902_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("receipt_keyword_mappings", "enabled")
    op.drop_column("receipt_keyword_mappings", "is_builtin")


def downgrade() -> None:
    op.add_column(
        "receipt_keyword_mappings",
        sa.Column("is_builtin", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "receipt_keyword_mappings",
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
