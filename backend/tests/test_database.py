from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from time import monotonic
from typing import Annotated

import pytest
from alembic import command
from alembic.config import Config
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.database.base import Base
from app.database.session import create_database_engine, create_session_factory, get_db
from app.main import create_app
from app.models.session import UserSession, utc_now
from app.models.setting import Setting


def make_settings(tmp_path: Path, database_name: str) -> Settings:
    return Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / database_name}",
        temp_dir=tmp_path / f"{database_name}-temp",
        reimbursement_staging_dir=tmp_path / f"{database_name}-staging",
    )


def test_sqlite_engine_configures_every_connection_for_integrity_and_contention(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'pragmas.db'}")
    try:
        with engine.connect() as first, engine.connect() as second:
            for connection in (first, second):
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
                assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5000
                assert (
                    connection.exec_driver_sql("PRAGMA journal_mode").scalar_one().lower() == "wal"
                )

            first.exec_driver_sql("CREATE TABLE pragma_parent (id INTEGER PRIMARY KEY)")
            first.exec_driver_sql(
                "CREATE TABLE pragma_child ("
                "id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL "
                "REFERENCES pragma_parent(id))"
            )
            first.commit()
            with pytest.raises(IntegrityError):
                second.exec_driver_sql("INSERT INTO pragma_child (id, parent_id) VALUES (1, 999)")
            second.rollback()
    finally:
        engine.dispose()


