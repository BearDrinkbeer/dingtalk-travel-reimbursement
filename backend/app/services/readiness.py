from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from functools import lru_cache
from importlib import metadata
from pathlib import Path

from sqlalchemy import Engine, inspect, text

from app.core.config import Settings
from app.ocr.model_artifacts import model_directory_ready
from app.services.excel_generator import load_validated_template


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    checks: dict[str, str]


_EXPECTED_ALEMBIC_REVISION = "20260903_0007"
_REQUIRED_COLUMNS = {
    "oa_template_profiles": {
        "profile_key",
        "process_code",
        "template_name",
        "schema_fingerprint",
        "confirmed_schema_fingerprint",
        "schema_json",
        "mapping_json",
        "config_version",
        "allowed_travel_process_codes_json",
        "related_approval_smoke_test_confirmed",
        "compatibility_status",
        "confirmed_by_user_id",
        "last_checked_at",
        "confirmed_at",
        "created_at",
        "updated_at",
    },
    "projects": {"id", "project_code", "project_name", "enabled", "created_at", "updated_at"},
    "settings": {"key", "value"},
    "receipt_keyword_mappings": {
        "id",
        "keyword",
        "normalized_keyword",
        "category_id",
    },
    "sessions": {
        "session_id_hash",
        "dingtalk_user_id",
        "name",
        "corp_id",
        "departments_json",
        "current_department_id",
        "current_department_name",
        "csrf_token_hash",
        "is_admin",
        "created_at",
        "expires_at",
        "last_seen_at",
    },
}


def _database_ready(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            if connection.execute(text("SELECT 1")).scalar_one() != 1:
                return False
            inspector = inspect(connection)
            table_names = set(inspector.get_table_names())
            if not {*_REQUIRED_COLUMNS, "alembic_version"}.issubset(table_names):
                return False
            for table_name, expected_columns in _REQUIRED_COLUMNS.items():
                actual_columns = {
                    str(column["name"]) for column in inspector.get_columns(table_name)
                }
                if not expected_columns.issubset(actual_columns):
                    return False
            revisions = (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            )
            return revisions == [_EXPECTED_ALEMBIC_REVISION]
    except Exception:
        return False


@lru_cache(maxsize=8)
def _validated_template_fingerprint(
    path_text: str,
    device: int,
    inode: int,
    size: int,
    modified_ns: int,
    changed_ns: int,
) -> bool:
    # The stat fields are deliberate cache-key material. Validation remains
    # strict, while the 10-second readiness probe does not repeatedly parse an
    # unchanged workbook. The bounded cache prevents unbounded path growth.
    del device, inode, size, modified_ns, changed_ns
    try:
        workbook, _ = load_validated_template(Path(path_text))
    except Exception:
        return False
    workbook.close()
    return True


def _template_ready(template_path: Path) -> bool:
    try:
        template_stat = template_path.stat()
    except OSError:
        return False
    return _validated_template_fingerprint(
        str(template_path),
        template_stat.st_dev,
        template_stat.st_ino,
        template_stat.st_size,
        template_stat.st_mtime_ns,
        template_stat.st_ctime_ns,
    )


def _temp_storage_ready(temp_dir: Path) -> bool:
    try:
        directory_stat = temp_dir.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode):
        return False
    return os.access(temp_dir, os.W_OK | os.X_OK)


def _ocr_readiness(settings: Settings) -> tuple[bool, str]:
    # Local OCR is enabled by default and participates in readiness. An explicit
    # disable remains available only for diagnosis and focused tests.
    if not settings.ocr_enabled:
        return True, "disabled"
    if settings.ocr_fake_enabled:
        return settings.app_env != "production", "development_fake"
    if not model_directory_ready(settings.ocr_detection_model_dir, "detection"):
        return False, "not_ready"
    if not model_directory_ready(settings.ocr_recognition_model_dir, "recognition"):
        return False, "not_ready"
    try:
        expected_versions = {
            "opencv-contrib-python": "4.10.0.84",
            "paddleocr": "3.7.0",
            "paddlepaddle": "3.3.1",
            "pypdfium2": "5.13.0",
        }
        versions_match = all(
            metadata.version(package) == expected for package, expected in expected_versions.items()
        )
    except metadata.PackageNotFoundError:
        return False, "not_ready"
    return versions_match, "configured" if versions_match else "not_ready"


def check_readiness(settings: Settings, engine: Engine) -> ReadinessReport:
    """Run bounded, non-recognition checks without returning paths or exception details."""

    database_ok = _database_ready(engine)
    template_ok = _template_ready(settings.excel_template_path)
    temp_ok = _temp_storage_ready(settings.temp_dir)
    ocr_ok, ocr_status = _ocr_readiness(settings)
    checks = {
        "database": "ok" if database_ok else "not_ready",
        "excelTemplate": "ok" if template_ok else "not_ready",
        "tempStorage": "ok" if temp_ok else "not_ready",
        "ocr": ocr_status,
    }
    return ReadinessReport(
        ready=database_ok and template_ok and temp_ok and ocr_ok,
        checks=checks,
    )
