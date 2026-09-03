"""Add persistent reimbursement drafts, submissions, and upload checkpoints.

Revision ID: 20260904_0009
Revises: 20260904_0008
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0009"
down_revision: str | None = "20260904_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reimbursement_drafts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("corp_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.String(length=128), nullable=False),
        sa.Column("department_name", sa.String(length=255), nullable=False),
        sa.Column("template_process_code", sa.String(length=128), nullable=False),
        sa.Column("template_config_version", sa.Integer(), nullable=False),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("related_instance_ids_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'REVIEW_READY', 'LOCKED', 'EXPIRED')",
            name="ck_reimbursement_drafts_status",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_reimbursement_drafts_revision_positive",
        ),
        sa.CheckConstraint(
            "length(schema_fingerprint) = 64",
            name="ck_reimbursement_drafts_schema_fingerprint_length",
        ),
        sa.CheckConstraint(
            "template_config_version > 0",
            name="ck_reimbursement_drafts_template_config_version_positive",
        ),
        sa.CheckConstraint(
            "status != 'LOCKED' OR locked_at IS NOT NULL",
            name="ck_reimbursement_drafts_locked_at",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "corp_id",
            "owner_user_id",
            name="uq_reimbursement_drafts_identity",
        ),
    )
    op.create_index(
        "ix_reimbursement_drafts_owner_updated",
        "reimbursement_drafts",
        ["corp_id", "owner_user_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_reimbursement_drafts_status_expires",
        "reimbursement_drafts",
        ["status", "expires_at"],
        unique=False,
    )

    op.create_table(
        "reimbursement_draft_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("draft_id", sa.String(length=36), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("processing_role", sa.String(length=32), nullable=False),
        sa.Column("file_status", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("part_storage_key", sa.String(length=255), nullable=True),
        sa.Column("reserved_bytes", sa.Integer(), nullable=False),
        sa.Column("reservation_expires_at", sa.DateTime(), nullable=True),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=16), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("ocr_status", sa.String(length=32), nullable=False),
        sa.Column("ocr_result_json", sa.Text(), nullable=True),
        sa.Column("purged_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "processing_role IN ('EXPENSE_SOURCE', 'ATTACHMENT_ONLY')",
            name="ck_reimbursement_draft_files_processing_role",
        ),
        sa.CheckConstraint(
            "file_status IN ('RESERVED', 'WRITING', 'ACTIVE', 'FAILED', 'DELETING', 'PURGED')",
            name="ck_reimbursement_draft_files_status",
        ),
        sa.CheckConstraint(
            "ocr_status IN ('NOT_REQUESTED', 'RUNNING', 'COMPLETE', 'FAILED')",
            name="ck_reimbursement_draft_files_ocr_status",
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name="ck_reimbursement_draft_files_sort_order",
        ),
        sa.CheckConstraint(
            "reserved_bytes > 0",
            name="ck_reimbursement_draft_files_reserved_bytes_positive",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes > 0",
            name="ck_reimbursement_draft_files_size_positive",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes <= reserved_bytes",
            name="ck_reimbursement_draft_files_size_within_reservation",
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR length(sha256) = 64",
            name="ck_reimbursement_draft_files_sha256_length",
        ),
        sa.CheckConstraint(
            "(size_bytes IS NULL) = (sha256 IS NULL)",
            name="ck_reimbursement_draft_files_metadata_pair",
        ),
        sa.CheckConstraint(
            "file_status NOT IN ('RESERVED', 'WRITING') OR "
            "(part_storage_key IS NOT NULL AND reservation_expires_at IS NOT NULL)",
            name="ck_reimbursement_draft_files_active_reservation",
        ),
        sa.CheckConstraint(
            "file_status != 'ACTIVE' OR "
            "(part_storage_key IS NULL AND size_bytes IS NOT NULL AND sha256 IS NOT NULL)",
            name="ck_reimbursement_draft_files_active_metadata",
        ),
        sa.CheckConstraint(
            "part_storage_key IS NULL OR file_status IN ('RESERVED', 'WRITING')",
            name="ck_reimbursement_draft_files_part_owned_by_active_reservation",
        ),
        sa.CheckConstraint(
            "reservation_expires_at IS NULL OR file_status IN ('RESERVED', 'WRITING')",
            name="ck_reimbursement_draft_files_expiry_for_active_reservation",
        ),
        sa.CheckConstraint(
            "file_status != 'PURGED' OR purged_at IS NOT NULL",
            name="ck_reimbursement_draft_files_purged_at",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["reimbursement_drafts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "draft_id",
            name="uq_reimbursement_draft_files_identity",
        ),
        sa.UniqueConstraint(
            "draft_id",
            "sort_order",
            name="uq_reimbursement_draft_files_draft_sort_order",
        ),
        sa.UniqueConstraint(
            "part_storage_key",
            name="uq_reimbursement_draft_files_part_storage_key",
        ),
        sa.UniqueConstraint(
            "storage_key",
            name="uq_reimbursement_draft_files_storage_key",
        ),
    )
    op.create_index(
        "ix_reimbursement_draft_files_draft_id",
        "reimbursement_draft_files",
        ["draft_id"],
        unique=False,
    )
    op.create_index(
        "ix_reimbursement_draft_files_draft_status",
        "reimbursement_draft_files",
        ["draft_id", "file_status"],
        unique=False,
    )

    op.create_table(
        "reimbursement_submissions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("draft_id", sa.String(length=36), nullable=False),
        sa.Column("corp_id", sa.String(length=128), nullable=False),
        sa.Column("originator_user_id", sa.String(length=128), nullable=False),
        sa.Column("originator_union_id", sa.String(length=128), nullable=False),
        sa.Column("originator_name", sa.String(length=128), nullable=False),
        sa.Column("department_id", sa.String(length=128), nullable=False),
        sa.Column("department_name", sa.String(length=255), nullable=False),
        sa.Column("template_process_code", sa.String(length=128), nullable=False),
        sa.Column("template_config_version", sa.Integer(), nullable=False),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("form_snapshot_json", sa.Text(), nullable=False),
        sa.Column("related_instance_ids_json", sa.Text(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resume_status", sa.String(length=32), nullable=True),
        sa.Column("status_version", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("reconciliation_attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("oa_create_started_at", sa.DateTime(), nullable=True),
        sa.Column("oa_request_hash", sa.String(length=64), nullable=True),
        sa.Column("reconciliation_deadline_at", sa.DateTime(), nullable=True),
        sa.Column("orphan_confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("orphan_confirmation_code", sa.String(length=128), nullable=True),
        sa.Column("orphan_confirmed_by_user_id", sa.String(length=128), nullable=True),
        sa.Column("process_instance_id", sa.String(length=128), nullable=True),
        sa.Column("business_id", sa.String(length=128), nullable=True),
        sa.Column("approval_url", sa.String(length=2048), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'VALIDATING', 'GENERATING_EXCEL', 'UPLOADING', "
            "'OA_CREATING', 'RECONCILING', 'VERIFYING', 'SUBMITTED', "
            "'FAILED_RETRYABLE', 'FAILED_FINAL', 'ORPHAN_CLEANUP', 'MANUAL_REVIEW')",
            name="ck_reimbursement_submissions_status",
        ),
        sa.CheckConstraint(
            "resume_status IS NULL OR resume_status IN ('VALIDATING', 'GENERATING_EXCEL', "
            "'UPLOADING', 'VERIFYING', 'ORPHAN_CLEANUP')",
            name="ck_reimbursement_submissions_resume_status",
        ),
        sa.CheckConstraint(
            "(status = 'FAILED_RETRYABLE' AND resume_status IS NOT NULL) OR "
            "(status != 'FAILED_RETRYABLE' AND resume_status IS NULL)",
            name="ck_reimbursement_submissions_resume_status_pair",
        ),
        sa.CheckConstraint(
            "status_version > 0",
            name="ck_reimbursement_submissions_status_version_positive",
        ),
        sa.CheckConstraint(
            "template_config_version > 0",
            name="ck_reimbursement_submissions_template_config_version_positive",
        ),
        sa.CheckConstraint(
            "length(schema_fingerprint) = 64",
            name="ck_reimbursement_submissions_schema_fingerprint_length",
        ),
        sa.CheckConstraint(
            "length(snapshot_sha256) = 64",
            name="ck_reimbursement_submissions_snapshot_sha256_length",
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64",
            name="ck_reimbursement_submissions_idempotency_hash_length",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND reconciliation_attempt_count >= 0",
            name="ck_reimbursement_submissions_attempt_counts",
        ),
        sa.CheckConstraint(
            "status NOT IN ('VERIFYING', 'SUBMITTED') OR process_instance_id IS NOT NULL",
            name="ck_reimbursement_submissions_instance_required",
        ),
        sa.CheckConstraint(
            "process_instance_id IS NULL OR "
            "status IN ('VERIFYING', 'SUBMITTED', 'MANUAL_REVIEW') OR "
            "(status = 'FAILED_RETRYABLE' AND resume_status = 'VERIFYING')",
            name="ck_reimbursement_submissions_instance_safe_status",
        ),
        sa.CheckConstraint(
            "status NOT IN ('OA_CREATING', 'RECONCILING', 'VERIFYING', 'SUBMITTED') OR "
            "(oa_create_started_at IS NOT NULL AND oa_request_hash IS NOT NULL)",
            name="ck_reimbursement_submissions_create_checkpoint",
        ),
        sa.CheckConstraint(
            "status != 'RECONCILING' OR reconciliation_deadline_at IS NOT NULL",
            name="ck_reimbursement_submissions_reconciliation_deadline",
        ),
        sa.CheckConstraint(
            "status != 'ORPHAN_CLEANUP' OR "
            "(orphan_confirmed_at IS NOT NULL AND orphan_confirmation_code IS NOT NULL)",
            name="ck_reimbursement_submissions_orphan_confirmation",
        ),
        sa.CheckConstraint(
            "status != 'SUBMITTED' OR "
            "(submitted_at IS NOT NULL AND business_id IS NOT NULL AND approval_url IS NOT NULL)",
            name="ck_reimbursement_submissions_submitted_at",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id", "corp_id", "originator_user_id"],
            [
                "reimbursement_drafts.id",
                "reimbursement_drafts.corp_id",
                "reimbursement_drafts.owner_user_id",
            ],
            ondelete="RESTRICT",
            name="fk_reimbursement_submissions_draft_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", name="uq_reimbursement_submissions_draft_id"),
        sa.UniqueConstraint(
            "corp_id",
            "originator_user_id",
            "draft_id",
            "idempotency_key_hash",
            name="uq_reimbursement_submissions_owner_draft_idempotency",
        ),
        sa.UniqueConstraint(
            "id",
            "draft_id",
            name="uq_reimbursement_submissions_identity",
        ),
        sa.UniqueConstraint(
            "process_instance_id",
            name="uq_reimbursement_submissions_process_instance_id",
        ),
    )
    op.create_index(
        "ix_reimbursement_submissions_due",
        "reimbursement_submissions",
        ["status", "next_attempt_at", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_reimbursement_submissions_owner_updated",
        "reimbursement_submissions",
        ["corp_id", "originator_user_id", "updated_at"],
        unique=False,
    )

    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_submissions_process_instance_immutable
        BEFORE UPDATE OF process_instance_id ON reimbursement_submissions
        WHEN OLD.process_instance_id IS NOT NULL
             AND NEW.process_instance_id IS NOT OLD.process_instance_id
        BEGIN
            SELECT RAISE(ABORT, 'process_instance_id is immutable once set');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_submissions_submitted_terminal
        BEFORE UPDATE OF status ON reimbursement_submissions
        WHEN OLD.status = 'SUBMITTED' AND NEW.status IS NOT OLD.status
        BEGIN
            SELECT RAISE(ABORT, 'submitted reimbursement is terminal');
        END
        """
    )

    op.create_table(
        "reimbursement_uploads",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("draft_id", sa.String(length=36), nullable=False),
        sa.Column("source_draft_file_id", sa.String(length=36), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("local_storage_key", sa.String(length=255), nullable=False),
        sa.Column("local_part_storage_key", sa.String(length=255), nullable=True),
        sa.Column("local_status", sa.String(length=32), nullable=False),
        sa.Column("reserved_bytes", sa.Integer(), nullable=False),
        sa.Column("reservation_expires_at", sa.DateTime(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=32), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("upload_status", sa.String(length=32), nullable=False),
        sa.Column("status_version", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("space_id", sa.String(length=128), nullable=True),
        sa.Column("file_id", sa.String(length=128), nullable=True),
        sa.Column("put_started_at", sa.DateTime(), nullable=True),
        sa.Column("commit_started_at", sa.DateTime(), nullable=True),
        sa.Column("cleanup_started_at", sa.DateTime(), nullable=True),
        sa.Column("linked_at", sa.DateTime(), nullable=True),
        sa.Column("cleaned_at", sa.DateTime(), nullable=True),
        sa.Column("local_deleted_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "role IN ('ORIGINAL', 'GENERATED_EXCEL')",
            name="ck_reimbursement_uploads_role",
        ),
        sa.CheckConstraint(
            "upload_status IN ('PENDING', 'PUTTING', 'PUT_DONE', 'COMMITTING', "
            "'COMMIT_UNCERTAIN', 'COMMITTED', 'CLEANUP_PENDING', 'CLEANED', 'LINKED', "
            "'DISCARDED')",
            name="ck_reimbursement_uploads_status",
        ),
        sa.CheckConstraint(
            "local_status IN ('RESERVED', 'WRITING', 'READY', 'DELETED', 'FAILED')",
            name="ck_reimbursement_uploads_local_status",
        ),
        sa.CheckConstraint(
            "(role = 'ORIGINAL' AND source_draft_file_id IS NOT NULL) OR "
            "(role = 'GENERATED_EXCEL' AND source_draft_file_id IS NULL)",
            name="ck_reimbursement_uploads_source_role",
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name="ck_reimbursement_uploads_sort_order",
        ),
        sa.CheckConstraint(
            "reserved_bytes > 0",
            name="ck_reimbursement_uploads_reserved_bytes_positive",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes > 0",
            name="ck_reimbursement_uploads_size_positive",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes <= reserved_bytes",
            name="ck_reimbursement_uploads_size_within_reservation",
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR length(sha256) = 64",
            name="ck_reimbursement_uploads_sha256_length",
        ),
        sa.CheckConstraint(
            "(size_bytes IS NULL) = (sha256 IS NULL)",
            name="ck_reimbursement_uploads_metadata_pair",
        ),
        sa.CheckConstraint(
            "local_status NOT IN ('RESERVED', 'WRITING') OR "
            "(local_part_storage_key IS NOT NULL AND reservation_expires_at IS NOT NULL)",
            name="ck_reimbursement_uploads_active_reservation",
        ),
        sa.CheckConstraint(
            "local_status != 'READY' OR "
            "(local_part_storage_key IS NULL AND size_bytes IS NOT NULL AND sha256 IS NOT NULL)",
            name="ck_reimbursement_uploads_local_ready_metadata",
        ),
        sa.CheckConstraint(
            "local_part_storage_key IS NULL OR local_status IN ('RESERVED', 'WRITING')",
            name="ck_reimbursement_uploads_part_owned_by_active_reservation",
        ),
        sa.CheckConstraint(
            "reservation_expires_at IS NULL OR local_status IN ('RESERVED', 'WRITING')",
            name="ck_reimbursement_uploads_expiry_for_active_reservation",
        ),
        sa.CheckConstraint(
            "upload_status = 'PENDING' OR local_status IN ('READY', 'DELETED')",
            name="ck_reimbursement_uploads_remote_requires_local_complete",
        ),
        sa.CheckConstraint(
            "upload_status IN ('PENDING', 'DISCARDED') OR "
            "(size_bytes IS NOT NULL AND sha256 IS NOT NULL)",
            name="ck_reimbursement_uploads_remote_ready_metadata",
        ),
        sa.CheckConstraint(
            "upload_status != 'DISCARDED' OR "
            "(local_status = 'DELETED' AND local_deleted_at IS NOT NULL "
            "AND space_id IS NULL AND file_id IS NULL)",
            name="ck_reimbursement_uploads_discarded_terminal_shape",
        ),
        sa.CheckConstraint(
            "status_version > 0 AND attempt_count >= 0",
            name="ck_reimbursement_uploads_versions",
        ),
        sa.CheckConstraint(
            "upload_status NOT IN ('COMMITTED', 'CLEANUP_PENDING', 'CLEANED', 'LINKED') "
            "OR (space_id IS NOT NULL AND file_id IS NOT NULL)",
            name="ck_reimbursement_uploads_remote_identity",
        ),
        sa.CheckConstraint(
            "(space_id IS NULL) = (file_id IS NULL)",
            name="ck_reimbursement_uploads_remote_identity_pair",
        ),
        sa.CheckConstraint(
            "upload_status != 'LINKED' OR linked_at IS NOT NULL",
            name="ck_reimbursement_uploads_linked_at",
        ),
        sa.CheckConstraint(
            "linked_at IS NULL OR upload_status = 'LINKED'",
            name="ck_reimbursement_uploads_linked_terminal",
        ),
        sa.CheckConstraint(
            "upload_status != 'CLEANED' OR cleaned_at IS NOT NULL",
            name="ck_reimbursement_uploads_cleaned_at",
        ),
        sa.CheckConstraint(
            "cleaned_at IS NULL OR upload_status = 'CLEANED'",
            name="ck_reimbursement_uploads_cleaned_terminal",
        ),
        sa.CheckConstraint(
            "upload_status NOT IN ('CLEANUP_PENDING', 'CLEANED') OR cleanup_started_at IS NOT NULL",
            name="ck_reimbursement_uploads_cleanup_started_at",
        ),
        sa.CheckConstraint(
            "local_deleted_at IS NULL OR "
            "(local_status = 'DELETED' AND "
            "upload_status IN ('LINKED', 'CLEANED', 'DISCARDED'))",
            name="ck_reimbursement_uploads_local_delete_safe_status",
        ),
        sa.CheckConstraint(
            "local_status != 'DELETED' OR local_deleted_at IS NOT NULL",
            name="ck_reimbursement_uploads_local_deleted_at",
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_file_id", "draft_id"],
            ["reimbursement_draft_files.id", "reimbursement_draft_files.draft_id"],
            ondelete="RESTRICT",
            name="fk_reimbursement_uploads_source_draft",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id", "draft_id"],
            ["reimbursement_submissions.id", "reimbursement_submissions.draft_id"],
            ondelete="CASCADE",
            name="fk_reimbursement_uploads_submission_draft",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "space_id",
            "file_id",
            name="uq_reimbursement_uploads_remote_file",
        ),
        sa.UniqueConstraint(
            "submission_id",
            "sort_order",
            name="uq_reimbursement_uploads_submission_sort_order",
        ),
        sa.UniqueConstraint(
            "submission_id",
            "source_draft_file_id",
            name="uq_reimbursement_uploads_submission_source_file",
        ),
        sa.UniqueConstraint(
            "local_storage_key",
            name="uq_reimbursement_uploads_local_storage_key",
        ),
        sa.UniqueConstraint(
            "local_part_storage_key",
            name="uq_reimbursement_uploads_local_part_storage_key",
        ),
    )
    op.create_index(
        "uq_reimbursement_uploads_generated_excel",
        "reimbursement_uploads",
        ["submission_id"],
        unique=True,
        sqlite_where=sa.text("role = 'GENERATED_EXCEL'"),
        postgresql_where=sa.text("role = 'GENERATED_EXCEL'"),
    )
    op.create_index(
        "ix_reimbursement_uploads_submission_id",
        "reimbursement_uploads",
        ["submission_id"],
        unique=False,
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_uploads_discarded_terminal
        BEFORE UPDATE OF upload_status ON reimbursement_uploads
        WHEN OLD.upload_status = 'DISCARDED' AND NEW.upload_status IS NOT 'DISCARDED'
        BEGIN
            SELECT RAISE(ABORT, 'discarded upload is terminal');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_uploads_linked_at_immutable
        BEFORE UPDATE OF linked_at ON reimbursement_uploads
        WHEN OLD.linked_at IS NOT NULL AND NEW.linked_at IS NOT OLD.linked_at
        BEGIN
            SELECT RAISE(ABORT, 'linked_at is immutable once set');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_uploads_cleaned_at_immutable
        BEFORE UPDATE OF cleaned_at ON reimbursement_uploads
        WHEN OLD.cleaned_at IS NOT NULL AND NEW.cleaned_at IS NOT OLD.cleaned_at
        BEGIN
            SELECT RAISE(ABORT, 'cleaned_at is immutable once set');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_uploads_local_deleted_at_immutable
        BEFORE UPDATE OF local_deleted_at ON reimbursement_uploads
        WHEN OLD.local_deleted_at IS NOT NULL
             AND NEW.local_deleted_at IS NOT OLD.local_deleted_at
        BEGIN
            SELECT RAISE(ABORT, 'local_deleted_at is immutable once set');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_uploads_remote_identity_immutable
        BEFORE UPDATE OF space_id, file_id ON reimbursement_uploads
        WHEN OLD.file_id IS NOT NULL
             AND (NEW.file_id IS NOT OLD.file_id OR NEW.space_id IS NOT OLD.space_id)
        BEGIN
            SELECT RAISE(ABORT, 'remote file identity is immutable once set');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_uploads_parent_immutable
        BEFORE UPDATE OF submission_id, draft_id ON reimbursement_uploads
        WHEN NEW.submission_id IS NOT OLD.submission_id
             OR NEW.draft_id IS NOT OLD.draft_id
        BEGIN
            SELECT RAISE(ABORT, 'upload parent is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_submissions_submitted_manifest_insert
        BEFORE INSERT ON reimbursement_submissions
        WHEN NEW.status = 'SUBMITTED'
             AND (
                 NOT EXISTS (
                     SELECT 1
                     FROM reimbursement_uploads AS upload
                     WHERE upload.submission_id = NEW.id
                       AND upload.draft_id = NEW.draft_id
                       AND upload.role = 'GENERATED_EXCEL'
                       AND upload.upload_status = 'LINKED'
                 )
                 OR EXISTS (
                     SELECT 1
                     FROM reimbursement_uploads AS upload
                     WHERE upload.submission_id = NEW.id
                       AND upload.draft_id = NEW.draft_id
                       AND upload.upload_status IS NOT 'LINKED'
                 )
             )
        BEGIN
            SELECT RAISE(ABORT, 'submitted reimbursement requires a fully linked manifest');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_submissions_submitted_manifest_update
        BEFORE UPDATE ON reimbursement_submissions
        WHEN NEW.status = 'SUBMITTED'
             AND (
                 NOT EXISTS (
                     SELECT 1
                     FROM reimbursement_uploads AS upload
                     WHERE upload.submission_id = NEW.id
                       AND upload.draft_id = NEW.draft_id
                       AND upload.role = 'GENERATED_EXCEL'
                       AND upload.upload_status = 'LINKED'
                 )
                 OR EXISTS (
                     SELECT 1
                     FROM reimbursement_uploads AS upload
                     WHERE upload.submission_id = NEW.id
                       AND upload.draft_id = NEW.draft_id
                       AND upload.upload_status IS NOT 'LINKED'
                 )
             )
        BEGIN
            SELECT RAISE(ABORT, 'submitted reimbursement requires a fully linked manifest');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_uploads_submitted_parent_insert
        BEFORE INSERT ON reimbursement_uploads
        WHEN EXISTS (
                 SELECT 1
                 FROM reimbursement_submissions AS submission
                 WHERE submission.id = NEW.submission_id
                   AND submission.draft_id = NEW.draft_id
                   AND submission.status = 'SUBMITTED'
             )
        BEGIN
            SELECT RAISE(ABORT, 'submitted reimbursement cannot accept new uploads');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_uploads_submitted_parent_delete
        BEFORE DELETE ON reimbursement_uploads
        WHEN EXISTS (
                 SELECT 1
                 FROM reimbursement_submissions AS submission
                 WHERE submission.id = OLD.submission_id
                   AND submission.draft_id = OLD.draft_id
                   AND submission.status = 'SUBMITTED'
             )
        BEGIN
            SELECT RAISE(ABORT, 'submitted reimbursement cannot lose uploads');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_uploads_submitted_manifest_update
        BEFORE UPDATE ON reimbursement_uploads
        WHEN EXISTS (
                 SELECT 1
                 FROM reimbursement_submissions AS submission
                 WHERE submission.id = OLD.submission_id
                   AND submission.draft_id = OLD.draft_id
                   AND submission.status = 'SUBMITTED'
             )
             AND (
                 NEW.submission_id IS NOT OLD.submission_id
                 OR NEW.draft_id IS NOT OLD.draft_id
                 OR NEW.role IS NOT OLD.role
                 OR NEW.source_draft_file_id IS NOT OLD.source_draft_file_id
                 OR NEW.sort_order IS NOT OLD.sort_order
                 OR NEW.local_storage_key IS NOT OLD.local_storage_key
                 OR NEW.reserved_bytes IS NOT OLD.reserved_bytes
                 OR NEW.file_name IS NOT OLD.file_name
                 OR NEW.file_type IS NOT OLD.file_type
                 OR NEW.media_type IS NOT OLD.media_type
                 OR NEW.size_bytes IS NOT OLD.size_bytes
                 OR NEW.sha256 IS NOT OLD.sha256
                 OR NEW.upload_status IS NOT OLD.upload_status
                 OR NEW.space_id IS NOT OLD.space_id
                 OR NEW.file_id IS NOT OLD.file_id
                 OR NEW.linked_at IS NOT OLD.linked_at
             )
        BEGIN
            SELECT RAISE(ABORT, 'submitted reimbursement upload manifest is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_uploads_linked_parent_insert
        BEFORE INSERT ON reimbursement_uploads
        WHEN NEW.upload_status = 'LINKED'
             AND NOT EXISTS (
                 SELECT 1
                 FROM reimbursement_submissions AS submission
                 WHERE submission.id = NEW.submission_id
                   AND submission.draft_id = NEW.draft_id
                   AND submission.process_instance_id IS NOT NULL
                   AND (
                       submission.status IN ('VERIFYING', 'SUBMITTED', 'MANUAL_REVIEW')
                       OR (
                           submission.status = 'FAILED_RETRYABLE'
                           AND submission.resume_status = 'VERIFYING'
                       )
                   )
             )
        BEGIN
            SELECT RAISE(ABORT, 'linked upload requires a confirmed OA instance');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_uploads_linked_parent_update
        BEFORE UPDATE ON reimbursement_uploads
        WHEN NEW.upload_status = 'LINKED'
             AND NOT EXISTS (
                 SELECT 1
                 FROM reimbursement_submissions AS submission
                 WHERE submission.id = NEW.submission_id
                   AND submission.draft_id = NEW.draft_id
                   AND submission.process_instance_id IS NOT NULL
                   AND (
                       submission.status IN ('VERIFYING', 'SUBMITTED', 'MANUAL_REVIEW')
                       OR (
                           submission.status = 'FAILED_RETRYABLE'
                           AND submission.resume_status = 'VERIFYING'
                       )
                   )
             )
        BEGIN
            SELECT RAISE(ABORT, 'linked upload requires a confirmed OA instance');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_uploads_cleanup_parent_insert
        BEFORE INSERT ON reimbursement_uploads
        WHEN NEW.upload_status IN ('CLEANUP_PENDING', 'CLEANED')
             AND (
                 NOT EXISTS (
                     SELECT 1
                     FROM reimbursement_submissions AS submission
                     WHERE submission.id = NEW.submission_id
                       AND submission.draft_id = NEW.draft_id
                       AND submission.status = 'ORPHAN_CLEANUP'
                       AND submission.process_instance_id IS NULL
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
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_uploads_cleanup_parent_update
        BEFORE UPDATE ON reimbursement_uploads
        WHEN NEW.upload_status IN ('CLEANUP_PENDING', 'CLEANED')
             AND (
                 NOT EXISTS (
                     SELECT 1
                     FROM reimbursement_submissions AS submission
                     WHERE submission.id = NEW.submission_id
                       AND submission.draft_id = NEW.draft_id
                       AND submission.status = 'ORPHAN_CLEANUP'
                       AND submission.process_instance_id IS NULL
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
    )
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_submissions_cleanup_guard_insert
        BEFORE INSERT ON reimbursement_submissions
        WHEN EXISTS (
                 SELECT 1
                 FROM reimbursement_uploads AS upload
                 WHERE upload.submission_id = NEW.id
                   AND upload.upload_status IN ('CLEANUP_PENDING', 'CLEANED')
             )
             AND (
                 NEW.status IS NOT 'ORPHAN_CLEANUP'
                 OR NEW.process_instance_id IS NOT NULL
             )
        BEGIN
            SELECT RAISE(ABORT, 'OA instance and remote cleanup cannot coexist');
        END
        """
    )
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
    op.execute(
        """
        CREATE TRIGGER trg_reimbursement_submissions_linked_guard_update
        BEFORE UPDATE ON reimbursement_submissions
        WHEN EXISTS (
                 SELECT 1
                 FROM reimbursement_uploads AS upload
                 WHERE upload.submission_id = OLD.id
                   AND upload.upload_status = 'LINKED'
             )
             AND (
                 NEW.id IS NOT OLD.id
                 OR NEW.process_instance_id IS NULL
                 OR NOT (
                     NEW.status IN ('VERIFYING', 'SUBMITTED', 'MANUAL_REVIEW')
                     OR (
                         NEW.status = 'FAILED_RETRYABLE'
                         AND NEW.resume_status = 'VERIFYING'
                     )
                 )
             )
        BEGIN
            SELECT RAISE(ABORT, 'linked upload requires a confirmed OA instance');
        END
        """
    )
    op.create_index(
        "ix_reimbursement_uploads_submission_status",
        "reimbursement_uploads",
        ["submission_id", "upload_status"],
        unique=False,
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_uploads_submitted_manifest_update")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_uploads_submitted_parent_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_uploads_submitted_parent_insert")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_submissions_submitted_manifest_update")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_submissions_submitted_manifest_insert")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_submissions_linked_guard_update")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_submissions_cleanup_guard_update")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_submissions_cleanup_guard_insert")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_uploads_cleanup_parent_update")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_uploads_cleanup_parent_insert")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_uploads_linked_parent_update")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_uploads_linked_parent_insert")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_uploads_parent_immutable")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_uploads_discarded_terminal")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_uploads_remote_identity_immutable")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_uploads_local_deleted_at_immutable")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_uploads_cleaned_at_immutable")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_uploads_linked_at_immutable")
    op.drop_index(
        "ix_reimbursement_uploads_submission_status",
        table_name="reimbursement_uploads",
    )
    op.drop_index(
        "uq_reimbursement_uploads_generated_excel",
        table_name="reimbursement_uploads",
    )
    op.drop_index(
        "ix_reimbursement_uploads_submission_id",
        table_name="reimbursement_uploads",
    )
    op.drop_table("reimbursement_uploads")

    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_submissions_submitted_terminal")
    op.execute("DROP TRIGGER IF EXISTS trg_reimbursement_submissions_process_instance_immutable")
    op.drop_index(
        "ix_reimbursement_submissions_owner_updated",
        table_name="reimbursement_submissions",
    )
    op.drop_index(
        "ix_reimbursement_submissions_due",
        table_name="reimbursement_submissions",
    )
    op.drop_table("reimbursement_submissions")

    op.drop_index(
        "ix_reimbursement_draft_files_draft_status",
        table_name="reimbursement_draft_files",
    )
    op.drop_index(
        "ix_reimbursement_draft_files_draft_id",
        table_name="reimbursement_draft_files",
    )
    op.drop_table("reimbursement_draft_files")

    op.drop_index(
        "ix_reimbursement_drafts_status_expires",
        table_name="reimbursement_drafts",
    )
    op.drop_index(
        "ix_reimbursement_drafts_owner_updated",
        table_name="reimbursement_drafts",
    )
    op.drop_table("reimbursement_drafts")
