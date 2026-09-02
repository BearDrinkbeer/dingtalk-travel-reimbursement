from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.ocr.qr import decode_invoice_qr, parse_legacy_invoice_qr


def test_legacy_invoice_qr_extracts_only_amount_and_issue_date() -> None:
    evidence = parse_legacy_invoice_qr(
        "01,10,036001600111,76675253,139.00,20201209,07404954883001579446,BB5C,"
    )

    assert evidence is not None
    assert evidence.amount == Decimal("139.00")
    assert evidence.issue_date == date(2020, 12, 9)


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "https://example.invalid/dynamic-invoice",
        "01,10,not-a-code,76675253,139.00,20201209,check",
        "01,10,036001600111,76675253,not-money,20201209,check",
        "01,10,036001600111,76675253,139.00,20201309,check",
    ],
)
def test_unknown_or_malformed_qr_payload_is_ignored(payload: str) -> None:
    assert parse_legacy_invoice_qr(payload) is None


def test_qr_decoder_reads_a_local_image_without_external_api(tmp_path) -> None:
    cv2 = pytest.importorskip("cv2")
    payload = "01,10,036001600111,76675253,139.00,20201209,07404954883001579446,BB5C,"
    encoded = cv2.QRCodeEncoder_create().encode(payload)
    encoded = cv2.copyMakeBorder(encoded, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
    encoded = cv2.resize(encoded, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    path = tmp_path / "invoice-qr.png"
    assert cv2.imwrite(str(path), encoded)

    evidence = decode_invoice_qr(path, "png", render_dpi=200)

    assert evidence is not None
    assert evidence.amount == Decimal("139.00")
    assert evidence.issue_date == date(2020, 12, 9)
