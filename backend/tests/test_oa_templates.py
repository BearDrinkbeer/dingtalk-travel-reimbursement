from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Callable

import httpx
import pytest
from conftest import mock_login

from app.core.errors import ApiError
from app.integrations.dingtalk.client import DingTalkOpenAPIClient
from app.integrations.dingtalk.workflow import DingTalkWorkflowClient
from app.models.oa_template_profile import OaTemplateProfile
from app.services.oa_template_profiles import load_fresh_submission_template

PROCESS_CODE = "PROC-TEST-REIMBURSEMENT"
TRAVEL_PROCESS_CODES = ["PROC-TRAVEL-DOMESTIC", "PROC-TRAVEL-INTERNATIONAL"]

MAPPINGS = {
    "company": "company-id",
    "budgetCode": "budget-id",
    "travelType": "travel-type-id",
    "startDate": "start-id",
    "endDate": "end-id",
    "durationDays": "duration-id",
    "description": "description-id",
    "reimbursementAmount": "amount-id",
    "relatedApprovals": "related-id",
    "attachments": "attachment-id",
}


def component(
    component_type: str,
    component_id: str,
    label: str,
    *,
    options: list[object] | None = None,
    required: bool = True,
    **props: object,
) -> dict[str, object]:
    return {
        "componentName": component_type,
        "props": {
            "id": component_id,
            "label": label,
            "required": required,
            **({"options": options} if options is not None else {}),
            **props,
        },
    }


def schema_payload(
    *,
    modified_at: str = "2026-09-03T10:00:00+08:00",
    company_options: list[object] | None = None,
) -> dict[str, object]:
    if company_options is None:
        company_options = [
            json.dumps({"value": "北京", "key": "option_0"}, ensure_ascii=False),
            json.dumps({"value": "无锡", "key": "option_1"}, ensure_ascii=False),
        ]
    items = [
        component("DDSelectField", "company-id", "所属公司", options=company_options),
        component(
            "DDSelectField",
            "budget-id",
            "预算代码",
            options=[json.dumps({"value": "10000", "key": "budget_0"})],
        ),
        component(
            "DDSelectField",
            "travel-type-id",
            "出差类别",
            options=["商务出差"],
        ),
        component("DDDateField", "start-id", "开始时间", format="yyyy-MM-dd"),
        component("DDDateField", "end-id", "结束时间", format="yyyy-MM-dd"),
        component("NumberField", "duration-id", "时长（天）", unit="天"),
        component("TextareaField", "description-id", "明细说明"),
        component("NumberField", "amount-id", "报销金额", unit="元"),
        component("RelateField", "related-id", "关联审批单"),
        component("DDAttachment", "attachment-id", "附件"),
    ]
    return {
        "result": {
            "formCode": PROCESS_CODE,
            "formUuid": "form-uuid-1",
            "name": "差旅费报销申请",
            "status": "PUBLISHED",
            "gmtModified": modified_at,
            "schemaContent": {"title": "差旅费报销申请", "items": items},
        }
    }


def transport_for_schema(
    response_factory: Callable[[int, httpx.Request], httpx.Response],
) -> tuple[httpx.MockTransport, dict[str, int]]:
    calls = {"token": 0, "schema": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            calls["token"] += 1
            body = json.loads(request.content.decode())
            assert body == {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "grant_type": "client_credentials",
            }
            return httpx.Response(
                200,
                json={"access_token": f"access-{calls['token']}", "expires_in": 7200},
            )

        calls["schema"] += 1
        assert request.method == "GET"
        assert request.url.path == "/v1.0/workflow/forms/schemas/processCodes"
        assert dict(request.url.params) == {"processCode": PROCESS_CODE}
        assert "access_token" not in request.url.params
        assert request.headers["x-acs-dingtalk-access-token"].startswith("access-")
        return response_factory(calls["schema"], request)

    return httpx.MockTransport(handler), calls


class GatedSchemaTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        payloads: list[dict[str, object]],
        started: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self.payloads = payloads
        self.started = started
        self.release = release
        self.schema_calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200,
                json={"access_token": "slow-access-token", "expires_in": 7200},
            )
        self.schema_calls += 1
        self.started.set()
        await self.release.wait()
        payload_index = min(self.schema_calls - 1, len(self.payloads) - 1)
        return httpx.Response(200, json=self.payloads[payload_index])


def admin_client(client_factory, transport: httpx.MockTransport):
    client = client_factory(
        transport=transport,
        auth_mock_enabled=True,
        auth_mock_user_id="admin-1",
        admin_user_ids="admin-1",
    )
    csrf = mock_login(client)["csrfToken"]
    return client, {"X-CSRF-Token": csrf}


