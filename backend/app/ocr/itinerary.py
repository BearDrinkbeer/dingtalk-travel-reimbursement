from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.domain.money import MAX_REIMBURSEMENT_AMOUNT
from app.ocr.document_evidence import extract_document_numbers
from app.ocr.types import ItinerarySummary, ItineraryTrip, OcrLine, ParsedItinerary

_DATE = re.compile(r"(?<!\d)(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?(?!\d)")
_TOTAL = re.compile(
    r"(?:行程金额合计|金额合计|合计金额|总金额|总计|合计|total(?:\s+amount)?)"
    r"\s*[:：]?\s*[¥￥]?\s*([0-9][0-9,]*(?:\.\d{1,2})?)(?![\d.]|\s*[笔项次])",
    re.I,
)
_SKIP_DATES = ("申请时间", "申请日期", "开票日期", "打印日期", "created", "issued")


def _currency(text: str) -> tuple[str | None, str | None]:
    codes = set(re.findall(r"\b(?:CNY|USD|EUR|GBP|JPY|VND|KRW|HKD|SGD|AUD|CAD|THB)\b", text, re.I))
    codes = {code.upper() for code in codes}
    yuan_text = text
    for name, code in {
        "美元": "USD",
        "日元": "JPY",
        "港元": "HKD",
        "欧元": "EUR",
        "加元": "CAD",
        "澳元": "AUD",
        "韩元": "KRW",
        "新加坡元": "SGD",
    }.items():
        if name in text:
            codes.add(code)
            yuan_text = yuan_text.replace(name, "")
    if re.search(r"人民币|\bRMB\b|元", yuan_text, re.I):
        codes.add("CNY")
    if len(codes) > 1:
        return None, "ITINERARY_CURRENCY_CONFLICT"
    if codes:
        return codes.pop(), None
    # The yen sign alone also occurs on Japanese documents.
    return None, "ITINERARY_CURRENCY_UNKNOWN"


@dataclass(frozen=True, slots=True)
class ItineraryPage:
    number: int
    lines: tuple[OcrLine, ...]
    layout_text: str
    source: Literal["pdf_text", "paddle"]


def _money(raw: str) -> Decimal | None:
    try:
        value = Decimal(raw.replace(",", ""))
        if value.is_finite() and Decimal(0) <= value <= MAX_REIMBURSEMENT_AMOUNT:
            return value.quantize(Decimal("0.01"))
    except InvalidOperation:
        pass
    return None


def _dates(text: str) -> list[date]:
    result = []
    for match in _DATE.finditer(text):
        try:
            result.append(date(*(int(value) for value in match.groups())))
        except ValueError:
            continue
    return result


def _occurrence_dates(lines: Sequence[OcrLine]) -> list[date]:
    values: list[date] = []
    skip_next = False
    for line in lines:
        found = _dates(line.text)
        if any(label in line.text.lower() for label in _SKIP_DATES):
            skip_next = not found
            continue
        if skip_next:
            skip_next = False
            if found:
                continue
        values.extend(found)
    return values


def _label_value(lines: Sequence[OcrLine], labels: str) -> str | None:
    pattern = re.compile(rf"^(?:{labels})\s*[:：]\s*(.+)$", re.I)
    for index, line in enumerate(lines):
        match = pattern.match(line.text.strip())
        if match:
            return match.group(1).strip()[:200] or None
        if re.fullmatch(rf"(?:{labels})\s*[:：]?", line.text.strip(), re.I):
            if index + 1 < len(lines):
                value = lines[index + 1].text.strip()
                if ":" not in value and "：" not in value:
                    return value[:200] or None
    return None


