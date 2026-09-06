from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.errors import ApiError
from app.ocr.engine import OcrRuntimeError, PaddleLocalOcrEngine
from app.ocr.itinerary import ItineraryPage, parse_itinerary_pages
from app.ocr.pdf_inspection import PdfLimits, inspect_single_page_pdf
from app.ocr.types import LocalOcrEngine, OcrLine
from app.ocr.workers import WorkerOcrSettings, apply_worker_limits

MAX_ITINERARY_PAGES = 30
MAX_SCANNED_PAGES = 5
MAX_TEXT_CHARACTERS = 100_000


def _native_page_has_evidence(text: str) -> bool:
    return len("".join(text.split())) >= 20 and bool(
        re.search(
            r"20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}|(?:合计|金额|total|amount)\s*[:：]?\s*[¥￥]?\d",
            text,
            re.I,
        )
    )


def recognize_itinerary_worker(
    path: str,
    extension: str,
    raw_ocr_settings: dict[str, Any],
    raw_pdf_limits: dict[str, int],
    raw_worker_limits: dict[str, int],
    reference_year: int | None,
    fake_engine: LocalOcrEngine | None = None,
) -> dict[str, object]:
    """One bounded process for the entire document; raw text never leaves it."""

    try:
        apply_worker_limits(raw_worker_limits)
        limits = PdfLimits(**raw_pdf_limits)
        reader = None
        if extension == "pdf":
            inspection = inspect_single_page_pdf(
                Path(path), limits, extract_text=False, max_pages=MAX_ITINERARY_PAGES
            )
            reader = PdfReader(path, strict=True)
        page_count = len(reader.pages) if reader is not None else 1
        pages: list[ItineraryPage] = []
        problems: list[str] = []
        scanned_count = 0
        characters = 0
        engine = fake_engine
        for index in range(page_count):
            text, layout = "", ""
            if reader is not None:
                pdf_logger = logging.getLogger("pypdf")
                previous = pdf_logger.level
                try:
                    pdf_logger.setLevel(logging.ERROR)
                    try:
                        text = reader.pages[index].extract_text() or ""
                        layout = reader.pages[index].extract_text(extraction_mode="layout") or ""
                    except (PdfReadError, OSError, ValueError, TypeError, KeyError, OverflowError):
                        text, layout = "", ""
                finally:
                    pdf_logger.setLevel(previous)
            source = "pdf_text"
            native_enough = _native_page_has_evidence(text)
            if native_enough and reader is not None and inspection.embedded_images:
                candidate = ItineraryPage(
                    index + 1,
                    tuple(OcrLine(line.strip(), 1) for line in text.splitlines() if line.strip()),
                    layout,
                    "pdf_text",
                )
                # Native headings/totals over a scanned table do not mean that
                # its rows have been read. Scan within the same five-page cap.
                native_enough = bool(
                    parse_itinerary_pages(
                        [candidate], page_count=1, reference_year=reference_year
                    ).trips
                )
            if native_enough:
                lines = tuple(
                    OcrLine(line.strip(), 1) for line in text.splitlines() if line.strip()
                )
            else:
                if scanned_count >= MAX_SCANNED_PAGES:
                    problems.append("ITINERARY_OCR_PAGE_LIMIT")
                    continue
                scanned_count += 1
                if engine is None:
                    detection = raw_ocr_settings.get("ocr_detection_model_dir")
                    recognition = raw_ocr_settings.get("ocr_recognition_model_dir")
                    if not detection or not recognition:
                        raise OcrRuntimeError("本地 OCR 模型未配置")
                    engine = PaddleLocalOcrEngine(
                        WorkerOcrSettings(
                            Path(detection),
                            Path(recognition),
                            str(raw_ocr_settings["ocr_engine"]),
                            int(raw_ocr_settings["ocr_cpu_threads"]),
                        )
                    )
                source = "paddle"
                if engine.is_fake:
                    lines = tuple(engine.recognize(path))
                    layout = "\n".join(line.text for line in lines)
                elif reader is not None:
                    import numpy as np
                    import pypdfium2 as pdfium

                    document = pdfium.PdfDocument(path)
                    try:
                        page = document[index]
                        try:
                            bitmap = page.render(scale=limits.render_dpi / 72)
                            try:
                                pixels = np.asarray(bitmap.to_pil().convert("RGB"))
                                recognized_lines, layout = engine.recognize_itinerary(pixels)
                                lines = tuple(recognized_lines)
                            finally:
                                bitmap.close()
                        finally:
                            page.close()
                    finally:
                        document.close()
                else:
                    recognized_lines, layout = engine.recognize_itinerary(path)
                    lines = tuple(recognized_lines)
            characters += sum(len(line.text) for line in lines) + len(layout)
            if characters > MAX_TEXT_CHARACTERS:
                problems.append("ITINERARY_TEXT_LIMIT")
                break
            pages.append(ItineraryPage(index + 1, lines, layout, source))
        return {
            "ok": True,
            "parsed": parse_itinerary_pages(
                pages,
                page_count=page_count,
                reference_year=reference_year,
                warnings=problems,
            ),
        }
    except ApiError as exc:
        return {"ok": False, "code": exc.code, "message": exc.message, "status": exc.status_code}
    except (MemoryError, RecursionError):
        return {
            "ok": False,
            "code": "OCR_FAILED",
            "message": "行程单超过本地处理限制",
            "status": 422,
        }
    except (OcrRuntimeError, PdfReadError, OSError, ValueError, TypeError, KeyError, OverflowError):
        return {
            "ok": False,
            "code": "OCR_FAILED",
            "message": "行程单识别失败，请人工关联",
            "status": 422,
        }
