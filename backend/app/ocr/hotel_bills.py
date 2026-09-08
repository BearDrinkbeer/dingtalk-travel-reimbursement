"""Best-effort stay summaries; these hints never create a reimbursable expense."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation

from app.domain.money import money_string
from app.ocr.itinerary import ItineraryPage

_DATE = r"(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})日?"
_MONEY = r"[¥￥$€£]?\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)"


def hotel_bill_details(pages: Sequence[ItineraryPage]) -> dict[str, object]:
    text = "\n".join(
        page.layout_text or "\n".join(line.text for line in page.lines) for page in pages
    )
    lines_text = "\n".join(line.text for page in pages for line in page.lines)
    text = text + "\n" + lines_text

    def labeled(pattern: str, value: str) -> str | None:
        match = re.search(rf"(?:{pattern})\s*[:：]?\s*{value}", text, re.I)
        return match.group(1).strip() if match else None

    def stay_date(pattern: str) -> str | None:
        match = re.search(rf"(?:{pattern})\s*(?:日期|时间)?\s*[:：]?\s*{_DATE}", text, re.I)
        if not match:
            return None
        try:
            return date(*map(int, match.groups())).isoformat()
        except ValueError:
            return None

    def amount(pattern: str) -> str | None:
        raw = labeled(pattern, _MONEY)
        try:
            return money_string(Decimal(raw.replace(",", ""))) if raw else None
        except (InvalidOperation, ValueError):
            return None

    check_in = stay_date(r"入住|抵店|到店|check[ -]?in|arrival")
    check_out = stay_date(r"退房|离店|check[ -]?out|departure")
    nights = labeled(r"间夜|晚数|住宿天数|入住天数|nights", r"(\d{1,3})")
    if not nights and check_in and check_out:
        duration = (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days
        nights = str(duration) if 0 < duration < 1000 else None
    currency = labeled(r"币种|currency", r"([A-Z]{3}|人民币)")
    if currency == "人民币":
        currency = "CNY"
    if not currency:
        match = re.search(r"\b(CNY|RMB|USD|EUR|GBP|HKD|JPY|SGD|AUD|CAD|VND)\b", text, re.I)
        currency = match.group(1).upper() if match else "CNY" if "人民币" in text else None
    if currency == "RMB":
        currency = "CNY"
    details: dict[str, object] = {
        "guest": labeled(
            r"住客姓名|宾客姓名|客人姓名|入住人|住客|宾客|客人|姓名|guest(?:\s+name)?",
            r"([^\n:：]{1,80})",
        ),
        "checkIn": check_in,
        "checkOut": check_out,
        "nights": int(nights) if nights else None,
        "nightlyRate": amount(r"每日房价|房价|单价|nightly\s+rate|room\s+rate|rate"),
        "total": amount(r"账单合计|总金额|需支付|合计|总计|total(?:\s+amount)?"),
        "currency": currency,
    }
    # Native hotel tables often put all labels on one row and values below it.
    # Recover the complete, known column signature rather than pairing a label
    # with the next label or guessing from arbitrary dates elsewhere in a bill.
    if all(
        label in text for label in ("房型", "单价", "姓名", "入住时间", "离店时间", "间夜", "价格")
    ):
        row_pattern = (
            r"(?:^|\n)\s*\d+\s+\S+\s+(\d+(?:\.\d{1,2})?)\s+([^\s]+)\s+"
            + _DATE
            + r"\s+"
            + _DATE
            + r"\s+(\d{1,3})\s+(\d+(?:\.\d{1,2})?)"
        )
        rows = list(re.finditer(row_pattern, text))
        # The text/layout views can contain the same table twice.
        unique_rows = {match.groups() for match in rows}
        if len(unique_rows) == 1:
            values = next(iter(unique_rows))
            try:
                details.update(
                    guest=values[1],
                    checkIn=date(*map(int, values[2:5])).isoformat(),
                    checkOut=date(*map(int, values[5:8])).isoformat(),
                    nights=int(values[8]),
                    nightlyRate=money_string(Decimal(values[0])),
                    total=details["total"] or money_string(Decimal(values[9])),
                )
            except (ValueError, InvalidOperation):
                pass
        elif len(unique_rows) > 1:
            # Multiple guests/rates cannot be represented by one stay hint.
            details.update(guest=None, checkIn=None, checkOut=None, nights=None, nightlyRate=None)
    if details["guest"] and re.search(r"入住|离店|间夜|单价|房价", str(details["guest"])):
        details["guest"] = None
    details["warnings"] = ["HOTEL_BILL_REVIEW_REQUIRED"]
    if any(value is None for value in details.values()):
        details["warnings"].append("HOTEL_BILL_INCOMPLETE")
    return details
