from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import ApiError
from app.integrations.dingtalk.workflow import (
    DingTalkWorkflowClient,
    FormComponent,
    FormSchema,
    form_schema_from_dict,
)
from app.models.oa_template_profile import OaTemplateProfile, utc_now

REIMBURSEMENT_PROFILE_KEY: Final = "reimbursement"

COMPATIBLE = "COMPATIBLE"
DRIFTED = "DRIFTED"
UNCONFIGURED = "UNCONFIGURED"
PROCESS_CODE_CHANGED = "PROCESS_CODE_CHANGED"

_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PROCESS_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MAX_CONFIG_CAS_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class LogicalFieldSpec:
    key: str
    label: str
    supported_component_types: frozenset[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "supportedComponentTypes": sorted(self.supported_component_types),
        }


@dataclass(frozen=True, slots=True)
class ReimbursementTemplateContract:
    """Validated data needed by the future OA submission orchestrator."""

    process_code: str
    schema: FormSchema
    mappings: dict[str, str]
    allowed_travel_process_codes: tuple[str, ...]

    def require_allowed_travel_process(self, actual_process_code: str) -> None:
        """Validate the processCode read back from a selected trip instance."""

        if actual_process_code not in self.allowed_travel_process_codes:
            raise ApiError(
                "TRAVEL_APPROVAL_TEMPLATE_NOT_ALLOWED",
                "所选出差审批不属于允许关联的出差模板",
                400,
            )


LOGICAL_FIELD_SPECS: Final[tuple[LogicalFieldSpec, ...]] = (
    LogicalFieldSpec("company", "所属公司", frozenset({"DDSelectField"})),
    LogicalFieldSpec("budgetCode", "预算代码", frozenset({"DDSelectField"})),
    LogicalFieldSpec("travelType", "出差类别", frozenset({"DDSelectField"})),
    LogicalFieldSpec("startDate", "开始日期", frozenset({"DDDateField"})),
    LogicalFieldSpec("endDate", "结束日期", frozenset({"DDDateField"})),
    LogicalFieldSpec("durationDays", "时长（天）", frozenset({"NumberField"})),
    LogicalFieldSpec(
        "description",
        "明细说明",
        frozenset({"TextField", "TextareaField"}),
    ),
    LogicalFieldSpec(
        "reimbursementAmount",
        "报销金额",
        frozenset({"MoneyField", "NumberField"}),
    ),
    LogicalFieldSpec("relatedApprovals", "关联审批单", frozenset({"RelateField"})),
    LogicalFieldSpec("attachments", "附件", frozenset({"DDAttachment"})),
)
LOGICAL_FIELD_BY_KEY: Final = {item.key: item for item in LOGICAL_FIELD_SPECS}


