"""Allow hotel stay details without rewriting immutable submission snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_0015"
down_revision: str | None = "20260907_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_check(*, allow_hotel: bool) -> None:
    connection = op.get_bind()
    # Recreating this table temporarily invalidates reimbursement triggers on
    # related tables. Restore exactly the deployed SQL after the batch copy.
    triggers = connection.execute(
        sa.text(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
            "AND name LIKE 'trg_reimbursement_%'"
        )
    ).all()
    for name, _sql in triggers:
        connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
    kinds = "'itinerary', 'payment_proof', 'other'" + (", 'hotel_bill'" if allow_hotel else "")
    table = sa.Table("reimbursement_draft_files", sa.MetaData(), autoload_with=connection)
    for constraint in list(table.constraints):
        if constraint.name == "ck_reimbursement_draft_files_attachment_kind":
            table.constraints.remove(constraint)
    # Preserve 0013's inline column constraint so a chained downgrade can
    # DROP COLUMN attachment_kind without leaving a table CHECK referencing it.
    table.append_column(
        sa.Column(
            "attachment_kind",
            sa.String(32),
            sa.CheckConstraint(
                f"attachment_kind IN ({kinds})", name="ck_reimbursement_draft_files_attachment_kind"
            ),
            nullable=False,
            server_default="other",
        ),
        replace_existing=True,
    )
    with op.batch_alter_table("reimbursement_draft_files", copy_from=table, recreate="always"):
        pass
    for _name, sql in triggers:
        connection.exec_driver_sql(sql)


def upgrade() -> None:
    _replace_check(allow_hotel=True)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT count(*) FROM reimbursement_draft_files WHERE attachment_kind = 'hotel_bill'"
        )
    ) or connection.scalar(
        sa.text("SELECT count(*) FROM reimbursement_submissions WHERE snapshot_version >= 4")
    ):
        raise RuntimeError("Cannot discard hotel stay evidence; restore a prior backup")
    _replace_check(allow_hotel=False)