def inspect(client, headers: dict[str, str]):
    return client.post(
        "/api/admin/oa/templates/inspect",
        json={"processCode": PROCESS_CODE},
        headers=headers,
    )


def confirm(
    client,
    headers: dict[str, str],
    fingerprint: str,
    mappings=None,
    *,
    allowed_travel_process_codes=None,
    smoke_test_confirmed: bool = True,
    expected_config_version: int | None = None,
):
    return client.put(
        "/api/admin/oa/templates/reimbursement",
        json={
            "processCode": PROCESS_CODE,
            "schemaFingerprint": fingerprint,
            "expectedConfigVersion": expected_config_version,
            "mappings": mappings or MAPPINGS,
            "allowedTravelProcessCodes": (
                allowed_travel_process_codes
                if allowed_travel_process_codes is not None
                else TRAVEL_PROCESS_CODES
            ),
            "relatedApprovalSmokeTestConfirmed": smoke_test_confirmed,
        },
        headers=headers,
    )


def test_inspect_uses_official_signature_and_normalizes_safe_schema(client_factory) -> None:
    payload = schema_payload()
    payload["result"]["schemaContent"]["items"][0]["props"]["uploadUrl"] = (
        "https://secret-upload.invalid/signed"
    )
    transport, calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=payload)
    )
    client, headers = admin_client(client_factory, transport)

    response = inspect(client, headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["compatibilityStatus"] == "UNCONFIGURED"
    assert data["isSubmissionReady"] is False
    assert data["schema"]["formUuid"] == "form-uuid-1"
    assert data["schema"]["modifiedAt"] == "2026-09-03T10:00:00+08:00"
    logical_keys = {item["key"] for item in data["logicalFields"]}
    assert {"startDate", "endDate"} <= logical_keys
    assert not {"startTime", "endTime"} & logical_keys
    company = data["schema"]["components"][0]
    assert company["options"] == [
        {"value": "北京", "label": "北京", "key": "option_0"},
        {"value": "无锡", "label": "无锡", "key": "option_1"},
    ]
    related = next(
        item for item in data["schema"]["components"] if item["componentId"] == "related-id"
    )
    assert related["relatedTemplatePolicy"] == {"mode": "UNKNOWN", "processCodes": []}
    assert "relatedApprovals" in related["compatibleLogicalFields"]
    assert "secret-upload" not in response.text
    assert "client-secret" not in response.text
    assert "access-1" not in response.text
    assert calls == {"token": 1, "schema": 1}


def test_schema_read_refreshes_one_rejected_access_token(client_factory) -> None:
    def response_factory(number: int, request: httpx.Request) -> httpx.Response:
        if number == 1:
            assert request.headers["x-acs-dingtalk-access-token"] == "access-1"
            return httpx.Response(401, json={"code": "InvalidAuthentication"})
        assert request.headers["x-acs-dingtalk-access-token"] == "access-2"
        return httpx.Response(200, json=schema_payload())

    transport, calls = transport_for_schema(response_factory)
    client, headers = admin_client(client_factory, transport)

    response = inspect(client, headers)

    assert response.status_code == 200
    assert calls == {"token": 2, "schema": 2}


def test_schema_permission_error_is_safe(client_factory) -> None:
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(
            403,
            json={
                "code": "Forbidden.AccessDenied.AccessTokenPermissionDenied",
                "message": "sensitive upstream permission detail",
            },
        )
    )
    client, headers = admin_client(client_factory, transport)

    response = inspect(client, headers)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "DINGTALK_PERMISSION_MISSING"
    assert "sensitive" not in response.text


@pytest.mark.parametrize(
    ("upstream_status", "upstream_code"),
    [
        (400, "formNotExist"),
        (400, "aflowProcessCodeIsError"),
        (400, "InvalidParameter"),
        (400, "invalidParameter.processCode"),
        (200, "formNotExist"),
    ],
)
def test_invalid_process_code_has_a_stable_actionable_error(
    client_factory,
    upstream_status,
    upstream_code,
) -> None:
    transport, calls = transport_for_schema(
        lambda _number, _request: httpx.Response(
            upstream_status,
            json={
                "code": upstream_code,
                "message": "sensitive upstream template detail",
            },
        )
    )
    client, headers = admin_client(client_factory, transport)

    response = inspect(client, headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OA_TEMPLATE_PROCESS_CODE_INVALID"
    assert "processCode" in response.json()["error"]["message"]
    assert "sensitive" not in response.text
    assert calls == {"token": 1, "schema": 1}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.pop("result"),
        lambda payload: payload["result"].update({"status": "DISABLED"}),
        lambda payload: payload["result"].update({"formCode": "PROC-OTHER"}),
        lambda payload: payload["result"]["schemaContent"].update({"items": "invalid"}),
        lambda payload: payload["result"]["schemaContent"]["items"].append(
            component("TextField", "company-id", "重复 ID")
        ),
    ],
)
def test_malformed_or_mismatched_schema_fails_closed(client_factory, mutate) -> None:
    payload = schema_payload()
    mutate(payload)
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=payload)
    )
    client, headers = admin_client(client_factory, transport)

    response = inspect(client, headers)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "DINGTALK_FORM_SCHEMA_INVALID"


