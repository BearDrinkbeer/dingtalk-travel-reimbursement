from __future__ import annotations

import base64
import gzip
import hashlib
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import create_database_engine
from app.models.reimbursement import (
    ReimbursementDraft,
    ReimbursementDraftFile,
    ReimbursementDraftFileRole,
    ReimbursementDraftFileStatus,
    ReimbursementDraftStatus,
    ReimbursementSubmission,
    ReimbursementSubmissionStatus,
    ReimbursementUpload,
    ReimbursementUploadLocalStatus,
    ReimbursementUploadRole,
    ReimbursementUploadStatus,
    utc_now,
)

EXPECTED_COLUMNS = {
    "reimbursement_drafts": {
        "id",
        "corp_id",
        "owner_user_id",
        "status",
        "revision",
        "department_id",
        "department_name",
        "template_process_code",
        "template_config_version",
        "schema_fingerprint",
        "input_json",
        "related_instance_ids_json",
        "expires_at",
        "locked_at",
        "created_at",
        "updated_at",
    },
    "reimbursement_draft_files": {
        "id",
        "draft_id",
        "sort_order",
        "processing_role",
        "file_status",
        "storage_key",
        "part_storage_key",
        "reserved_bytes",
        "reservation_expires_at",
        "original_name",
        "extension",
        "media_type",
        "size_bytes",
        "sha256",
        "ocr_status",
        "ocr_result_json",
        "purged_at",
        "created_at",
        "updated_at",
    },
    "reimbursement_submissions": {
        "id",
        "draft_id",
        "corp_id",
        "originator_user_id",
        "originator_union_id",
        "originator_name",
        "department_id",
        "department_name",
        "template_process_code",
        "template_config_version",
        "schema_fingerprint",
        "form_snapshot_json",
        "related_instance_ids_json",
        "snapshot_sha256",
        "idempotency_key_hash",
        "status",
        "resume_status",
        "status_version",
        "attempt_count",
        "reconciliation_attempt_count",
        "next_attempt_at",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "oa_create_started_at",
        "oa_request_hash",
        "reconciliation_deadline_at",
        "orphan_confirmed_at",
        "orphan_confirmation_code",
        "orphan_confirmed_by_user_id",
        "process_instance_id",
        "business_id",
        "approval_url",
        "last_error_code",
        "last_error_message",
        "submitted_at",
        "created_at",
        "updated_at",
    },
    "reimbursement_uploads": {
        "id",
        "submission_id",
        "draft_id",
        "source_draft_file_id",
        "role",
        "sort_order",
        "local_storage_key",
        "local_part_storage_key",
        "local_status",
        "reserved_bytes",
        "reservation_expires_at",
        "file_name",
        "file_type",
        "media_type",
        "size_bytes",
        "sha256",
        "upload_status",
        "status_version",
        "attempt_count",
        "space_id",
        "file_id",
        "put_started_at",
        "commit_started_at",
        "cleanup_started_at",
        "linked_at",
        "cleaned_at",
        "local_deleted_at",
        "last_error_code",
        "created_at",
        "updated_at",
    },
}

RELATED_APPROVAL_COLUMNS = {
    "id",
    "draft_id",
    "corp_id",
    "owner_user_id",
    "sort_order",
    "process_instance_id",
    "travel_profile_key",
    "process_code",
    "catalog_config_version",
    "travel_schema_fingerprint",
    "listed_from_ms",
    "listed_to_ms",
    "travel_start_date",
    "travel_end_date",
    "title",
    "business_id",
    "instance_created_at",
    "verified_at",
    "created_at",
    "updated_at",
}

# Frozen from commit bd4ba4a after applying the unmodified migration chain through 0010.
# Keeping the SQLite artifact independent of today's migration modules is intentional: it
# exercises the same upgrade surface as an already-deployed database.
_PUBLISHED_0010_FIXTURE = (
    Path(__file__).parent / "fixtures" / "published_20260904_0010.sqlite3.gz.b64"
)
_PUBLISHED_0010_DATABASE_SHA256 = (
    "7b55e6e60154bdfd5725d4016dffa3ebeb520cb0a5d84775c04d85575f30b138"
)
_SUBMITTED_CHECK_SQL = (
    "status != 'SUBMITTED' OR "
    "(submitted_at IS NOT NULL AND business_id IS NOT NULL AND approval_url IS NOT NULL)"
)
_INSTANCE_REQUIRED_CHECK_SQL = (
    "status NOT IN ('VERIFYING', 'SUBMITTED') OR process_instance_id IS NOT NULL"
)


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    return config


def table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def restore_published_0010_database(database_path: Path) -> None:
    compressed = base64.b64decode(_PUBLISHED_0010_FIXTURE.read_text(encoding="ascii"))
    database_bytes = gzip.decompress(compressed)
    assert hashlib.sha256(database_bytes).hexdigest() == _PUBLISHED_0010_DATABASE_SHA256
    database_path.write_bytes(database_bytes)


def migrate_database_to_head(database_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    get_settings.cache_clear()
    command.upgrade(alembic_config(), "head")


def normalized_submission_table_sql(database_path: Path) -> str:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'reimbursement_submissions'"
        ).fetchone()
    assert row is not None
    return " ".join(str(row[0]).split())


