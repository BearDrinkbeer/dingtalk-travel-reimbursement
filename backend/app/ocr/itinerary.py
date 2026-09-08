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

_DATE = re.compile(r"(?<!\d)(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?(?:(?!\d)|(?=\d{2}:\d{2}))")
_TOTAL = re.compile(
    r"(?:行程金额合计|金额合计|合计金额|总金额|总计(?:可开票金额)?|合计|total(?:\s+amount)?)"
    r"\s*[:：]?\s*[¥￥]?\s*([0-9][0-9,]*(?:\.\d{1,2})?)(?![\d.]|\s*[笔项次])",
    re.I,
)
_SKIP_DATES = ("申请时间", "申请日期", "开票日期", "打印日期", "created", "issued")
_TRIP_DATE_LABEL = re.compile(r"行程(?:起止)?(?:日期|时间)|乘车日期|trip\s+date", re.I)
_TABLE_LABEL = re.compile(
    r"起点|终点|(?:可开票|实付)?金额(?:[\[（(]元[\]）)])?|里程.*|城市|所在城市|备注"
)


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
    # This identified mainland ride-hailing template prints only the currency
    # symbol. Explicit foreign currency above always takes precedence; a title
    # or yen sign alone is not enough to infer CNY.
    if (
        all(
            label in text
            for label in (
                "百度地图打车行程单",
                "BAIDU MAP ITINERARY",
                "用车时间",
                "服务方",
                "车型",
                "城市",
                "起点",
                "终点",
                "实付金额",
            )
        )
        and re.search(r"哈啰出行|曹操出行", text)
        and re.search(r"[¥￥]\s*\d", text)
    ):
        return "CNY", None
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
        trip_label = _TRIP_DATE_LABEL.search(line.text)
        found = _dates(line.text[trip_label.end() :] if trip_label else line.text)
        if trip_label:
            skip_next = False
            values.extend(found)
            continue
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
            value = match.group(1).strip()
            if not _TABLE_LABEL.fullmatch(value):
                return value[:200] or None
        if re.fullmatch(rf"(?:{labels})\s*[:：]?", line.text.strip(), re.I):
            if index + 1 < len(lines):
                value = lines[index + 1].text.strip()
                if ":" not in value and "：" not in value and not _TABLE_LABEL.fullmatch(value):
                    return value[:200] or None
    return None


