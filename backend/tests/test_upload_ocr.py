from __future__ import annotations

import io
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from PIL import Image
from pypdf import PdfWriter

from app.ocr import engine as engine_module
from app.ocr.engine import FakeOcrEngine, OcrRuntimeError, PaddleLocalOcrEngine
from app.ocr.types import OcrLine
from app.ocr.workers import verify_ocr_runtime_worker
from app.services.ocr_service import OcrService
from app.services.temp_files import StoredFile, cleanup_expired_temp_files
from tests.conftest import mock_login


def image_bytes(image_format: str = "PNG", size: tuple[int, int] = (40, 20)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "white").save(output, format=image_format)
    return output.getvalue()


def noisy_png_bytes(size: tuple[int, int] = (50, 50)) -> bytes:
    pixels = os.urandom(size[0] * size[1] * 3)
    output = io.BytesIO()
    Image.frombytes("RGB", size, pixels).save(output, format="PNG")
    return output.getvalue()


@pytest.mark.asyncio
async def test_production_startup_runs_real_ocr_runtime_smoke(
    settings_factory,
    tmp_path: Path,
) -> None:
    class RecordingRunner:
        function = None

        async def run(self, function, *_args, **_kwargs):
            self.function = function
            return {"ok": True}

    settings = settings_factory(
        ocr_enabled=True,
        ocr_detection_model_dir=tmp_path / "det",
        ocr_recognition_model_dir=tmp_path / "rec",
    )
    settings.app_env = "production"
    runner = RecordingRunner()

    await OcrService(settings, None, runner).ensure_ready()  # type: ignore[arg-type]

    assert runner.function is verify_ocr_runtime_worker


def pdf_bytes(page_count: int = 1, *, encrypted: bool = False) -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt("secret")
    writer.write(output)
    return output.getvalue()


def upload_one(client, csrf: str, name: str, content: bytes, content_type: str) -> object:
    return client.post(
        "/api/files/upload",
        headers={"X-CSRF-Token": csrf},
        files=[("files[]", (name, content, content_type))],
    )


