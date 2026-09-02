from __future__ import annotations

import logging
from datetime import date

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.categories import CATEGORY_BY_ID, ExpenseCategory
from app.domain.money import money_string
from app.ocr.extractors import normalized_lines
from app.ocr.parsers import ReceiptParserRegistry, is_passenger_transport_text
from app.ocr.qr import invoice_qr_from_payload
from app.ocr.types import (
    InvoiceQrEvidence,
    LocalOcrEngine,
    OcrLine,
    ParseContext,
    ParsedExpense,
    ReceiptKeywordRule,
)
from app.ocr.workers import (
    extract_pdf_text_worker,
    recognize_document_worker,
    verify_ocr_runtime_worker,
)
from app.services.process_jobs import (
    KillableProcessRunner,
    ProcessJobBusy,
    ProcessJobResourceLimit,
    ProcessJobTimeout,
)
from app.services.temp_files import StoredFile

logger = logging.getLogger(__name__)


def parsed_expense_payload(file_id: str, parsed: ParsedExpense) -> dict[str, object]:
    category = CATEGORY_BY_ID[parsed.category]
    return {
        "fileId": file_id,
        "type": parsed.receipt_type,
        "categoryId": parsed.category.value,
        "categoryName": category.name,
        "date": parsed.date.isoformat() if parsed.date else None,
        "description": parsed.description,
        "amount": money_string(parsed.amount) if parsed.amount is not None else None,
        "receiptCount": 1,
        "source": "ocr",
        "confidence": f"{max(0.0, min(1.0, parsed.confidence)):.2f}",
        "warnings": list(parsed.warnings),
        "status": "recognized",
        "error": None,
    }


def failed_expense_payload(file_id: str, code: str, message: str) -> dict[str, object]:
    return {
        "fileId": file_id,
        "type": "other",
        "categoryId": "other",
        "categoryName": CATEGORY_BY_ID[ExpenseCategory.OTHER].name,
        "date": None,
        "description": None,
        "amount": None,
        "receiptCount": 1,
        "source": "ocr",
        "confidence": "0.00",
        "warnings": ["MANUAL_REVIEW_REQUIRED"],
        "status": "failed",
        "error": {"code": code, "message": message},
    }


