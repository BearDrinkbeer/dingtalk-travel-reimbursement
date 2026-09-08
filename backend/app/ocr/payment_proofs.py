"""Optional manual-entry hints from payment evidence; never an expense parser."""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from app.domain.money import money_string, quantize_money
from app.ocr.extractors import extract_date
from app.ocr.itinerary import ItineraryPage
from app.ocr.types import OcrLine

_AMOUNT_LABELS = ("转账金额", "支付金额", "付款金额", "交易金额", "实付金额", "实付")
_DATE_LABELS = (
    "交易时间",
    "付款时间",
    "支付时间",
    "转账时间",
    "业务日期",
    "付款日期",
    "支付日期",
    "交易日期",
)
_DESCRIPTION_LABELS = ("附言", "用途", "付款说明", "摘要", "商品说明", "商品名称", "备注")
_FIELD_LABELS = (
    *_AMOUNT_LABELS,
    *_DATE_LABELS,
    *_DESCRIPTION_LABELS,
    "收款",
    "付款",
    "币种",
    "交易渠道",
    "交易单号",
    "订单号",
    "余额",
)
_AMOUNT = re.compile(r"[¥￥]?\s*(\d{1,9}(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:元|人民币|CNY|RMB)?$")
_FOREIGN_CURRENCY = re.compile(
    r"\b(?:USD|VND|EUR|GBP|HKD|JPY|SGD|AUD|CAD)\b|[$€£]|美元|越南盾|港币", re.I
)
_CNY = re.compile(r"人民币|[¥￥]|\b(?:CNY|RMB)\b", re.I)
_NON_EXPENDITURE = re.compile(
    r"退款|退回|退还|撤销|失败|已关闭|已取消|待支付|待付款|需支付|refund|failed|cancelled", re.I
)


def _labeled_values(texts: list[str], labels: tuple[str, ...]) -> list[str]:
    """Accept a label's own value or the immediately following OCR line only."""
    result = []
    for label in labels:
        for index, text in enumerate(texts):
            if not text.startswith(label):
                continue
            value = text[len(label) :].strip(" :：")
            if not value and index + 1 < len(texts):
                value = texts[index + 1]
                if any(value.startswith(other) for other in _FIELD_LABELS):
                    continue
            if value:
                result.append(value)
    return result


def payment_proof_details(
    pages: Sequence[ItineraryPage], *, reference_year: int
) -> dict[str, str | None]:
    """Leave ambiguous/foreign amounts blank because the manual form is in RMB."""
    empty: dict[str, str | None] = {
        "amount": None,
        "date": None,
        "description": None,
        "categoryId": None,
    }
    if len(pages) != 1:
        return empty
    texts = [" ".join(line.text.split()) for line in pages[0].lines if line.text.strip()]
    text = "\n".join(texts)
    currencies = _labeled_values(texts, ("币种",))
    foreign_currency = any(value.upper() not in {"人民币", "CNY", "RMB"} for value in currencies)
    amounts: set[Decimal] = set()
    if (
        _CNY.search(text)
        and not foreign_currency
        and not _FOREIGN_CURRENCY.search(text)
        and not _NON_EXPENDITURE.search(text)
    ):
        for value in _labeled_values(texts, _AMOUNT_LABELS):
            match = _AMOUNT.fullmatch(value)
            if not match:
                continue
            try:
                amount = quantize_money(Decimal(match.group(1).replace(",", "")))
            except (InvalidOperation, ValueError):
                continue
            if amount > 0:
                amounts.add(amount)
    date_values = _labeled_values(texts, _DATE_LABELS)
    dates = {
        value
        for text in (date_values or texts)
        if (value := extract_date([OcrLine(text, 1)], reference_year)) is not None
    }
    descriptions = set(_labeled_values(texts, _DESCRIPTION_LABELS))
    return {
        "amount": money_string(next(iter(amounts))) if len(amounts) == 1 else None,
        "date": next(iter(dates)).isoformat() if len(dates) == 1 else None,
        "description": next(iter(descriptions))[:200] if len(descriptions) == 1 else None,
        # A transfer does not establish the business category. Ask the employee.
        "categoryId": None,
    }
