"""Shared PDF sufficiency and complementary evidence rules for receipt parsing."""

from app.domain.categories import ExpenseCategory
from app.ocr.parsers import is_passenger_transport_text
from app.ocr.types import OcrLine, ParsedExpense


def needs_pdf_ocr_fallback(parsed: ParsedExpense, lines: list[OcrLine]) -> bool:
    if parsed.receipt_type == "foreign_receipt":
        # CNY is deliberately empty for foreign receipts. Only a missing original
        # amount warrants another pass, not ambiguous dates/currency.
        return parsed.original_amount is None
    if parsed.amount is None or parsed.date is None:
        return True
    if parsed.receipt_type == "train":
        return not parsed.description
    if parsed.receipt_type != "invoice":
        return False
    if not is_passenger_transport_text(" ".join(line.text for line in lines)):
        return False
    return (
        parsed.category is ExpenseCategory.OTHER
        or parsed.description is None
        or "INVOICE_DATE_USED_AS_OCCURRENCE" in parsed.warnings
    )


def merge_pdf_ocr_lines(
    pdf_text_lines: list[OcrLine], image_ocr_lines: list[OcrLine]
) -> list[OcrLine]:
    """Keep image row order while preserving fields only present in native text."""
    merged: list[OcrLine] = []
    seen: set[str] = set()
    for line in (*image_ocr_lines, *pdf_text_lines):
        if line.text not in seen:
            seen.add(line.text)
            merged.append(line)
    return merged