def test_template_administration_requires_current_admin_and_csrf(client_factory) -> None:
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=schema_payload())
    )
    anonymous = client_factory(transport=transport, auth_mock_enabled=True)
    assert anonymous.get("/api/admin/oa/templates/reimbursement").status_code == 401
    assert (
        anonymous.post(
            "/api/admin/oa/templates/inspect", json={"processCode": PROCESS_CODE}
        ).status_code
        == 401
    )

    login = mock_login(anonymous)
    assert anonymous.get("/api/admin/oa/templates/reimbursement").status_code == 403
    assert inspect(anonymous, {"X-CSRF-Token": login["csrfToken"]}).status_code == 403

    admin, headers = admin_client(client_factory, transport)
    assert inspect(admin, {}).status_code == 403
    inspected = inspect(admin, headers)
    fingerprint = inspected.json()["data"]["schema"]["schemaFingerprint"]
    assert confirm(admin, {}, fingerprint).status_code == 403


@pytest.mark.parametrize(
    ("mapping_change", "message"),
    [
        ({"company": "missing-id"}, "控件不存在"),
        ({"budgetCode": "company-id"}, "不能对应多个"),
        ({"company": "start-id"}, "不支持控件类型"),
    ],
)
def test_mapping_rejects_unknown_duplicate_and_wrong_type(
    client_factory, mapping_change, message
) -> None:
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=schema_payload())
    )
    client, headers = admin_client(client_factory, transport)
    fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]

    response = confirm(client, headers, fingerprint, MAPPINGS | mapping_change)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OA_TEMPLATE_MAPPING_INVALID"
    assert message in response.json()["error"]["message"]


def test_mapping_rejects_unmapped_required_component_and_empty_select(client_factory) -> None:
    extra_required = schema_payload()
    extra_required["result"]["schemaContent"]["items"].append(
        component("TextField", "extra-required", "额外必填字段")
    )
    payloads = [schema_payload(company_options=[]), extra_required]

    for payload, expected in [(payloads[0], "没有可用选项"), (payloads[1], "未映射的必填控件")]:
        transport, _calls = transport_for_schema(
            lambda _number, _request, current=payload: httpx.Response(200, json=current)
        )
        client, headers = admin_client(client_factory, transport)
        fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]
        response = confirm(client, headers, fingerprint)
        assert response.status_code == 400
        assert expected in response.json()["error"]["message"]


def test_mapping_rejects_unmapped_required_component_in_visible_container(
    client_factory,
) -> None:
    payload = schema_payload()
    payload["result"]["schemaContent"]["items"].append(
        {
            "componentName": "FieldGroup",
            "props": {},
            "children": [component("TextField", "nested-required", "容器内必填字段")],
        }
    )
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=payload)
    )
    client, headers = admin_client(client_factory, transport)
    fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]

    response = confirm(client, headers, fingerprint)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OA_TEMPLATE_MAPPING_INVALID"
    assert "容器内必填字段" in response.json()["error"]["message"]


def test_schema_rejects_malformed_json_or_duplicate_select_options(client_factory) -> None:
    payloads = [
        schema_payload(company_options=['{"value":"北京"']),
        schema_payload(
            company_options=[
                json.dumps({"value": "北京", "key": "option_0"}, ensure_ascii=False),
                json.dumps({"value": "北京", "key": "option_1"}, ensure_ascii=False),
            ]
        ),
    ]

    for payload in payloads:
        transport, _calls = transport_for_schema(
            lambda _number, _request, current=payload: httpx.Response(200, json=current)
        )
        client, headers = admin_client(client_factory, transport)
        response = inspect(client, headers)
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "DINGTALK_FORM_SCHEMA_INVALID"


