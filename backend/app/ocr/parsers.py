from __future__ import annotations

from dataclasses import replace

from app.domain.categories import ExpenseCategory
from app.ocr.document_evidence import extract_document_numbers, extract_rail_type
from app.ocr.extractors import (
    PASSENGER_OCCURRENCE_DATE_PREFIX,
    average_confidence,
    extract_amount,
    extract_date,
    extract_invoice_amount,
    extract_passenger_occurrence_date,
    extract_passenger_transport_type,
    extract_route,
    extract_train_travel_date,
    uses_invoice_date_as_occurrence,
)
from app.ocr.keyword_defaults import DEFAULT_RECEIPT_KEYWORD_RULES
from app.ocr.special_receipts import ForeignReceiptParser, PhysicalTaxiReceiptParser
from app.ocr.types import (
    BaseReceiptParser,
    OcrLine,
    ParseContext,
    ParsedExpense,
    ReceiptKeywordRule,
)

MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
MISSING_AMOUNT = "MISSING_AMOUNT"
MISSING_DATE = "MISSING_DATE"
MISSING_ROUTE = "MISSING_ROUTE"
LOW_OCR_CONFIDENCE = "LOW_OCR_CONFIDENCE"
QR_ISSUE_DATE_USED = "QR_ISSUE_DATE_USED"
INVOICE_DATE_USED_AS_OCCURRENCE = "INVOICE_DATE_USED_AS_OCCURRENCE"
_PASSENGER_TRANSPORT_KEYWORDS = (
    "旅客运输服务",
    "交通运输服务",
    "客运服务费",
    "交通工具类型",
)
_RIDE_HAILING_PROVIDERS = (
    "滴滴出行",
    "滴滴快车",
    "滴滴专车",
    "曹操出行",
    "t3出行",
    "首汽约车",
    "花小猪",
    "高德打车",
)


def _with_transport_evidence(parsed: ParsedExpense, lines: list[OcrLine]) -> ParsedExpense:
    if parsed.transport_type is not None:
        return parsed
    text = " ".join(line.text.casefold() for line in lines)
    transport = extract_passenger_transport_type(lines)
    ride_hailing = (
        transport == "网约车"
        or any(
            phrase in text for phrase in ("网约车服务", "网约车发票", "网约车行程", "网络预约出租")
        )
        or (
            any(provider in text for provider in _RIDE_HAILING_PROVIDERS)
            and any(
                service in text for service in ("旅客运输", "客运", "运输服务", "行程单", "出租车")
            )
        )
    )
    if ride_hailing:
        return replace(
            parsed,
            category=ExpenseCategory.LOCAL_TRANSPORT,
            transport_type="ride_hailing",
            requires_itinerary=True,
        )
    if parsed.category is ExpenseCategory.RAIL_FARE:
        return replace(parsed, transport_type="rail")
    if parsed.category is ExpenseCategory.LODGING:
        return replace(parsed, transport_type="hotel")
    if transport in {"出租车", "出租汽车"}:
        return replace(parsed, transport_type="taxi")
    return parsed


def is_passenger_transport_text(text: str) -> bool:
    return any(keyword in text for keyword in _PASSENGER_TRANSPORT_KEYWORDS)


def _warnings(lines: list[OcrLine], *, amount: object, parsed_date: object) -> list[str]:
    warnings: list[str] = []
    if amount is None:
        warnings.append(MISSING_AMOUNT)
    if parsed_date is None:
        warnings.append(MISSING_DATE)
    if average_confidence(lines) < 0.65:
        warnings.append(LOW_OCR_CONFIDENCE)
    return warnings


