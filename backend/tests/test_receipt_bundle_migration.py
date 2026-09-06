from __future__ import annotations

import sqlite3

import pytest
from alembic import command
from sqlalchemy.orm import Session
from test_reimbursement_migration import (
    alembic_config,
    seed_verifying_submission_with_linked_excel,
)
from test_reimbursement_quota import _new_draft, _new_generating_submission

from app.core.config import get_settings
from app.database.session import create_database_engine


def test_pdf_role_upgrade_roundtrip_preserves_existing_upload_and_snapshot(tmp_path, monkeypatch):
    database_path = tmp_path / "bundle-migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    get_settings.cache_clear()
    config = alembic_config()
    try:
        command.upgrade(config, "20260904_0011")
        with sqlite3.connect(database_path) as connection:
            seed_verifying_submission_with_linked_excel(connection, suffix="legacy")
            before_uploads = connection.execute("SELECT * FROM reimbursement_uploads").fetchall()
            before_submissions = connection.execute(
                "SELECT * FROM reimbursement_submissions"
            ).fetchall()
        for revision in ("head", "20260904_0011", "head"):
            if revision == "head":
                command.upgrade(config, revision)
            else:
                command.downgrade(config, revision)
            with sqlite3.connect(database_path) as connection:
                assert (
                    connection.execute("SELECT * FROM reimbursement_uploads").fetchall()
                    == before_uploads
                )
                assert (
                    connection.execute("SELECT * FROM reimbursement_submissions").fetchall()
                    == before_submissions
                )
                table_sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'reimbursement_uploads'"
                ).fetchone()[0]
                assert ("GENERATED_PDF" in table_sql) is (revision == "head")
    finally:
        get_settings.cache_clear()


def test_bundle_downgrade_refuses_to_discard_v2_audit_records(tmp_path, monkeypatch):
    database_path = tmp_path / "bundle-downgrade.db"
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = alembic_config()
    command.upgrade(config, "head")
    engine = create_database_engine(url)
    try:
        with Session(engine) as database:
            draft = _new_draft()
            database.add(draft)
            database.flush()
            submission = _new_generating_submission(draft)
            submission.snapshot_version = 2
            database.add(submission)
            database.commit()
        with pytest.raises(RuntimeError, match="version 2 submissions"):
            command.downgrade(config, "20260904_0011")
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "20260906_0012",
            )
            assert connection.execute(
                "SELECT snapshot_version FROM reimbursement_submissions"
            ).fetchone() == (2,)
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'uq_reimbursement_uploads_generated_pdf'"
            ).fetchone() == (1,)
    finally:
        engine.dispose()
        get_settings.cache_clear()
