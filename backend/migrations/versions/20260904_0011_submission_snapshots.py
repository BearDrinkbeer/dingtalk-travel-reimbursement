"""Add immutable reimbursement submission and OA request snapshots.

Revision ID: 20260904_0011
Revises: 20260904_0010
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0011"
down_revision: str | None = "20260904_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CLEANUP_UPLOAD_TRIGGER_NAMES = (
    "trg_reimbursement_uploads_cleanup_parent_insert",
    "trg_reimbursement_uploads_cleanup_parent_update",
)


def _cleanup_upload_trigger_sql(*, operation: str, allow_completed_parent: bool) -> str:
    completed_parent = (
        """
                       OR (
                           NEW.upload_status = 'CLEANED'
                           AND submission.status = 'FAILED_FINAL'
                       )
        """
        if allow_completed_parent
        else ""
    )
    return f"""
        CREATE TRIGGER trg_reimbursement_uploads_cleanup_parent_{operation.lower()}
        BEFORE {operation} ON reimbursement_uploads
        WHEN NEW.upload_status IN ('CLEANUP_PENDING', 'CLEANED')
             AND (
                 NOT EXISTS (
                     SELECT 1
                     FROM reimbursement_submissions AS submission
                     WHERE submission.id = NEW.submission_id
                       AND submission.draft_id = NEW.draft_id
                       AND submission.process_instance_id IS NULL
                       AND (
                           submission.status = 'ORPHAN_CLEANUP'
                           {completed_parent}
                       )
                 )
                 OR EXISTS (
                     SELECT 1
                     FROM reimbursement_uploads AS upload
                     WHERE upload.submission_id = NEW.submission_id
                       AND upload.upload_status = 'LINKED'
                 )
             )
        BEGIN
            SELECT RAISE(ABORT, 'remote cleanup requires an uncreated orphan submission');
        END
    """


def _replace_cleanup_upload_triggers(*, allow_completed_parent: bool) -> None:
    for name in _CLEANUP_UPLOAD_TRIGGER_NAMES:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    for operation in ("INSERT", "UPDATE"):
        op.execute(
            _cleanup_upload_trigger_sql(
                operation=operation,
                allow_completed_parent=allow_completed_parent,
            )
        )


def upgrade() -> None:
    op.add_column(
        "reimbursement_submissions",
        sa.Column(
            "snapshot_version",
            sa.Integer(),
            sa.CheckConstraint(
                "snapshot_version > 0",
                name="ck_reimbursement_submissions_snapshot_version_positive",
            ),
            server_default="1",
            nullable=False,
        ),
    )
    op.add_column(
        "reimbursement_submissions",
        sa.Column("oa_request_json", sa.Text(), nullable=True),
    )

    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_submissions_snapshot_immutable
        BEFORE UPDATE ON reimbursement_submissions
        WHEN NEW.draft_id IS NOT OLD.draft_id
             OR NEW.corp_id IS NOT OLD.corp_id
             OR NEW.originator_user_id IS NOT OLD.originator_user_id
             OR NEW.originator_union_id IS NOT OLD.originator_union_id
             OR NEW.originator_name IS NOT OLD.originator_name
             OR NEW.department_id IS NOT OLD.department_id
             OR NEW.department_name IS NOT OLD.department_name
             OR NEW.template_process_code IS NOT OLD.template_process_code
             OR NEW.template_config_version IS NOT OLD.template_config_version
             OR NEW.schema_fingerprint IS NOT OLD.schema_fingerprint
             OR NEW.snapshot_version IS NOT OLD.snapshot_version
             OR NEW.form_snapshot_json IS NOT OLD.form_snapshot_json
             OR NEW.related_instance_ids_json IS NOT OLD.related_instance_ids_json
             OR NEW.snapshot_sha256 IS NOT OLD.snapshot_sha256
             OR NEW.idempotency_key_hash IS NOT OLD.idempotency_key_hash
        BEGIN
            SELECT RAISE(ABORT, 'reimbursement submission snapshot is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_submissions_oa_request_immutable
        BEFORE UPDATE OF oa_create_started_at, oa_request_json, oa_request_hash
        ON reimbursement_submissions
        WHEN OLD.oa_create_started_at IS NOT NULL
             AND (
                 NEW.oa_create_started_at IS NOT OLD.oa_create_started_at
                 OR NEW.oa_request_json IS NOT OLD.oa_request_json
                 OR NEW.oa_request_hash IS NOT OLD.oa_request_hash
             )
        BEGIN
            SELECT RAISE(ABORT, 'OA create checkpoint is immutable once set');
        END
        """
    )
    _replace_cleanup_upload_triggers(allow_completed_parent=True)
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_submissions_cleanup_guard_update")
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_submissions_cleanup_guard_update
        BEFORE UPDATE ON reimbursement_submissions
        WHEN EXISTS (
                 SELECT 1
                 FROM reimbursement_uploads AS upload
                 WHERE upload.submission_id = OLD.id
                   AND upload.upload_status IN ('CLEANUP_PENDING', 'CLEANED')
             )
             AND (
                 NEW.id IS NOT OLD.id
                 OR NEW.process_instance_id IS NOT NULL
                 OR (
                     NEW.status IS NOT 'ORPHAN_CLEANUP'
                     AND (
                         NEW.status IS NOT 'FAILED_FINAL'
                         OR EXISTS (
                             SELECT 1
                             FROM reimbursement_uploads AS unfinished
                             WHERE unfinished.submission_id = OLD.id
                               AND unfinished.upload_status NOT IN (
                                   'PENDING', 'PUTTING', 'PUT_DONE', 'CLEANED', 'DISCARDED'
                               )
                         )
                     )
                 )
             )
        BEGIN
            SELECT RAISE(ABORT, 'OA instance and remote cleanup cannot coexist');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_submissions_cleanup_guard_update")
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_submissions_cleanup_guard_update
        BEFORE UPDATE ON reimbursement_submissions
        WHEN EXISTS (
                 SELECT 1
                 FROM reimbursement_uploads AS upload
                 WHERE upload.submission_id = OLD.id
                   AND upload.upload_status IN ('CLEANUP_PENDING', 'CLEANED')
             )
             AND (
                 NEW.id IS NOT OLD.id
                 OR NEW.status IS NOT 'ORPHAN_CLEANUP'
                 OR NEW.process_instance_id IS NOT NULL
             )
        BEGIN
            SELECT RAISE(ABORT, 'OA instance and remote cleanup cannot coexist');
        END
        """
    )
    _replace_cleanup_upload_triggers(allow_completed_parent=False)
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_submissions_oa_request_immutable")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_submissions_snapshot_immutable")
    op.drop_column("reimbursement_submissions", "oa_request_json")
    op.drop_column("reimbursement_submissions", "snapshot_version")