def test_mapping_rejects_an_unsupported_date_format(client_factory) -> None:
    payload = schema_payload()
    start = payload["result"]["schemaContent"]["items"][3]
    start["props"]["format"] = "yyyy-MM-dd HH:mm"
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=payload)
    )
    client, headers = admin_client(client_factory, transport)
    inspected = inspect(client, headers).json()["data"]
    start_component = next(
        item for item in inspected["schema"]["components"] if item["componentId"] == "start-id"
    )
    assert "startDate" not in start_component["compatibleLogicalFields"]
    fingerprint = inspected["schema"]["schemaFingerprint"]

    response = confirm(client, headers, fingerprint)

    assert response.status_code == 400
    assert "yyyy-MM-dd" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("wrapper_state", "ancestor_flag"),
    [({"hidden": True}, "ancestorHidden"), ({"disabled": True}, "ancestorDisabled")],
)
def test_idless_container_state_is_propagated_and_blocks_descendant_mapping(
    client_factory, wrapper_state, ancestor_flag
) -> None:
    payload = schema_payload()
    items = payload["result"]["schemaContent"]["items"]
    company = items[0]
    items[0] = {
        "componentName": "FieldGroup",
        "props": wrapper_state,
        "children": [company],
    }
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=payload)
    )
    client, headers = admin_client(client_factory, transport)

    inspected = inspect(client, headers).json()["data"]
    normalized = next(
        item for item in inspected["schema"]["components"] if item["componentId"] == "company-id"
    )
    assert normalized["parentComponentId"] is None
    assert normalized["nested"] is True
    assert normalized[ancestor_flag] is True
    assert normalized["compatibleLogicalFields"] == []

    response = confirm(
        client,
        headers,
        inspected["schema"]["schemaFingerprint"],
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OA_TEMPLATE_MAPPING_INVALID"


def test_ordinary_field_group_allows_mapping_visible_leaf_component(client_factory) -> None:
    payload = schema_payload()
    items = payload["result"]["schemaContent"]["items"]
    company = items[0]
    items[0] = {
        "componentName": "FieldGroup",
        "props": {},
        "children": [company],
    }
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=payload)
    )
    client, headers = admin_client(client_factory, transport)

    inspected = inspect(client, headers).json()["data"]
    normalized = next(
        item for item in inspected["schema"]["components"] if item["componentId"] == "company-id"
    )
    assert normalized["nested"] is True
    assert normalized["unsupportedContainerAncestor"] is False
    assert "company" in normalized["compatibleLogicalFields"]

    response = confirm(client, headers, inspected["schema"]["schemaFingerprint"])

    assert response.status_code == 200


def test_idless_table_container_marks_and_blocks_subtable_descendant(client_factory) -> None:
    payload = schema_payload()
    items = payload["result"]["schemaContent"]["items"]
    description = items[6]
    items[6] = {
        "componentName": "TableField",
        "props": {},
        "children": [description],
    }
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=payload)
    )
    client, headers = admin_client(client_factory, transport)

    inspected = inspect(client, headers).json()["data"]
    normalized = next(
        item
        for item in inspected["schema"]["components"]
        if item["componentId"] == "description-id"
    )
    assert normalized["parentComponentId"] is None
    assert normalized["nested"] is True
    assert normalized["inSubtable"] is True
    assert normalized["unsupportedContainerAncestor"] is True
    assert normalized["compatibleLogicalFields"] == []

    response = confirm(
        client,
        headers,
        inspected["schema"]["schemaFingerprint"],
    )
    assert response.status_code == 400
    assert "子控件" in response.json()["error"]["message"]