def app_with_setting(settings: Settings, value: str):
    application = create_app(settings)
    Base.metadata.create_all(application.state.database_engine)
    with application.state.database_session_factory() as database:
        database.add(Setting(key="marker", value=value))
        database.commit()

    def read_marker(database: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
        marker = database.scalar(select(Setting).where(Setting.key == "marker"))
        assert marker is not None
        return {"marker": marker.value}

    application.add_api_route("/api/_test/marker", read_marker, methods=["GET"])
    return application


def test_custom_app_settings_use_isolated_database_factories(tmp_path: Path) -> None:
    first = app_with_setting(make_settings(tmp_path, "first.db"), "first")
    second = app_with_setting(make_settings(tmp_path, "second.db"), "second")

    with TestClient(first) as client:
        first_response = client.get("/api/_test/marker")
    with TestClient(second) as client:
        second_response = client.get("/api/_test/marker")

    assert first_response.json() == {"marker": "first"}
    assert second_response.json() == {"marker": "second"}
    assert first.state.database_engine.url.database == str(tmp_path / "first.db")
    assert second.state.database_engine.url.database == str(tmp_path / "second.db")


def test_sqlite_session_expiry_uses_utc_naive_values(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'sessions.db'}")
    session_factory = create_session_factory(engine)
    Base.metadata.create_all(engine)
    now = utc_now()

    with session_factory() as database:
        database.add_all(
            [
                UserSession(
                    session_id_hash="expired",
                    dingtalk_user_id="user-expired",
                    name="Expired User",
                    departments_json="[]",
                    current_department_id=None,
                    current_department_name=None,
                    csrf_token_hash="csrf-expired",
                    expires_at=now - timedelta(seconds=1),
                ),
                UserSession(
                    session_id_hash="current",
                    dingtalk_user_id="user-current",
                    name="Current User",
                    departments_json="[]",
                    current_department_id=None,
                    current_department_name=None,
                    csrf_token_hash="csrf-current",
                    expires_at=now + timedelta(hours=1),
                ),
            ]
        )
        database.commit()
        database.expunge_all()

        expired = database.get(UserSession, "expired")
        current = database.get(UserSession, "current")

        assert expired is not None and current is not None
        assert expired.expires_at.tzinfo is None
        assert current.expires_at.tzinfo is None
        assert expired.expires_at <= utc_now()
        assert current.expires_at > utc_now()

    engine.dispose()


def test_application_startup_purges_expired_sessions(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, "startup-cleanup.db")
    application = create_app(settings)
    Base.metadata.create_all(application.state.database_engine)
    now = utc_now()
    with application.state.database_session_factory() as database:
        database.add_all(
            [
                UserSession(
                    session_id_hash="expired-sensitive-session",
                    dingtalk_user_id="former-user",
                    name="Sensitive Former Employee",
                    corp_id="",
                    departments_json='[{"id":"10","name":"Former Department"}]',
                    current_department_id="10",
                    current_department_name="Former Department",
                    csrf_token_hash="expired-csrf",
                    expires_at=now - timedelta(seconds=1),
                ),
                UserSession(
                    session_id_hash="current-session",
                    dingtalk_user_id="current-user",
                    name="Current Employee",
                    corp_id="",
                    departments_json='[{"id":"20","name":"Current Department"}]',
                    current_department_id="20",
                    current_department_name="Current Department",
                    csrf_token_hash="current-csrf",
                    expires_at=now + timedelta(hours=1),
                ),
            ]
        )
        database.commit()

    with TestClient(application):
        with application.state.database_session_factory() as database:
            assert database.get(UserSession, "expired-sensitive-session") is None
            assert database.get(UserSession, "current-session") is not None


def test_session_load_opportunistically_purges_expired_sessions(
    client_factory, monkeypatch
) -> None:
    client = client_factory(
        auth_mock_enabled=True,
        auth_mock_departments="10:测试部门",
        session_cleanup_interval_seconds=300,
    )
    assert client.post("/api/auth/mock").status_code == 200
    with client.app.state.database_session_factory() as database:
        database.add(
            UserSession(
                session_id_hash="expired-after-startup",
                dingtalk_user_id="stale-user",
                name="Stale Sensitive Employee",
                corp_id="corp-fixed",
                departments_json='[{"id":"99","name":"Stale Department"}]',
                current_department_id="99",
                current_department_name="Stale Department",
                csrf_token_hash="stale-csrf",
                expires_at=utc_now() - timedelta(seconds=1),
            )
        )
        database.commit()

    future = monotonic() + 1000
    monkeypatch.setattr("app.services.sessions.monotonic", lambda: future)
    assert client.get("/api/me").status_code == 200

    with client.app.state.database_session_factory() as database:
        assert database.get(UserSession, "expired-after-startup") is None

def test_alembic_0003_preserves_legacy_rate_for_old_automatic_types(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "legacy-settings.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    get_settings.cache_clear()
    # A programmatic config keeps this test from replacing pytest's logging
    # handlers via alembic.ini's fileConfig side effect.
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    try:
        command.upgrade(config, "20260901_0002")
        with sqlite3.connect(database_path) as connection:
            connection.execute("UPDATE settings SET value = '88.50' WHERE key = 'subsidy_per_day'")
            connection.commit()

        command.upgrade(config, "head")

        with sqlite3.connect(database_path) as connection:
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            values = dict(
                connection.execute(
                    "SELECT key, value FROM settings WHERE key LIKE 'subsidy_%'"
                ).fetchall()
            )
            keyword_count = connection.execute(
                "SELECT COUNT(*) FROM receipt_keyword_mappings"
            ).fetchone()
            keyword_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(receipt_keyword_mappings)"
                ).fetchall()
            }
            oa_template_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(oa_template_profiles)").fetchall()
            }
        assert revision == ("20260907_0016",)
        assert keyword_count == (23,)
        assert keyword_columns == {
            "id",
            "keyword",
            "normalized_keyword",
            "category_id",
        }
        assert oa_template_columns == {
            "profile_key",
            "process_code",
            "template_name",
            "schema_fingerprint",
            "confirmed_schema_fingerprint",
            "schema_json",
            "mapping_json",
            "config_version",
            "allowed_travel_process_codes_json",
            "travel_profiles_json",
            "related_approval_smoke_test_confirmed",
            "compatibility_status",
            "confirmed_by_user_id",
            "last_checked_at",
            "confirmed_at",
            "created_at",
            "updated_at",
        }
        with sqlite3.connect(database_path) as connection:
            session_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
        assert "dingtalk_union_id" in session_columns
        assert values == {
            "subsidy_per_day": "88.50",
            "subsidy_business_per_day": "88.50",
            "subsidy_short_term_project_per_day": "88.50",
            "subsidy_long_term_project_per_day": "150.00",
            "subsidy_same_city_project_per_day": "50.00",
            "subsidy_internal_per_day": "100.00",
        }
    finally:
        get_settings.cache_clear()


def test_alembic_0008_adds_nullable_union_id_without_fabricating_legacy_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "legacy-session.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    get_settings.cache_clear()
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    try:
        command.upgrade(config, "20260903_0007")
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id_hash, dingtalk_user_id, name, departments_json,
                    current_department_id, current_department_name, csrf_token_hash,
                    created_at, expires_at, last_seen_at, corp_id, is_admin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-session",
                    "legacy-user",
                    "Legacy User",
                    '[{"id":"10","name":"Legacy Department"}]',
                    "10",
                    "Legacy Department",
                    "legacy-csrf",
                    "2026-09-03 00:00:00",
                    "2099-09-03 00:00:00",
                    "2026-09-03 00:00:00",
                    "corp-fixed",
                    0,
                ),
            )
            connection.commit()

        command.upgrade(config, "head")

        with sqlite3.connect(database_path) as connection:
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            session_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            union_id = connection.execute(
                "SELECT dingtalk_union_id FROM sessions WHERE session_id_hash = ?",
                ("legacy-session",),
            ).fetchone()

        assert revision == ("20260907_0016",)
        assert "dingtalk_union_id" in session_columns
        assert union_id == (None,)
    finally:
        get_settings.cache_clear()
