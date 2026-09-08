from __future__ import annotations

import json

import pytest
from test_material_classification import _pages
from test_reimbursement_drafts import _input
from test_reimbursement_intake import _proof_setup

from app.core.errors import ApiError
from app.models.reimbursement import ReimbursementDraft, ReimbursementDraftFile
from app.ocr.hotel_bills import hotel_bill_details
from app.ocr.materials import classify_material
from app.schemas.reimbursements import ReimbursementDraftInput
from app.services.reimbursement_drafts import (
    detach_draft_files_from_input,
    validate_draft_file_references,
)

HOTEL = (
    "测试酒店\n序号 房型 单价 姓名 入住时间 离店时间 间夜 价格\n"
    "1 大床 100 测试客人 2026-09-01 2026-09-03 2 200\n总合计需支付200"
)


def test_hotel_table_is_auxiliary_even_when_its_heading_has_no_bill_title():
    assert classify_material(_pages(HOTEL), page_count=1)[0] == "hotel_bill"
    assert classify_material(_pages(HOTEL + "\n电子发票 价税合计200"), page_count=1)[0] == "expense"
    assert (
        classify_material(_pages("酒店交易明细\n需支付200\n金额200"), page_count=1)[0] == "unknown"
    )
    assert (
        classify_material(
            _pages(
                "Guest Invoice\nGuest:A\nCheck in:2026-09-01\n"
                "Check out:2026-09-03\nRate100\nTotal 200"
            ),
            page_count=1,
        )[0]
        == "expense"
    )


def test_table_summary_recovers_stay_and_warns_when_currency_is_missing():
    details = hotel_bill_details(_pages(HOTEL))
    assert details == {
        "guest": "测试客人",
        "checkIn": "2026-09-01",
        "checkOut": "2026-09-03",
        "nights": 2,
        "nightlyRate": "100.00",
        "total": "200.00",
        "currency": None,
        "warnings": ["HOTEL_BILL_REVIEW_REQUIRED", "HOTEL_BILL_INCOMPLETE"],
    }
    assert "HOTEL_BILL_INCOMPLETE" in hotel_bill_details(_pages("住宿明细"))["warnings"]


def test_advisory_summary_does_not_guess_yen_currency_or_fail_on_huge_total():
    details = hotel_bill_details(_pages("Hotel bill\nTotal ¥999999999999999"))
    assert details["currency"] is None
    assert details["total"] is None
    assert "HOTEL_BILL_INCOMPLETE" in details["warnings"]


@pytest.mark.parametrize("amount", ["0.00", "20.00", "500.00"])
def test_lodging_requires_hotel_bill_at_every_amount(client_factory, monkeypatch, amount):
    client, _, draft_id, _, support = _proof_setup(client_factory, monkeypatch)
    value = _input()
    value["items"][0].update(category="lodging", amount=amount)
    draft_input = ReimbursementDraftInput.model_validate(value)
    with client.app.state.database_session_factory() as database:
        with pytest.raises(ApiError, match="住宿明细"):
            validate_draft_file_references(
                database, draft_id=draft_id, draft_input=draft_input, require_submission_proofs=True
            )
        file = database.get(ReimbursementDraftFile, support)
        file.attachment_kind = "hotel_bill"
        file.ocr_status = "FAILED"
        file.ocr_result_json = json.dumps(
            {"_materialClassification": {"status": "confirmed", "kind": "hotel_bill"}}
        )
        database.commit()
        draft_input.items[0].hotel_bill_file_ids = [support]
        validate_draft_file_references(
            database, draft_id=draft_id, draft_input=draft_input, require_submission_proofs=True
        )


def test_hotel_bill_does_not_satisfy_independent_payment_requirement(client_factory, monkeypatch):
    client, _, draft_id, _, support = _proof_setup(client_factory, monkeypatch)
    value = _input()
    value["items"][0].update(category="lodging", amount="500.01", hotelBillFileIds=[support])
    with client.app.state.database_session_factory() as database:
        database.get(ReimbursementDraftFile, support).attachment_kind = "hotel_bill"
        database.commit()
        draft_input = ReimbursementDraftInput.model_validate(value)
        with pytest.raises(ApiError, match="付款凭证"):
            validate_draft_file_references(
                database, draft_id=draft_id, draft_input=draft_input, require_submission_proofs=True
            )
        draft_input.items[0].payment_proof_file_ids = [support]
        with pytest.raises(ApiError, match="票据文件无效"):
            validate_draft_file_references(
                database, draft_id=draft_id, draft_input=draft_input, require_submission_proofs=True
            )


def test_shared_hotel_bill_detach_preserves_both_manual_costs(client_factory, monkeypatch):
    client, _, draft_id, _, support = _proof_setup(client_factory, monkeypatch)
    value = _input()
    value["items"] = [
        {
            **value["items"][0],
            "category": "lodging",
            "amount": "100.00",
            "hotelBillFileIds": [support],
        }
        for _ in range(2)
    ]
    with client.app.state.database_session_factory() as database:
        database.get(ReimbursementDraftFile, support).attachment_kind = "hotel_bill"
        draft = database.get(ReimbursementDraft, draft_id)
        draft.input_json = json.dumps(value)
        database.commit()
        validate_draft_file_references(
            database,
            draft_id=draft_id,
            draft_input=ReimbursementDraftInput.model_validate(value),
            require_submission_proofs=True,
        )
        detached = detach_draft_files_from_input(database, draft=draft, file_ids={support})
        items = json.loads(detached.canonical_json)["items"]
        assert len(items) == 2
        assert all(item["amount"] == "100.00" and item["hotelBillFileIds"] == [] for item in items)
