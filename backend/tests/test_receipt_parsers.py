from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.categories import ExpenseCategory
from app.ocr.extractors import (
    PASSENGER_OCCURRENCE_DATE_PREFIX,
    PASSENGER_ROUTE_PREFIX,
    extract_amount,
    extract_date,
    extract_passenger_occurrence_date,
    extract_passenger_occurrence_date_from_layout,
    extract_passenger_route_from_layout,
    extract_route,
)
from app.ocr.keyword_defaults import DEFAULT_RECEIPT_KEYWORD_RULES
from app.ocr.parsers import GenericInvoiceParser, ReceiptParserRegistry, TrainTicketParser
from app.ocr.types import InvoiceQrEvidence, OcrLine, ParseContext, ReceiptKeywordRule


def lines(*values: str, confidence: float = 0.95) -> list[OcrLine]:
    return [OcrLine(value, confidence) for value in values]


def test_amount_date_route_helpers_are_strict_and_contextual() -> None:
    values = lines(
        "发票号码 12345678901234567890",
        "手机号 13800138000",
        "开票日期 2026年06月30日",
        "价税合计（小写） ￥44.89",
        "北京南-合肥南",
    )
    assert extract_amount(values, ("价税合计（小写）",)) == Decimal("44.89")
    assert extract_date(values, 2030, ("开票日期",)) == date(2026, 6, 30)
    assert extract_route(values) == "北京南-合肥南"
    assert extract_date(lines("乘车日期 6月30日"), 2027) == date(2027, 6, 30)


@pytest.mark.parametrize(
    ("layout_text", "expected_date", "expected_route"),
    [
        (
            "王  广 硕        证件号        2026-07-06      "
            "泊  寓 · 新  桥 产 业  园 店 东  侧      "
            "长 鑫 存 储 公 司 东 一 门        惠选        出 租 车",
            date(2026, 7, 6),
            "泊寓·新桥产业园店东侧-长鑫存储公司东一门",
        ),
        (
            "姓名        证件号        2026-07-01        长鑫存储技术有限公司(东        "
            "全季酒店(合肥新桥国际机        无        出租车\n"
            "大门)        场店)",
            date(2026, 7, 1),
            "长鑫存储技术有限公司(东大门)-全季酒店(合肥新桥国际机场店)",
        ),
        (
            "姓名        证件号        2026-07-06  长鑫存储技术有限公司(东        "
            "泊寓·新桥产业园店        出租车\n"
            "门)",
            date(2026, 7, 6),
            "长鑫存储技术有限公司(东门)-泊寓·新桥产业园店",
        ),
        (
            "  孙增增        411481********4516  2026-07-02    "
            "蔚来交付中心-西5门        长鑫存储技术有限公司-东            无         出租车\n"
            "                                                                         大门",
            date(2026, 7, 2),
            "蔚来交付中心-西5门-长鑫存储技术有限公司-东大门",
        ),
        (
            "    孙增增               411481********4516               2026-07-03          "
            "全季酒店(合肥新桥国际                      蜀山区|长鑫存储技术有          "
            "                                出租车\n"
            "                                                                                    "
            "机场店)                          限公司-东1门",
            date(2026, 7, 3),
            "全季酒店(合肥新桥国际机场店)-蜀山区长鑫存储技术有限公司-东1门",
        ),
        (
            "  孙增增     411************5162026-07-01全季酒店(合肥新桥国际     "
            "长鑫存储技术有限公司-东大        其他       其他\n"
            "                                            机场店)               门",
            date(2026, 7, 1),
            "全季酒店(合肥新桥国际机场店)-长鑫存储技术有限公司-东大门",
        ),
    ],
)
def test_passenger_route_is_extracted_from_pdf_layout(
    layout_text: str,
    expected_date: date,
    expected_route: str,
) -> None:
    assert extract_passenger_occurrence_date_from_layout(layout_text) == expected_date
    assert extract_passenger_route_from_layout(layout_text) == expected_route
    assert extract_route(lines(f"{PASSENGER_ROUTE_PREFIX}{expected_route}")) == expected_route
    assert (
        extract_passenger_occurrence_date(
            lines(f"{PASSENGER_OCCURRENCE_DATE_PREFIX}{expected_date.isoformat()}"),
            2000,
        )
        == expected_date
    )