def _table_trips(
    page: ItineraryPage, inherited_dates: Sequence[date] = ()
) -> tuple[list[ItineraryTrip], bool]:
    columns: dict[str, int] = {}
    header_positions: list[int] = []
    header_cells: list[str] = []
    trips: list[ItineraryTrip] = []
    incomplete = False
    document_dates = _occurrence_dates(page.lines) or list(inherited_dates)
    document_years = {value.year for value in document_dates}
    pending_route: dict[str, str] = {}
    pending_amount_position: int | None = None
    labels = {
        "date": r"日期|乘车时间|上车时间|出发时间|用车时间|date|time",
        "amount": r"可开票金额|实付金额|金额|费用|实付|amount|fare",
        "route": r"起点[/／]终点",
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
            if re.fullmatch(rf"(?:{pattern})(?:[\[（(]元[\]）)])?", cell.strip(), re.I)
        }
        # OCR may put the amount heading one visual line above the other headers.
        if "amount" in header and "date" not in header:
            pending_amount_position = raw_cells[header["amount"]][1]
            continue
        if "date" in header and "amount" not in header and pending_amount_position is not None:
            raw_cells = [
                (value, position)
                for value, position in raw_cells
                if not re.fullmatch(r"[\[（(]元[\]）)]", value.strip())
            ]
            raw_cells.append(("金额", pending_amount_position))
            raw_cells.sort(key=lambda entry: entry[1])
            cells = [value for value, _ in raw_cells]
            header = {
                field: index
                for field, pattern in labels.items()
                for index, cell in enumerate(cells)
                if re.fullmatch(rf"(?:{pattern})(?:[\[（(]元[\]）)])?", cell.strip(), re.I)
            }
        if "date" in header and "amount" in header:
            columns = header
            header_positions = [position for _value, position in raw_cells]
            header_cells = cells
            pending_amount_position = None
            pending_route = {}
            continue
        if not header_positions:
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
        partial_date = re.search(r"(?<![\d-])(\d{2})-(\d{2})(?=\s+\d{2}:\d{2})", raw)
        resolved_partial_date = None
        if not _DATE.search(raw) and partial_date and len(document_years) == 1:
            candidate = f"{next(iter(document_years))}-{partial_date.group()}"
            if _dates(candidate) and min(document_dates) <= _dates(candidate)[0] <= max(
                document_dates
            ):
                resolved_partial_date = _dates(candidate)[0]
        if not _DATE.search(raw) and resolved_partial_date is None:
            if len(raw_cells) <= 3 and not re.search(
                r"以上为|以下为|实际报销|^\s*\d+[.、]|^\s*\*|页码|第\s*\d+\s*页", raw
            ):
                for value, position in raw_cells:
                    nearest = min(
                        range(len(header_positions)),
                        key=lambda i: abs(header_positions[i] - position),
                    )
                    for field in ("origin", "destination", "route"):
                        if (
                            (
                                columns.get(field) == nearest
                                or (
                                    field == "route"
                                    and field in columns
                                    and re.search(r"[/／]", value)
                                )
                            )
                            and len(value) <= 100
                            and not re.search(r"[:：]|备注|说明|合计|页", value)
                            and not _TABLE_LABEL.fullmatch(value)
                        ):
                            if trips:
                                target = "destination" if field == "route" else field
                                previous = getattr(trips[-1], target)
                                if previous:
                                    trips[-1] = replace(
                                        trips[-1], **{target: (previous + value)[:200]}
                                    )
                            else:
                                pending_route[field] = pending_route.get(field, "") + value
            continue
        mapped = dict(enumerate(cells))
        empty_trailing_note = (
            len(cells) == len(header_positions) - 1
            and header_cells[-1] in {"备注", "说明"}
            and max(columns.values()) < len(cells)
            and raw_cells[-1][1] < (header_positions[-1] + header_positions[-2]) / 2
        )
        if len(cells) != len(header_positions) and not empty_trailing_note:
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
        if (
            not dates
            and resolved_partial_date
            and partial_date
            and partial_date.group() in mapped[columns["date"]]
        ):
            dates = [resolved_partial_date]
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
        if "route" in columns:
            route = pending_route.pop("route", "") + mapped[columns["route"]].strip()
            endpoints = re.split(r"[/／]", route)
            if len(endpoints) != 2 or not all(endpoints):
                incomplete = True
                continue
            origin, destination = endpoints
        if origin:
            origin = pending_route.pop("origin", "") + origin
        if destination:
            destination = pending_route.pop("destination", "") + destination
        # A native PDF can concatenate the mileage into the destination column.
        # Require the OCR fallback rather than accepting a corrupted route.
        if (
            re.search(r"里程|\bmileage\b", page.layout_text, re.I)
            and destination
            and re.search(r"\d+\.\d+$", destination)
        ):
            incomplete = True
            continue
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
    # Only inherit an explicit, unambiguous travel range from this document,
    # never the current year or an application/issue date.
    ranges = set()
    for line in all_lines:
        # PDF text extraction can place the application date before the range
        # on the same line. Only inspect the text after the travel-range label.
        labeled = re.split(
            r"行程起止日期|行程日期|用车日期|travel dates|trip dates",
            line.text,
            maxsplit=1,
            flags=re.I,
        )
        if len(labeled) == 2 and len(dates_in_range := _dates(labeled[1])) == 2:
            ranges.add(tuple(dates_in_range))
    inherited_dates = next(iter(ranges)) if len(ranges) == 1 else ()
    if inherited_dates and (
        inherited_dates[0] > inherited_dates[1]
        or inherited_dates[0].year != inherited_dates[1].year
    ):
        inherited_dates = ()
    trips: list[ItineraryTrip] = []
    for page in pages:
        rows, incomplete = _table_trips(page, inherited_dates)
        trips.extend(rows)
        if (
            not rows
            and re.search(r"起点|origin", page.layout_text, re.I)
            and re.search(r"终点|destination", page.layout_text, re.I)
            and re.search(r"上车时间|用车时间|序号", page.layout_text)
        ):
            incomplete = True
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
