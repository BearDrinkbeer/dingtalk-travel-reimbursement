from __future__ import annotations

import json
import sqlite3

import pytest
from alembic import command
from sqlalchemy.orm import Session
from test_reimbursement_migration import alembic_config
from test_reimbursement_quota import _new_draft, _new_generating_submission

from app.core.config import get_settings
from app.database.session import create_database_engine


def test_purpose_migration_preserves_linked_itineraries_and_locked_snapshots(tmp_path, monkeypatch):
    path = tmp_path / "purpose.db"
    url = f"sqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = alembic_config()
    try:
        command.upgrade(config, "20260906_0012")
        engine = create_database_engine(url)
        with Session(engine) as database:
            draft = _new_draft()
            draft.input_json = json.dumps({"items": [{"itineraryFileIds": ["itinerary-old"]}]})
            database.add(draft)
            database.flush()
            submission = _new_generating_submission(draft)
            submission.snapshot_version = 2
            database.add(submission)
            database.commit()
            draft_id = draft.id
        engine.dispose()
        with sqlite3.connect(path) as connection:
            for index, file_id in enumerate(("itinerary-old", "unclassified-old")):
                connection.execute(
                    """
                    INSERT INTO reimbursement_draft_files (
                        id, draft_id, sort_order, processing_role, file_status,
                        storage_key, reserved_bytes, original_name, extension,
                        media_type, size_bytes, sha256, ocr_status, created_at, updated_at
                    ) VALUES (?, ?, ?, 'ATTACHMENT_ONLY', 'ACTIVE', ?, 1,
                              'evidence.pdf', 'pdf', 'application/pdf', 1, ?,
                              'NOT_REQUESTED', '2026-09-06', '2026-09-06')
                """,
                    (file_id, draft_id, index, f"drafts/{draft_id}/{file_id}.pdf", "a" * 64),
                )
            before = connection.execute(
                "SELECT form_snapshot_json, snapshot_sha256 FROM reimbursement_submissions"
            ).fetchall()
        command.upgrade(config, "head")
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT id, attachment_kind FROM reimbursement_draft_files ORDER BY id"
            ).fetchall() == [("itinerary-old", "itinerary"), ("unclassified-old", "other")]
            assert (
                connection.execute(
                    "SELECT form_snapshot_json, snapshot_sha256 FROM reimbursement_submissions"
                ).fetchall()
                == before
            )
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        with pytest.raises(RuntimeError, match="purpose"):
            command.downgrade(config, "20260906_0012")
    finally:
        get_settings.cache_clear()


def test_empty_purpose_migration_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    get_settings.cache_clear()
    try:
        config = alembic_config()
        command.upgrade(config, "head")
        command.downgrade(config, "20260906_0012")
        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()