def test_date_label_priority_is_independent_of_ocr_line_order() -> None:
    values = lines(
        "开票日期 2026年07月02日",
        "无标签日期 2026年07月03日",
        "出行日期 2026年06月30日",
    )
    assert extract_date(values, 2026, ("出行日期", "乘车日期", "开票日期")) == date(2026, 6, 30)


def test_train_and_invoice_parsers_use_travel_date_before_invoice_date() -> None:
    train = TrainTicketParser().parse(
        lines(
            "铁路电子客票 G1234 二等座",
            "开票日期 2026年07月02日",
            "乘车日期 2026年06月30日",
            "票价 ￥454.00",
            "北京南-合肥南",
        ),
        ParseContext(2026),
    )
    invoice = GenericInvoiceParser().parse(
        lines(
            "电子发票 旅客运输服务",
            "交通工具类型 铁路",
            "开票日期 2026年07月02日",
            "出行日期 2026年06月30日",
            "价税合计 ￥454.00",
        ),
        ParseContext(2026),
    )
    assert train.date == date(2026, 6, 30)
    assert invoice.date == date(2026, 6, 30)


@pytest.mark.parametrize(
    "travel_line",
    [
        "2026年06月30日 10:35开",
        "2026年06月30日",
    ],
)
def test_train_parser_uses_unlabeled_boarding_date_before_invoice_date(
    travel_line: str,
) -> None:
    parsed = TrainTicketParser().parse(
        lines(
            "铁路电子客票 G1234 二等座",
            "开票日期 2026年07月07日",
            travel_line,
            "票价 ￥473.50",
            "合肥南-北京南",
        ),
        ParseContext(2026),
    )

    assert parsed.date == date(2026, 6, 30)


def test_invoice_parser_uses_invoice_date_when_no_travel_date_exists() -> None:
    parsed = GenericInvoiceParser().parse(
        lines(
            "电子发票",
            "交通工具类型 铁路",
            "开票日期 2026年07月02日",
            "价税合计 ￥100.00",
        ),
        ParseContext(2026),
    )
    assert parsed.date == date(2026, 7, 2)
    assert "INVOICE_DATE_USED_AS_OCCURRENCE" in parsed.warnings


def test_train_parser_marks_invoice_date_fallback_but_not_explicit_same_day_travel() -> None:
    fallback = TrainTicketParser().parse(
        lines(
            "铁路电子客票 G1234 二等座",
            "开票日期 2026年07月02日",
            "票价 ￥100.00",
            "北京南-合肥南",
        ),
        ParseContext(2026),
    )
    explicit = TrainTicketParser().parse(
        lines(
            "铁路电子客票 G1234 二等座",
            "开票日期 2026年07月02日",
            "乘车日期 2026年07月02日",
            "票价 ￥100.00",
            "北京南-合肥南",
        ),
        ParseContext(2026),
    )

    assert "INVOICE_DATE_USED_AS_OCCURRENCE" in fallback.warnings
    assert "INVOICE_DATE_USED_AS_OCCURRENCE" not in explicit.warnings