def test_upload_requires_auth_csrf_and_selected_department(client_factory) -> None:
    client = client_factory(auth_mock_enabled=True)
    assert upload_one(client, "missing", "a.png", image_bytes(), "image/png").status_code == 401
    mock_login(client)
    assert upload_one(client, "wrong", "a.png", image_bytes(), "image/png").status_code == 403

    multi = client_factory(
        auth_mock_enabled=True,
        auth_mock_user_id="multi-user",
        auth_mock_departments="10:一部,20:二部",
    )
    multi_login = mock_login(multi)
    response = upload_one(
        multi,
        str(multi_login["csrfToken"]),
        "a.png",
        image_bytes(),
        "image/png",
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DEPARTMENT_REQUIRED"


def test_upload_magic_limits_pdf_rules_and_rejects_multiple_parts(client_factory) -> None:
    client = client_factory(
        auth_mock_enabled=True,
        upload_max_file_bytes=2048,
    )
    csrf = str(mock_login(client)["csrfToken"])

    mismatch = upload_one(client, csrf, "wrong.jpg", image_bytes(), "image/jpeg")
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == "FILE_TYPE_MISMATCH"

    multi_page = upload_one(client, csrf, "many.pdf", pdf_bytes(2), "application/pdf")
    assert multi_page.status_code == 400
    assert multi_page.json()["error"]["code"] == "MULTI_PAGE_PDF_UNSUPPORTED"

    encrypted = upload_one(client, csrf, "locked.pdf", pdf_bytes(encrypted=True), "application/pdf")
    assert encrypted.status_code == 400
    assert encrypted.json()["error"]["code"] == "ENCRYPTED_PDF_UNSUPPORTED"

    malformed = upload_one(client, csrf, "broken.pdf", b"%PDF-not-valid", "application/pdf")
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "INVALID_PDF"

    too_large = upload_one(
        client,
        csrf,
        "large.png",
        b"\x89PNG\r\n\x1a\n" + b"x" * 3000,
        "image/png",
    )
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "FILE_TOO_LARGE"

    response = client.post(
        "/api/files/upload",
        headers={"X-CSRF-Token": csrf},
        files=[
            ("files[]", ("valid.png", image_bytes(), "image/png")),
            ("files[]", ("bad.png", b"not-an-image", "image/png")),
        ],
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "TOO_MANY_FILES"
    temp_root = client.app.state.settings.temp_dir
    assert not any(path.is_file() for path in temp_root.rglob("*"))


def test_upload_rejects_two_valid_file_parts_without_retaining_either(client_factory) -> None:
    content = noisy_png_bytes()
    client = client_factory(
        auth_mock_enabled=True,
        upload_max_file_bytes=len(content) + 100,
    )
    csrf = str(mock_login(client)["csrfToken"])
    response = client.post(
        "/api/files/upload",
        headers={"X-CSRF-Token": csrf},
        files=[
            ("files[]", ("a.png", content, "image/png")),
            ("files[]", ("b.png", content, "image/png")),
        ],
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "TOO_MANY_FILES"
    assert not any(path.is_file() for path in client.app.state.settings.temp_dir.rglob("*"))


def test_upload_image_dimension_and_count_limits(client_factory) -> None:
    client = client_factory(
        auth_mock_enabled=True,
        image_max_dimension=30,
    )
    csrf = str(mock_login(client)["csrfToken"])
    too_wide = upload_one(client, csrf, "wide.png", image_bytes(size=(31, 2)), "image/png")
    assert too_wide.status_code == 400
    assert too_wide.json()["error"]["code"] == "IMAGE_TOO_LARGE"
    too_many = client.post(
        "/api/files/upload",
        headers={"X-CSRF-Token": csrf},
        files=[
            ("files[]", ("a.png", image_bytes(), "image/png")),
            ("files[]", ("b.png", image_bytes(), "image/png")),
        ],
    )
    assert too_many.status_code == 413
    assert too_many.json()["error"]["code"] == "TOO_MANY_FILES"


def test_temp_file_ownership_traversal_delete_logout_and_expiry(client_factory) -> None:
    first = client_factory(auth_mock_enabled=True, upload_ttl_minutes=4)
    first_login = mock_login(first)
    first_csrf = str(first_login["csrfToken"])
    uploaded = upload_one(first, first_csrf, "receipt.png", image_bytes(), "image/png")
    file_id = uploaded.json()["data"]["files"][0]["id"]

    second = client_factory(auth_mock_enabled=True, auth_mock_user_id="another-user")
    second_csrf = str(mock_login(second)["csrfToken"])
    denied = second.delete(f"/api/files/{file_id}", headers={"X-CSRF-Token": second_csrf})
    assert denied.status_code == 404
    traversal = first.delete("/api/files/..%2Fetc%2Fpasswd", headers={"X-CSRF-Token": first_csrf})
    assert traversal.status_code == 404

    deleted = first.delete(f"/api/files/{file_id}", headers={"X-CSRF-Token": first_csrf})
    assert deleted.status_code == 200
    uploaded_again = upload_one(first, first_csrf, "receipt.png", image_bytes(), "image/png")
    assert uploaded_again.status_code == 200
    session_directories = [
        path
        for path in first.app.state.settings.temp_dir.iterdir()
        if path.is_dir() and len(path.name) == 64
    ]
    assert session_directories
    logout = first.post("/api/auth/logout", headers={"X-CSRF-Token": first_csrf})
    assert logout.status_code == 200
    assert not any(
        path.is_dir() and len(path.name) == 64
        for path in first.app.state.settings.temp_dir.iterdir()
    )


def test_cleanup_expired_does_not_follow_symlink(settings_factory, tmp_path: Path) -> None:
    settings = settings_factory(temp_dir=tmp_path / "temp", upload_ttl_minutes=4)
    settings.temp_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep")
    symlink = settings.temp_dir / ("a" * 64)
    symlink.symlink_to(outside, target_is_directory=True)
    old = datetime.now(UTC) - timedelta(minutes=5)
    os.utime(symlink, (old.timestamp(), old.timestamp()), follow_symlinks=False)
    assert cleanup_expired_temp_files(settings) == 1
    assert marker.read_text() == "keep"


def test_ocr_requires_one_file_and_isolates_separate_requests(client_factory) -> None:
    engine = FakeOcrEngine(
        {
            "*": [
                OcrLine("铁路电子客票 G123", 0.98),
                OcrLine("乘车日期 2026年6月30日", 0.97),
                OcrLine("北京南-合肥南", 0.96),
                OcrLine("票价 ￥454.00", 0.99),
            ]
        }
    )
    client = client_factory(auth_mock_enabled=True, ocr_enabled=True, ocr_engine=engine)
    csrf = str(mock_login(client)["csrfToken"])
    uploaded = upload_one(client, csrf, "train.png", image_bytes(), "image/png")
    file_id = uploaded.json()["data"]["files"][0]["id"]
    missing_id = str(uuid4())
    too_many = client.post(
        "/api/ocr",
        headers={"X-CSRF-Token": csrf},
        json={"fileIds": [file_id, missing_id], "tripYear": 2026},
    )
    assert too_many.status_code == 422

    response = client.post(
        "/api/ocr",
        headers={"X-CSRF-Token": csrf},
        json={"fileIds": [file_id], "tripYear": 2026},
    )
    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item == {
        "fileId": file_id,
        "type": "train",
        "categoryId": "rail_fare",
        "categoryName": "火车票",
        "date": "2026-06-30",
        "description": "北京南-合肥南",
        "amount": "454.00",
        "receiptCount": 1,
        "source": "ocr",
        "confidence": "0.60",
        "warnings": [],
        "status": "recognized",
        "error": None,
    }
    missing = client.post(
        "/api/ocr",
        headers={"X-CSRF-Token": csrf},
        json={"fileIds": [missing_id], "tripYear": 2026},
    )
    missing_item = missing.json()["data"]["items"][0]
    assert missing_item["status"] == "failed"
    assert missing_item["error"]["code"] == "TEMP_FILE_NOT_FOUND"


def test_ocr_disabled_is_per_file_failure(client_factory) -> None:
    client = client_factory(auth_mock_enabled=True, ocr_enabled=False)
    csrf = str(mock_login(client)["csrfToken"])
    uploaded = upload_one(client, csrf, "receipt.png", image_bytes(), "image/png")
    file_id = uploaded.json()["data"]["files"][0]["id"]
    response = client.post(
        "/api/ocr",
        headers={"X-CSRF-Token": csrf},
        json={"fileIds": [file_id]},
    )
    item = response.json()["data"]["items"][0]
    assert item["status"] == "failed"
    assert item["error"]["code"] == "OCR_DISABLED"


@pytest.mark.asyncio
async def test_pdf_text_missing_amount_falls_back_once_to_paddle(
    settings_factory,
    tmp_path: Path,
) -> None:
    class PdfFallbackRunner:
        def __init__(self) -> None:
            self.preferences: list[bool] = []

        async def run(self, _function, *_args, timeout_seconds: float):
            del timeout_seconds
            prefer_pdf_text = bool(_args[-1])
            self.preferences.append(prefer_pdf_text)
            common = [
                ("铁路电子客票 G123", 0.98),
                ("乘车日期 2026年7月7日", 0.97),
                ("合肥南站-北京南站", 0.96),
            ]
            if prefer_pdf_text:
                return {"ok": True, "source": "pdf_text", "lines": common}
            return {
                "ok": True,
                "source": "paddle",
                "lines": [*common, ("票价 ￥473.50", 0.99)],
            }

    receipt = tmp_path / "train.pdf"
    receipt.write_bytes(b"%PDF-test-fixture")
    stored = StoredFile(
        temp_id="train-1",
        path=receipt,
        extension="pdf",
        media_type="application/pdf",
        size=receipt.stat().st_size,
        original_name="高铁1.pdf",
    )
    settings = settings_factory(
        ocr_enabled=True,
        ocr_detection_model_dir=tmp_path / "det",
        ocr_recognition_model_dir=tmp_path / "rec",
    )
    runner = PdfFallbackRunner()
    parsed = await OcrService(settings, None, runner).recognize_file(  # type: ignore[arg-type]
        stored,
        reference_year=2026,
    )

    assert runner.preferences == [True, False]
    assert parsed.receipt_type == "train"
    assert str(parsed.amount) == "473.50"
    assert parsed.date.isoformat() == "2026-07-07"
    assert parsed.description == "合肥南站-北京南站"
    assert "MISSING_AMOUNT" not in parsed.warnings


@pytest.mark.asyncio
async def test_ambiguous_passenger_pdf_text_falls_back_once_to_paddle(
    settings_factory,
    tmp_path: Path,
) -> None:
    class PassengerTransportFallbackRunner:
        def __init__(self) -> None:
            self.preferences: list[bool] = []

        async def run(self, _function, *_args, timeout_seconds: float):
            del timeout_seconds
            prefer_pdf_text = bool(_args[-1])
            self.preferences.append(prefer_pdf_text)
            common = [
                ("电子发票 旅客运输服务", 0.98),
                ("开票日期 2026年07月08日", 0.97),
                ("价税合计 ￥9.20", 0.99),
            ]
            if prefer_pdf_text:
                return {"ok": True, "source": "pdf_text", "lines": common}
            return {
                "ok": True,
                "source": "paddle",
                "lines": [*common, ("交通工具类型 出租车", 0.96)],
            }

    receipt = tmp_path / "taxi.pdf"
    receipt.write_bytes(b"%PDF-test-fixture")
    stored = StoredFile(
        temp_id="taxi-1",
        path=receipt,
        extension="pdf",
        media_type="application/pdf",
        size=receipt.stat().st_size,
        original_name="打车发票.pdf",
    )
    settings = settings_factory(
        ocr_enabled=True,
        ocr_detection_model_dir=tmp_path / "det",
        ocr_recognition_model_dir=tmp_path / "rec",
    )
    runner = PassengerTransportFallbackRunner()
    parsed = await OcrService(settings, None, runner).recognize_file(  # type: ignore[arg-type]
        stored,
        reference_year=2026,
    )

    assert runner.preferences == [True, False]
    assert parsed.category.value == "local_transport"
    assert "MANUAL_REVIEW_REQUIRED" not in parsed.warnings


@pytest.mark.asyncio
async def test_passenger_pdf_fallback_merges_occurrence_route_with_text_amount(
    settings_factory,
    tmp_path: Path,
) -> None:
    class PassengerFieldFallbackRunner:
        def __init__(self) -> None:
            self.preferences: list[bool] = []

        async def run(self, _function, *_args, timeout_seconds: float):
            del timeout_seconds
            prefer_pdf_text = bool(_args[-1])
            self.preferences.append(prefer_pdf_text)
            if prefer_pdf_text:
                return {
                    "ok": True,
                    "source": "pdf_text",
                    "lines": [
                        ("电子发票 旅客运输服务", 0.98),
                        ("*交通运输服务*客运服务费", 0.98),
                        ("开票日期：", 0.99),
                        ("2026年07月08日", 0.99),
                        ("价税合计（小写） ￥9.20", 0.99),
                    ],
                }
            return {
                "ok": True,
                "source": "paddle",
                "lines": [
                    ("出行人", 0.99),
                    ("有效身份证件号", 0.99),
                    ("出行日期", 0.99),
                    ("出发地", 0.99),
                    ("到达地", 0.99),
                    ("等级", 0.99),
                    ("交通工具类型", 0.99),
                    ("开票日期：2026年07月08日", 0.99),
                    ("2026-07-06", 0.99),
                    ("长鑫存储技术有限公司(东", 0.99),
                    ("泊寓·新桥产业园店", 0.99),
                    ("其他", 0.99),
                    ("出租车", 0.99),
                    ("门)", 0.93),
                ],
            }

    receipt = tmp_path / "taxi-fields.pdf"
    receipt.write_bytes(b"%PDF-test-fixture")
    stored = StoredFile(
        temp_id="taxi-fields-1",
        path=receipt,
        extension="pdf",
        media_type="application/pdf",
        size=receipt.stat().st_size,
        original_name="打车发票.pdf",
    )
    settings = settings_factory(
        ocr_enabled=True,
        ocr_detection_model_dir=tmp_path / "det",
        ocr_recognition_model_dir=tmp_path / "rec",
    )
    runner = PassengerFieldFallbackRunner()
    parsed = await OcrService(settings, None, runner).recognize_file(  # type: ignore[arg-type]
        stored,
        reference_year=2026,
    )

    assert runner.preferences == [True, False]
    assert parsed.date.isoformat() == "2026-07-06"
    assert parsed.amount is not None and str(parsed.amount) == "9.20"
    assert parsed.description == "长鑫存储技术有限公司(东门)-泊寓·新桥产业园店"
    assert "INVOICE_DATE_USED_AS_OCCURRENCE" not in parsed.warnings


@pytest.mark.asyncio
async def test_unclassified_non_transport_pdf_text_does_not_use_paddle(
    settings_factory,
    tmp_path: Path,
) -> None:
    class PlainInvoiceRunner:
        def __init__(self) -> None:
            self.preferences: list[bool] = []

        async def run(self, _function, *_args, timeout_seconds: float):
            del timeout_seconds
            self.preferences.append(bool(_args[-1]))
            return {
                "ok": True,
                "source": "pdf_text",
                "lines": [
                    ("电子发票", 0.98),
                    ("开票日期 2026年07月08日", 0.97),
                    ("价税合计 ￥9.20", 0.99),
                ],
            }

    receipt = tmp_path / "plain.pdf"
    receipt.write_bytes(b"%PDF-test-fixture")
    stored = StoredFile(
        temp_id="plain-1",
        path=receipt,
        extension="pdf",
        media_type="application/pdf",
        size=receipt.stat().st_size,
        original_name="普通发票.pdf",
    )
    settings = settings_factory(
        ocr_enabled=True,
        ocr_detection_model_dir=tmp_path / "det",
        ocr_recognition_model_dir=tmp_path / "rec",
    )
    runner = PlainInvoiceRunner()
    parsed = await OcrService(settings, None, runner).recognize_file(  # type: ignore[arg-type]
        stored,
        reference_year=2026,
    )

    assert runner.preferences == [True]
    assert parsed.category.value == "other"


def test_production_rejects_fake_and_missing_models(
    settings_factory,
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_engine_created = False

    def unexpected_database_engine(_database_url: str):
        nonlocal database_engine_created
        database_engine_created = True
        raise AssertionError("production fake OCR must fail before resources are created")

    monkeypatch.setattr("app.main.create_database_engine", unexpected_database_engine)
    with pytest.raises(ValueError, match="fake OCR"):
        from app.main import create_app

        create_app(
            settings_factory(
                app_env="production",
                auth_mock_enabled=False,
                ocr_enabled=False,
                session_cookie_secure=True,
            ),
            ocr_engine=FakeOcrEngine(),
        )
    assert database_engine_created is False

    settings = settings_factory(
        ocr_enabled=True,
        ocr_detection_model_dir=tmp_path / "missing-det",
        ocr_recognition_model_dir=tmp_path / "missing-rec",
    )
    with pytest.raises(OcrRuntimeError, match="模型目录不可用"):
        PaddleLocalOcrEngine(settings).ensure_ready()


def test_paddle_static_cpu_runtime_disables_mkldnn(
    monkeypatch,
    settings_factory,
    tmp_path: Path,
) -> None:
    received: dict[str, object] = {}

    class FakePaddleOcr:
        def __init__(self, **kwargs: object) -> None:
            received.update(kwargs)

    fake_module = ModuleType("paddleocr")
    fake_module.PaddleOCR = FakePaddleOcr  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "paddleocr", fake_module)
    monkeypatch.setattr(engine_module, "model_directory_ready", lambda *_args: True)
    monkeypatch.setattr(
        engine_module.metadata,
        "version",
        lambda package: {"paddleocr": "3.7.0", "paddlepaddle": "3.3.1"}[package],
    )

    PaddleLocalOcrEngine(
        settings_factory(
            ocr_detection_model_dir=tmp_path / "det",
            ocr_recognition_model_dir=tmp_path / "rec",
        )
    ).ensure_ready()

    assert received["enable_mkldnn"] is False


def test_paddle_result_contract_rejects_mismatch_without_runtime(
    settings_factory,
) -> None:
    class Result:
        json = {"res": {"rec_texts": ["文字"], "rec_scores": []}}

    class Pipeline:
        def predict(self, _path: str) -> list[Result]:
            return [Result()]

    engine = PaddleLocalOcrEngine(settings_factory())
    engine._pipeline = Pipeline()
    with pytest.raises(OcrRuntimeError, match="数量不一致"):
        engine.recognize("not-opened-by-fake-pipeline.png")


def test_paddle_recovers_passenger_route_from_separate_header_guided_cell_crops(
    settings_factory,
) -> None:
    texts = [
        "出行人",
        "有效身份证件号",
        "出行日期",
        "出发地",
        "到达地",
        "等级",
        "交通工具类型",
        "测试用户",
        "2026-07-01",
        "甲方园区(北乙方酒店(南",
        "无",
        "出租车",
        "门)",
        "门)",
    ]
    boxes = [
        [50, 100, 110, 120],
        [170, 100, 300, 120],
        [390, 100, 470, 120],
        [570, 100, 630, 120],
        [790, 100, 850, 120],
        [950, 100, 1010, 120],
        [1040, 100, 1150, 120],
        [50, 122, 110, 144],
        [380, 122, 470, 144],
        [500, 122, 930, 144],
        [950, 122, 990, 144],
        [1060, 122, 1120, 144],
        [570, 146, 620, 166],
        [800, 146, 850, 166],
    ]

    class PageImage:
        shape = (1000, 1200, 3)

    class Result(dict):
        json = {
            "res": {
                "rec_texts": texts,
                "rec_scores": [0.99] * len(texts),
                "rec_boxes": boxes,
            }
        }

        def __init__(self) -> None:
            super().__init__({"doc_preprocessor_res": {"output_img": PageImage()}})

    class Pipeline:
        def __init__(self) -> None:
            self.calls = 0

        def predict(self, value: object) -> list[Result]:
            assert value == "passenger-invoice.png"
            self.calls += 1
            return [Result()]

    pipeline = Pipeline()
    engine = PaddleLocalOcrEngine(settings_factory())
    engine._pipeline = pipeline
    crop_regions: list[tuple[int, int, int, int]] = []

    def recognize_region(
        _page_image: object,
        region: tuple[int, int, int, int],
    ) -> list[OcrLine]:
        crop_regions.append(region)
        if len(crop_regions) == 1:
            return [OcrLine("甲方园区(北", 0.98), OcrLine("门)", 0.97)]
        return [OcrLine("乙方酒店(南", 0.98), OcrLine("门)", 0.97)]

    engine._recognize_region = recognize_region  # type: ignore[attr-defined,method-assign]

    lines = engine.recognize("passenger-invoice.png")

    assert pipeline.calls == 1
    assert len(crop_regions) == 2
    assert OcrLine("行程路线：甲方园区(北门)-乙方酒店(南门)", 0.97) in lines
