from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_use_local_ocr_and_sqlite(tmp_path: Path) -> None:
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        temp_dir=tmp_path / "receipts",
    )

    assert settings.ocr_mode == "local"
    assert settings.ocr_enabled is True
    assert settings.admin_ids == frozenset()


def test_admin_ids_are_trimmed() -> None:
    settings = Settings(admin_user_ids="user-1, user-2,,")

    assert settings.admin_ids == frozenset({"user-1", "user-2"})


def test_mock_departments_are_parsed_without_accepting_invalid_entries() -> None:
    settings = Settings(auth_mock_departments="10:部门一,invalid,20: 部门二")

    assert settings.mock_departments == (("10", "部门一"), ("20", "部门二"))


def test_non_sqlite_database_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://example.invalid/expense")


def test_v1_requires_single_ocr_worker_and_safe_temp_ttl() -> None:
    with pytest.raises(ValidationError, match="OCR_CONCURRENCY"):
        Settings(ocr_concurrency=2)
    with pytest.raises(ValidationError, match="UPLOAD_TTL_MINUTES"):
        Settings(upload_ttl_minutes=3, ocr_timeout_seconds=120)
    assert Settings(upload_ttl_minutes=4, ocr_timeout_seconds=120).upload_ttl_minutes == 4


def test_global_temp_quota_must_reserve_tmpfs_headroom() -> None:
    with pytest.raises(ValidationError, match="TEMP_STORAGE_MAX_BYTES"):
        Settings(temp_storage_max_bytes=1024 * 1024 * 1024)


def test_file_and_ocr_workers_have_distinct_memory_limits() -> None:
    settings = Settings()
    assert settings.file_worker_limits["memory_bytes"] == 512 * 1024 * 1024
    assert settings.ocr_worker_limits["memory_bytes"] == 2 * 1024 * 1024 * 1024


def test_production_ocr_requires_linux_limits(
    settings_factory,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.core.config.sys.platform", "darwin")
    monkeypatch.setattr("app.core.config.platform.machine", lambda: "arm64")
    common = {
        "app_env": "production",
        "session_cookie_secure": True,
        "ocr_enabled": True,
        "ocr_detection_model_dir": tmp_path / "det",
        "ocr_recognition_model_dir": tmp_path / "rec",
    }
    with pytest.raises(ValidationError, match="Linux x86_64/amd64 worker limits"):
        settings_factory(**common)
    monkeypatch.setattr("app.core.config.sys.platform", "linux")
    with pytest.raises(ValidationError, match="Linux x86_64/amd64 worker limits"):
        settings_factory(**common)
    monkeypatch.setattr("app.core.config.platform.machine", lambda: "AMD64")
    assert settings_factory(**common).ocr_enabled
