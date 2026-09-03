"""Persist the DingTalk union ID required for server-side file uploads.

Revision ID: 20260904_0008
Revises: 20260903_0007
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0008"
down_revision: str | None = "20260903_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows intentionally remain NULL. A union ID cannot be derived
    # safely from userId, so those sessions must authenticate again.
    op.add_column(
        "sessions",
        sa.Column("dingtalk_union_id", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_column("dingtalk_union_id")
