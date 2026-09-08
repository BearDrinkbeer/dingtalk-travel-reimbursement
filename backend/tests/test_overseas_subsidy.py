from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from io import BytesIO

import pytest
from conftest import mock_login
from openpyxl import load_workbook
from pydantic import ValidationError
from test_oa_reimbursement_payload import _source
from test_reimbursement_drafts import _create, _input, _install_catalog

from app.domain.expenses import calculate_expense_totals
from app.domain.subsidy import TripType, calculate_subsidy
from app.models.setting import Setting
from app.schemas.expenses import TripInput
from app.services.oa_reimbursement_payload import (
    build_snapshot,
    draft_input_from_snapshot,
    parse_snapshot,
    serialize_snapshot,
    snapshot_excel_input,
    snapshot_sha256,
)


def overseas_trip(**overrides):
    return {
        "tripType": "overseas",
        "startDate": "2026-09-01",
        "startTime": "09:00",
        "endDate": "2026-09-03",
        "endTime": "18:00",
        **overrides,
    }


def test_overseas_total_uses_configured_daily_rate():
    trip = TripInput.model_validate(overseas_trip())
    subsidy = calculate_subsidy(
        trip_type=trip.subsidy_trip_type(),
        period=trip.as_period(),
        configured_daily_rate=Decimal("120.00"),
    )
    assert subsidy.total == Decimal("360.00")
    assert subsidy.daily_rate == Decimal("120.00")
    assert subsidy.effective_days == Decimal("3.0")
    assert subsidy.calendar_days == 3


def test_manual_overseas_amount_is_rejected():
    with pytest.raises(ValidationError):
        TripInput.model_validate(overseas_trip(manualSubsidyAmount="321.45"))


def test_overseas_totals_and_excel_use_admin_daily_rate(client_factory):
    client = client_factory(auth_mock_enabled=True)
    headers = {"X-CSRF-Token": mock_login(client)["csrfToken"]}
    with client.app.state.database_session_factory() as database:
        database.add(Setting(key="subsidy_overseas_per_day", value="120.00"))
        database.commit()
    trip = overseas_trip()
    item = {
        "category": "other",
        "date": "2026-09-01",
        "displayDate": "9月1日",
        "description": "费用",
        "amount": "10.00",
        "receiptCount": 1,
    }
    response = client.post(
        "/api/calculate/totals",
        headers=headers,
        json={"trip": trip, "items": [item | {"id": "manual-1", "source": "manual"}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["totalAmount"] == "370.00"
    assert response.json()["data"]["subsidyTotal"] == "360.00"
    response = client.post(
        "/api/excel/generate",
        headers=headers,
        json={"project": {"mode": "manual", "text": "海外项目"}, "trip": trip, "items": [item]},
    )
    assert response.status_code == 200, response.text
    sheet = load_workbook(BytesIO(response.content), data_only=False).active
    assert "共3天出差补助" in sheet["D5"].value
    assert sheet["G5"].value == 360
    assert sheet["C55"].value == "叁佰柒拾元整"


def test_overseas_draft_uses_zero_default_rate(client_factory, monkeypatch):
    _install_catalog(monkeypatch)
    client = client_factory(auth_mock_enabled=True)
    headers = {"X-CSRF-Token": mock_login(client)["csrfToken"]}
    body = _input()
    body["trip"] = overseas_trip()
    body["editingState"] = {"includeSubsidy": True, "trip": body["trip"]}
    created = _create(client, headers, body)
    assert created.status_code == 201, created.text
    detail = client.get(
        f"/api/reimbursements/drafts/{created.json()['data']['id']}"
    ).json()["data"]
    assert "manualSubsidyAmount" not in detail["input"]["trip"]
    assert detail["totals"]["subsidyTotal"] == "0.00"
    assert detail["totals"]["totalAmount"] == "44.89"


def test_overseas_snapshot_reloads_and_rejects_rate_total_mismatch():
    source = _source()
    source.draft_input.trip = TripInput.model_validate(overseas_trip())
    subsidy = calculate_subsidy(
        trip_type=TripType.OVERSEAS,
        period=source.draft_input.trip.as_period(),
        configured_daily_rate=Decimal("120.00"),
    )
    totals = calculate_expense_totals(source.draft_input.items, subsidy).as_api_dict()
    totals["subsidy"] = subsidy.as_api_dict()
    snapshot = build_snapshot(replace(source, totals_data=totals))
    parsed = parse_snapshot(
        serialize_snapshot(snapshot), expected_sha256=snapshot_sha256(snapshot)
    )
    restored = draft_input_from_snapshot(parsed)
    assert restored.trip is not None
    assert restored.trip.trip_type.value == "overseas"
    assert snapshot_excel_input(parsed).subsidy.total == Decimal("360.00")
    description = next(
        value.value for value in parsed.form_values if value.logical_key == "description"
    )
    assert "出差补助：3.0天 × 120.00 = 360.00" in description
    assert parsed.subsidy is not None
    tampered_subsidy = parsed.subsidy.model_copy(update={"total": Decimal("100.00")})
    tampered = parsed.model_copy(update={"subsidy": tampered_subsidy})
    with pytest.raises(ValueError):
        serialize_snapshot(tampered)