def test_unknown_business_suite_container_blocks_descendant_mapping(client_factory) -> None:
    payload = schema_payload()
    items = payload["result"]["schemaContent"]["items"]
    company = items[0]
    items[0] = {
        "componentName": "TravelBusinessSuite",
        "props": {},
        "children": [company],
    }
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=payload)
    )
    client, headers = admin_client(client_factory, transport)

    inspected = inspect(client, headers).json()["data"]
    normalized = next(
        item for item in inspected["schema"]["components"] if item["componentId"] == "company-id"
    )
    assert normalized["inSubtable"] is False
    assert normalized["unsupportedContainerAncestor"] is True
    assert normalized["compatibleLogicalFields"] == []

    response = confirm(client, headers, inspected["schema"]["schemaFingerprint"])

    assert response.status_code == 400
    assert "复杂业务组件" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("allowed_process_codes", "smoke_test_confirmed", "message"),
    [
        (TRAVEL_PROCESS_CODES, False, "冒烟测试"),
        ([TRAVEL_PROCESS_CODES[0], TRAVEL_PROCESS_CODES[0]], True, "不能重复"),
        ([PROCESS_CODE], True, "不能同时作为"),
        (["invalid process code"], True, "格式无效"),
    ],
)
def test_relationship_requires_explicit_smoke_test_and_valid_local_allowlist(
    client_factory, allowed_process_codes, smoke_test_confirmed, message
) -> None:
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=schema_payload())
    )
    client, headers = admin_client(client_factory, transport)
    fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]

    response = confirm(
        client,
        headers,
        fingerprint,
        allowed_travel_process_codes=allowed_process_codes,
        smoke_test_confirmed=smoke_test_confirmed,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OA_TEMPLATE_RELATIONSHIP_INVALID"
    assert message in response.json()["error"]["message"]


def test_declared_relationship_policy_restricts_the_local_allowlist(client_factory) -> None:
    payload = schema_payload()
    related = payload["result"]["schemaContent"]["items"][8]
    related["props"]["availableTemplates"] = [
        {"name": "境内出差", "processCode": TRAVEL_PROCESS_CODES[0]}
    ]
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=payload)
    )
    client, headers = admin_client(client_factory, transport)
    inspected = inspect(client, headers).json()["data"]
    related_component = next(
        item for item in inspected["schema"]["components"] if item["componentId"] == "related-id"
    )
    assert related_component["relatedTemplatePolicy"] == {
        "mode": "RESTRICTED",
        "processCodes": [TRAVEL_PROCESS_CODES[0]],
    }
    fingerprint = inspected["schema"]["schemaFingerprint"]

    rejected = confirm(client, headers, fingerprint)
    accepted = confirm(
        client,
        headers,
        fingerprint,
        allowed_travel_process_codes=[TRAVEL_PROCESS_CODES[0]],
    )

    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "OA_TEMPLATE_RELATIONSHIP_INVALID"
    assert accepted.status_code == 200
    assert accepted.json()["data"]["isSubmissionReady"] is True


def test_submission_readiness_fails_closed_if_relationship_confirmation_is_lost(
    client_factory,
) -> None:
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=schema_payload())
    )
    client, headers = admin_client(client_factory, transport)
    fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]
    assert confirm(client, headers, fingerprint).status_code == 200
    with client.app.state.database_session_factory() as database:
        profile = database.get(OaTemplateProfile, "reimbursement")
        assert profile is not None
        profile.related_approval_smoke_test_confirmed = False
        database.commit()

    current = client.get("/api/admin/oa/templates/reimbursement")

    assert current.status_code == 200
    assert current.json()["data"]["compatibilityStatus"] == "COMPATIBLE"
    assert current.json()["data"]["isSubmissionReady"] is False
    assert current.json()["data"]["profile"]["isSubmissionReady"] is False
    assert current.json()["data"]["profile"]["relatedApprovalSmokeTestConfirmed"] is False


def test_submission_readiness_is_false_when_travel_allowlist_is_empty(client_factory) -> None:
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=schema_payload())
    )
    client, headers = admin_client(client_factory, transport)
    fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]
    assert confirm(client, headers, fingerprint).status_code == 200
    with client.app.state.database_session_factory() as database:
        profile = database.get(OaTemplateProfile, "reimbursement")
        assert profile is not None
        profile.allowed_travel_process_codes_json = "[]"
        database.commit()

    current = client.get("/api/admin/oa/templates/reimbursement")

    assert current.status_code == 200
    assert current.json()["data"]["isSubmissionReady"] is False
    assert current.json()["data"]["requiresConfirmation"] is True
    assert current.json()["data"]["profile"]["allowedTravelProcessCodes"] == []


def test_submission_readiness_fails_closed_for_self_referential_travel_allowlist(
    client_factory,
) -> None:
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=schema_payload())
    )
    client, headers = admin_client(client_factory, transport)
    fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]
    assert confirm(client, headers, fingerprint).status_code == 200
    with client.app.state.database_session_factory() as database:
        profile = database.get(OaTemplateProfile, "reimbursement")
        assert profile is not None
        profile.allowed_travel_process_codes_json = json.dumps([PROCESS_CODE])
        database.commit()

    current = client.get("/api/admin/oa/templates/reimbursement")

    assert current.status_code == 200
    assert current.json()["data"]["isSubmissionReady"] is False
    assert current.json()["data"]["profile"]["isSubmissionReady"] is False