async def inspect_reimbursement_template(
    database: Session,
    workflow: DingTalkWorkflowClient,
    process_code: str,
) -> dict[str, object]:
    profile: OaTemplateProfile | None = None
    schema: FormSchema | None = None
    status = UNCONFIGURED
    for _attempt in range(_MAX_CONFIG_CAS_ATTEMPTS):
        schema = await workflow.get_form_schema(process_code)
        profile = _fresh_profile(database)
        if profile is None:
            status = UNCONFIGURED
            break
        if profile.process_code != process_code:
            status = PROCESS_CODE_CHANGED
            break
        status = (
            COMPATIBLE if profile.confirmed_schema_fingerprint == schema.fingerprint else DRIFTED
        )
        expected_config_version = profile.config_version
        if _record_schema_observation(
            database,
            schema,
            expected_config_version=expected_config_version,
            expected_schema_fingerprint=profile.schema_fingerprint,
            compatibility_status=status,
        ):
            profile = _fresh_profile(database)
            if (
                profile is not None
                and profile.process_code == process_code
                and profile.config_version == expected_config_version
                and profile.schema_fingerprint == schema.fingerprint
            ):
                status = profile.compatibility_status
                break
            database.rollback()
    else:
        raise _configuration_changed_error()

    if schema is None:
        raise _configuration_changed_error()

    existing_mappings = None
    if profile is not None and profile.process_code == process_code:
        existing_mappings = _display_mapping(profile.mapping_json)
    allowed_travel_process_codes = (
        _display_process_codes(profile.allowed_travel_process_codes_json)
        if profile is not None and profile.process_code == process_code
        else []
    )
    relationship_confirmed = bool(
        profile is not None
        and profile.process_code == process_code
        and profile.related_approval_smoke_test_confirmed
    )
    is_submission_ready = bool(
        profile is not None
        and profile.process_code == process_code
        and _profile_submission_ready(profile)
    )
    return {
        "schema": _schema_api_data(schema),
        "logicalFields": [item.as_dict() for item in LOGICAL_FIELD_SPECS],
        "compatibilityStatus": status,
        "requiresConfirmation": not is_submission_ready,
        "isSubmissionReady": is_submission_ready,
        "configuredProcessCode": profile.process_code if profile is not None else None,
        "configuredConfigVersion": profile.config_version if profile is not None else None,
        "confirmedSchemaFingerprint": (
            profile.confirmed_schema_fingerprint
            if profile is not None and profile.process_code == process_code
            else None
        ),
        "mappings": existing_mappings,
        "allowedTravelProcessCodes": allowed_travel_process_codes,
        "relatedApprovalSmokeTestConfirmed": relationship_confirmed,
    }


async def confirm_reimbursement_template(
    database: Session,
    workflow: DingTalkWorkflowClient,
    *,
    process_code: str,
    expected_config_version: int | None,
    inspected_fingerprint: str,
    mappings: dict[str, str],
    allowed_travel_process_codes: list[str],
    related_approval_smoke_test_confirmed: bool,
    administrator_user_id: str,
) -> dict[str, object]:
    if not _FINGERPRINT_PATTERN.fullmatch(inspected_fingerprint):
        raise _mapping_error("Schema 指纹格式无效")

    schema = await workflow.get_form_schema(process_code)
    if schema.fingerprint != inspected_fingerprint:
        raise ApiError(
            "OA_TEMPLATE_SCHEMA_CHANGED",
            "审批模板已更新，请重新检查字段并确认",
            409,
        )
    normalized_mapping = validate_template_mapping(schema, mappings)
    normalized_travel_process_codes = validate_related_approval_configuration(
        schema,
        normalized_mapping,
        reimbursement_process_code=process_code,
        allowed_travel_process_codes=allowed_travel_process_codes,
        smoke_test_confirmed=related_approval_smoke_test_confirmed,
    )

    now = utc_now()
    profile = _fresh_profile(database)
    values = {
        "process_code": process_code,
        "template_name": schema.template_name,
        "schema_fingerprint": schema.fingerprint,
        "confirmed_schema_fingerprint": schema.fingerprint,
        "schema_json": _serialize_schema(schema),
        "mapping_json": _serialize_mapping(normalized_mapping),
        "allowed_travel_process_codes_json": _serialize_process_codes(
            normalized_travel_process_codes
        ),
        "related_approval_smoke_test_confirmed": True,
        "compatibility_status": COMPATIBLE,
        "confirmed_by_user_id": administrator_user_id,
        "last_checked_at": now,
        "confirmed_at": now,
        "updated_at": now,
    }
    if expected_config_version is None:
        if profile is not None:
            raise _configuration_changed_error()
        profile = OaTemplateProfile(
            profile_key=REIMBURSEMENT_PROFILE_KEY,
            config_version=1,
            created_at=now,
            **values,
        )
        database.add(profile)
        try:
            database.commit()
        except IntegrityError:
            database.rollback()
            raise _configuration_changed_error() from None
    else:
        if profile is None or profile.config_version != expected_config_version:
            raise _configuration_changed_error()
        next_version = expected_config_version + 1
        result = database.execute(
            update(OaTemplateProfile)
            .where(
                OaTemplateProfile.profile_key == REIMBURSEMENT_PROFILE_KEY,
                OaTemplateProfile.config_version == expected_config_version,
            )
            .values(config_version=next_version, **values),
            execution_options={"synchronize_session": False},
        )
        if result.rowcount != 1:
            database.rollback()
            raise _configuration_changed_error()
        database.commit()
    profile = _fresh_profile(database)
    if profile is None:
        raise _configuration_changed_error()
    return template_profile_data(profile)