class OcrService:
    def __init__(
        self,
        settings: Settings,
        engine: LocalOcrEngine | None,
        process_runner: KillableProcessRunner,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._process_runner = process_runner

    async def ensure_ready(self) -> None:
        if not self._settings.ocr_enabled:
            return
        if self._engine is not None and self._engine.is_fake:
            self._engine.ensure_ready()
            return
        if self._settings.app_env != "production":
            return
        detection = self._settings.ocr_detection_model_dir
        recognition = self._settings.ocr_recognition_model_dir
        if detection is None or recognition is None:
            raise RuntimeError("本地 OCR 模型未就绪")
        try:
            result = await self._process_runner.run(
                verify_ocr_runtime_worker,
                {
                    "ocr_detection_model_dir": str(detection),
                    "ocr_recognition_model_dir": str(recognition),
                    "ocr_engine": self._settings.ocr_engine,
                    "ocr_cpu_threads": self._settings.ocr_cpu_threads,
                },
                self._settings.ocr_worker_limits,
                timeout_seconds=self._settings.ocr_timeout_seconds,
            )
        except (ProcessJobBusy, ProcessJobTimeout, ProcessJobResourceLimit) as exc:
            raise RuntimeError("本地 OCR 运行时自检失败") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("本地 OCR 运行时自检失败")

    async def close(self) -> None:
        return None

    async def _fake_lines(self, stored: StoredFile) -> list[OcrLine]:
        """Deterministic test/development seam; never used in production."""

        if self._engine is None or not self._engine.is_fake:
            raise ApiError("OCR_FAILED", "票据识别失败，请手工填写", 422)
        if stored.extension == "pdf":
            try:
                result = await self._process_runner.run(
                    extract_pdf_text_worker,
                    str(stored.path),
                    self._settings.pdf_limits,
                    self._settings.file_worker_limits,
                    timeout_seconds=self._settings.pdf_preflight_timeout_seconds,
                )
            except ProcessJobBusy as exc:
                raise ApiError("OCR_BUSY", "本地识别繁忙，请稍后重试", 429) from exc
            except ProcessJobTimeout as exc:
                raise ApiError("OCR_TIMEOUT", "票据识别超时，请手工填写", 504) from exc
            except ProcessJobResourceLimit as exc:
                raise ApiError("OCR_FAILED", "票据识别失败，请手工填写", 422) from exc
            if not isinstance(result, dict) or not result.get("ok"):
                raise ApiError("OCR_FAILED", "票据识别失败，请手工填写", 422)
            raw_lines = result.get("lines")
            if isinstance(raw_lines, list) and raw_lines:
                return normalized_lines(
                    [OcrLine(text=str(line), confidence=float(score)) for line, score in raw_lines]
                )
        return normalized_lines(self._engine.recognize(str(stored.path)))

    async def _ocr_lines(
        self,
        stored: StoredFile,
        *,
        prefer_pdf_text: bool = True,
    ) -> tuple[list[OcrLine], str, InvoiceQrEvidence | None]:
        if not self._settings.ocr_enabled:
            raise ApiError("OCR_DISABLED", "本地 OCR 尚未配置，可手工填写票据信息", 503)
        if self._engine is not None and self._engine.is_fake:
            return await self._fake_lines(stored), "fake", None
        detection = self._settings.ocr_detection_model_dir
        recognition = self._settings.ocr_recognition_model_dir
        if detection is None or recognition is None:
            raise ApiError("OCR_FAILED", "本地 OCR 模型未配置，请手工填写", 503)
        try:
            result = await self._process_runner.run(
                recognize_document_worker,
                str(stored.path),
                stored.extension,
                {
                    "ocr_detection_model_dir": str(detection),
                    "ocr_recognition_model_dir": str(recognition),
                    "ocr_engine": self._settings.ocr_engine,
                    "ocr_cpu_threads": self._settings.ocr_cpu_threads,
                },
                self._settings.pdf_limits,
                self._settings.ocr_worker_limits,
                prefer_pdf_text,
                timeout_seconds=self._settings.ocr_timeout_seconds,
            )
        except ProcessJobBusy as exc:
            raise ApiError("OCR_BUSY", "本地识别正在处理另一张票据，请稍后重试", 429) from exc
        except ProcessJobTimeout as exc:
            raise ApiError("OCR_TIMEOUT", "票据识别超时，请重试或手工填写", 504) from exc
        except ProcessJobResourceLimit as exc:
            raise ApiError("OCR_FAILED", "票据识别失败，请手工填写", 422) from exc
        if not isinstance(result, dict) or not result.get("ok"):
            code = (
                str(result.get("code", "OCR_FAILED")) if isinstance(result, dict) else "OCR_FAILED"
            )
            message = (
                str(result.get("message", "票据识别失败，请手工填写"))
                if isinstance(result, dict)
                else "票据识别失败，请手工填写"
            )
            status_code = int(result.get("status", 422)) if isinstance(result, dict) else 422
            if code in {"WORKER_LIMIT_SETUP_FAILED", "WORKER_RESOURCE_LIMIT"}:
                code, message, status_code = "OCR_FAILED", "票据识别失败，请手工填写", 422
            logger.warning("Local OCR failed", extra={"error_code": code})
            raise ApiError(code, message, status_code)
        raw_lines = result.get("lines")
        if not isinstance(raw_lines, list):
            raise ApiError("OCR_FAILED", "票据识别失败，请手工填写", 422)
        source = result.get("source", "paddle")
        if source not in {"pdf_text", "paddle"}:
            raise ApiError("OCR_FAILED", "票据识别失败，请手工填写", 422)
        try:
            lines = normalized_lines(
                [OcrLine(text=str(text), confidence=float(score)) for text, score in raw_lines]
            )
            return lines, str(source), invoice_qr_from_payload(result.get("invoiceQr"))
        except (TypeError, ValueError) as exc:
            raise ApiError("OCR_FAILED", "票据识别失败，请手工填写", 422) from exc

    @staticmethod
    def _has_required_fields(parsed: ParsedExpense) -> bool:
        if parsed.amount is None or parsed.date is None:
            return False
        return parsed.receipt_type != "train" or bool(parsed.description)

    @classmethod
    def _needs_pdf_ocr_fallback(
        cls,
        parsed: ParsedExpense,
        lines: list[OcrLine],
    ) -> bool:
        if not cls._has_required_fields(parsed):
            return True
        if parsed.receipt_type != "invoice":
            return False
        text = " ".join(line.text for line in lines)
        if not is_passenger_transport_text(text):
            return False
        return (
            parsed.category is ExpenseCategory.OTHER
            or parsed.description is None
            or "INVOICE_DATE_USED_AS_OCCURRENCE" in parsed.warnings
        )

    @staticmethod
    def _merge_pdf_ocr_lines(
        pdf_text_lines: list[OcrLine],
        image_ocr_lines: list[OcrLine],
    ) -> list[OcrLine]:
        """Combine complementary evidence while keeping image row order for route parsing."""

        merged: list[OcrLine] = []
        seen: set[str] = set()
        for line in (*image_ocr_lines, *pdf_text_lines):
            if line.text in seen:
                continue
            seen.add(line.text)
            merged.append(line)
        return merged

    async def recognize_file(
        self,
        stored: StoredFile,
        *,
        reference_year: int | None,
        keyword_rules: tuple[ReceiptKeywordRule, ...] | None = None,
    ) -> ParsedExpense:
        lines, source, invoice_qr = await self._ocr_lines(stored)
        if not lines:
            raise ApiError("OCR_NO_TEXT", "未识别到可用文字，请手工填写", 422)
        context = ParseContext(
            reference_year=reference_year or date.today().year,
            invoice_qr=invoice_qr,
        )
        registry = ReceiptParserRegistry(keyword_rules=keyword_rules)
        parsed = registry.parse(lines, context)
        if source != "pdf_text" or not self._needs_pdf_ocr_fallback(parsed, lines):
            return parsed

        # A PDF text layer can omit visually rendered totals, routes, or transport
        # types. Retry once with real OCR only when those omissions affect output.
        try:
            fallback_lines, _fallback_source, fallback_qr = await self._ocr_lines(
                stored,
                prefer_pdf_text=False,
            )
        except ApiError as exc:
            logger.warning("PDF text fallback OCR failed", extra={"error_code": exc.code})
            return parsed
        if not fallback_lines:
            return parsed
        if context.invoice_qr is None and fallback_qr is not None:
            context = ParseContext(reference_year=context.reference_year, invoice_qr=fallback_qr)
        combined_lines = self._merge_pdf_ocr_lines(lines, fallback_lines)
        return registry.parse(combined_lines, context)