@pytest.mark.parametrize(
    "stored_allowlist",
    ["not-json", json.dumps([TRAVEL_PROCESS_CODES[0], TRAVEL_PROCESS_CODES[0]])],
)
def test_submission_readiness_fails_closed_for_corrupted_travel_allowlist(
    client_factory,
    stored_allowlist,
) -> None:
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=schema_payload())
    )
    client, headers = admin_client(client_factory, transport)
    fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]
    assert confirm(client, headers, fingerprint).status_code == 200
    with client.app.state.database_session_factory() as database:
        profile = database.get(OaTemplateProfile, "reimbursement")
        assert profile is not None
        profile.allowed_travel_process_codes_json = stored_allowlist
        database.commit()

    current = client.get("/api/admin/oa/templates/reimbursement")

    assert current.status_code == 200
    assert current.json()["data"]["isSubmissionReady"] is False
    assert current.json()["data"]["profile"]["isSubmissionReady"] is False
    assert current.json()["data"]["profile"]["allowedTravelProcessCodes"] == []


def test_submission_readiness_revalidates_the_full_persisted_mapping(client_factory) -> None:
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=schema_payload())
    )
    client, headers = admin_client(client_factory, transport)
    fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]
    assert confirm(client, headers, fingerprint).status_code == 200
    with client.app.state.database_session_factory() as database:
        profile = database.get(OaTemplateProfile, "reimbursement")
        assert profile is not None
        profile.mapping_json = json.dumps(MAPPINGS | {"company": "missing-component"})
        database.commit()

    current = client.get("/api/admin/oa/templates/reimbursement")

    assert current.status_code == 200
    assert current.json()["data"]["isSubmissionReady"] is False
    assert current.json()["data"]["profile"]["isSubmissionReady"] is False


def test_admin_can_reinspect_and_recover_a_malformed_persisted_mapping(client_factory) -> None:
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=schema_payload())
    )
    client, headers = admin_client(client_factory, transport)
    fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]
    assert confirm(client, headers, fingerprint).status_code == 200
    with client.app.state.database_session_factory() as database:
        profile = database.get(OaTemplateProfile, "reimbursement")
        assert profile is not None
        profile.mapping_json = "not-json"
        database.commit()

    current = client.get("/api/admin/oa/templates/reimbursement")
    reinspected = inspect(client, headers)

    assert current.status_code == 200
    assert current.json()["data"]["isSubmissionReady"] is False
    assert current.json()["data"]["profile"]["mappings"] is None
    assert reinspected.status_code == 200
    assert reinspected.json()["data"]["isSubmissionReady"] is False
    assert reinspected.json()["data"]["mappings"] is None


def test_fresh_submission_contract_allows_only_configured_travel_templates(
    client_factory,
) -> None:
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=schema_payload())
    )
    client, headers = admin_client(client_factory, transport)
    fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]
    assert confirm(client, headers, fingerprint).status_code == 200

    async def load_contract():
        return await load_fresh_submission_template(
            client.app.state.database_session_factory,
            client.app.state.dingtalk_workflow,
        )

    contract = asyncio.run(load_contract())

    contract.require_allowed_travel_process(TRAVEL_PROCESS_CODES[0])
    with pytest.raises(ApiError) as rejected:
        contract.require_allowed_travel_process("PROC-TRAVEL-NOT-CONFIGURED")
    assert rejected.value.code == "TRAVEL_APPROVAL_TEMPLATE_NOT_ALLOWED"


def test_fresh_submission_contract_persists_remote_schema_drift(client_factory) -> None:
    original = schema_payload()
    changed = schema_payload(modified_at="2026-09-04T09:30:00+08:00")
    transport, _calls = transport_for_schema(
        lambda number, _request: httpx.Response(
            200,
            json=original if number <= 2 else changed,
        )
    )
    client, headers = admin_client(client_factory, transport)
    fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]
    assert confirm(client, headers, fingerprint).status_code == 200

    async def load_contract():
        return await load_fresh_submission_template(
            client.app.state.database_session_factory,
            client.app.state.dingtalk_workflow,
        )

    with pytest.raises(ApiError) as drifted:
        asyncio.run(load_contract())

    assert drifted.value.code == "OA_TEMPLATE_CONFIRMATION_REQUIRED"
    with client.app.state.database_session_factory() as database:
        profile = database.get(OaTemplateProfile, "reimbursement")
        assert profile is not None
        assert profile.compatibility_status == "DRIFTED"
        assert profile.schema_fingerprint != profile.confirmed_schema_fingerprint


