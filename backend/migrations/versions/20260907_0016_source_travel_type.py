"""Persist the verified source travel category on selected approvals."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_0016"
down_revision: str | None = "20260907_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reimbursement_draft_related_approvals",
        sa.Column("source_travel_type_value", sa.String(1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reimbursement_draft_related_approvals", "source_travel_type_value")