def current_reimbursement_template(database: Session) -> dict[str, object]:
    profile = _fresh_profile(database)
    if profile is None:
        return {
            "configured": False,
            "compatibilityStatus": UNCONFIGURED,
            "isSubmissionReady": False,
            "requiresConfirmation": True,
            "profile": None,
        }
    is_submission_ready = _profile_submission_ready(profile)
    return {
        "configured": True,
        "compatibilityStatus": profile.compatibility_status,
        "isSubmissionReady": is_submission_ready,
        "requiresConfirmation": not is_submission_ready,
        "profile": template_profile_data(profile),
    }


def require_submission_ready_template(database: Session) -> OaTemplateProfile:
    """Validate persisted state without making a network request.

    Submission orchestration should normally use ``load_fresh_submission_template``
    so checking the remote schema cannot be skipped accidentally.
    """

    profile = _fresh_profile(database)
    if profile is None:
        raise ApiError(
            "OA_TEMPLATE_NOT_CONFIGURED",
            "报销审批模板尚未配置，请联系管理员",
            409,
        )
    if (
        profile.compatibility_status != COMPATIBLE
        or profile.schema_fingerprint != profile.confirmed_schema_fingerprint
    ):
        raise ApiError(
            "OA_TEMPLATE_CONFIRMATION_REQUIRED",
            "审批模板已经变化，请管理员重新确认字段对应关系",
            409,
        )
    if not _profile_relationship_ready(profile):
        raise ApiError(
            "OA_TEMPLATE_RELATIONSHIP_CONFIRMATION_REQUIRED",
            "关联审批模板白名单尚未确认，请管理员完成配置和关联测试",
            409,
        )
    try:
        _validated_persisted_contract(profile)
    except ApiError:
        raise ApiError(
            "OA_TEMPLATE_CONFIRMATION_REQUIRED",
            "审批模板配置已经失效，请管理员重新检查并确认",
            409,
        ) from None
    return profile


async def load_fresh_submission_template(
    database_session_factory: sessionmaker[Session],
    workflow: DingTalkWorkflowClient,
) -> ReimbursementTemplateContract:
    """Refresh compatibility and return a fail-closed submission contract.

    Future submission code should call this once before uploading files. Keeping
    the refresh and readiness check in one boundary prevents callers from using
    a stale locally-compatible profile accidentally.
    """

    for _attempt in range(_MAX_CONFIG_CAS_ATTEMPTS):
        with database_session_factory() as snapshot_database:
            profile = require_submission_ready_template(snapshot_database)
            process_code = profile.process_code
            config_version = profile.config_version
            observed_fingerprint = profile.schema_fingerprint
            confirmed_fingerprint = profile.confirmed_schema_fingerprint

        # The database session is closed before this network wait, so a slow
        # DingTalk response never pins a transaction or pooled connection.
        schema = await workflow.get_form_schema(process_code)
        compatibility_status = (
            COMPATIBLE if schema.fingerprint == confirmed_fingerprint else DRIFTED
        )
        with database_session_factory() as observation_database:
            observed = _record_schema_observation(
                observation_database,
                schema,
                expected_config_version=config_version,
                expected_schema_fingerprint=observed_fingerprint,
                compatibility_status=compatibility_status,
            )
        if not observed:
            continue

        with database_session_factory() as final_database:
            current = final_database.scalar(
                select(OaTemplateProfile).where(
                    OaTemplateProfile.profile_key == REIMBURSEMENT_PROFILE_KEY,
                    OaTemplateProfile.process_code == process_code,
                    OaTemplateProfile.config_version == config_version,
                )
            )
            if current is None:
                continue
            require_submission_ready_template(final_database)
            return _validated_persisted_contract(current)
    raise _configuration_changed_error()