def test_fresh_submission_contract_retries_after_concurrent_admin_reconfirmation(
    client_factory,
) -> None:
    version_one = schema_payload()
    description = version_one["result"]["schemaContent"]["items"][6]
    description["props"]["required"] = False
    version_one["result"]["schemaContent"]["items"].append(
        component(
            "TextareaField",
            "description-v2-id",
            "明细说明新版",
            required=False,
        )
    )
    version_two = copy.deepcopy(version_one)
    version_two["result"]["gmtModified"] = "2026-09-04T09:30:00+08:00"
    current_admin_payload = {"value": version_one}
    initial_transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=current_admin_payload["value"])
    )
    client, headers = admin_client(client_factory, initial_transport)
    fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]
    assert confirm(client, headers, fingerprint).status_code == 200
    version_two_mapping = MAPPINGS | {"description": "description-v2-id"}

    async def exercise_race():
        started = asyncio.Event()
        release = asyncio.Event()
        slow_transport = GatedSchemaTransport([version_one, version_two], started, release)
        slow_client = DingTalkOpenAPIClient(client.app.state.settings, transport=slow_transport)
        try:

            async def load_contract():
                return await load_fresh_submission_template(
                    client.app.state.database_session_factory,
                    DingTalkWorkflowClient(slow_client),
                )

            slow_load = asyncio.create_task(load_contract())
            await started.wait()
            current_admin_payload["value"] = version_two
            version_two_fingerprint = inspect(client, headers).json()["data"]["schema"][
                "schemaFingerprint"
            ]
            saved_response = confirm(
                client,
                headers,
                version_two_fingerprint,
                version_two_mapping,
                expected_config_version=1,
            )
            assert saved_response.status_code == 200
            release.set()
            contract = await slow_load
            return (
                saved_response.json()["data"],
                contract,
                slow_transport.schema_calls,
                version_two_fingerprint,
            )
        finally:
            await slow_client.close()

    saved, contract, schema_calls, version_two_fingerprint = asyncio.run(exercise_race())

    assert saved["configVersion"] == 2
    assert contract.mappings == version_two_mapping
    assert contract.schema.fingerprint == version_two_fingerprint
    assert schema_calls == 2
    with client.app.state.database_session_factory() as database:
        profile = database.get(OaTemplateProfile, "reimbursement")
        assert profile is not None
        assert profile.config_version == 2
        assert profile.compatibility_status == "COMPATIBLE"
        assert profile.schema_fingerprint == version_two_fingerprint
        assert profile.confirmed_schema_fingerprint == version_two_fingerprint
        assert json.loads(profile.schema_json)["modifiedAt"] == "2026-09-04T09:30:00+08:00"
        assert json.loads(profile.mapping_json) == version_two_mapping


def test_confirmed_profile_is_persisted_and_returned_without_refetch(client_factory) -> None:
    transport, calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=schema_payload())
    )
    client, headers = admin_client(client_factory, transport)
    fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]

    saved = confirm(client, headers, fingerprint)
    current = client.get("/api/admin/oa/templates/reimbursement")

    assert saved.status_code == 200
    assert current.status_code == 200
    data = current.json()["data"]
    assert data["configured"] is True
    assert data["compatibilityStatus"] == "COMPATIBLE"
    assert data["isSubmissionReady"] is True
    assert data["profile"]["processCode"] == PROCESS_CODE
    assert data["profile"]["configVersion"] == 1
    assert data["profile"]["mappings"] == MAPPINGS
    assert data["profile"]["allowedTravelProcessCodes"] == TRAVEL_PROCESS_CODES
    assert data["profile"]["relatedApprovalSmokeTestConfirmed"] is True
    assert calls == {"token": 1, "schema": 2}
    with client.app.state.database_session_factory() as database:
        profile = database.get(OaTemplateProfile, "reimbursement")
        assert profile is not None
        assert profile.process_code == PROCESS_CODE
        assert profile.config_version == 1
        assert profile.confirmed_schema_fingerprint == fingerprint
        assert profile.confirmed_by_user_id == "admin-1"
        assert json.loads(profile.allowed_travel_process_codes_json) == TRAVEL_PROCESS_CODES
        assert profile.related_approval_smoke_test_confirmed is True
        assert "client-secret" not in profile.schema_json


def test_stale_unconfigured_confirmation_cannot_overwrite_first_admin(client_factory) -> None:
    payload = schema_payload()
    payload["result"]["schemaContent"]["items"][6]["props"]["required"] = False
    payload["result"]["schemaContent"]["items"].append(
        component("TextareaField", "description-v2-id", "明细说明新版", required=False)
    )
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=payload)
    )
    client, headers = admin_client(client_factory, transport)
    first_inspection = inspect(client, headers).json()["data"]
    second_inspection = inspect(client, headers).json()["data"]
    assert first_inspection["configuredConfigVersion"] is None
    assert second_inspection["configuredConfigVersion"] is None

    first = confirm(
        client,
        headers,
        first_inspection["schema"]["schemaFingerprint"],
        MAPPINGS,
        expected_config_version=None,
    )
    stale = confirm(
        client,
        headers,
        second_inspection["schema"]["schemaFingerprint"],
        MAPPINGS | {"description": "description-v2-id"},
        expected_config_version=None,
    )

    assert first.status_code == 200
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "OA_TEMPLATE_CONFIGURATION_CHANGED"
    current = client.get("/api/admin/oa/templates/reimbursement").json()["data"]["profile"]
    assert current["configVersion"] == 1
    assert current["mappings"] == MAPPINGS