def test_train_parser_does_not_mark_same_day_departure_time_as_invoice_fallback() -> None:
    parsed = TrainTicketParser().parse(
        lines(
            "铁路电子客票 G2564 二等座",
            "开票日期:2026年07月07日",
            "2026年07月07日 13:30开",
            "票价 ￥473.50",
            "合肥南站-北京南站",
        ),
        ParseContext(2026),
    )

    assert parsed.date == date(2026, 7, 7)
    assert "INVOICE_DATE_USED_AS_OCCURRENCE" not in parsed.warnings


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ("交通工具类型 铁路", ExpenseCategory.RAIL_FARE),
        ("交通工具类型 航空", ExpenseCategory.AIRFARE),
        ("交通工具类型 出租汽车", ExpenseCategory.LOCAL_TRANSPORT),
        ("酒店住宿 房费", ExpenseCategory.LODGING),
    ],
)
def test_generic_invoice_requires_explicit_category_evidence(
    evidence: str, expected: ExpenseCategory
) -> None:
    document_heading = (
        "电子发票 旅客运输服务" if expected is not ExpenseCategory.LODGING else "电子发票"
    )
    parsed = GenericInvoiceParser().parse(
        lines(document_heading, evidence, "开票日期 2026-07-01", "价税合计 ￥100.00"),
        ParseContext(2026),
    )
    assert parsed.category is expected
    assert "MANUAL_REVIEW_REQUIRED" not in parsed.warnings


def test_taxi_transport_type_wins_over_hotel_name_in_destination() -> None:
    parsed = GenericInvoiceParser().parse(
        lines(
            "电子发票 旅客运输服务",
            "出行人 出行日期 出发地 到达地 等级 交通工具类型",
            "2026-07-01 项目地 全季酒店 无 出租车",
            "价税合计 ￥16.90",
        ),
        ParseContext(2026),
    )

    assert parsed.category is ExpenseCategory.LOCAL_TRANSPORT
    assert "MANUAL_REVIEW_REQUIRED" not in parsed.warnings


def test_unknown_passenger_transport_type_does_not_classify_from_route_name() -> None:
    parsed = ReceiptParserRegistry().parse(
        lines(
            "电子发票 旅客运输服务",
            "出行日期 出发地 到达地 交通工具类型",
            "2026-07-01 项目地 航空大酒店 未知",
            "价税合计 ￥100.00",
        ),
        ParseContext(2026),
    )

    assert parsed.category is ExpenseCategory.OTHER
    assert "MANUAL_REVIEW_REQUIRED" in parsed.warnings


@pytest.mark.parametrize(
    ("amount_lines", "expected"),
    [
        (("合计 ￥100.00", "税额 ￥6.00"), Decimal("106.00")),
        (("合计 ￥100.00 税额 ￥6.00",), Decimal("106.00")),
        (("合 计 6.89¥ 0.21¥",), Decimal("7.10")),
        (("价税合计（大写） 壹佰零陆元整",), Decimal("106.00")),
        (("合计 ￥100.00",), None),
    ],
)
def test_invoice_total_uses_price_tax_semantics(
    amount_lines: tuple[str, ...], expected: Decimal | None
) -> None:
    parsed = GenericInvoiceParser().parse(
        lines("电子发票", "开票日期 2026-07-01", *amount_lines),
        ParseContext(2026),
    )

    assert parsed.amount == expected


def test_taxi_invoice_uses_unlabeled_occurrence_date_before_invoice_date() -> None:
    parsed = GenericInvoiceParser().parse(
        lines(
            "电子发票 旅客运输服务",
            "开票日期 2026年07月08日",
            "出行人 出行日期 出发地 到达地 等级 交通工具类型",
            "王广硕 2026-07-01 项目地 全季酒店 无 出租车",
            "价税合计 ￥16.90",
        ),
        ParseContext(2026),
    )

    assert parsed.date == date(2026, 7, 1)