def validate_template_mapping(schema: FormSchema, mappings: dict[str, str]) -> dict[str, str]:
    supplied_keys = set(mappings)
    required_keys = set(LOGICAL_FIELD_BY_KEY)
    if supplied_keys != required_keys:
        missing = sorted(required_keys - supplied_keys)
        unknown = sorted(supplied_keys - required_keys)
        if missing:
            raise _mapping_error(f"缺少字段对应关系：{', '.join(missing)}")
        raise _mapping_error(f"包含未知系统字段：{', '.join(unknown)}")

    normalized: dict[str, str] = {}
    used_component_ids: set[str] = set()
    component_by_id = {component.component_id: component for component in schema.components}
    for spec in LOGICAL_FIELD_SPECS:
        raw_component_id = mappings[spec.key]
        component_id = raw_component_id.strip() if isinstance(raw_component_id, str) else ""
        if not component_id:
            raise _mapping_error(f"{spec.label}尚未选择 OA 控件")
        if component_id in used_component_ids:
            raise _mapping_error("同一个 OA 控件不能对应多个系统字段")
        component = component_by_id.get(component_id)
        if component is None:
            raise _mapping_error(f"{spec.label}对应的 OA 控件不存在")
        incompatibility = _component_incompatibility(component, spec)
        if incompatibility is not None:
            raise _mapping_error(f"{spec.label}{incompatibility}")
        used_component_ids.add(component_id)
        normalized[spec.key] = component_id

    unsupported_required = [
        component.label
        for component in schema.components
        if not component.in_subtable
        and component.required
        and not component.disabled
        and not component.hidden
        and not component.ancestor_disabled
        and not component.ancestor_hidden
        and component.component_id not in used_component_ids
    ]
    if unsupported_required:
        raise _mapping_error("模板还有未映射的必填控件：" + "、".join(unsupported_required))
    return normalized


def validate_related_approval_configuration(
    schema: FormSchema,
    mappings: dict[str, str],
    *,
    reimbursement_process_code: str,
    allowed_travel_process_codes: list[str],
    smoke_test_confirmed: bool,
) -> tuple[str, ...]:
    if not smoke_test_confirmed:
        raise _relationship_error("必须确认已经完成关联审批冒烟测试")
    if not allowed_travel_process_codes:
        raise _relationship_error("至少配置一个允许关联的出差审批模板")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_process_code in allowed_travel_process_codes:
        process_code = raw_process_code.strip() if isinstance(raw_process_code, str) else ""
        if not _PROCESS_CODE_PATTERN.fullmatch(process_code):
            raise _relationship_error("出差审批模板 processCode 格式无效")
        if process_code == reimbursement_process_code:
            raise _relationship_error("报销模板不能同时作为允许关联的出差模板")
        if process_code in seen:
            raise _relationship_error("允许关联的出差审批模板不能重复")
        seen.add(process_code)
        normalized.append(process_code)

    component_by_id = {component.component_id: component for component in schema.components}
    relation_component = component_by_id[mappings["relatedApprovals"]]
    policy = relation_component.related_template_policy
    if policy is None:
        raise _relationship_error("映射控件不是关联审批控件")
    if policy.mode == "RESTRICTED":
        outside_policy = sorted(set(normalized) - set(policy.process_codes))
        if outside_policy:
            raise _relationship_error("出差审批模板不在关联控件声明的允许范围内")
    # UNKNOWN remains UNKNOWN. The explicit local allowlist plus the confirmed
    # smoke test is the fail-closed fallback; it is never interpreted as allow-all.
    return tuple(normalized)


