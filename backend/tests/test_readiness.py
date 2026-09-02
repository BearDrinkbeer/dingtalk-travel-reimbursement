from __future__ import annotations

from pathlib import Path
from shutil import copyfile

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database.session import create_database_engine
from app.main import create_app
from app.services import readiness


def test_ready_reports_required_components_without_paths(client_factory) -> None:
    client = client_factory(ocr_enabled=False)

    response = client.get("/api/ready")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "status": "ready",
            "checks": {
                "database": "ok",
                "excelTemplate": "ok",
                "tempStorage": "ok",
                "ocr": "disabled",
            },
        },
    }
    assert "path" not in response.text.lower()


def test_ready_fails_closed_for_invalid_template(settings_factory, tmp_path: Path) -> None:
    invalid_template = tmp_path / "invalid.xlsx"
    invalid_template.write_bytes(b"not an xlsx")
    settings = settings_factory(excel_template_path=invalid_template)
    application = create_app(settings)

    with TestClient(application) as client:
        response = client.get("/api/ready", headers={"X-Request-ID": "ready-check-1"})

    body = response.json()
    assert response.status_code == 503
    assert body["success"] is False
    assert body["error"] == {
        "code": "SERVICE_NOT_READY",
        "message": "服务尚未就绪",
    }
    assert body["data"]["status"] == "not_ready"
    assert body["data"]["checks"]["excelTemplate"] == "not_ready"
    assert body["requestId"] == "ready-check-1"
    assert str(invalid_template) not in response.text


def test_ocr_enabled_requires_local_models_and_locked_packages(settings_factory) -> None:
    settings = settings_factory(
        ocr_enabled=True,
        ocr_detection_model_dir=Path("/missing/detection"),
        ocr_recognition_model_dir=Path("/missing/recognition"),
    )
    application = create_app(settings)

    with TestClient(application) as client:
        response = client.get("/api/ready")

    assert response.status_code == 503
    assert response.json()["data"]["checks"]["ocr"] == "not_ready"
    assert "/missing" not in response.text


def test_app_process_does_not_construct_real_ocr_engine(settings_factory, tmp_path: Path) -> None:
    settings = settings_factory(
        ocr_enabled=True,
        ocr_detection_model_dir=tmp_path / "detection",
        ocr_recognition_model_dir=tmp_path / "recognition",
    )

    application = create_app(settings)

    # Real Paddle initialization belongs exclusively to the killable OCR worker.
    assert application.state.ocr_service._engine is None


def test_ready_rejects_model_files_with_wrong_sha256(settings_factory, tmp_path: Path) -> None:
    detection = tmp_path / "det"
    recognition = tmp_path / "rec"
    for directory in (detection, recognition):
        directory.mkdir()
        for name in ("inference.json", "inference.pdiparams", "inference.yml"):
            (directory / name).write_bytes(b"tampered")
    settings = settings_factory(
        ocr_enabled=True,
        ocr_detection_model_dir=detection,
        ocr_recognition_model_dir=recognition,
    )
    application = create_app(settings)

    with TestClient(application) as client:
        response = client.get("/api/ready")

    assert response.status_code == 503
    assert response.json()["data"]["checks"]["ocr"] == "not_ready"


def test_temp_storage_check_rejects_symlink(settings_factory, tmp_path: Path) -> None:
    real_directory = tmp_path / "real-temp"
    real_directory.mkdir()
    linked_directory = tmp_path / "linked-temp"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    settings = settings_factory(temp_dir=linked_directory)

    engine = create_database_engine(settings.database_url)
    try:
        report = readiness.check_readiness(settings, engine)
    finally:
        engine.dispose()

    assert report.ready is False
    assert report.checks["tempStorage"] == "not_ready"


def test_database_check_rejects_missing_migration_revision(client_factory) -> None:
    client = client_factory()
    engine = client.app.state.database_engine
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('20260901_0001')")
        )

    response = client.get("/api/ready")

    assert response.status_code == 503
    assert response.json()["data"]["checks"]["database"] == "not_ready"


def test_template_contract_validation_is_cached_by_bounded_fingerprint(
    settings_factory,
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = Path(__file__).parents[1] / "app" / "templates" / "expense_template.xlsx"
    template = tmp_path / "cached-template.xlsx"
    copyfile(source, template)
    settings = settings_factory(excel_template_path=template)
    engine = create_database_engine(settings.database_url)
    calls = 0
    original = readiness.load_validated_template

    def counted(path: Path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(readiness, "load_validated_template", counted)
    readiness._validated_template_fingerprint.cache_clear()
    try:
        readiness.check_readiness(settings, engine)
        readiness.check_readiness(settings, engine)
    finally:
        engine.dispose()
        readiness._validated_template_fingerprint.cache_clear()

    assert calls == 1
