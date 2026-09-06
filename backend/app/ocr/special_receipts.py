from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from app.domain.categories import ExpenseCategory
from app.domain.money import quantize_money
from app.ocr.extractors import average_confidence, extract_date
from app.ocr.types import OcrLine, ParseContext, ParsedExpense

_TAXI_LABELS = ("实收金额", "应付金额", "合计金额", "金额", "实收")
_TAXI_VALUE = re.compile(r"[：:¥￥\s]*(\d{1,6}(?:\.\d{1,2})?)[元圆¥￥\s]*$")
_CURRENCY_CODES = re.compile(
    r"(?<![A-Z])(?:USD|EUR|GBP|JPY|VND|HKD|MOP|TWD|KRW|SGD|MYR|THB|IDR|PHP|"
    r"INR|AUD|NZD|CAD|CHF|AED|SAR|QAR|TRY|BRL|MXN|ZAR|RUB|SEK|NOK|DKK|PLN|"
    r"CZK|HUF|ILS|CNY|RMB)(?![A-Z])"
)
_CURRENCY_SYMBOLS = (
    ("€", "EUR"),
    ("£", "GBP"),
    ("₩", "KRW"),
    ("₫", "VND"),
    ("฿", "THB"),
    ("₹", "INR"),
)
_DOLLAR_CODES = (
    ("US$", "USD"),
    ("HK$", "HKD"),
    ("S$", "SGD"),
    ("A$", "AUD"),
    ("C$", "CAD"),
    ("NZ$", "NZD"),
    ("NT$", "TWD"),
)
_FOREIGN_HEADING = re.compile(r"\b(?:invoice|receipt|hotel|khach san|hoa don)\b")
_HOTEL_HEADING = re.compile(r"\b(?:hotel|khach san|room|accommodation|lodging)\b")
_TOTAL_LABELS = (
    re.compile(r"\bgrand\s+total\b"),
    re.compile(r"\b(?:total\s+(?:amount|payable)|tong\s+(?:tien|cong))\b"),
    re.compile(r"\btotal\b"),
    re.compile(r"\bamount\s+due\b"),
)
_NON_TOTAL_PREFIX = re.compile(r"\b(?:sub|sub\s*total|tax|vat|fee|unit|quantity)\b")
_NEXT_FIELD = re.compile(r"\b(?:tax|vat|sub\s*total|unit|quantity|change|paid|balance)\b")
_NUMBER_TOKEN = re.compile(r"(?<![\w.,])\d+(?:[.,'’ \u00a0\u202f]\d+)*(?![\w.,])")
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,4})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{1,4})(?!\d)")


def latin_text(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    ).replace("đ", "d")


def currency_evidence(lines: list[OcrLine]) -> tuple[bool, str | None]:
    """Detect foreign currency without assigning ambiguous dollar/yen symbols."""

    text = "\n".join(line.text for line in lines)
    upper = text.upper()
    codes = {"CNY" if code == "RMB" else code for code in _CURRENCY_CODES.findall(upper)}
    codes.update(code for symbol, code in _CURRENCY_SYMBOLS if symbol in text)
    codes.update(
        code
        for symbol, code in _DOLLAR_CODES
        if re.search(r"(?<![A-Z])" + re.escape(symbol), upper)
    )
    if any(code != "CNY" for code in codes):
        return True, next(iter(codes)) if len(codes) == 1 else None
    normalized = latin_text(text)
    if "$" in text:
        return True, None
    # A foreign-looking receipt with no currency is kept out of the CNY amount
    # field as well. Explicit CNY/RMB remains domestic even with English labels.
    if (
        not codes
        and _FOREIGN_HEADING.search(normalized)
        and any(label.search(normalized) for label in _TOTAL_LABELS)
    ):
        return True, None
    return False, None


def parse_foreign_number(raw: str) -> Decimal | None:
    """Parse a complete number, accepting grouping only when every group is valid."""

    token = raw.strip().replace("’", "'").replace("\u00a0", " ").replace("\u202f", " ")
    decimal_separator: str | None = None
    punctuation = {separator for separator in (",", ".") if separator in token}
    if punctuation:
        last = max(token.rfind(separator) for separator in punctuation)
        fraction = token[last + 1 :]
        if len(fraction) in {1, 2} and fraction.isdigit():
            decimal_separator = token[last]
            if token.count(decimal_separator) != 1:
                return None
        elif len(punctuation) > 1:
            return None
    if decimal_separator:
        integer, fraction = token.rsplit(decimal_separator, 1)
    else:
        integer, fraction = token, ""
    separators = {char for char in integer if not char.isdigit()}
    if separators:
        if len(separators) != 1 or not separators <= {",", ".", "'", " "}:
            return None
        groups = integer.split(next(iter(separators)))
        if not (1 <= len(groups[0]) <= 3 and all(len(group) == 3 for group in groups[1:])):
            return None
        integer = "".join(groups)
    if not integer.isdigit() or len(integer) > 12:
        return None
    try:
        return quantize_money(Decimal(integer + (f".{fraction}" if fraction else "")))
    except (InvalidOperation, ValueError):
        return None


def _number_candidates(text: str) -> list[Decimal]:
    text = _CURRENCY_CODES.sub(" ", text.upper())
    text = re.sub(r"(?<![A-Z])(?:US|HK|S|A|C|NZ|NT)\$", " ", text)
    text = re.sub(r"[€£₩₫฿₹$¥￥]", " ", text)
    values: list[Decimal] = []
    for match in _NUMBER_TOKEN.finditer(text):
        before = text[: match.start()].rstrip()
        after = text[match.end() :].lstrip()
        if (before and before[-1] in "-−(/") or (after and after[0] in "%/)-−"):
            continue
        value = parse_foreign_number(match.group())
        if value is not None:
            values.append(value)
    return values