class TrainTicketParser:
    _keywords = ("铁路电子客票", "火车票", "车次", "二等座", "一等座", "检票口")

    def match(self, lines: list[OcrLine]) -> float:
        text = " ".join(line.text for line in lines)
        hits = sum(keyword in text for keyword in self._keywords)
        has_train_number = any(
            token and token[0] in "GDCZTK" and token[1:].isdigit()
            for token in text.replace("/", " ").split()
        )
        has_route_signal = "-" in text or "—" in text or "→" in text
        return min(
            1.0,
            hits * 0.25 + (0.2 if has_train_number else 0.0) + (0.15 if has_route_signal else 0.0),
        )

    def parse(self, lines: list[OcrLine], context: ParseContext) -> ParsedExpense:
        parsed_date = extract_train_travel_date(lines, context.reference_year)
        amount = extract_amount(lines, ("票价", "金额", "价税合计"))
        route = extract_route(lines)
        warnings = _warnings(lines, amount=amount, parsed_date=parsed_date)
        if uses_invoice_date_as_occurrence(
            lines,
            context.reference_year,
            parsed_date,
            ("出行日期", "乘车日期", "开车时间"),
        ):
            warnings.append(INVOICE_DATE_USED_AS_OCCURRENCE)
        if route is None:
            warnings.append(MISSING_ROUTE)
        parser_score = self.match(lines)
        confidence = min(average_confidence(lines), parser_score) if lines else 0.0
        return ParsedExpense(
            receipt_type="train",
            category=ExpenseCategory.RAIL_FARE,
            date=parsed_date,
            description=route,
            amount=amount,
            confidence=confidence,
            warnings=tuple(warnings),
        )


class GenericInvoiceParser:
    _invoice_keywords = ("发票", "价税合计", "开票日期", "发票号码", "旅客运输服务")

    def __init__(
        self,
        keyword_rules: tuple[ReceiptKeywordRule, ...] | None = None,
    ) -> None:
        self._keyword_rules = (
            DEFAULT_RECEIPT_KEYWORD_RULES if keyword_rules is None else keyword_rules
        )

    def match(self, lines: list[OcrLine]) -> float:
        text = " ".join(line.text for line in lines)
        hits = sum(keyword in text for keyword in self._invoice_keywords)
        return min(0.9, 0.2 + hits * 0.15) if hits else 0.0

    def _rule_matches(self, text: str) -> set[ExpenseCategory]:
        normalized = text.casefold()
        return {
            rule.category
            for rule in self._keyword_rules
            if rule.keyword and rule.keyword in normalized
        }

    def _classification(
        self,
        text: str,
        lines: list[OcrLine],
    ) -> tuple[ExpenseCategory, bool]:
        if is_passenger_transport_text(text):
            transport_type = extract_passenger_transport_type(lines)
            if transport_type is not None:
                transport_matches = self._rule_matches(transport_type)
                if len(transport_matches) == 1:
                    return next(iter(transport_matches)), False
                return ExpenseCategory.OTHER, True

            # A service item explicitly naming passenger service is useful even
            # when the structured transport-type cell is absent. Do not scan
            # route/company names: words such as "航空大酒店" are not transport proof.
            service_keywords = ("客运服务费", "公路客运", "道路旅客运输", "汽车客运")
            normalized = text.casefold()
            present_service_matches = {
                rule.category
                for rule in self._keyword_rules
                if rule.keyword and rule.keyword in service_keywords and rule.keyword in normalized
            }
            if len(present_service_matches) == 1:
                return next(iter(present_service_matches)), False
            return ExpenseCategory.OTHER, True

        matches = self._rule_matches(text)
        if len(matches) == 1:
            return next(iter(matches)), False
        return ExpenseCategory.OTHER, True

    def parse(self, lines: list[OcrLine], context: ParseContext) -> ParsedExpense:
        text = " ".join(line.text for line in lines)
        category, needs_review = self._classification(text, lines)
        transport_invoice = is_passenger_transport_text(text) or category in {
            ExpenseCategory.LOCAL_TRANSPORT,
            ExpenseCategory.RAIL_FARE,
            ExpenseCategory.AIRFARE,
        }
        if transport_invoice:
            parsed_date = extract_passenger_occurrence_date(lines, context.reference_year)
        else:
            parsed_date = extract_date(lines, context.reference_year, ("开票日期",))
        qr_amount = context.invoice_qr.amount if context.invoice_qr else None
        amount, qr_warnings = extract_invoice_amount(lines, qr_amount)
        if parsed_date is None and context.invoice_qr and context.invoice_qr.issue_date:
            parsed_date = context.invoice_qr.issue_date
            qr_warnings = (*qr_warnings, QR_ISSUE_DATE_USED)
        route = extract_route(lines)
        warnings = _warnings(lines, amount=amount, parsed_date=parsed_date)
        warnings.extend(qr_warnings)
        if transport_invoice and uses_invoice_date_as_occurrence(
            lines,
            context.reference_year,
            parsed_date,
            (PASSENGER_OCCURRENCE_DATE_PREFIX.removesuffix("："), "出行日期", "乘车日期"),
        ):
            warnings.append(INVOICE_DATE_USED_AS_OCCURRENCE)
        if needs_review or category is ExpenseCategory.OTHER:
            warnings.append(MANUAL_REVIEW_REQUIRED)
        confidence = min(average_confidence(lines), self.match(lines)) if lines else 0.0
        return ParsedExpense(
            receipt_type="invoice",
            category=category,
            date=parsed_date,
            description=route,
            amount=amount,
            confidence=confidence,
            warnings=tuple(dict.fromkeys(warnings)),
        )