def seed_verifying_submission_with_linked_excel(
    connection: sqlite3.Connection,
    *,
    suffix: str,
) -> str:
    submission_id = f"submission-{suffix}"
    draft_id = f"draft-{suffix}"
    now = "2026-09-04 12:00:00"
    connection.execute(
        """
        INSERT INTO reimbursement_submissions (
            id, draft_id, corp_id, originator_user_id, originator_union_id,
            originator_name, department_id, department_name, template_process_code,
            template_config_version, schema_fingerprint, form_snapshot_json,
            related_instance_ids_json, snapshot_sha256, idempotency_key_hash,
            status, resume_status, status_version, attempt_count,
            reconciliation_attempt_count, oa_create_started_at, oa_request_json,
            oa_request_hash, process_instance_id, created_at, updated_at
        ) VALUES (
            ?, ?, 'corp-test', 'employee-1', 'union-1',
            '测试员工', 'department-1', '测试部门', 'PROC-REIMBURSEMENT',
            1, ?, '{}', '[]', ?, ?,
            'VERIFYING', NULL, 1, 1,
            0, ?, '{}', ?, ?, ?, ?
        )
        """,
        (
            submission_id,
            draft_id,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            now,
            "d" * 64,
            f"process-{suffix}",
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO reimbursement_uploads (
            id, submission_id, draft_id, source_draft_file_id, role, sort_order,
            local_storage_key, local_part_storage_key, local_status, reserved_bytes,
            reservation_expires_at, file_name, file_type, media_type, size_bytes,
            sha256, upload_status, status_version, attempt_count, space_id, file_id,
            linked_at, created_at, updated_at
        ) VALUES (
            ?, ?, ?, NULL, 'GENERATED_EXCEL', 0,
            ?, NULL, 'READY', 1,
            NULL, '报销单.xlsx', 'xlsx',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 1,
            ?, 'LINKED', 1, 1, ?, ?,
            ?, ?, ?
        )
        """,
        (
            f"upload-{suffix}",
            submission_id,
            draft_id,
            f"generated/{submission_id}/final.xlsx",
            "e" * 64,
            f"space-{suffix}",
            f"file-{suffix}",
            now,
            now,
            now,
        ),
    )
    connection.commit()
    return submission_id


def assert_submitted_field_contract(database_path: Path) -> None:
    fields = ("submitted_at", "process_instance_id", "business_id", "approval_url")
    with sqlite3.connect(database_path) as connection:
        for index, missing_field in enumerate(fields):
            submission_id = seed_verifying_submission_with_linked_excel(
                connection,
                suffix=f"missing-{index}",
            )
            values = {
                "status": "SUBMITTED",
                "submitted_at": "2026-09-04 12:05:00",
                "process_instance_id": f"process-missing-{index}",
                "business_id": f"business-missing-{index}",
                "approval_url": f"dingtalk://approval/missing-{index}",
                "submission_id": submission_id,
            }
            values[missing_field] = None
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE reimbursement_submissions
                    SET status = :status,
                        submitted_at = :submitted_at,
                        process_instance_id = :process_instance_id,
                        business_id = :business_id,
                        approval_url = :approval_url
                    WHERE id = :submission_id
                    """,
                    values,
                )
            connection.rollback()

        valid_submission_id = seed_verifying_submission_with_linked_excel(
            connection,
            suffix="complete",
        )
        connection.execute(
            """
            UPDATE reimbursement_submissions
            SET status = 'SUBMITTED',
                submitted_at = '2026-09-04 12:05:00',
                business_id = 'business-complete',
                approval_url = 'dingtalk://approval/complete'
            WHERE id = ?
            """,
            (valid_submission_id,),
        )
        connection.commit()
        assert connection.execute(
            "SELECT status FROM reimbursement_submissions WHERE id = ?",
            (valid_submission_id,),
        ).fetchone() == ("SUBMITTED",)


def assert_failed_final_accepts_safe_local_only_remainders(database_path: Path) -> None:
    now = "2026-09-04 12:00:00"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO reimbursement_submissions (
                id, draft_id, corp_id, originator_user_id, originator_union_id,
                originator_name, department_id, department_name, template_process_code,
                template_config_version, schema_fingerprint, form_snapshot_json,
                related_instance_ids_json, snapshot_sha256, idempotency_key_hash,
                status, resume_status, status_version, attempt_count,
                reconciliation_attempt_count, orphan_confirmed_at,
                orphan_confirmation_code, created_at, updated_at
            ) VALUES (
                'submission-orphan', 'draft-orphan', 'corp-test', 'employee-1', 'union-1',
                '测试员工', 'department-1', '测试部门', 'PROC-REIMBURSEMENT',
                1, ?, '{}', '[]', ?, ?,
                'ORPHAN_CLEANUP', NULL, 1, 1,
                0, ?, 'OA_CREATE_REJECTED', ?, ?
            )
            """,
            ("a" * 64, "b" * 64, "c" * 64, now, now, now),
        )
        connection.execute(
            """
            INSERT INTO reimbursement_uploads (
                id, submission_id, draft_id, source_draft_file_id, role, sort_order,
                local_storage_key, local_part_storage_key, local_status, reserved_bytes,
                reservation_expires_at, file_name, file_type, media_type, size_bytes,
                sha256, upload_status, status_version, attempt_count, space_id, file_id,
                cleanup_started_at, cleaned_at, created_at, updated_at
            ) VALUES (
                'upload-cleaned', 'submission-orphan', 'draft-orphan', NULL,
                'GENERATED_EXCEL', 0, 'generated/submission-orphan/final.xlsx', NULL,
                'READY', 1, NULL, '报销单.xlsx', 'xlsx',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 1,
                ?, 'CLEANED', 1, 1, 'space-cleaned', 'file-cleaned', ?, ?, ?, ?
            )
            """,
            ("d" * 64, now, now, now, now),
        )
        connection.execute(
            """
            INSERT INTO reimbursement_uploads (
                id, submission_id, draft_id, source_draft_file_id, role, sort_order,
                local_storage_key, local_part_storage_key, local_status, reserved_bytes,
                reservation_expires_at, file_name, file_type, media_type, size_bytes,
                sha256, upload_status, status_version, attempt_count, created_at, updated_at
            ) VALUES (
                'upload-local-only', 'submission-orphan', 'draft-orphan', 'source-local-only',
                'ORIGINAL', 1, 'drafts/draft-orphan/original.pdf', NULL,
                'READY', 1, NULL, '原始发票.pdf', 'pdf', 'application/pdf', 1,
                ?, 'PENDING', 1, 0, ?, ?
            )
            """,
            ("e" * 64, now, now),
        )
        connection.commit()

        connection.execute(
            """
            UPDATE reimbursement_submissions
            SET status = 'FAILED_FINAL', updated_at = ?
            WHERE id = 'submission-orphan'
            """,
            (now,),
        )
        connection.commit()
        assert connection.execute(
            "SELECT status FROM reimbursement_submissions WHERE id = 'submission-orphan'"
        ).fetchone() == ("FAILED_FINAL",)


def test_published_0010_upgrade_and_fresh_head_share_submitted_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    upgraded_path = tmp_path / "published-0010-upgraded.db"
    fresh_path = tmp_path / "fresh-head.db"

    restore_published_0010_database(upgraded_path)
    with sqlite3.connect(upgraded_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "20260904_0010",
        )

    migrate_database_to_head(upgraded_path, monkeypatch)
    migrate_database_to_head(fresh_path, monkeypatch)

    try:
        upgraded_sql = normalized_submission_table_sql(upgraded_path)
        fresh_sql = normalized_submission_table_sql(fresh_path)
        assert upgraded_sql == fresh_sql
        assert f"CHECK ({_SUBMITTED_CHECK_SQL})" in fresh_sql
        assert f"CHECK ({_INSTANCE_REQUIRED_CHECK_SQL})" in fresh_sql

        assert_submitted_field_contract(upgraded_path)
        assert_submitted_field_contract(fresh_path)
        assert_failed_final_accepts_safe_local_only_remainders(upgraded_path)
        assert_failed_final_accepts_safe_local_only_remainders(fresh_path)
    finally:
        get_settings.cache_clear()


def test_reimbursement_migration_upgrade_downgrade_and_reupgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "reimbursement-migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    get_settings.cache_clear()
    config = alembic_config()
    try:
        command.upgrade(config, "20260904_0008")
        with sqlite3.connect(database_path) as connection:
            assert not set(EXPECTED_COLUMNS).intersection(table_names(connection))

        command.upgrade(config, "20260904_0009")
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "20260904_0009",
            )
            for table, expected in EXPECTED_COLUMNS.items():
                actual = {
                    str(row[1])
                    for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
                }
                assert actual == expected

            draft_file_foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(reimbursement_draft_files)"
            ).fetchall()
            upload_foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(reimbursement_uploads)"
            ).fetchall()
            assert any(
                row[2] == "reimbursement_drafts" and row[6] == "CASCADE"
                for row in draft_file_foreign_keys
            )
            assert {row[2] for row in upload_foreign_keys} == {
                "reimbursement_draft_files",
                "reimbursement_submissions",
            }
            submission_foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(reimbursement_submissions)"
            ).fetchall()
            assert {(row[2], row[3], row[4]) for row in submission_foreign_keys} == {
                ("reimbursement_drafts", "draft_id", "id"),
                ("reimbursement_drafts", "corp_id", "corp_id"),
                ("reimbursement_drafts", "originator_user_id", "owner_user_id"),
            }
            assert {(row[2], row[3], row[4]) for row in upload_foreign_keys} == {
                ("reimbursement_submissions", "submission_id", "id"),
                ("reimbursement_submissions", "draft_id", "draft_id"),
                ("reimbursement_draft_files", "source_draft_file_id", "id"),
                ("reimbursement_draft_files", "draft_id", "draft_id"),
            }

            submission_sql = str(
                connection.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'reimbursement_submissions'"
                ).fetchone()[0]
            )
            draft_file_sql = str(
                connection.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'reimbursement_draft_files'"
                ).fetchone()[0]
            )
            upload_sql = str(
                connection.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'reimbursement_uploads'"
                ).fetchone()[0]
            )
            assert "ck_reimbursement_submissions_status" in submission_sql
            assert "ck_reimbursement_submissions_resume_status_pair" in submission_sql
            assert "uq_reimbursement_submissions_owner_draft_idempotency" in submission_sql
            assert "fk_reimbursement_submissions_draft_owner" in submission_sql
            assert "ck_reimbursement_uploads_source_role" in upload_sql
            assert "uq_reimbursement_uploads_remote_file" in upload_sql
            assert "fk_reimbursement_uploads_submission_draft" in upload_sql
            assert "fk_reimbursement_uploads_source_draft" in upload_sql
            assert "ck_reimbursement_draft_files_size_within_reservation" in draft_file_sql
            assert "ck_reimbursement_uploads_size_within_reservation" in upload_sql
            assert "ck_reimbursement_uploads_discarded_terminal_shape" in upload_sql

            generated_excel_index_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' "
                "AND name = 'uq_reimbursement_uploads_generated_excel'"
            ).fetchone()
            assert generated_excel_index_sql is not None
            assert "WHERE role = 'GENERATED_EXCEL'" in str(generated_excel_index_sql[0])
            trigger_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                ).fetchall()
            }
            assert {
                "trg_reimbursement_submissions_process_instance_immutable",
                "trg_reimbursement_submissions_submitted_terminal",
                "trg_reimbursement_uploads_discarded_terminal",
                "trg_reimbursement_uploads_linked_at_immutable",
                "trg_reimbursement_uploads_cleaned_at_immutable",
                "trg_reimbursement_uploads_local_deleted_at_immutable",
                "trg_reimbursement_uploads_remote_identity_immutable",
                "trg_reimbursement_uploads_parent_immutable",
                "trg_reimbursement_uploads_linked_parent_insert",
                "trg_reimbursement_uploads_linked_parent_update",
                "trg_reimbursement_uploads_cleanup_parent_insert",
                "trg_reimbursement_uploads_cleanup_parent_update",
                "trg_reimbursement_submissions_cleanup_guard_insert",
                "trg_reimbursement_submissions_cleanup_guard_update",
                "trg_reimbursement_submissions_linked_guard_update",
                "trg_reimbursement_submissions_submitted_manifest_insert",
                "trg_reimbursement_submissions_submitted_manifest_update",
                "trg_reimbursement_uploads_submitted_parent_insert",
                "trg_reimbursement_uploads_submitted_parent_delete",
                "trg_reimbursement_uploads_submitted_manifest_update",
            }.issubset(trigger_names)

        command.downgrade(config, "20260904_0008")
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "20260904_0008",
            )
            assert not set(EXPECTED_COLUMNS).intersection(table_names(connection))
            assert "sessions" in table_names(connection)

        command.upgrade(config, "head")
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "20260906_0012",
            )
            assert set(EXPECTED_COLUMNS).issubset(table_names(connection))
    finally:
        get_settings.cache_clear()


def test_related_approval_catalog_migration_upgrade_downgrade_and_reupgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "related-approval-catalog-migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    get_settings.cache_clear()
    config = alembic_config()
    try:
        command.upgrade(config, "20260904_0009")
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                INSERT INTO oa_template_profiles (
                    profile_key, process_code, template_name, schema_fingerprint,
                    confirmed_schema_fingerprint, schema_json, mapping_json, config_version,
                    allowed_travel_process_codes_json,
                    related_approval_smoke_test_confirmed, compatibility_status,
                    confirmed_by_user_id, last_checked_at, confirmed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "reimbursement",
                    "PROC-REIMBURSEMENT",
                    "差旅费报销",
                    "a" * 64,
                    "a" * 64,
                    "{}",
                    "{}",
                    1,
                    "[]",
                    1,
                    "COMPATIBLE",
                    "admin-1",
                    "2026-09-04 01:00:00",
                    "2026-09-04 01:00:00",
                    "2026-09-04 01:00:00",
                    "2026-09-04 01:00:00",
                ),
            )
            connection.commit()
            assert "travel_profiles_json" not in {
                row[1]
                for row in connection.execute("PRAGMA table_info(oa_template_profiles)").fetchall()
            }
            assert "reimbursement_draft_related_approvals" not in table_names(connection)

        command.upgrade(config, "20260904_0010")
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "20260904_0010",
            )
            assert connection.execute(
                "SELECT travel_profiles_json FROM oa_template_profiles WHERE profile_key = ?",
                ("reimbursement",),
            ).fetchone() == ("[]",)
            assert {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(reimbursement_draft_related_approvals)"
                ).fetchall()
            } == RELATED_APPROVAL_COLUMNS
            foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(reimbursement_draft_related_approvals)"
            ).fetchall()
            assert {(row[2], row[3], row[4], row[6]) for row in foreign_keys} == {
                ("reimbursement_drafts", "draft_id", "id", "CASCADE"),
                ("reimbursement_drafts", "corp_id", "corp_id", "CASCADE"),
                ("reimbursement_drafts", "owner_user_id", "owner_user_id", "CASCADE"),
            }
            table_sql = str(
                connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'reimbursement_draft_related_approvals'"
                ).fetchone()[0]
            )
            assert "fk_reimbursement_draft_related_approvals_draft_owner" in table_sql
            assert "uq_reimbursement_draft_related_approvals_draft_instance" in table_sql
            assert "uq_reimbursement_draft_related_approvals_draft_sort_order" in table_sql
            assert "ck_reimbursement_draft_related_approvals_listing_window" in table_sql
            assert "ck_reimbursement_draft_related_approvals_travel_dates" in table_sql
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'index' "
                "AND name = 'ix_reimbursement_draft_related_approvals_owner_instance'"
            ).fetchone() == (1,)

        command.downgrade(config, "20260904_0009")
        with sqlite3.connect(database_path) as connection:
            assert "reimbursement_draft_related_approvals" not in table_names(connection)
            assert "travel_profiles_json" not in {
                row[1]
                for row in connection.execute("PRAGMA table_info(oa_template_profiles)").fetchall()
            }

        command.upgrade(config, "head")
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "20260906_0012",
            )
            assert "reimbursement_draft_related_approvals" in table_names(connection)
    finally:
        get_settings.cache_clear()


def test_submission_snapshot_migration_upgrade_downgrade_and_reupgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "submission-snapshot-migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    get_settings.cache_clear()
    config = alembic_config()
    try:
        command.upgrade(config, "20260904_0010")
        with sqlite3.connect(database_path) as connection:
            before = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(reimbursement_submissions)"
                ).fetchall()
            }
            assert "snapshot_version" not in before
            assert "oa_request_json" not in before

        command.upgrade(config, "20260904_0011")
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "20260904_0011",
            )
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(reimbursement_submissions)"
                ).fetchall()
            }
            assert {"snapshot_version", "oa_request_json"} <= columns
            trigger_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                ).fetchall()
            }
            assert {
                "trg_reimbursement_submissions_snapshot_immutable",
                "trg_reimbursement_submissions_oa_request_immutable",
                "trg_reimbursement_submissions_cleanup_guard_update",
                "trg_reimbursement_uploads_cleanup_parent_insert",
                "trg_reimbursement_uploads_cleanup_parent_update",
            } <= trigger_names

        command.downgrade(config, "20260904_0010")
        with sqlite3.connect(database_path) as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(reimbursement_submissions)"
                ).fetchall()
            }
            assert "snapshot_version" not in columns
            assert "oa_request_json" not in columns
            trigger_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                ).fetchall()
            }
            assert "trg_reimbursement_submissions_snapshot_immutable" not in trigger_names
            assert "trg_reimbursement_submissions_oa_request_immutable" not in trigger_names
            assert "trg_reimbursement_submissions_cleanup_guard_update" in trigger_names

        command.upgrade(config, "20260904_0011")
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "20260904_0011",
            )
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("status", "resume_status", "is_valid"),
    [
        (
            ReimbursementSubmissionStatus.FAILED_RETRYABLE.value,
            ReimbursementSubmissionStatus.VALIDATING.value,
            True,
        ),
        (ReimbursementSubmissionStatus.FAILED_RETRYABLE.value, None, False),
        (
            ReimbursementSubmissionStatus.QUEUED.value,
            ReimbursementSubmissionStatus.VALIDATING.value,
            False,
        ),
    ],
)
def test_migrated_schema_enforces_resume_status_pair(
    tmp_path: Path,
    monkeypatch,
    status: str,
    resume_status: str | None,
    is_valid: bool,
) -> None:
    database_path = tmp_path / "reimbursement-resume-status.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.upgrade(alembic_config(), "head")
    engine = create_database_engine(database_url)
    try:
        with Session(engine) as database:
            draft = ReimbursementDraft(
                corp_id="corp-test",
                owner_user_id="employee-1",
                status=ReimbursementDraftStatus.LOCKED.value,
                revision=1,
                department_id="department-1",
                department_name="测试部门",
                template_process_code="PROC-REIMBURSEMENT",
                template_config_version=1,
                schema_fingerprint="a" * 64,
                input_json="{}",
                related_instance_ids_json="[]",
                expires_at=utc_now() + timedelta(days=30),
                locked_at=utc_now(),
            )
            database.add(draft)
            database.flush()
            submission = ReimbursementSubmission(
                draft_id=draft.id,
                corp_id=draft.corp_id,
                originator_user_id=draft.owner_user_id,
                originator_union_id="union-1",
                originator_name="测试员工",
                department_id=draft.department_id,
                department_name=draft.department_name,
                template_process_code=draft.template_process_code,
                template_config_version=draft.template_config_version,
                schema_fingerprint=draft.schema_fingerprint,
                form_snapshot_json="{}",
                related_instance_ids_json="[]",
                snapshot_sha256="b" * 64,
                idempotency_key_hash="c" * 64,
                status=status,
                resume_status=resume_status,
            )
            database.add(submission)
            if is_valid:
                database.commit()
            else:
                with pytest.raises(IntegrityError):
                    database.commit()
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_migrated_schema_preserves_irreversible_remote_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "reimbursement-history.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = alembic_config()
    command.upgrade(config, "head")
    engine = create_database_engine(database_url)
    try:
        now = utc_now()
        with Session(engine) as database:
            draft = ReimbursementDraft(
                corp_id="corp-test",
                owner_user_id="employee-1",
                status=ReimbursementDraftStatus.LOCKED.value,
                revision=1,
                department_id="department-1",
                department_name="测试部门",
                template_process_code="PROC-REIMBURSEMENT",
                template_config_version=1,
                schema_fingerprint="a" * 64,
                input_json="{}",
                related_instance_ids_json="[]",
                expires_at=now + timedelta(days=30),
                locked_at=now,
            )
            database.add(draft)
            database.flush()
            sources = [
                ReimbursementDraftFile(
                    draft_id=draft.id,
                    sort_order=index,
                    processing_role=ReimbursementDraftFileRole.ATTACHMENT_ONLY.value,
                    file_status=ReimbursementDraftFileStatus.ACTIVE.value,
                    storage_key=f"drafts/{draft.id}/source-{index}.pdf",
                    part_storage_key=None,
                    reserved_bytes=10,
                    reservation_expires_at=None,
                    original_name=f"source-{index}.pdf",
                    extension="pdf",
                    media_type="application/pdf",
                    size_bytes=10,
                    sha256=chr(ord("b") + index) * 64,
                )
                for index in range(3)
            ]
            database.add_all(sources)
            database.flush()
            submission = ReimbursementSubmission(
                draft_id=draft.id,
                corp_id=draft.corp_id,
                originator_user_id=draft.owner_user_id,
                originator_union_id="union-1",
                originator_name="测试员工",
                department_id=draft.department_id,
                department_name=draft.department_name,
                template_process_code=draft.template_process_code,
                template_config_version=draft.template_config_version,
                schema_fingerprint=draft.schema_fingerprint,
                form_snapshot_json="{}",
                related_instance_ids_json="[]",
                snapshot_sha256="d" * 64,
                idempotency_key_hash="e" * 64,
                status=ReimbursementSubmissionStatus.ORPHAN_CLEANUP.value,
                orphan_confirmed_at=now,
                orphan_confirmation_code="CREATE_REJECTED",
            )
            database.add(submission)
            database.flush()
            committed = ReimbursementUpload(
                submission_id=submission.id,
                draft_id=draft.id,
                source_draft_file_id=None,
                role=ReimbursementUploadRole.GENERATED_EXCEL.value,
                sort_order=0,
                local_storage_key=f"generated/{submission.id}/final.xlsx",
                local_part_storage_key=None,
                local_status=ReimbursementUploadLocalStatus.READY.value,
                reserved_bytes=10,
                reservation_expires_at=None,
                file_name="报销单.xlsx",
                file_type="xlsx",
                media_type="application/octet-stream",
                size_bytes=10,
                sha256="1" * 64,
                upload_status=ReimbursementUploadStatus.COMMITTED.value,
                space_id="space-1",
                file_id="file-1",
            )
            cleaned = ReimbursementUpload(
                submission_id=submission.id,
                draft_id=draft.id,
                source_draft_file_id=sources[0].id,
                role=ReimbursementUploadRole.ORIGINAL.value,
                sort_order=1,
                local_storage_key=sources[0].storage_key,
                local_part_storage_key=None,
                local_status=ReimbursementUploadLocalStatus.READY.value,
                reserved_bytes=10,
                reservation_expires_at=None,
                file_name=sources[0].original_name,
                file_type="pdf",
                media_type="application/pdf",
                size_bytes=10,
                sha256=sources[0].sha256,
                upload_status=ReimbursementUploadStatus.CLEANED.value,
                space_id="space-2",
                file_id="file-2",
                cleanup_started_at=now,
                cleaned_at=now,
            )
            discarded = ReimbursementUpload(
                submission_id=submission.id,
                draft_id=draft.id,
                source_draft_file_id=sources[1].id,
                role=ReimbursementUploadRole.ORIGINAL.value,
                sort_order=2,
                local_storage_key=sources[1].storage_key,
                local_part_storage_key=None,
                local_status=ReimbursementUploadLocalStatus.DELETED.value,
                reserved_bytes=10,
                reservation_expires_at=None,
                file_name=sources[1].original_name,
                file_type="pdf",
                media_type="application/pdf",
                size_bytes=10,
                sha256=sources[1].sha256,
                upload_status=ReimbursementUploadStatus.DISCARDED.value,
                local_deleted_at=now,
            )
            database.add_all([committed, cleaned, discarded])
            result_draft = ReimbursementDraft(
                corp_id="corp-test",
                owner_user_id="employee-2",
                status=ReimbursementDraftStatus.LOCKED.value,
                revision=1,
                department_id="department-1",
                department_name="测试部门",
                template_process_code="PROC-REIMBURSEMENT",
                template_config_version=1,
                schema_fingerprint="a" * 64,
                input_json="{}",
                related_instance_ids_json="[]",
                expires_at=now + timedelta(days=30),
                locked_at=now,
            )
            database.add(result_draft)
            database.flush()
            result_source = ReimbursementDraftFile(
                draft_id=result_draft.id,
                sort_order=0,
                processing_role=ReimbursementDraftFileRole.ATTACHMENT_ONLY.value,
                file_status=ReimbursementDraftFileStatus.ACTIVE.value,
                storage_key=f"drafts/{result_draft.id}/source.pdf",
                part_storage_key=None,
                reserved_bytes=10,
                reservation_expires_at=None,
                original_name="source.pdf",
                extension="pdf",
                media_type="application/pdf",
                size_bytes=10,
                sha256="9" * 64,
            )
            database.add(result_source)
            database.flush()
            result_submission = ReimbursementSubmission(
                draft_id=result_draft.id,
                corp_id=result_draft.corp_id,
                originator_user_id=result_draft.owner_user_id,
                originator_union_id="union-2",
                originator_name="另一员工",
                department_id=result_draft.department_id,
                department_name=result_draft.department_name,
                template_process_code=result_draft.template_process_code,
                template_config_version=result_draft.template_config_version,
                schema_fingerprint=result_draft.schema_fingerprint,
                form_snapshot_json="{}",
                related_instance_ids_json="[]",
                snapshot_sha256="7" * 64,
                idempotency_key_hash="8" * 64,
                status=ReimbursementSubmissionStatus.VERIFYING.value,
                oa_create_started_at=now,
                oa_request_hash="6" * 64,
                process_instance_id="process-1",
            )
            database.add(result_submission)
            database.flush()

            linked_draft = ReimbursementDraft(
                corp_id="corp-test",
                owner_user_id="employee-3",
                status=ReimbursementDraftStatus.LOCKED.value,
                revision=1,
                department_id="department-1",
                department_name="测试部门",
                template_process_code="PROC-REIMBURSEMENT",
                template_config_version=1,
                schema_fingerprint="a" * 64,
                input_json="{}",
                related_instance_ids_json="[]",
                expires_at=now + timedelta(days=30),
                locked_at=now,
            )
            database.add(linked_draft)
            database.flush()
            linked_submission = ReimbursementSubmission(
                draft_id=linked_draft.id,
                corp_id=linked_draft.corp_id,
                originator_user_id=linked_draft.owner_user_id,
                originator_union_id="union-3",
                originator_name="已关联员工",
                department_id=linked_draft.department_id,
                department_name=linked_draft.department_name,
                template_process_code=linked_draft.template_process_code,
                template_config_version=linked_draft.template_config_version,
                schema_fingerprint=linked_draft.schema_fingerprint,
                form_snapshot_json="{}",
                related_instance_ids_json="[]",
                snapshot_sha256="4" * 64,
                idempotency_key_hash="3" * 64,
                status=ReimbursementSubmissionStatus.VERIFYING.value,
                oa_create_started_at=now,
                oa_request_hash="2" * 64,
                process_instance_id="process-linked",
            )
            database.add(linked_submission)
            database.flush()
            linked = ReimbursementUpload(
                submission_id=linked_submission.id,
                draft_id=linked_draft.id,
                source_draft_file_id=None,
                role=ReimbursementUploadRole.GENERATED_EXCEL.value,
                sort_order=0,
                local_storage_key=f"generated/{linked_submission.id}/final.xlsx",
                local_part_storage_key=None,
                local_status=ReimbursementUploadLocalStatus.READY.value,
                reserved_bytes=10,
                reservation_expires_at=None,
                file_name="已关联报销单.xlsx",
                file_type="xlsx",
                media_type="application/octet-stream",
                size_bytes=10,
                sha256="1" * 64,
                upload_status=ReimbursementUploadStatus.LINKED.value,
                space_id="space-linked",
                file_id="file-linked",
                linked_at=now,
            )
            database.add(linked)
            database.commit()
            cleanup_draft_id = draft.id
            cleanup_submission_id = submission.id
            result_draft_id = result_draft.id
            result_source_id = result_source.id
            result_submission_id = result_submission.id
            linked_submission_id = linked_submission.id
            linked_id = linked.id
            committed_id = committed.id
            cleaned_id = cleaned.id
            discarded_id = discarded.id
            cleanup_source_id = sources[2].id

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE reimbursement_submissions "
                    "SET status = 'MANUAL_REVIEW', process_instance_id = NULL WHERE id = ?",
                    (result_submission_id,),
                )

        submit_result_sql = (
            "UPDATE reimbursement_submissions SET status = 'SUBMITTED', "
            "business_id = 'business-1', approval_url = 'dingtalk://approval/process-1', "
            "submitted_at = ? WHERE id = ?"
        )
        with pytest.raises(
            IntegrityError,
            match="submitted reimbursement requires a fully linked manifest",
        ):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    submit_result_sql,
                    (now, result_submission_id),
                )

        with Session(engine) as database:
            pending_excel = ReimbursementUpload(
                submission_id=result_submission_id,
                draft_id=result_draft_id,
                source_draft_file_id=None,
                role=ReimbursementUploadRole.GENERATED_EXCEL.value,
                sort_order=0,
                local_storage_key=f"generated/{result_submission_id}/final.xlsx",
                local_part_storage_key=None,
                local_status=ReimbursementUploadLocalStatus.READY.value,
                reserved_bytes=10,
                reservation_expires_at=None,
                file_name="待关联报销单.xlsx",
                file_type="xlsx",
                media_type="application/octet-stream",
                size_bytes=10,
                sha256="5" * 64,
                upload_status=ReimbursementUploadStatus.PENDING.value,
            )
            database.add(pending_excel)
            database.commit()
            pending_excel_id = pending_excel.id

        with pytest.raises(
            IntegrityError,
            match="submitted reimbursement requires a fully linked manifest",
        ):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    submit_result_sql,
                    (now, result_submission_id),
                )

        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE reimbursement_uploads SET upload_status = 'COMMITTED', "
                "space_id = 'space-result', file_id = 'file-result' WHERE id = ?",
                (pending_excel_id,),
            )
        with pytest.raises(
            IntegrityError,
            match="submitted reimbursement requires a fully linked manifest",
        ):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    submit_result_sql,
                    (now, result_submission_id),
                )

        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE reimbursement_uploads SET upload_status = 'LINKED', linked_at = ? "
                "WHERE id = ?",
                (now, pending_excel_id),
            )
        with engine.begin() as connection:
            connection.exec_driver_sql(
                submit_result_sql,
                (now, result_submission_id),
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE reimbursement_submissions SET status = 'MANUAL_REVIEW' WHERE id = ?",
                    (result_submission_id,),
                )
        with pytest.raises(
            IntegrityError,
            match="submitted reimbursement upload manifest is immutable",
        ):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE reimbursement_uploads SET role = 'ORIGINAL', "
                    "source_draft_file_id = ? WHERE id = ?",
                    (result_source_id, pending_excel_id),
                )
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE reimbursement_uploads SET local_status = 'DELETED', "
                "local_deleted_at = ?, status_version = status_version + 1 WHERE id = ?",
                (now, pending_excel_id),
            )
        with pytest.raises(
            IntegrityError,
            match="submitted reimbursement cannot lose uploads",
        ):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "DELETE FROM reimbursement_uploads WHERE id = ?",
                    (pending_excel_id,),
                )
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT upload_status, role, source_draft_file_id, local_status "
                "FROM reimbursement_uploads WHERE id = ?",
                (pending_excel_id,),
            ).one() == (
                ReimbursementUploadStatus.LINKED.value,
                ReimbursementUploadRole.GENERATED_EXCEL.value,
                None,
                ReimbursementUploadLocalStatus.DELETED.value,
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE reimbursement_uploads SET upload_status = 'PENDING', "
                    "linked_at = NULL WHERE id = ?",
                    (linked_id,),
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE reimbursement_uploads SET upload_status = 'CLEANUP_PENDING', "
                    "cleaned_at = NULL WHERE id = ?",
                    (cleaned_id,),
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE reimbursement_uploads SET submission_id = ?, draft_id = ?, "
                    "source_draft_file_id = ? WHERE id = ?",
                    (result_submission_id, result_draft_id, result_source_id, cleaned_id),
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE reimbursement_submissions SET status = 'ORPHAN_CLEANUP', "
                    "process_instance_id = NULL, orphan_confirmed_at = ?, "
                    "orphan_confirmation_code = 'UNSAFE_CLEAR' WHERE id = ?",
                    (now, linked_submission_id),
                )

        with Session(engine) as database:
            invalid_linked = ReimbursementUpload(
                submission_id=cleanup_submission_id,
                draft_id=cleanup_draft_id,
                source_draft_file_id=cleanup_source_id,
                role=ReimbursementUploadRole.ORIGINAL.value,
                sort_order=3,
                local_storage_key=f"drafts/{cleanup_draft_id}/unsafe-linked.pdf",
                local_part_storage_key=None,
                local_status=ReimbursementUploadLocalStatus.READY.value,
                reserved_bytes=10,
                reservation_expires_at=None,
                file_name="unsafe-linked.pdf",
                file_type="pdf",
                media_type="application/pdf",
                size_bytes=10,
                sha256="8" * 64,
                upload_status=ReimbursementUploadStatus.LINKED.value,
                space_id="unsafe-linked-space",
                file_id="unsafe-linked-file",
                linked_at=now,
            )
            database.add(invalid_linked)
            with pytest.raises(
                IntegrityError,
                match="linked upload requires a confirmed OA instance",
            ):
                database.commit()

        # Seed the legacy contradiction that the previous migration test accepted,
        # then restore the insert guard so this specifically exercises the sibling
        # cleanup guard rather than the LINKED insert guard.
        with engine.begin() as connection:
            linked_insert_trigger_sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_reimbursement_uploads_linked_parent_insert'"
            ).scalar_one()
            connection.exec_driver_sql(
                "DROP TRIGGER trg_reimbursement_uploads_linked_parent_insert"
            )
        with Session(engine) as database:
            legacy_linked = ReimbursementUpload(
                submission_id=cleanup_submission_id,
                draft_id=cleanup_draft_id,
                source_draft_file_id=cleanup_source_id,
                role=ReimbursementUploadRole.ORIGINAL.value,
                sort_order=3,
                local_storage_key=f"drafts/{cleanup_draft_id}/legacy-linked.pdf",
                local_part_storage_key=None,
                local_status=ReimbursementUploadLocalStatus.READY.value,
                reserved_bytes=10,
                reservation_expires_at=None,
                file_name="legacy-linked.pdf",
                file_type="pdf",
                media_type="application/pdf",
                size_bytes=10,
                sha256="8" * 64,
                upload_status=ReimbursementUploadStatus.LINKED.value,
                space_id="legacy-linked-space",
                file_id="legacy-linked-file",
                linked_at=now,
            )
            database.add(legacy_linked)
            database.commit()
            legacy_linked_id = legacy_linked.id
        with engine.begin() as connection:
            connection.exec_driver_sql(linked_insert_trigger_sql)

        with pytest.raises(
            IntegrityError,
            match="remote cleanup requires an uncreated orphan submission",
        ):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE reimbursement_uploads SET upload_status = 'CLEANUP_PENDING', "
                    "cleanup_started_at = ? WHERE id = ?",
                    (now, committed_id),
                )
        with pytest.raises(
            IntegrityError,
            match="linked upload requires a confirmed OA instance",
        ):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE reimbursement_submissions SET updated_at = ? WHERE id = ?",
                    (now, cleanup_submission_id),
                )
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "DELETE FROM reimbursement_uploads WHERE id = ?",
                (legacy_linked_id,),
            )

        with Session(engine) as database:
            link_candidate = ReimbursementUpload(
                submission_id=cleanup_submission_id,
                draft_id=cleanup_draft_id,
                source_draft_file_id=cleanup_source_id,
                role=ReimbursementUploadRole.ORIGINAL.value,
                sort_order=4,
                local_storage_key=f"drafts/{cleanup_draft_id}/link-candidate.pdf",
                local_part_storage_key=None,
                local_status=ReimbursementUploadLocalStatus.READY.value,
                reserved_bytes=10,
                reservation_expires_at=None,
                file_name="link-candidate.pdf",
                file_type="pdf",
                media_type="application/pdf",
                size_bytes=10,
                sha256="8" * 64,
                upload_status=ReimbursementUploadStatus.PENDING.value,
            )
            database.add(link_candidate)
            database.commit()
            link_candidate_id = link_candidate.id

        with pytest.raises(
            IntegrityError,
            match="linked upload requires a confirmed OA instance",
        ):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE reimbursement_uploads SET upload_status = 'LINKED', "
                    "space_id = 'unsafe-linked-space', file_id = 'unsafe-linked-file', "
                    "linked_at = ? WHERE id = ?",
                    (now, link_candidate_id),
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE reimbursement_uploads SET local_status = 'READY', "
                    "local_deleted_at = NULL WHERE id = ?",
                    (discarded_id,),
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE reimbursement_uploads SET space_id = 'space-new', "
                    "file_id = 'file-new' WHERE id = ?",
                    (cleaned_id,),
                )

        with Session(engine) as database:
            invalid_cleanup = ReimbursementUpload(
                submission_id=result_submission_id,
                draft_id=result_draft_id,
                source_draft_file_id=result_source_id,
                role=ReimbursementUploadRole.ORIGINAL.value,
                sort_order=0,
                local_storage_key=f"drafts/{result_draft_id}/source.pdf",
                local_part_storage_key=None,
                local_status=ReimbursementUploadLocalStatus.READY.value,
                reserved_bytes=10,
                reservation_expires_at=None,
                file_name="source.pdf",
                file_type="pdf",
                media_type="application/pdf",
                size_bytes=10,
                sha256="9" * 64,
                upload_status=ReimbursementUploadStatus.CLEANUP_PENDING.value,
                space_id="unsafe-space",
                file_id="unsafe-file",
                cleanup_started_at=now,
            )
            database.add(invalid_cleanup)
            with pytest.raises(
                IntegrityError,
                match="remote cleanup requires an uncreated orphan submission",
            ):
                database.commit()

        with Session(engine) as database:
            pending = ReimbursementUpload(
                submission_id=result_submission_id,
                draft_id=result_draft_id,
                source_draft_file_id=result_source_id,
                role=ReimbursementUploadRole.ORIGINAL.value,
                sort_order=0,
                local_storage_key=f"drafts/{result_draft_id}/source.pdf",
                local_part_storage_key=None,
                local_status=ReimbursementUploadLocalStatus.READY.value,
                reserved_bytes=10,
                reservation_expires_at=None,
                file_name="source.pdf",
                file_type="pdf",
                media_type="application/pdf",
                size_bytes=10,
                sha256="9" * 64,
                upload_status=ReimbursementUploadStatus.PENDING.value,
            )
            database.add(pending)
            with pytest.raises(
                IntegrityError,
                match="submitted reimbursement cannot accept new uploads",
            ):
                database.commit()

        with pytest.raises(
            IntegrityError,
            match="OA instance and remote cleanup cannot coexist",
        ):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE reimbursement_submissions SET status = 'VERIFYING', "
                    "oa_create_started_at = ?, oa_request_hash = ?, "
                    "process_instance_id = 'process-unsafe' WHERE id = ?",
                    (now, "5" * 64, cleanup_submission_id),
                )

        with pytest.raises(
            IntegrityError,
            match="OA instance and remote cleanup cannot coexist",
        ):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE reimbursement_submissions SET status = 'QUEUED' WHERE id = ?",
                    (cleanup_submission_id,),
                )

        with Session(engine) as database:
            duplicate_excel = ReimbursementUpload(
                submission_id=cleanup_submission_id,
                draft_id=cleanup_draft_id,
                source_draft_file_id=None,
                role=ReimbursementUploadRole.GENERATED_EXCEL.value,
                sort_order=3,
                local_storage_key=f"generated/{cleanup_submission_id}/duplicate.xlsx",
                local_part_storage_key=None,
                local_status=ReimbursementUploadLocalStatus.READY.value,
                reserved_bytes=10,
                reservation_expires_at=None,
                file_name="重复报销单.xlsx",
                file_type="xlsx",
                media_type="application/octet-stream",
                size_bytes=10,
                sha256="2" * 64,
                upload_status=ReimbursementUploadStatus.PENDING.value,
            )
            database.add(duplicate_excel)
            with pytest.raises(IntegrityError):
                database.commit()
    finally:
        engine.dispose()
        get_settings.cache_clear()