def test_stale_existing_confirmation_cannot_overwrite_newer_admin_mapping(
    client_factory,
) -> None:
    payload = schema_payload()
    payload["result"]["schemaContent"]["items"][6]["props"]["required"] = False
    payload["result"]["schemaContent"]["items"].append(
        component("TextareaField", "description-v2-id", "明细说明新版", required=False)
    )
    transport, _calls = transport_for_schema(
        lambda _number, _request: httpx.Response(200, json=payload)
    )
    client, headers = admin_client(client_factory, transport)
    initial = inspect(client, headers).json()["data"]
    fingerprint = initial["schema"]["schemaFingerprint"]
    assert confirm(client, headers, fingerprint).status_code == 200
    first_admin = inspect(client, headers).json()["data"]
    stale_admin = inspect(client, headers).json()["data"]
    assert first_admin["configuredConfigVersion"] == 1
    assert stale_admin["configuredConfigVersion"] == 1
    version_two_mapping = MAPPINGS | {"description": "description-v2-id"}

    newer = confirm(
        client,
        headers,
        fingerprint,
        version_two_mapping,
        expected_config_version=first_admin["configuredConfigVersion"],
    )
    stale = confirm(
        client,
        headers,
        fingerprint,
        MAPPINGS,
        expected_config_version=stale_admin["configuredConfigVersion"],
    )

    assert newer.status_code == 200
    assert newer.json()["data"]["configVersion"] == 2
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "OA_TEMPLATE_CONFIGURATION_CHANGED"
    current = client.get("/api/admin/oa/templates/reimbursement").json()["data"]["profile"]
    assert current["configVersion"] == 2
    assert current["mappings"] == version_two_mapping


def test_schema_drift_is_durable_and_blocks_readiness_until_reconfirmed(client_factory) -> None:
    original = schema_payload()
    changed = schema_payload(
        modified_at="2026-09-04T09:30:00+08:00",
        company_options=[json.dumps({"value": "苏州", "key": "option_2"})],
    )

    def response_factory(number: int, _request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=original if number <= 2 else changed)

    transport, _calls = transport_for_schema(response_factory)
    client, headers = admin_client(client_factory, transport)
    original_fingerprint = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]
    assert confirm(client, headers, original_fingerprint).status_code == 200

    drift = inspect(client, headers)
    current = client.get("/api/admin/oa/templates/reimbursement")

    assert drift.status_code == 200
    drift_data = drift.json()["data"]
    assert drift_data["compatibilityStatus"] == "DRIFTED"
    assert drift_data["requiresConfirmation"] is True
    assert drift_data["isSubmissionReady"] is False
    changed_fingerprint = drift_data["schema"]["schemaFingerprint"]
    assert changed_fingerprint != original_fingerprint
    assert current.json()["data"]["compatibilityStatus"] == "DRIFTED"
    assert current.json()["data"]["isSubmissionReady"] is False
    assert current.json()["data"]["profile"]["confirmedSchemaFingerprint"] == (original_fingerprint)
    assert current.json()["data"]["profile"]["configVersion"] == 1

    stale_confirmation = confirm(client, headers, original_fingerprint)
    assert stale_confirmation.status_code == 409
    assert stale_confirmation.json()["error"]["code"] == "OA_TEMPLATE_SCHEMA_CHANGED"

    reconfirmed = confirm(
        client,
        headers,
        changed_fingerprint,
        expected_config_version=1,
    )
    assert reconfirmed.status_code == 200
    assert reconfirmed.json()["data"]["compatibilityStatus"] == "COMPATIBLE"
    assert reconfirmed.json()["data"]["isSubmissionReady"] is True
    assert reconfirmed.json()["data"]["configVersion"] == 2


def test_fingerprint_is_stable_across_json_key_and_component_order(client_factory) -> None:
    first = schema_payload()
    reordered = copy.deepcopy(first)
    reordered_items = reordered["result"]["schemaContent"]["items"]
    reordered_items.reverse()
    transport, _calls = transport_for_schema(
        lambda number, _request: httpx.Response(200, json=first if number == 1 else reordered)
    )
    client, headers = admin_client(client_factory, transport)

    first_hash = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]
    second_hash = inspect(client, headers).json()["data"]["schema"]["schemaFingerprint"]

    assert first_hash == second_hash