def template_profile_data(profile: OaTemplateProfile) -> dict[str, object]:
    schema = _deserialize_schema(profile.schema_json)
    if schema.fingerprint != profile.schema_fingerprint:
        raise _configuration_error()
    mappings = _display_mapping(profile.mapping_json)
    allowed_travel_process_codes = _display_process_codes(profile.allowed_travel_process_codes_json)
    is_submission_ready = _profile_submission_ready(
        profile,
        schema=schema,
        mappings=mappings,
        allowed_travel_process_codes=allowed_travel_process_codes,
    )
    return {
        "processCode": profile.process_code,
        "configVersion": profile.config_version,
        "templateName": profile.template_name,
        "schemaFingerprint": profile.schema_fingerprint,
        "confirmedSchemaFingerprint": profile.confirmed_schema_fingerprint,
        "compatibilityStatus": profile.compatibility_status,
        "isSubmissionReady": is_submission_ready,
        "requiresConfirmation": not is_submission_ready,
        "schema": _schema_api_data(schema),
        "logicalFields": [item.as_dict() for item in LOGICAL_FIELD_SPECS],
        "mappings": mappings,
        "allowedTravelProcessCodes": allowed_travel_process_codes,
        "relatedApprovalSmokeTestConfirmed": profile.related_approval_smoke_test_confirmed,
        "lastCheckedAt": _timestamp(profile.last_checked_at),
        "confirmedAt": _timestamp(profile.confirmed_at),
        "updatedAt": _timestamp(profile.updated_at),
    }


def _schema_api_data(schema: FormSchema) -> dict[str, object]:
    data = schema.as_dict()
    components = []
    for component in schema.components:
        component_data = component.as_dict()
        component_data["compatibleLogicalFields"] = [
            spec.key for spec in LOGICAL_FIELD_SPECS if _component_supports(component, spec)
        ]
        components.append(component_data)
    data["components"] = components
    return data


def _component_supports(component: FormComponent, spec: LogicalFieldSpec) -> bool:
    return _component_incompatibility(component, spec) is None


def _component_incompatibility(
    component: FormComponent,
    spec: LogicalFieldSpec,
) -> str | None:
    if component.in_subtable or component.unsupported_container_ancestor:
        return "暂不支持映射到明细表或复杂业务组件的子控件"
    if (
        component.disabled
        or component.hidden
        or component.ancestor_disabled
        or component.ancestor_hidden
    ):
        return "不能映射到隐藏或禁用的 OA 控件"
    if component.component_type not in spec.supported_component_types:
        return f"不支持控件类型 {component.component_type}"
    if component.component_type == "DDSelectField" and not component.options:
        return "对应的选择控件没有可用选项"
    if spec.key in {"startDate", "endDate"} and component.value_format != "yyyy-MM-dd":
        return "仅支持 yyyy-MM-dd 日期格式"
    return None


def _fresh_profile(database: Session) -> OaTemplateProfile | None:
    return database.scalar(
        select(OaTemplateProfile)
        .where(OaTemplateProfile.profile_key == REIMBURSEMENT_PROFILE_KEY)
        .execution_options(populate_existing=True)
    )


def _record_schema_observation(
    database: Session,
    schema: FormSchema,
    *,
    expected_config_version: int,
    expected_schema_fingerprint: str,
    compatibility_status: str,
) -> bool:
    result = database.execute(
        update(OaTemplateProfile)
        .where(
            OaTemplateProfile.profile_key == REIMBURSEMENT_PROFILE_KEY,
            OaTemplateProfile.process_code == schema.process_code,
            OaTemplateProfile.config_version == expected_config_version,
            OaTemplateProfile.schema_fingerprint == expected_schema_fingerprint,
        )
        .values(
            template_name=schema.template_name,
            schema_fingerprint=schema.fingerprint,
            schema_json=_serialize_schema(schema),
            compatibility_status=compatibility_status,
            last_checked_at=utc_now(),
        ),
        execution_options={"synchronize_session": False},
    )
    if result.rowcount != 1:
        database.rollback()
        return False
    database.commit()
    return True


