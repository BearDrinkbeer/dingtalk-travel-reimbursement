"""Remove the unverifiable OA relationship smoke-test flag."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0017"
down_revision: str | None = "20260907_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("oa_template_profiles") as batch_op:
        batch_op.drop_column("related_approval_smoke_test_confirmed")


def downgrade() -> None:
    with op.batch_alter_table("oa_template_profiles") as batch_op:
        batch_op.add_column(
            sa.Column(
                "related_approval_smoke_test_confirmed",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