@pytest.mark.parametrize(
    ("row_values", "expected_route"),
    [
        (
            (
                "2026-07-06",
                "泊寓·新桥产业园店东侧",
                "长鑫存储公司东一门",
                "惠选",
                "出租车",
            ),
            "泊寓·新桥产业园店东侧-长鑫存储公司东一门",
        ),
        (
            (
                "2026-07-06",
                "长鑫存储技术有限公司(东",
                "泊寓·新桥产业园店",
                "其他",
                "出租车",
                "门)",
            ),
            "长鑫存储技术有限公司(东门)-泊寓·新桥产业园店",
        ),
        (
            (
                "2026-07-02",
                "蔚来交付中心-西5门",
                "长鑫存储技术有限公司-东",
                "无",
                "出租车",
                "大门",
            ),
            "蔚来交付中心-西5门-长鑫存储技术有限公司-东大门",
        ),
        (
            (
                "2026-06-30",
                "合肥北城站-进站口",
                "长鑫存储技术有限公司-东大",
                "其他",
                "其他",
                "门",
            ),
            "合肥北城站-进站口-长鑫存储技术有限公司-东大门",
        ),
        (
            (
                "2026-07-03",
                "全季酒店(合肥新桥国际",
                "蜀山区长鑫存储技术有",
                "出租车",
                "机场店)",
                "限公司-东1门",
            ),
            "全季酒店(合肥新桥国际机场店)-蜀山区长鑫存储技术有限公司-东1门",
        ),
        (
            (
                "2026-07-07",
                "长鑫存储技术有限公司(东1",
                "门)",
                "合肥南站(东进站口)",
                "无",
                "出租车",
            ),
            "长鑫存储技术有限公司(东1门)-合肥南站(东进站口)",
        ),
    ],
)
def test_taxi_route_is_extracted_from_flattened_ocr_passenger_row(
    row_values: tuple[str, ...],
    expected_route: str,
) -> None:
    values = lines(
        "电子发票 旅客运输服务",
        "出行人",
        "有效身份证件号",
        "出行日期",
        "出发地",
        "到达地",
        "等级",
        "交通工具类型",
        *row_values,
        "价税合计（小写） ￥10.13",
    )

    assert extract_route(values) == expected_route


def test_passenger_service_fee_is_local_transport_without_transport_type() -> None:
    parsed = GenericInvoiceParser().parse(
        lines(
            "电子发票 旅客运输服务",
            "*交通运输服务*客运服务费",
            "开票日期 2026-07-08",
            "价税合计 ￥9.20",
        ),
        ParseContext(2026),
    )

    assert parsed.category is ExpenseCategory.LOCAL_TRANSPORT
    assert "MANUAL_REVIEW_REQUIRED" not in parsed.warnings


def test_ambiguous_passenger_transport_and_conflicting_evidence_require_review() -> None:
    parser = GenericInvoiceParser()
    for evidence in ("旅客运输服务", "交通工具类型 航空 铁路"):
        parsed = parser.parse(
            lines("电子发票", evidence, "开票日期 2026-07-01", "价税合计 ￥100.00"),
            ParseContext(2026),
        )
        assert parsed.category is ExpenseCategory.OTHER
        assert "MANUAL_REVIEW_REQUIRED" in parsed.warnings


def test_itinerary_phrase_does_not_enable_airfare_classification() -> None:
    parsed = GenericInvoiceParser().parse(
        lines(
            "电子发票 电子客票行程单",
            "开票日期 2026-07-01",
            "价税合计 ￥100.00",
        ),
        ParseContext(2026),
    )

    assert parsed.category is ExpenseCategory.OTHER
    assert "MANUAL_REVIEW_REQUIRED" in parsed.warnings


def test_registry_fallback_missing_fields_never_invents_values() -> None:
    parsed = ReceiptParserRegistry().parse(
        lines("无法识别的普通文字", confidence=0.4),
        ParseContext(2026),
    )
    assert parsed.category is ExpenseCategory.OTHER
    assert parsed.amount is None
    assert parsed.date is None
    assert parsed.description is None
    assert set(parsed.warnings) >= {
        "MANUAL_REVIEW_REQUIRED",
        "MISSING_AMOUNT",
        "MISSING_DATE",
        "LOW_OCR_CONFIDENCE",
    }


