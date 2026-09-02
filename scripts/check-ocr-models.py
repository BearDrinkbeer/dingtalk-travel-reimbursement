#!/usr/bin/env python3
"""Read-only validation for the two externally provisioned OCR model directories."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.ocr.model_artifacts import ModelKind, model_directory_ready  # noqa: E402


def _validate(label: str, directory: Path, model_kind: ModelKind) -> bool:
    if not model_directory_ready(directory, model_kind):
        print(f"{label}模型目录缺失、内容不完整或 SHA-256 不匹配：{directory}", file=sys.stderr)
        return False
    print(f"{label}模型目录与固定 SHA-256 manifest 一致：{directory}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="检查本地 PP-OCRv6 small 模型目录")
    parser.add_argument("--detection", type=Path, required=True)
    parser.add_argument("--recognition", type=Path, required=True)
    arguments = parser.parse_args()

    detection_ready = _validate("检测", arguments.detection, "detection")
    recognition_ready = _validate("识别", arguments.recognition, "recognition")
    return 0 if detection_ready and recognition_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
