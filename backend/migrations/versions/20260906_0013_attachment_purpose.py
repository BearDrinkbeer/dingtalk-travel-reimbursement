"""Persist supporting-document purpose without rewriting existing snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_0013"
down_revision: str | None = "20260906_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reimbursement_draft_files",
        sa.Column(
            "attachment_kind",
            sa.String(32),
            sa.CheckConstraint(
                "attachment_kind IN ('itinerary', 'payment_proof', 'other')",
                name="ck_reimbursement_draft_files_attachment_kind",
            ),
            nullable=False,
            server_default="other",
        ),
    )
    # Existing explicit relationships are sufficient purpose evidence. Unlinked
    # attachments stay "other"; immutable v1/v2 snapshot JSON is never rewritten.
    op.execute("""
        UPDATE reimbursement_draft_files AS file SET attachment_kind = 'itinerary'
        WHERE file.processing_role = 'ATTACHMENT_ONLY' AND EXISTS (
            SELECT 1 FROM reimbursement_drafts AS draft,
            json_each(CASE WHEN json_valid(draft.input_json)
                           THEN draft.input_json ELSE '{}' END, '$.items') AS item,
            json_each(item.value, '$.itineraryFileIds') AS linked
            WHERE draft.id = file.draft_id AND linked.value = file.id
        )
    """)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT count(*) FROM reimbursement_submissions WHERE snapshot_version >= 3")
    ) or connection.scalar(
        sa.text("SELECT count(*) FROM reimbursement_draft_files WHERE attachment_kind != 'other'")
    ):
        raise RuntimeError("Cannot discard supporting-document purpose; restore a prior backup")
    op.drop_column("reimbursement_draft_files", "attachment_kind")