def test_visible_price_tax_total_remains_authoritative_over_qr_amount() -> None:
    parsed = GenericInvoiceParser().parse(
        lines(
            "电子发票",
            "开票日期 2026-07-01",
            "价税合计（小写） ￥500.00",
            "税额 ￥14.56",
        ),
        ParseContext(
            2026,
            invoice_qr=InvoiceQrEvidence(
                amount=Decimal("400.00"),
                issue_date=date(2026, 7, 1),
            ),
        ),
    )

    assert parsed.amount == Decimal("500.00")
    assert "QR_AMOUNT_MISMATCH" in parsed.warnings


def test_tax_exclusive_qr_amount_can_confirm_visible_price_tax_total() -> None:
    parsed = GenericInvoiceParser().parse(
        lines(
            "电子发票",
            "开票日期 2026-07-01",
            "价税合计 ￥113.00",
            "税额 ￥13.00",
        ),
        ParseContext(2026, invoice_qr=InvoiceQrEvidence(amount=Decimal("100.00"))),
    )

    assert parsed.amount == Decimal("113.00")
    assert "QR_AMOUNT_MISMATCH" not in parsed.warnings


def test_qr_only_fields_are_editable_candidates_with_warning() -> None:
    parsed = GenericInvoiceParser().parse(
        lines("电子发票 发票号码 12345678"),
        ParseContext(
            2026,
            invoice_qr=InvoiceQrEvidence(
                amount=Decimal("139.00"),
                issue_date=date(2020, 12, 9),
            ),
        ),
    )

    assert parsed.amount == Decimal("139.00")
    assert parsed.date == date(2020, 12, 9)
    assert set(parsed.warnings) >= {
        "QR_AMOUNT_REQUIRES_REVIEW",
        "QR_ISSUE_DATE_USED",
        "MANUAL_REVIEW_REQUIRED",
    }


def test_admin_keywords_classify_only_other_and_conflicts_remain_other() -> None:
    invoice_lines = lines(
        "电子发票",
        "开票日期 2026-07-01",
        "*办公用品*文具",
        "价税合计 ￥56.43",
    )
    office_rule = ReceiptKeywordRule("文具", ExpenseCategory.OFFICE)
    classified = ReceiptParserRegistry(keyword_rules=(office_rule,)).parse(
        invoice_lines,
        ParseContext(2026),
    )
    conflicting = ReceiptParserRegistry(
        keyword_rules=(
            office_rule,
            ReceiptKeywordRule("办公用品", ExpenseCategory.HOSPITALITY),
        )
    ).parse(invoice_lines, ParseContext(2026))

    assert classified.category is ExpenseCategory.OFFICE
    assert "MANUAL_REVIEW_REQUIRED" not in classified.warnings
    assert conflicting.category is ExpenseCategory.OTHER
    assert "MANUAL_REVIEW_REQUIRED" in conflicting.warnings


def test_admin_keyword_does_not_override_clear_builtin_transport_category() -> None:
    parsed = ReceiptParserRegistry(
        keyword_rules=(
            *DEFAULT_RECEIPT_KEYWORD_RULES,
            ReceiptKeywordRule("全季酒店", ExpenseCategory.LODGING),
        )
    ).parse(
        lines(
            "电子发票 旅客运输服务",
            "2026-07-01 项目地 全季酒店 交通工具类型 出租车",
            "价税合计 ￥16.90",
        ),
        ParseContext(2026),
    )

    assert parsed.category is ExpenseCategory.LOCAL_TRANSPORT


def test_builtin_category_keywords_can_be_removed_from_the_active_rules() -> None:
    invoice_lines = lines(
        "电子发票",
        "开票日期 2026-07-01",
        "全季酒店 住宿服务",
        "价税合计 ￥399.00",
    )

    default_result = GenericInvoiceParser().parse(invoice_lines, ParseContext(2026))
    removed_result = GenericInvoiceParser(keyword_rules=()).parse(
        invoice_lines,
        ParseContext(2026),
    )

    assert default_result.category is ExpenseCategory.LODGING
    assert removed_result.category is ExpenseCategory.OTHER
