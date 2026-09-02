from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType, SimpleNamespace

REPOSITORY_ROOT = Path(__file__).parents[2]


def _load_runtime_checker():
    script = REPOSITORY_ROOT / "scripts" / "check-ocr-runtime.py"
    spec = importlib.util.spec_from_file_location("check_ocr_runtime", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_ocr_extra_is_locked_for_linux_production_and_macos_arm_development() -> None:
    project = tomllib.loads((REPOSITORY_ROOT / "backend" / "pyproject.toml").read_text())

    dependencies = project["project"]["optional-dependencies"]["ocr"]
    platform_marker = (
        "(sys_platform == 'linux' and platform_machine == 'x86_64') or "
        "(sys_platform == 'darwin' and platform_machine == 'arm64')"
    )

    assert dependencies == [
        f"opencv-contrib-python==4.10.0.84; {platform_marker}",
        f"paddleocr==3.7.0; {platform_marker}",
        f"paddlepaddle==3.3.1; {platform_marker}",
        f"pypdfium2==5.13.0; {platform_marker}",
    ]


def test_model_checker_rejects_files_that_do_not_match_fixed_manifest(tmp_path: Path) -> None:
    script = REPOSITORY_ROOT / "scripts" / "check-ocr-models.py"
    detection = tmp_path / "det"
    recognition = tmp_path / "rec"
    detection.mkdir()
    recognition.mkdir()
    for name in ("inference.json", "inference.pdiparams", "inference.yml"):
        (detection / name).write_bytes(b"det")

    invalid = subprocess.run(
        [
            sys.executable,
            str(script),
            "--detection",
            str(detection),
            "--recognition",
            str(recognition),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    for name in ("inference.json", "inference.pdiparams", "inference.yml"):
        (recognition / name).write_bytes(b"rec")
    tampered = subprocess.run(
        [
            sys.executable,
            str(script),
            "--detection",
            str(detection),
            "--recognition",
            str(recognition),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 1
    assert tampered.returncode == 1
    assert "SHA-256 不匹配" in tampered.stderr


def test_runtime_checker_accepts_locked_macos_arm_cpu_runtime(monkeypatch) -> None:
    checker = _load_runtime_checker()
    run_check_calls = 0

    def run_check() -> None:
        nonlocal run_check_calls
        run_check_calls += 1

    fake_paddle = ModuleType("paddle")
    fake_paddle.utils = SimpleNamespace(run_check=run_check)

    class FakeEngine:
        def __init__(self, _config) -> None:
            self.ready = False

        def ensure_ready(self) -> None:
            self.ready = True

        def recognize(self, _path: str) -> list[object]:
            assert self.ready
            return []

    monkeypatch.setattr(checker.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(checker.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(
        checker.metadata,
        "version",
        lambda package: {
            "opencv-contrib-python": "4.10.0.84",
            "paddleocr": "3.7.0",
            "paddlepaddle": "3.3.1",
            "pypdfium2": "5.13.0",
        }[package],
    )
    monkeypatch.setenv("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "1")
    monkeypatch.setitem(sys.modules, "paddle", fake_paddle)
    monkeypatch.setattr(checker, "PaddleLocalOcrEngine", FakeEngine)
    monkeypatch.setattr(checker, "model_directory_ready", lambda *_args: True)

    assert checker.main(["--detection", "/models/det", "--recognition", "/models/rec"]) == 0
    assert run_check_calls == 1


def test_default_development_script_enables_verified_local_ocr() -> None:
    default_script = (REPOSITORY_ROOT / "scripts" / "dev-backend.sh").read_text()

    assert "export OCR_ENABLED=true" in default_script
    assert "export OCR_FAKE_ENABLED=false" in default_script
    assert "Darwin:arm64|Linux:x86_64|Linux:amd64" in default_script
    assert "check-ocr-models.py" in default_script
    assert "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=1" in default_script
    assert "--extra ocr" in default_script
    assert "curl " not in default_script
    assert "wget " not in default_script


def test_normal_install_and_compose_enable_local_ocr_by_default() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text()
    dockerfile = (REPOSITORY_ROOT / "backend" / "Dockerfile").read_text()
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text()
    example_env = (REPOSITORY_ROOT / ".env.example").read_text()

    backend_install = makefile.split("backend-install:", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
    assert "--extra dev --extra ocr" in backend_install
    assert "ARG INSTALL_OCR=true" in dockerfile
    assert "INSTALL_OCR: ${INSTALL_OCR:-true}" in compose
    assert "platform: ${BACKEND_PLATFORM:-linux/amd64}" in compose
    assert "OCR_ENABLED: ${OCR_ENABLED:-true}" in compose
    assert 'PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK: "1"' in compose
    assert (
        "OCR_DETECTION_MODEL_DIR: "
        "${OCR_DETECTION_MODEL_DIR:-/opt/expense/models/PP-OCRv6_small_det}"
    ) in compose
    assert (
        "OCR_RECOGNITION_MODEL_DIR: "
        "${OCR_RECOGNITION_MODEL_DIR:-/opt/expense/models/PP-OCRv6_small_rec}"
    ) in compose
    assert "OCR_ENABLED=true" in example_env
    assert "INSTALL_OCR=true" in example_env
    assert "BACKEND_PLATFORM=linux/amd64" in example_env
