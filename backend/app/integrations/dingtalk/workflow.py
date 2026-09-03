from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from app.core.errors import ApiError
from app.integrations.dingtalk.client import DingTalkOpenAPIClient, DingTalkOpenAPIError

FORM_SCHEMA_PATH = "/v1.0/workflow/forms/schemas/processCodes"
_LAYOUT_CONTAINER_TYPES = frozenset({"FieldGroup"})
_SUBTABLE_CONTAINER_TYPES = frozenset({"DDTableField", "TableField"})
_INVALID_PROCESS_CODE_NAMES = frozenset({"formnotexist", "aflowprocesscodeiserror"})


@dataclass(frozen=True, slots=True)
class FormOption:
    value: str
    label: str
    key: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {"value": self.value, "label": self.label, "key": self.key}


@dataclass(frozen=True, slots=True)
class RelatedTemplatePolicy:
    mode: str
    process_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"mode": self.mode, "processCodes": list(self.process_codes)}


@dataclass(frozen=True, slots=True)
class FormComponent:
    component_id: str
    component_type: str
    label: str
    biz_alias: str | None
    required: bool
    disabled: bool
    hidden: bool
    ancestor_disabled: bool
    ancestor_hidden: bool
    nested: bool
    in_subtable: bool
    unsupported_container_ancestor: bool
    value_format: str | None
    unit: str | None
    options: tuple[FormOption, ...]
    parent_component_id: str | None
    related_template_policy: RelatedTemplatePolicy | None

    def as_dict(self) -> dict[str, object]:
        return {
            "componentId": self.component_id,
            "componentType": self.component_type,
            "label": self.label,
            "bizAlias": self.biz_alias,
            "required": self.required,
            "disabled": self.disabled,
            "hidden": self.hidden,
            "ancestorDisabled": self.ancestor_disabled,
            "ancestorHidden": self.ancestor_hidden,
            "nested": self.nested,
            "inSubtable": self.in_subtable,
            "unsupportedContainerAncestor": self.unsupported_container_ancestor,
            "format": self.value_format,
            "unit": self.unit,
            "options": [option.as_dict() for option in self.options],
            "parentComponentId": self.parent_component_id,
            "relatedTemplatePolicy": (
                self.related_template_policy.as_dict()
                if self.related_template_policy is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class FormSchema:
    process_code: str
    form_code: str | None
    form_uuid: str | None
    modified_at: str | None
    status: str
    template_name: str
    title: str
    components: tuple[FormComponent, ...]
    fingerprint: str

    def as_dict(self) -> dict[str, object]:
        return {
            "processCode": self.process_code,
            "formCode": self.form_code,
            "formUuid": self.form_uuid,
            "modifiedAt": self.modified_at,
            "status": self.status,
            "templateName": self.template_name,
            "title": self.title,
            "schemaFingerprint": self.fingerprint,
            "components": [component.as_dict() for component in self.components],
        }


class DingTalkWorkflowClient:
    """Typed Workflow facade over the shared organization-app HTTP client."""

    def __init__(self, client: DingTalkOpenAPIClient) -> None:
        self._client = client

    async def get_form_schema(self, process_code: str) -> FormSchema:
        # Official contract: GET with processCode in the query string and the
        # organization access token in x-acs-dingtalk-access-token.
        try:
            payload = await self._client.request_openapi_json(
                "GET",
                FORM_SCHEMA_PATH,
                params={"processCode": process_code},
            )
        except DingTalkOpenAPIError as exc:
            if _is_invalid_process_code(exc.upstream_code):
                raise _process_code_error() from None
            raise
        if _is_invalid_process_code(payload.get("code")):
            raise _process_code_error()
        return normalize_form_schema(process_code, payload)


def normalize_form_schema(process_code: str, payload: dict[str, Any]) -> FormSchema:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise _schema_error()

    schema_content = _schema_content(result.get("schemaContent"))
    raw_items = schema_content.get("items")
    if not isinstance(raw_items, list):
        raise _schema_error()

    components: list[FormComponent] = []
    seen_ids: set[str] = set()
    for raw_item in raw_items:
        _collect_components(
            raw_item,
            parent_component_id=None,
            ancestor_disabled=False,
            ancestor_hidden=False,
            nesting_depth=0,
            in_subtable=False,
            unsupported_container_ancestor=False,
            components=components,
            seen_ids=seen_ids,
        )
    if not components:
        raise _schema_error()

    template_name = _optional_text(result.get("name")) or _optional_text(
        schema_content.get("title")
    )
    title = _optional_text(schema_content.get("title")) or template_name
    if not template_name or not title:
        raise _schema_error()

    try:
        form_code = _required_text(result.get("formCode"))
        form_uuid = _required_text(result.get("formUuid"))
        modified_at = _required_text(result.get("gmtModified"))
        status = _required_text(result.get("status"))
    except ValueError:
        raise _schema_error() from None
    if form_code != process_code or status != "PUBLISHED":
        raise _schema_error()
    fingerprint = schema_fingerprint(
        process_code,
        form_code,
        form_uuid,
        modified_at,
        status,
        components,
    )
    return FormSchema(
        process_code=process_code,
        form_code=form_code,
        form_uuid=form_uuid,
        modified_at=modified_at,
        status=status,
        template_name=template_name,
        title=title,
        components=tuple(components),
        fingerprint=fingerprint,
    )


def schema_fingerprint(
    process_code: str,
    form_code: str | None,
    form_uuid: str | None,
    modified_at: str | None,
    status: str,
    components: list[FormComponent] | tuple[FormComponent, ...],
) -> str:
    canonical_components: list[dict[str, object]] = []
    for component in sorted(components, key=lambda item: item.component_id):
        canonical_components.append(
            {
                "componentId": component.component_id,
                "componentType": component.component_type,
                "label": component.label,
                "bizAlias": component.biz_alias,
                "required": component.required,
                "disabled": component.disabled,
                "hidden": component.hidden,
                "ancestorDisabled": component.ancestor_disabled,
                "ancestorHidden": component.ancestor_hidden,
                "nested": component.nested,
                "inSubtable": component.in_subtable,
                "unsupportedContainerAncestor": component.unsupported_container_ancestor,
                "format": component.value_format,
                "unit": component.unit,
                "parentComponentId": component.parent_component_id,
                "relatedTemplatePolicy": (
                    component.related_template_policy.as_dict()
                    if component.related_template_policy is not None
                    else None
                ),
                "options": sorted(
                    (option.as_dict() for option in component.options),
                    key=lambda item: (item["value"] or "", item["label"] or ""),
                ),
            }
        )
    canonical = json.dumps(
        {
            "processCode": process_code,
            "formCode": form_code,
            "formUuid": form_uuid,
            "modifiedAt": modified_at,
            "status": status,
            "components": canonical_components,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def form_schema_from_dict(value: dict[str, Any]) -> FormSchema:
    """Restore a persisted normalized schema without accepting a raw OA payload."""

    try:
        process_code = _required_text(value["processCode"])
        form_code = _required_text(value["formCode"])
        form_uuid = _required_text(value["formUuid"])
        modified_at = _required_text(value["modifiedAt"])
        status = _required_text(value["status"])
        template_name = _required_text(value["templateName"])
        title = _required_text(value["title"])
        raw_components = value["components"]
        if not isinstance(raw_components, list):
            raise TypeError
        components: list[FormComponent] = []
        seen_ids: set[str] = set()
        for raw_component in raw_components:
            if not isinstance(raw_component, dict):
                raise TypeError
            component_id = _required_text(raw_component["componentId"])
            if component_id in seen_ids:
                raise TypeError
            seen_ids.add(component_id)
            raw_options = raw_component.get("options", [])
            if not isinstance(raw_options, list):
                raise TypeError
            options = tuple(
                FormOption(
                    value=_required_text(option["value"]),
                    label=_required_text(option["label"]),
                    key=_optional_text(option.get("key")),
                )
                for option in raw_options
                if isinstance(option, dict)
            )
            if len(options) != len(raw_options):
                raise TypeError
            components.append(
                FormComponent(
                    component_id=component_id,
                    component_type=_required_text(raw_component["componentType"]),
                    label=_required_text(raw_component["label"]),
                    biz_alias=_optional_text(raw_component.get("bizAlias")),
                    required=raw_component.get("required") is True,
                    disabled=raw_component.get("disabled") is True,
                    hidden=raw_component.get("hidden") is True,
                    ancestor_disabled=raw_component.get("ancestorDisabled") is True,
                    ancestor_hidden=raw_component.get("ancestorHidden") is True,
                    nested=raw_component.get("nested") is True,
                    in_subtable=raw_component.get("inSubtable") is True,
                    unsupported_container_ancestor=(
                        raw_component.get("unsupportedContainerAncestor") is True
                    ),
                    value_format=_optional_text(raw_component.get("format")),
                    unit=_optional_text(raw_component.get("unit")),
                    options=options,
                    parent_component_id=_optional_text(raw_component.get("parentComponentId")),
                    related_template_policy=_stored_related_template_policy(
                        raw_component.get("relatedTemplatePolicy")
                    ),
                )
            )
        if form_code != process_code or status != "PUBLISHED":
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise _stored_schema_error() from None

    fingerprint = schema_fingerprint(
        process_code,
        form_code,
        form_uuid,
        modified_at,
        status,
        components,
    )
    return FormSchema(
        process_code=process_code,
        form_code=form_code,
        form_uuid=form_uuid,
        modified_at=modified_at,
        status=status,
        template_name=template_name,
        title=title,
        components=tuple(components),
        fingerprint=fingerprint,
    )


def _schema_content(raw_value: object) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    if isinstance(raw_value, str):
        try:
            decoded = json.loads(raw_value)
        except ValueError:
            raise _schema_error() from None
        if isinstance(decoded, dict):
            return decoded
    raise _schema_error()


def _collect_components(
    raw_item: object,
    parent_component_id: str | None,
    ancestor_disabled: bool,
    ancestor_hidden: bool,
    nesting_depth: int,
    in_subtable: bool,
    unsupported_container_ancestor: bool,
    components: list[FormComponent],
    seen_ids: set[str],
) -> None:
    if not isinstance(raw_item, dict):
        raise _schema_error()
    component_type = _optional_text(raw_item.get("componentName"))
    props = raw_item.get("props")
    if not component_type or not isinstance(props, dict):
        raise _schema_error()

    component_id = _optional_text(props.get("id"))
    children = raw_item.get("children", [])
    if children is None:
        children = []
    if not isinstance(children, list):
        raise _schema_error()
    disabled = props.get("disabled") is True
    hidden = props.get("hidden") is True

    if component_id:
        if component_id in seen_ids:
            raise _schema_error()
        seen_ids.add(component_id)
        components.append(
            FormComponent(
                component_id=component_id,
                component_type=component_type,
                label=_optional_text(props.get("label")) or component_id,
                biz_alias=_optional_text(props.get("bizAlias")),
                required=props.get("required") is True,
                disabled=disabled,
                hidden=hidden,
                ancestor_disabled=ancestor_disabled,
                ancestor_hidden=ancestor_hidden,
                nested=nesting_depth > 0,
                in_subtable=in_subtable,
                unsupported_container_ancestor=unsupported_container_ancestor,
                value_format=_optional_text(props.get("format")),
                unit=_optional_text(props.get("unit")),
                options=_normalize_options(props),
                parent_component_id=parent_component_id,
                related_template_policy=_related_template_policy(component_type, props),
            )
        )
    elif not children:
        raise _schema_error()

    child_parent_id = component_id or parent_component_id
    is_complex_container = bool(children) and component_type not in _LAYOUT_CONTAINER_TYPES
    for child in children:
        _collect_components(
            child,
            parent_component_id=child_parent_id,
            ancestor_disabled=ancestor_disabled or disabled,
            ancestor_hidden=ancestor_hidden or hidden,
            nesting_depth=nesting_depth + 1,
            in_subtable=in_subtable or component_type in _SUBTABLE_CONTAINER_TYPES,
            unsupported_container_ancestor=(unsupported_container_ancestor or is_complex_container),
            components=components,
            seen_ids=seen_ids,
        )


def _normalize_options(props: dict[str, Any]) -> tuple[FormOption, ...]:
    raw_options = props.get("options")
    if raw_options is None:
        raw_options = props.get("objOptions", [])
    if raw_options is None:
        return ()
    if not isinstance(raw_options, list):
        raise _schema_error()

    options: list[FormOption] = []
    seen_values: set[str] = set()
    seen_keys: set[str] = set()
    for raw_option in raw_options:
        if isinstance(raw_option, str):
            decoded_option: object = None
            try:
                decoded_option = json.loads(raw_option)
            except ValueError:
                if raw_option.lstrip().startswith(("{", "[")):
                    raise _schema_error() from None
            if isinstance(decoded_option, dict):
                raw_option = decoded_option
            else:
                value = raw_option.strip()
                label = value
                key = None
        if isinstance(raw_option, dict):
            value = _optional_text(raw_option.get("value")) or _optional_text(raw_option.get("key"))
            label = (
                _optional_text(raw_option.get("label"))
                or _optional_text(raw_option.get("text"))
                or value
            )
            key = _optional_text(raw_option.get("key"))
        elif not isinstance(raw_option, str):
            raise _schema_error()
        if not value or not label:
            raise _schema_error()
        if value in seen_values:
            raise _schema_error()
        if key is not None and key in seen_keys:
            raise _schema_error()
        seen_values.add(value)
        if key is not None:
            seen_keys.add(key)
        options.append(FormOption(value=value, label=label, key=key))
    return tuple(options)


def _related_template_policy(
    component_type: str,
    props: dict[str, Any],
) -> RelatedTemplatePolicy | None:
    if component_type != "RelateField":
        return None

    raw_templates = props.get("availableTemplates")
    if not isinstance(raw_templates, list) or not raw_templates:
        # The current official schema response does not declare this property.
        # Missing/empty therefore cannot safely be interpreted as allow-all.
        return RelatedTemplatePolicy(mode="UNKNOWN", process_codes=())

    process_codes: list[str] = []
    for raw_template in raw_templates:
        if isinstance(raw_template, str):
            process_code = raw_template.strip()
        elif isinstance(raw_template, dict):
            process_code = (
                _optional_text(raw_template.get("processCode"))
                or _optional_text(raw_template.get("code"))
                or ""
            )
        else:
            return RelatedTemplatePolicy(mode="UNKNOWN", process_codes=())
        if not process_code:
            return RelatedTemplatePolicy(mode="UNKNOWN", process_codes=())
        process_codes.append(process_code)
    return RelatedTemplatePolicy(
        mode="RESTRICTED",
        process_codes=tuple(sorted(set(process_codes))),
    )


def _stored_related_template_policy(raw: object) -> RelatedTemplatePolicy | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TypeError
    mode = _required_text(raw.get("mode"))
    raw_codes = raw.get("processCodes")
    if mode not in {"UNKNOWN", "RESTRICTED"} or not isinstance(raw_codes, list):
        raise TypeError
    codes = tuple(_required_text(item) for item in raw_codes)
    if mode == "UNKNOWN" and codes:
        raise TypeError
    if mode == "RESTRICTED" and not codes:
        raise TypeError
    return RelatedTemplatePolicy(mode=mode, process_codes=codes)


def _required_text(value: object) -> str:
    normalized = _optional_text(value)
    if not normalized:
        raise ValueError
    return normalized


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _is_invalid_process_code(value: object) -> bool:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return False
    normalized = re.sub(r"[^a-z0-9]", "", str(value).lower())
    return normalized in _INVALID_PROCESS_CODE_NAMES or normalized.startswith("invalidparameter")


def _process_code_error() -> ApiError:
    return ApiError(
        "OA_TEMPLATE_PROCESS_CODE_INVALID",
        "未找到对应的钉钉审批模板，请检查 processCode 是否正确且模板已发布",
        400,
    )


def _schema_error() -> ApiError:
    return ApiError(
        "DINGTALK_FORM_SCHEMA_INVALID",
        "钉钉审批模板返回的数据无效，请联系管理员",
        502,
    )


def _stored_schema_error() -> ApiError:
    return ApiError(
        "INVALID_SYSTEM_CONFIGURATION",
        "审批模板配置无效，请联系管理员重新确认",
        500,
    )