def _serialize_schema(schema: FormSchema) -> str:
    return json.dumps(
        schema.as_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _deserialize_schema(raw: str) -> FormSchema:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise _configuration_error() from None
    if not isinstance(value, dict):
        raise _configuration_error()
    return form_schema_from_dict(value)


def _serialize_mapping(mapping: dict[str, str]) -> str:
    return json.dumps(mapping, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _deserialize_mapping(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise _configuration_error() from None
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(component_id, str)
        for key, component_id in value.items()
    ):
        raise _configuration_error()
    return value


def _display_mapping(raw: str) -> dict[str, str] | None:
    try:
        return _deserialize_mapping(raw)
    except ApiError:
        return None


def _serialize_process_codes(process_codes: tuple[str, ...]) -> str:
    return json.dumps(process_codes, ensure_ascii=False, separators=(",", ":"))


def _deserialize_process_codes(raw: str, *, allow_empty: bool = False) -> list[str]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise _configuration_error() from None
    if (
        not isinstance(value, list)
        or (not value and not allow_empty)
        or any(
            not isinstance(process_code, str) or not _PROCESS_CODE_PATTERN.fullmatch(process_code)
            for process_code in value
        )
        or len(set(value)) != len(value)
    ):
        raise _configuration_error()
    return value


def _display_process_codes(raw: str) -> list[str]:
    try:
        return _deserialize_process_codes(raw, allow_empty=True)
    except ApiError:
        return []


def _profile_relationship_ready(profile: OaTemplateProfile) -> bool:
    if not profile.related_approval_smoke_test_confirmed:
        return False
    try:
        return bool(_deserialize_process_codes(profile.allowed_travel_process_codes_json))
    except ApiError:
        return False


def _validated_persisted_contract(
    profile: OaTemplateProfile,
    *,
    schema: FormSchema | None = None,
    mappings: dict[str, str] | None = None,
    allowed_travel_process_codes: list[str] | None = None,
) -> ReimbursementTemplateContract:
    if (
        profile.compatibility_status != COMPATIBLE
        or profile.schema_fingerprint != profile.confirmed_schema_fingerprint
    ):
        raise _configuration_error()
    schema = schema or _deserialize_schema(profile.schema_json)
    if (
        schema.process_code != profile.process_code
        or schema.fingerprint != profile.schema_fingerprint
    ):
        raise _configuration_error()
    normalized_mapping = validate_template_mapping(
        schema,
        mappings if mappings is not None else _deserialize_mapping(profile.mapping_json),
    )
    normalized_process_codes = validate_related_approval_configuration(
        schema,
        normalized_mapping,
        reimbursement_process_code=profile.process_code,
        allowed_travel_process_codes=(
            allowed_travel_process_codes
            if allowed_travel_process_codes is not None
            else _deserialize_process_codes(profile.allowed_travel_process_codes_json)
        ),
        smoke_test_confirmed=profile.related_approval_smoke_test_confirmed,
    )
    return ReimbursementTemplateContract(
        process_code=profile.process_code,
        schema=schema,
        mappings=normalized_mapping,
        allowed_travel_process_codes=normalized_process_codes,
    )


def _profile_submission_ready(
    profile: OaTemplateProfile,
    *,
    schema: FormSchema | None = None,
    mappings: dict[str, str] | None = None,
    allowed_travel_process_codes: list[str] | None = None,
) -> bool:
    try:
        _validated_persisted_contract(
            profile,
            schema=schema,
            mappings=mappings,
            allowed_travel_process_codes=allowed_travel_process_codes,
        )
    except ApiError:
        return False
    return True


def _timestamp(value: datetime) -> str:
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _mapping_error(detail: str) -> ApiError:
    return ApiError("OA_TEMPLATE_MAPPING_INVALID", detail, 400)


def _relationship_error(detail: str) -> ApiError:
    return ApiError("OA_TEMPLATE_RELATIONSHIP_INVALID", detail, 400)


def _configuration_error() -> ApiError:
    return ApiError(
        "INVALID_SYSTEM_CONFIGURATION",
        "审批模板配置无效，请联系管理员重新确认",
        500,
    )


def _configuration_changed_error() -> ApiError:
    return ApiError(
        "OA_TEMPLATE_CONFIGURATION_CHANGED",
        "审批模板配置刚刚被其他管理员更新，请重试",
        409,
    )
