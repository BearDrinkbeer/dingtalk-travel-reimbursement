import pytest

from app.ocr.document_evidence import extract_document_numbers, extract_rail_type
from app.ocr.extractors import extract_passenger_transport_from_layout
from app.ocr.parsers import ReceiptParserRegistry
from app.ocr.types import OcrLine, ParseContext
from app.ocr.workers import _append_passenger_fields
from app.services.ocr_service import parsed_expense_payload


def test_rail_receipt_exposes_explicit_numbers_and_train_subtype():
    parsed = ReceiptParserRegistry().parse(
        [
            OcrLine(text, 0.99)
            for text in (
                "铁路电子客票",
                "车次 G123",
                "乘车日期 2026-09-03",
                "票价 12.30",
                "发票号码：00001234567890123456",
                "订单号: ORDER-00123",
                "购买方纳税人识别号：91330000123456789X",
                "联系电话 13800138000",
            )
        ],
        ParseContext(reference_year=2026),
    )
    result = parsed_expense_payload("receipt-id", parsed)
    assert result["railType"] == "high_speed"
    assert result["invoiceNumbers"] == ["00001234567890123456"]
    assert result["orderNumbers"] == ["ORDER-00123"]


@pytest.mark.parametrize(
    "train,expected",
    [
        ("G123", "high_speed"),
        ("D234", "emu"),
        ("C123", "emu"),
        ("Z123", "regular"),
        ("T123", "regular"),
        ("K12", "regular"),
        ("1234", "regular"),
        ("不清晰", "unknown"),
        ("G123 D234", "unknown"),
    ],
)
def test_rail_subtypes_require_train_evidence(train, expected):
    lines = [OcrLine("车次：" + train, 0.99)]
    assert extract_rail_type(lines, railway_evidence=True) == expected
    assert extract_rail_type(lines, railway_evidence=False) is None


@pytest.mark.parametrize(
    "text",
    [
        "电话号码：13800138000\n纳税人识别号：91330000123456789X\n编号：12345678",
        "发票号码：\n1234 extra text",
        "订单号：0000 1234",
        "发票号码：13800138000联系电话",
        "Invoice notification1234\nOrder identity00001",
    ],
)
def test_unlabelled_or_incomplete_identifiers_are_not_invented(text):
    assert extract_document_numbers([OcrLine(line, 1) for line in text.splitlines()]) == ((), ())


def test_labelled_identifiers_preserve_zeros_and_normalize_ascii_case():
    text = "Invoice No: 00001234\nOrder ID: abc-00001\n发票号码：\n00005678\n联系电话：13800138000"
    invoices, orders = extract_document_numbers([OcrLine(line, 1) for line in text.splitlines()])
    assert invoices == ("00001234", "00005678")
    assert orders == ("ABC-00001",)


def test_invoice_transport_uses_explicit_layout_cell_when_plain_order_is_scrambled():
    layout = (
        "出行日期    出发地    到达地    等级    交通工具类型\n"
        "2026-09-03    测试起点    测试终点    无    出租车"
    )
    plain = "电子发票\n交通工具类型\n" + "其他字段\n" * 20 + "出租车\n价税合计12.30元"
    lines = [(line, 1) for line in plain.splitlines()]
    _append_passenger_fields(lines, "出发地 到达地 交通工具类型 " + plain, layout)
    parsed = ReceiptParserRegistry().parse(
        [OcrLine(text, score) for text, score in lines], ParseContext(reference_year=2026)
    )
    assert parsed.transport_type == "taxi"


@pytest.mark.parametrize(
    "layout",
    [
        "2026-09-03    测试起点    测试终点    无    出租车",
        "出行日期    出发地    到达地    交通工具类型\n"
        "2026-09-03    出租车公司    测试终点    未知",
        "出行日期    出发地    到达地    交通工具类型\n"
        "2026-09-03    测试起点    测试终点    小客车",
        "出行日期    出发地    到达地    交通工具类型\n"
        "2026-09-03    测试起点    测试终点    出租车\n2026-09-04    测试甲    测试乙    飞机",
    ],
)
def test_layout_transport_does_not_guess_from_route_or_unknown_vehicle(layout):
    assert extract_passenger_transport_from_layout(layout) is None
