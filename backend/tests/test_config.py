from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

REPOSITORY_ROOT = Path(__file__).parents[2]


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
    assert settings.ocr_worker_limits["memory_bytes"] == 5 * 1024 * 1024 * 1024


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


def test_production_requires_positive_numeric_dingtalk_agent_id(settings_factory) -> None:
    with pytest.raises(ValidationError, match="DINGTALK_AGENT_ID"):
        settings_factory(
            app_env="production",
            session_cookie_secure=True,
            dingtalk_agent_id=None,
        )
    with pytest.raises(ValidationError, match="DINGTALK_AGENT_ID"):
        settings_factory(dingtalk_agent_id=0)
    invalid_agent_id = "agent-id-must-not-reach-errors"
    with pytest.raises(ValidationError, match="DINGTALK_AGENT_ID") as caught:
        settings_factory(dingtalk_agent_id=invalid_agent_id)
    assert invalid_agent_id not in str(caught.value)
    assert invalid_agent_id not in repr(caught.value)

    assert settings_factory(dingtalk_agent_id="1234567890").dingtalk_agent_id == 1234567890


def test_agent_id_is_wired_through_deployment_and_development_entrypoints() -> None:
    for env_name in (".env.example", ".env.production.example", ".env.dingtalk-dev.example"):
        assert "DINGTALK_AGENT_ID=" in (REPOSITORY_ROOT / env_name).read_text()

    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text()
    assert "DINGTALK_AGENT_ID: ${DINGTALK_AGENT_ID:-}" in compose

    dingtalk_script = (REPOSITORY_ROOT / "scripts" / "dev-dingtalk-backend.sh").read_text()
    required_variables = dingtalk_script.split("for variable_name in ", maxsplit=1)[1].split(
        "; do", maxsplit=1
    )[0]
    assert "DINGTALK_AGENT_ID" in required_variables

    frontend_script = (REPOSITORY_ROOT / "scripts" / "dev-dingtalk-frontend.sh").read_text()
    unset_line = next(line for line in frontend_script.splitlines() if line.startswith("unset "))
    assert "DINGTALK_AGENT_ID" in unset_line
