#!/usr/bin/env python3
"""Verify locked Paddle packages, model initialization, and one local CPU inference."""

from __future__ import annotations

import argparse
import os
import platform
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from app.ocr.engine import PaddleLocalOcrEngine
from app.ocr.model_artifacts import model_directory_ready

SUPPORTED_DEVELOPMENT_PLATFORM = ("Darwin", "arm64")
SUPPORTED_PRODUCTION_PLATFORM = ("Linux", "x86_64")
SUPPORTED_PRODUCTION_ALIAS = ("Linux", "amd64")


@dataclass(frozen=True, slots=True)
class RuntimeOcrConfig:
    ocr_detection_model_dir: Path | None
    ocr_recognition_model_dir: Path | None
    ocr_engine: str = "paddle_static"
    ocr_cpu_threads: int = 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="执行本地 PaddleOCR 初始化和最小 CPU 推理"
    )
    parser.add_argument("--detection", type=Path, required=True)
    parser.add_argument("--recognition", type=Path, required=True)
    arguments = parser.parse_args(argv)

    current_platform = (platform.system(), platform.machine())
    if current_platform not in {
        SUPPORTED_DEVELOPMENT_PLATFORM,
        SUPPORTED_PRODUCTION_PLATFORM,
        SUPPORTED_PRODUCTION_ALIAS,
    }:
        print(
            "OCR 依赖检查仅支持 macOS arm64 开发机或 Linux x86_64 生产机",
            file=sys.stderr,
        )
        return 2

    if not model_directory_ready(
        arguments.detection, "detection"
    ) or not model_directory_ready(arguments.recognition, "recognition"):
        print("本地 OCR 模型与固定 SHA-256 manifest 不一致", file=sys.stderr)
        return 1

    if os.environ.get("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK") != "1":
        print("必须禁用 PaddleX 模型源联网检查", file=sys.stderr)
        return 2

    expected_versions = {
        "opencv-contrib-python": "4.10.0.84",
        "paddleocr": "3.7.0",
        "paddlepaddle": "3.3.1",
        "pypdfium2": "5.13.0",
    }
    for package, expected in expected_versions.items():
        try:
            actual = metadata.version(package)
        except metadata.PackageNotFoundError:
            print(f"缺少已锁定依赖：{package}=={expected}", file=sys.stderr)
            return 1
        if actual != expected:
            print(
                f"依赖版本不匹配：{package} 应为 {expected}，实际为 {actual}",
                file=sys.stderr,
            )
            return 1

    try:
        import paddle

        paddle.utils.run_check()
        engine = PaddleLocalOcrEngine(
            RuntimeOcrConfig(
                ocr_detection_model_dir=arguments.detection,
                ocr_recognition_model_dir=arguments.recognition,
            )
        )
        engine.ensure_ready()
        with TemporaryDirectory(prefix="expense-ocr-runtime-") as temporary_directory:
            image_path = Path(temporary_directory) / "blank.png"
            Image.new("RGB", (64, 64), "white").save(image_path, format="PNG")
            line_count = len(engine.recognize(str(image_path)))
    except Exception as exc:
        print(f"Paddle 本地运行时检查失败：{type(exc).__name__}", file=sys.stderr)
        return 1

    print(
        "PaddleOCR 3.7.0 / PaddlePaddle 3.3.1 本地 CPU 初始化与最小推理通过"
        f"（识别行数：{line_count}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
