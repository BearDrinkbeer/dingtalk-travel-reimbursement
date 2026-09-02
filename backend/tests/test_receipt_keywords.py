from __future__ import annotations

import io

from PIL import Image

from app.models.receipt_keyword import ReceiptKeywordMapping
from app.ocr.engine import FakeOcrEngine
from app.ocr.types import OcrLine
from tests.conftest import mock_login


def _image_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 40), "white").save(output, format="PNG")
    return output.getvalue()


def test_receipt_keyword_crud_is_admin_only_and_validated(client_factory) -> None:
    user = client_factory(auth_mock_enabled=True)
    user_login = mock_login(user)
    assert user.get("/api/admin/receipt-keywords").status_code == 403
    assert (
        user.post(
            "/api/admin/receipt-keywords",
            json={"keyword": "文具", "categoryId": "office"},
            headers={"X-CSRF-Token": user_login["csrfToken"]},
        ).status_code
        == 403
    )

    admin = client_factory(
        auth_mock_enabled=True,
        auth_mock_user_id="admin-1",
        admin_user_ids="admin-1",
    )
    csrf = mock_login(admin)["csrfToken"]
    created = admin.post(
        "/api/admin/receipt-keywords",
        json={"keyword": " 文具 ", "categoryId": "office"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    mapping = created.json()["data"]
    assert mapping == {
        "id": mapping["id"],
        "keyword": "文具",
        "categoryId": "office",
        "categoryName": "办公费",
    }
    assert admin.get("/api/admin/receipt-keywords").json()["data"] == [mapping]

    duplicate = admin.post(
        "/api/admin/receipt-keywords",
        json={"keyword": "  文具  ", "categoryId": "hospitality"},
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "RECEIPT_KEYWORD_EXISTS"

    for invalid in (
        {"keyword": "水", "categoryId": "utilities"},
        {"keyword": "任意关键词", "categoryId": "subsidy"},
        {"keyword": "任意关键词", "categoryId": "other"},
        {"keyword": "任意关键词", "categoryId": "unknown"},
    ):
        assert (
            admin.post(
                "/api/admin/receipt-keywords",
                json=invalid,
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 422
        )

    updated = admin.put(
        f"/api/admin/receipt-keywords/{mapping['id']}",
        json={"keyword": "办公用品", "categoryId": "office"},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["keyword"] == "办公用品"

    deleted = admin.delete(
        f"/api/admin/receipt-keywords/{mapping['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200
    assert admin.get("/api/admin/receipt-keywords").json()["data"] == []


def test_admin_keyword_is_used_only_as_other_category_fallback(client_factory) -> None:
    engine = FakeOcrEngine(
        {
            "*": [
                OcrLine("电子发票", 0.98),
                OcrLine("开票日期 2026-07-01", 0.98),
                OcrLine("*办公用品*文具", 0.98),
                OcrLine("价税合计 ￥56.43", 0.99),
            ]
        }
    )
    client = client_factory(
        auth_mock_enabled=True,
        auth_mock_user_id="admin-1",
        admin_user_ids="admin-1",
        ocr_enabled=True,
        ocr_engine=engine,
    )
    csrf = mock_login(client)["csrfToken"]
    created = client.post(
        "/api/admin/receipt-keywords",
        json={"keyword": "文具", "categoryId": "office"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    uploaded = client.post(
        "/api/files/upload",
        headers={"X-CSRF-Token": csrf},
        files=[("files[]", ("office.png", _image_bytes(), "image/png"))],
    )
    file_id = uploaded.json()["data"]["files"][0]["id"]

    response = client.post(
        "/api/ocr",
        headers={"X-CSRF-Token": csrf},
        json={"fileIds": [file_id], "tripYear": 2026},
    )

    item = response.json()["data"]["items"][0]
    assert item["categoryId"] == "office"
    assert item["categoryName"] == "办公费"
    assert "MANUAL_REVIEW_REQUIRED" not in item["warnings"]


def test_seeded_keyword_can_be_edited_moved_and_deleted(client_factory) -> None:
    admin = client_factory(
        auth_mock_enabled=True,
        auth_mock_user_id="admin-1",
        admin_user_ids="admin-1",
    )
    csrf = mock_login(admin)["csrfToken"]
    with admin.app.state.database_session_factory() as database:
        mapping = ReceiptKeywordMapping(
            keyword="酒店",
            normalized_keyword="酒店",
            category_id="lodging",
        )
        database.add(mapping)
        database.commit()
        mapping_id = mapping.id

    listed = admin.get("/api/admin/receipt-keywords").json()["data"]
    assert listed == [
        {
            "id": mapping_id,
            "keyword": "酒店",
            "categoryId": "lodging",
            "categoryName": "住宿费",
        }
    ]

    updated = admin.put(
        f"/api/admin/receipt-keywords/{mapping_id}",
        json={"keyword": "酒店住宿", "categoryId": "hospitality"},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    assert updated.json()["data"] == {
        **listed[0],
        "keyword": "酒店住宿",
        "categoryId": "hospitality",
        "categoryName": "招待费",
    }

    deleted = admin.delete(
        f"/api/admin/receipt-keywords/{mapping_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200
    assert admin.get("/api/admin/receipt-keywords").json()["data"] == []