class FallbackParser:
    def match(self, lines: list[OcrLine]) -> float:
        return 0.01

    def parse(self, lines: list[OcrLine], context: ParseContext) -> ParsedExpense:
        parsed_date = extract_date(lines, context.reference_year)
        qr_amount = context.invoice_qr.amount if context.invoice_qr else None
        amount, qr_warnings = extract_invoice_amount(lines, qr_amount)
        if parsed_date is None and context.invoice_qr and context.invoice_qr.issue_date:
            parsed_date = context.invoice_qr.issue_date
            qr_warnings = (*qr_warnings, QR_ISSUE_DATE_USED)
        warnings = _warnings(lines, amount=amount, parsed_date=parsed_date)
        warnings.extend(qr_warnings)
        warnings.append(MANUAL_REVIEW_REQUIRED)
        return ParsedExpense(
            receipt_type="other",
            category=ExpenseCategory.OTHER,
            date=parsed_date,
            description=None,
            amount=amount,
            confidence=min(average_confidence(lines), 0.2),
            warnings=tuple(dict.fromkeys(warnings)),
        )


class ReceiptParserRegistry:
    def __init__(
        self,
        parsers: tuple[BaseReceiptParser, ...] | None = None,
        keyword_rules: tuple[ReceiptKeywordRule, ...] | None = None,
    ) -> None:
        rules = DEFAULT_RECEIPT_KEYWORD_RULES if keyword_rules is None else keyword_rules
        self._parsers = (
            (
                ForeignReceiptParser(),
                PhysicalTaxiReceiptParser(),
                TrainTicketParser(),
                GenericInvoiceParser(keyword_rules=rules),
            )
            if parsers is None
            else parsers
        )
        self._fallback = FallbackParser()
        self._keyword_rules = rules

    def _apply_keyword_fallback(
        self,
        parsed: ParsedExpense,
        lines: list[OcrLine],
    ) -> ParsedExpense:
        if parsed.category is not ExpenseCategory.OTHER or not self._keyword_rules:
            return parsed
        raw_text = " ".join(line.text for line in lines)
        if is_passenger_transport_text(raw_text):
            return parsed
        text = raw_text.casefold()
        matches = {
            rule.category for rule in self._keyword_rules if rule.keyword and rule.keyword in text
        }
        if len(matches) != 1:
            return parsed
        category = next(iter(matches))
        warnings = tuple(
            warning
            for warning in parsed.warnings
            if warning != MANUAL_REVIEW_REQUIRED or parsed.receipt_type == "foreign_receipt"
        )
        return replace(parsed, category=category, warnings=warnings)

    def parse(self, lines: list[OcrLine], context: ParseContext) -> ParsedExpense:
        parser = max(self._parsers, key=lambda candidate: candidate.match(lines))
        if parser.match(lines) < 0.2:
            parsed = self._fallback.parse(lines, context)
        else:
            parsed = parser.parse(lines, context)
        parsed = _with_transport_evidence(self._apply_keyword_fallback(parsed, lines), lines)
        invoice_numbers, order_numbers = extract_document_numbers(lines)
        return replace(
            parsed,
            invoice_numbers=invoice_numbers,
            order_numbers=order_numbers,
            rail_type=extract_rail_type(
                lines,
                railway_evidence=parsed.receipt_type == "train" or parsed.transport_type == "rail",
            ),
        )
