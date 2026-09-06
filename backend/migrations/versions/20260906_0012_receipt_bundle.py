"""Add generated receipt PDF without changing existing approval snapshots.

Revision ID: 20260906_0012
Revises: 20260904_0011
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_0012"
down_revision: str | None = "20260904_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _bundle_trigger(operation: str) -> str:
    return f"""
    CREATE TRIGGER trg_reimbursement_submissions_bundle_manifest_{operation.lower()}
    BEFORE {operation} ON reimbursement_submissions
    WHEN NEW.snapshot_version >= 2 AND NEW.status = 'SUBMITTED'
         AND (
             (SELECT count(*) FROM reimbursement_uploads WHERE submission_id = NEW.id) != 2
             OR NOT EXISTS (
                 SELECT 1 FROM reimbursement_uploads
                 WHERE submission_id = NEW.id AND role = 'GENERATED_PDF'
                   AND sort_order = 0 AND upload_status = 'LINKED'
             )
             OR NOT EXISTS (
                 SELECT 1 FROM reimbursement_uploads
                 WHERE submission_id = NEW.id AND role = 'GENERATED_EXCEL'
                   AND sort_order = 1 AND upload_status = 'LINKED'
             )
         )
    BEGIN
        SELECT RAISE(ABORT, 'submitted reimbursement requires PDF and Excel');
    END
    """


def _replace_role_checks(*, allow_pdf: bool) -> None:
    connection = op.get_bind()
    # SQLite table recreation invalidates triggers on both this table and its
    # parent. Preserve deployed trigger SQL rather than importing today's model.
    triggers = connection.execute(
        sa.text(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
            "AND name LIKE 'trg_reimbursement_%'"
        )
    ).all()
    for name, _sql in triggers:
        connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
    roles = "'ORIGINAL', 'GENERATED_EXCEL'" + (", 'GENERATED_PDF'" if allow_pdf else "")
    generated = (
        "role IN ('GENERATED_EXCEL', 'GENERATED_PDF')" if allow_pdf else "role = 'GENERATED_EXCEL'"
    )
    with op.batch_alter_table("reimbursement_uploads", recreate="always") as batch:
        batch.drop_constraint("ck_reimbursement_uploads_role", type_="check")
        batch.drop_constraint("ck_reimbursement_uploads_source_role", type_="check")
        batch.create_check_constraint("ck_reimbursement_uploads_role", f"role IN ({roles})")
        batch.create_check_constraint(
            "ck_reimbursement_uploads_source_role",
            "(role = 'ORIGINAL' AND source_draft_file_id IS NOT NULL) OR "
            f"({generated} AND source_draft_file_id IS NULL)",
        )
    for _name, sql in triggers:
        connection.exec_driver_sql(sql)


def upgrade() -> None:
    _replace_role_checks(allow_pdf=True)
    op.create_index(
        "uq_reimbursement_uploads_generated_pdf",
        "reimbursement_uploads",
        ["submission_id"],
        unique=True,
        sqlite_where=sa.text("role = 'GENERATED_PDF'"),
        postgresql_where=sa.text("role = 'GENERATED_PDF'"),
    )
    for operation in ("INSERT", "UPDATE"):
        op.execute(_bundle_trigger(operation))


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT count(*) FROM reimbursement_submissions WHERE snapshot_version >= 2")
    ) or connection.scalar(
        sa.text("SELECT count(*) FROM reimbursement_uploads WHERE role = 'GENERATED_PDF'")
    ):
        raise RuntimeError(
            "Cannot downgrade receipt bundles while version 2 submissions exist; "
            "restore a pre-upgrade backup instead of discarding approval audit records."
        )
    for operation in ("insert", "update"):
        op.execute(f"DROP TRIGGER trg_reimbursement_submissions_bundle_manifest_{operation}")
    op.drop_index("uq_reimbursement_uploads_generated_pdf", table_name="reimbursement_uploads")
    _replace_role_checks(allow_pdf=False)