def _table_trips(page: ItineraryPage) -> tuple[list[ItineraryTrip], bool]:
    columns: dict[str, int] = {}
    header_positions: list[int] = []
    trips: list[ItineraryTrip] = []
    incomplete = False
    labels = {
        "date": r"日期|乘车时间|上车时间|出发时间|用车时间|date|time",
        "amount": r"金额|费用|实付|amount|fare",
        "origin": r"起点|出发地|上车地点|上车地址|origin|from",
        "destination": r"终点|到达地|下车地点|下车地址|destination|to",
        "invoiceNumbers": r"发票号码|发票号|invoice no\.?|invoice number",
        "orderNumbers": r"订单号|订单编号|order no\.?|order id|order number",
    }
    for raw in (page.layout_text or "\n".join(line.text for line in page.lines)).splitlines():
        raw_cells = [
            (match.group(), match.start())
            for match in re.finditer(r"\S+(?: \S+)*", raw.expandtabs(4))
        ]
        cells = [value for value, _position in raw_cells]
        header = {
            field: index
            for field, pattern in labels.items()
            for index, cell in enumerate(cells)
            if re.fullmatch(rf"(?:{pattern})(?:[（(]元[）)])?", cell.strip(), re.I)
        }
        if "date" in header and "amount" in header:
            columns = header
            header_positions = [position for _value, position in raw_cells]
            continue
        if not columns:
            # A continuation page can omit table headers. Do not silently sum
            # only the preceding page when a dated data row cannot be mapped.
            if (
                _DATE.search(raw)
                and len(cells) >= 3
                and re.fullmatch(r"[¥￥]?\d[\d,]*\.\d{1,2}元?", cells[-1])
            ):
                incomplete = True
            continue
        if _TOTAL.search(raw):
            continue
        if not _DATE.search(raw):
            if trips and len(raw_cells) <= 2:
                for value, position in raw_cells:
                    nearest = min(
                        range(len(header_positions)),
                        key=lambda i: abs(header_positions[i] - position),
                    )
                    for field in ("origin", "destination"):
                        if (
                            columns.get(field) == nearest
                            and len(value) <= 100
                            and not re.search(r"[:：\d]|备注|说明|合计|页", value)
                        ):
                            previous = getattr(trips[-1], field)
                            if previous:
                                trips[-1] = replace(trips[-1], **{field: (previous + value)[:200]})
            continue
        mapped = dict(enumerate(cells))
        if len(cells) != len(header_positions):
            mapped = {}
            for value, position in raw_cells:
                nearest = min(
                    range(len(header_positions)), key=lambda i: abs(header_positions[i] - position)
                )
                if nearest in mapped:
                    mapped[nearest] += " " + value
                else:
                    mapped[nearest] = value
        if any(index not in mapped for index in columns.values()):
            incomplete = True
            continue
        dates = _dates(mapped[columns["date"]])
        amount_text = mapped[columns["amount"]].strip().strip("¥￥元 ")
        amount = (
            _money(amount_text) if re.fullmatch(r"\d[\d,]*(?:\.\d{1,2})?", amount_text) else None
        )
        if len(dates) != 1 or amount is None:
            incomplete = True
            continue
        origin = mapped[columns["origin"]].strip()[:200] if "origin" in columns else None
        destination = (
            mapped[columns["destination"]].strip()[:200] if "destination" in columns else None
        )
        number_lines = [OcrLine(raw, 1)]
        for field, label in (("invoiceNumbers", "发票号码"), ("orderNumbers", "订单号")):
            if field in columns:
                number_lines.append(OcrLine(f"{label}：{mapped[columns[field]]}", 1))
        invoices, orders = extract_document_numbers(number_lines)
        trips.append(
            ItineraryTrip(
                page.number, len(trips) + 1, dates[0], amount, origin, destination, invoices, orders
            )
        )
    return trips, incomplete


def parse_itinerary_pages(
    pages: Sequence[ItineraryPage],
    *,
    page_count: int,
    reference_year: int | None,
    warnings: Sequence[str] = (),
) -> ParsedItinerary:
    """Parse evidence only; these amounts never become reimbursement charges."""

    all_lines = [line for page in pages for line in page.lines]
    text = "\n".join(line.text for line in all_lines)
    problems = list(warnings)
    recognized = bool(
        re.search(r"行程单|行程明细|出行记录|\bitinerary\b|\btrip\s+details\b", text, re.I)
    )
    if not recognized:
        problems.append("ITINERARY_NOT_RECOGNIZED")
    trips: list[ItineraryTrip] = []
    for page in pages:
        rows, incomplete = _table_trips(page)
        trips.extend(rows)
        if incomplete:
            problems.append("ITINERARY_ROWS_INCOMPLETE")
    amounts = {_money(match.group(1)) for match in _TOTAL.finditer(text)} - {None}
    amount = next(iter(amounts)) if len(amounts) == 1 else None
    if len(amounts) > 1:
        problems.append("ITINERARY_TOTAL_CONFLICT")
    row_total = sum((trip.amount for trip in trips if trip.amount is not None), Decimal(0))
    if amount is not None and trips and amount != row_total:
        problems.append("ITINERARY_TOTAL_CONFLICT")
    if not amounts and trips and not problems and len(pages) == page_count:
        amount = row_total
    if amount is None:
        problems.append("MISSING_AMOUNT")
    dates = [trip.date for trip in trips if trip.date] or _occurrence_dates(all_lines)
    if not dates:
        problems.append("MISSING_DATE")
    for line in all_lines:
        range_dates = _dates(line.text)
        if len(range_dates) == 2 and re.search(r"至|到|\bto\b|~|～", line.text, re.I):
            if range_dates[0] > range_dates[1]:
                problems.append("ITINERARY_DATE_CONFLICT")
    currency, currency_warning = _currency(text)
    if currency_warning:
        problems.append(currency_warning)
    invoices, orders = extract_document_numbers(all_lines)
    invoices = tuple(
        dict.fromkeys((*invoices, *(value for trip in trips for value in trip.invoice_numbers)))
    )[:100]
    orders = tuple(
        dict.fromkeys((*orders, *(value for trip in trips for value in trip.order_numbers)))
    )[:100]
    summary = ItinerarySummary(
        currency,
        amount,
        min(dates) if dates else None,
        max(dates) if dates else None,
        invoices,
        orders,
    )
    if not trips and len(pages) == 1 and len(set(dates)) == 1 and amount is not None:
        origin = _label_value(all_lines, r"起点|出发地|上车地点|from|origin")
        destination = _label_value(all_lines, r"终点|到达地|下车地点|to|destination")
        if origin and destination:
            trips.append(
                ItineraryTrip(
                    pages[0].number, 1, dates[0], amount, origin, destination, invoices, orders
                )
            )
    if len(pages) != page_count:
        problems.append("ITINERARY_PARTIAL")
    if any(line.confidence < 0.65 for line in all_lines):
        problems.append("LOW_OCR_CONFIDENCE")
    sources = {page.source for page in pages}
    source = next(iter(sources)) if len(sources) == 1 else "mixed" if sources else "unknown"
    return ParsedItinerary(
        source,
        page_count,
        len(pages),
        not problems,
        summary,
        tuple(trips),
        tuple(dict.fromkeys(problems)),
    )
