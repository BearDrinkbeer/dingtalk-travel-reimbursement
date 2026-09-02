from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.domain.money import MAX_REIMBURSEMENT_AMOUNT, quantize_money
from app.ocr.types import InvoiceQrEvidence

MAX_QR_PAYLOAD_LENGTH = 2048


def parse_legacy_invoice_qr(payload: str) -> InvoiceQrEvidence | None:
    """Parse the common comma-separated VAT invoice QR payload.

    Only amount and issue date enter the OCR pipeline. Invoice identifiers and
    check codes are intentionally not retained because the product does not
    perform invoice verification and should minimize sensitive data handling.
    """

    value = payload.strip()
    if not value or len(value) > MAX_QR_PAYLOAD_LENGTH:
        return None
    fields = [field.strip() for field in value.split(",")]
    if len(fields) < 7 or fields[0] != "01":
        return None
    if not fields[2].isdigit() or not fields[3].isdigit():
        return None

    try:
        amount = Decimal(fields[4])
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0 or amount > MAX_REIMBURSEMENT_AMOUNT:
        return None

    raw_date = fields[5]
    if len(raw_date) != 8 or not raw_date.isdigit():
        return None
    try:
        issue_date = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:8]))
    except ValueError:
        return None
    if not 2000 <= issue_date.year <= 2100:
        return None
    return InvoiceQrEvidence(amount=quantize_money(amount), issue_date=issue_date)


def _decoded_payloads(image: Any) -> tuple[str, ...]:
    try:
        import cv2
    except ImportError:
        return ()

    detector = cv2.QRCodeDetector()
    payloads: list[str] = []
    try:
        detected, decoded, _points, _straight = detector.detectAndDecodeMulti(image)
        if detected:
            payloads.extend(str(value) for value in decoded if value)
    except (cv2.error, TypeError, ValueError):
        pass
    if not payloads:
        try:
            value, _points, _straight = detector.detectAndDecode(image)
            if value:
                payloads.append(str(value))
        except (cv2.error, TypeError, ValueError):
            pass
    return tuple(dict.fromkeys(payloads))


def _load_image(path: Path) -> Any | None:
    try:
        import cv2
        import numpy as np

        encoded = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    except (ImportError, OSError, ValueError):
        return None


def _render_pdf(path: Path, render_dpi: int) -> Any | None:
    try:
        import numpy as np
        import pypdfium2

        document = pypdfium2.PdfDocument(path)
        try:
            if len(document) != 1:
                return None
            page = document[0]
            try:
                bitmap = page.render(scale=render_dpi / 72)
                try:
                    rgb = bitmap.to_pil().convert("RGB")
                    return np.asarray(rgb)[:, :, ::-1].copy()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            document.close()
    except (ImportError, OSError, RuntimeError, ValueError):
        return None


def decode_invoice_qr(path: Path, extension: str, *, render_dpi: int) -> InvoiceQrEvidence | None:
    """Best-effort local QR decoding; malformed or unsupported QR data is ignored."""

    try:
        image = _render_pdf(path, render_dpi) if extension == "pdf" else _load_image(path)
        if image is None:
            return None
        for payload in _decoded_payloads(image):
            evidence = parse_legacy_invoice_qr(payload)
            if evidence is not None:
                return evidence
    except MemoryError:
        raise
    except Exception:
        # QR data is supplemental. Decoder/runtime failures must not turn an
        # otherwise readable receipt into an OCR failure.
        return None
    return None


def invoice_qr_to_payload(evidence: InvoiceQrEvidence | None) -> dict[str, str] | None:
    if evidence is None:
        return None
    payload: dict[str, str] = {}
    if evidence.amount is not None:
        payload["amount"] = format(evidence.amount, ".2f")
    if evidence.issue_date is not None:
        payload["issueDate"] = evidence.issue_date.isoformat()
    return payload or None


def invoice_qr_from_payload(payload: object) -> InvoiceQrEvidence | None:
    if not isinstance(payload, dict):
        return None
    raw_amount = payload.get("amount")
    raw_date = payload.get("issueDate")
    amount: Decimal | None = None
    issue_date: date | None = None
    if isinstance(raw_amount, str):
        try:
            candidate = Decimal(raw_amount)
            if (
                candidate.is_finite()
                and 0 <= candidate <= MAX_REIMBURSEMENT_AMOUNT
                and candidate.as_tuple().exponent >= -2
            ):
                amount = quantize_money(candidate)
        except (InvalidOperation, ValueError):
            pass
    if isinstance(raw_date, str):
        try:
            issue_date = date.fromisoformat(raw_date)
        except ValueError:
            pass
        if issue_date is not None and not 2000 <= issue_date.year <= 2100:
            issue_date = None
    if amount is None and issue_date is None:
        return None
    return InvoiceQrEvidence(amount=amount, issue_date=issue_date)