def _foreign_total(lines: list[OcrLine]) -> tuple[Decimal | None, bool]:
    texts = [latin_text(line.text) for line in lines]
    for label in _TOTAL_LABELS:
        candidates: set[Decimal] = set()
        for index, text in enumerate(texts):
            match = label.search(text)
            if match is None or _NON_TOTAL_PREFIX.search(text[: match.start()]):
                continue
            suffix = _NEXT_FIELD.split(text[match.end() :], maxsplit=1)[0]
            values = _number_candidates(suffix)
            if not values:
                # OCR can put a total label, numeric value and currency on
                # separate lines. Never scan through another invoice field.
                for following in texts[index + 1 : index + 3]:
                    cleaned = _CURRENCY_CODES.sub("", following.upper())
                    cleaned = re.sub(r"[€£₩₫฿₹$¥￥:：()\s]", "", cleaned)
                    if cleaned and not re.fullmatch(r"[\d.,'’]+", cleaned):
                        break
                    values = _number_candidates(following)
                    if values:
                        break
            candidates.update(values)
        if len(candidates) == 1:
            return next(iter(candidates)), False
        if candidates:
            return None, True
    return None, False


def _foreign_date(lines: list[OcrLine]) -> tuple[date | None, tuple[str, ...]]:
    values: set[date] = set()
    ambiguous = False
    for line in lines:
        for match in _NUMERIC_DATE.finditer(line.text):
            first, middle, last = (int(value) for value in match.groups())
            if len(match.group(1)) == 4:
                year, month, day = first, middle, last
            elif len(match.group(3)) == 4:
                year = last
                if first <= 12 and middle <= 12 and first != middle:
                    ambiguous = True
                    continue
                day, month = (first, middle) if first > 12 else (middle, first)
            else:
                ambiguous = True
                continue
            try:
                values.add(date(year, month, day))
            except ValueError:
                continue
    if ambiguous:
        return None, ("AMBIGUOUS_RECEIPT_DATE",)
    if len(values) > 1:
        return None, ("MULTIPLE_RECEIPT_DATES",)
    return (next(iter(values)) if values else None), ()


class PhysicalTaxiReceiptParser:
    def match(self, lines: list[OcrLine]) -> float:
        text = "".join("".join(line.text.split()) for line in lines)
        meter_signals = sum(label in text for label in ("车号", "工号", "里程", "等候", "单价"))
        if meter_signals >= 3 or (meter_signals >= 2 and "出租" in text):
            return 0.98
        return 0.0

    def parse(self, lines: list[OcrLine], context: ParseContext) -> ParsedExpense:
        texts = ["".join(line.text.split()) for line in lines]
        amount = None
        for label in _TAXI_LABELS:
            candidates: set[Decimal] = set()
            for index, text in enumerate(texts):
                if label not in text:
                    continue
                suffix = text.split(label, 1)[1]
                if not suffix.strip("：:") and index + 1 < len(texts):
                    suffix = texts[index + 1]
                match = _TAXI_VALUE.fullmatch(suffix)
                if match:
                    candidates.add(Decimal(match.group(1)).quantize(Decimal("0.01")))
            if candidates:
                amount = next(iter(candidates)) if len(candidates) == 1 else None
                break
        parsed_date = extract_date(lines, context.reference_year, ("日期", "乘车日期"))
        warnings = []
        if amount is None:
            warnings.append("MISSING_AMOUNT")
        if parsed_date is None:
            warnings.append("MISSING_DATE")
        if average_confidence(lines) < 0.65:
            warnings.append("LOW_OCR_CONFIDENCE")
        return ParsedExpense(
            receipt_type="taxi_receipt",
            category=ExpenseCategory.LOCAL_TRANSPORT,
            date=parsed_date,
            description="出租车车费",
            amount=amount,
            confidence=min(average_confidence(lines), self.match(lines)),
            warnings=tuple(warnings),
            transport_type="taxi",
        )


class ForeignReceiptParser:
    def match(self, lines: list[OcrLine]) -> float:
        return 1.0 if currency_evidence(lines)[0] else 0.0

    def parse(self, lines: list[OcrLine], context: ParseContext) -> ParsedExpense:
        _foreign, currency = currency_evidence(lines)
        original_amount, conflicting_amounts = _foreign_total(lines)
        parsed_date, date_warnings = _foreign_date(lines)
        text = latin_text(" ".join(line.text for line in lines))
        is_hotel = bool(_HOTEL_HEADING.search(text))
        warnings = ["FOREIGN_CURRENCY_REQUIRES_CNY_AMOUNT", "MANUAL_REVIEW_REQUIRED"]
        if currency is None:
            warnings.append("CURRENCY_REQUIRES_REVIEW")
        if original_amount is None:
            warnings.append("MISSING_ORIGINAL_AMOUNT")
        if conflicting_amounts:
            warnings.append("AMBIGUOUS_ORIGINAL_AMOUNT")
        if parsed_date is None:
            warnings.append("MISSING_DATE")
        if average_confidence(lines) < 0.65:
            warnings.append("LOW_OCR_CONFIDENCE")
        return ParsedExpense(
            receipt_type="foreign_receipt",
            category=ExpenseCategory.LODGING if is_hotel else ExpenseCategory.OTHER,
            date=parsed_date,
            description="境外住宿费" if is_hotel else "境外票据",
            amount=None,
            confidence=min(average_confidence(lines), 0.85),
            warnings=tuple([*warnings, *date_warnings]),
            transport_type="hotel" if is_hotel else "other",
            original_currency=currency,
            original_amount=original_amount,
        )
