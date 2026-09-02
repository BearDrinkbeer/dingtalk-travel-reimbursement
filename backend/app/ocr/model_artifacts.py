from __future__ import annotations

import hashlib
import json
import stat
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

ModelKind = Literal["detection", "recognition"]
MODEL_MANIFEST_PATH = Path(__file__).with_name("model_manifest.json")
_HASH_CHUNK_BYTES = 1024 * 1024


@lru_cache(maxsize=1)
def _load_manifest() -> dict[str, Any]:
    with MODEL_MANIFEST_PATH.open("rb") as stream:
        manifest = json.load(stream)
    if manifest.get("schemaVersion") != 1 or not isinstance(manifest.get("models"), dict):
        raise ValueError("unsupported OCR model manifest")
    return manifest


def _model_spec(model_kind: ModelKind) -> dict[str, Any]:
    spec = _load_manifest()["models"].get(model_kind)
    if not isinstance(spec, dict) or not isinstance(spec.get("files"), dict):
        raise ValueError("invalid OCR model manifest")
    return spec


def _directory_fingerprint(
    directory: Path,
    expected_files: tuple[str, ...],
) -> tuple[tuple[int, int, int, int, int, int], ...] | None:
    try:
        directory_stat = directory.lstat()
        if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode):
            return None
        actual_entries = tuple(sorted(path.name for path in directory.iterdir()))
        if actual_entries != expected_files:
            return None
        fingerprint: list[tuple[int, int, int, int, int, int]] = []
        for name in expected_files:
            file_stat = (directory / name).lstat()
            if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
                return None
            fingerprint.append(
                (
                    file_stat.st_dev,
                    file_stat.st_ino,
                    file_stat.st_size,
                    file_stat.st_mtime_ns,
                    file_stat.st_ctime_ns,
                    file_stat.st_mode,
                )
            )
        return tuple(fingerprint)
    except OSError:
        return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=8)
def _validate_fingerprint(
    model_kind: ModelKind,
    directory_text: str,
    fingerprint: tuple[tuple[int, int, int, int, int, int], ...],
) -> bool:
    del fingerprint
    try:
        spec = _model_spec(model_kind)
        expected_hashes = spec["files"]
        directory = Path(directory_text)
        actual_hashes = {name: _sha256_file(directory / name) for name in sorted(expected_hashes)}
        if actual_hashes != expected_hashes:
            return False
        manifest_lines = "".join(
            f"{actual_hashes[name]}  ./{name}\n" for name in sorted(actual_hashes)
        )
        aggregate = hashlib.sha256(manifest_lines.encode()).hexdigest()
        return aggregate == spec.get("directoryManifestSha256")
    except (KeyError, OSError, TypeError, ValueError):
        return False


def model_directory_ready(directory: Path | None, model_kind: ModelKind) -> bool:
    """Verify an exact local model artifact, caching unchanged stat fingerprints."""

    if directory is None:
        return False
    try:
        spec = _model_spec(model_kind)
        expected_files = tuple(sorted(str(name) for name in spec["files"]))
    except (KeyError, OSError, TypeError, ValueError):
        return False
    fingerprint = _directory_fingerprint(directory, expected_files)
    if fingerprint is None:
        return False
    return _validate_fingerprint(model_kind, str(directory), fingerprint)


def clear_model_validation_cache() -> None:
    """Clear bounded caches after a deployment replaces model artifacts."""

    _load_manifest.cache_clear()
    _validate_fingerprint.cache_clear()
