from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.domain.expenses import calculate_expense_totals
from app.domain.subsidy import calculate_subsidy
from app.integrations.dingtalk.workflow import DingTalkWorkflowClient, FormOption
from app.models.project import Project
from app.models.reimbursement import (
    ReimbursementDraft,
    ReimbursementDraftFile,
    ReimbursementDraftFileStatus,
    ReimbursementDraftRelatedApproval,
    ReimbursementDraftStatus,
    ReimbursementOcrStatus,
    utc_now,
)
from app.schemas.excel import ManualProjectInput, SelectedProjectInput
from app.schemas.reimbursements import (
    ReimbursementDraftInput,
    RelatedApprovalSelectionInput,
)
from app.services.application_settings import get_expense_settings
from app.services.oa_template_profiles import (
    OaTemplateCatalogContract,
    require_submission_ready_catalog,
)
from app.services.sessions import CurrentSession, require_selected_department
from app.services.travel_approvals import (
    TravelApprovalQueryWindow,
    TravelApprovalSelection,
    VerifiedTravelSelection,
    reverify_travel_approval_selection,
)

_MUTABLE_STATUSES = (
    ReimbursementDraftStatus.DRAFT.value,
    ReimbursementDraftStatus.REVIEW_READY.value,
)


@dataclass(frozen=True, slots=True)
class DraftActor:
    corp_id: str
    user_id: str
    department_id: str
    department_name: str


@dataclass(frozen=True, slots=True)
class DraftCalculation:
    canonical_json: str
    input_data: dict[str, object]
    totals_data: dict[str, object]


@dataclass(frozen=True, slots=True)
class CatalogBinding:
    process_code: str
    config_version: int
    schema_fingerprint: str

    @classmethod
    def from_catalog(cls, catalog: OaTemplateCatalogContract) -> CatalogBinding:
        return cls(
            process_code=catalog.reimbursement.process_code,
            config_version=catalog.config_version,
            schema_fingerprint=catalog.reimbursement.schema.fingerprint,
        )


def draft_actor(current: CurrentSession) -> DraftActor:
    department_name = require_selected_department(current)
    department_id = str(current.record.current_department_id)
    if not any(
        department.id == department_id and department.name == department_name
        for department in current.departments
    ):
        raise ApiError(
            "INVALID_DEPARTMENT_CONTEXT",
            "当前报销部门不在登录员工的所属部门中，请重新选择",
            409,
        )
    corp_id = str(current.record.corp_id or "").strip()
    user_id = str(current.record.dingtalk_user_id or "").strip()
    if not corp_id or not user_id:
        raise ApiError("UNAUTHORIZED", "登录身份数据无效，请重新进入", 401)
    return DraftActor(
        corp_id=corp_id,
        user_id=user_id,
        department_id=department_id,
        department_name=department_name,
    )


def require_owned_draft(
    database: Session,
    *,
    draft_id: str,
    actor: DraftActor,
    mutable: bool = False,
    now: datetime | None = None,
) -> ReimbursementDraft:
    draft = database.scalar(
        select(ReimbursementDraft).where(
            ReimbursementDraft.id == draft_id,
            ReimbursementDraft.corp_id == actor.corp_id,
            ReimbursementDraft.owner_user_id == actor.user_id,
        )
    )
    if draft is None:
        raise _not_found_error()
    _require_department(draft, actor)
    if mutable:
        _require_mutable(draft, now=now or utc_now())
    return draft


def bump_owned_draft_revision(
    database: Session,
    *,
    draft_id: str,
    actor: DraftActor,
    expected_revision: int,
    now: datetime | None = None,
) -> int:
    """Atomically claim one mutation revision without committing the caller's transaction."""

    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        raise ValueError("expected_revision must be a positive integer")
    changed_at = now or utc_now()
    result = database.execute(
        update(ReimbursementDraft)
        .where(
            ReimbursementDraft.id == draft_id,
            ReimbursementDraft.corp_id == actor.corp_id,
            ReimbursementDraft.owner_user_id == actor.user_id,
            ReimbursementDraft.department_id == actor.department_id,
            ReimbursementDraft.department_name == actor.department_name,
            ReimbursementDraft.revision == expected_revision,
            ReimbursementDraft.status.in_(_MUTABLE_STATUSES),
            ReimbursementDraft.locked_at.is_(None),
            ReimbursementDraft.expires_at > changed_at,
        )
        .values(
            revision=expected_revision + 1,
            status=ReimbursementDraftStatus.DRAFT.value,
            updated_at=changed_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 1:
        return expected_revision + 1

    database.expire_all()
    current = require_owned_draft(database, draft_id=draft_id, actor=actor)
    _require_mutable(current, now=changed_at)
    if current.revision != expected_revision:
        raise ApiError(
            "REIMBURSEMENT_DRAFT_REVISION_CONFLICT",
            "草稿已在其他页面更新，请刷新后重试",
            409,
        )
    raise ApiError(
        "REIMBURSEMENT_DRAFT_REVISION_CONFLICT",
        "草稿状态已变化，请刷新后重试",
        409,
    )


def create_reimbursement_draft(
    database: Session,
    *,
    actor: DraftActor,
    draft_input: ReimbursementDraftInput,
    ttl_days: int,
    max_items: int,
    now: datetime | None = None,
) -> dict[str, object]:
    created_at = now or utc_now()
    catalog = require_submission_ready_catalog(database)
    calculation = validate_and_calculate_input(
        database,
        catalog=catalog,
        draft_input=draft_input,
        max_items=max_items,
        validate_project=True,
    )
    binding = CatalogBinding.from_catalog(catalog)
    draft = ReimbursementDraft(
        corp_id=actor.corp_id,
        owner_user_id=actor.user_id,
        status=ReimbursementDraftStatus.DRAFT.value,
        revision=1,
        department_id=actor.department_id,
        department_name=actor.department_name,
        template_process_code=binding.process_code,
        template_config_version=binding.config_version,
        schema_fingerprint=binding.schema_fingerprint,
        input_json=calculation.canonical_json,
        related_instance_ids_json="[]",
        expires_at=created_at + timedelta(days=ttl_days),
        created_at=created_at,
        updated_at=created_at,
    )
    try:
        database.add(draft)
        database.commit()
        database.refresh(draft)
    except Exception:
        database.rollback()
        raise
    return draft_data(database, draft, calculation=calculation, now=created_at)


def list_reimbursement_drafts(
    database: Session,
    *,
    actor: DraftActor,
    offset: int,
    limit: int,
    now: datetime | None = None,
) -> dict[str, object]:
    statement = select(ReimbursementDraft).where(
        ReimbursementDraft.corp_id == actor.corp_id,
        ReimbursementDraft.owner_user_id == actor.user_id,
        ReimbursementDraft.department_id == actor.department_id,
        ReimbursementDraft.department_name == actor.department_name,
    )
    total = database.scalar(select(func.count()).select_from(statement.subquery())) or 0
    drafts = database.scalars(
        statement.order_by(
            ReimbursementDraft.updated_at.desc(),
            ReimbursementDraft.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    ).all()
    observed_at = now or utc_now()
    return {
        "items": [_draft_summary(draft, now=observed_at) for draft in drafts],
        "offset": offset,
        "limit": limit,
        "total": total,
    }


def get_reimbursement_draft(
    database: Session,
    *,
    actor: DraftActor,
    draft_id: str,
    now: datetime | None = None,
) -> dict[str, object]:
    draft = require_owned_draft(database, draft_id=draft_id, actor=actor)
    return draft_data(database, draft, now=now)


def update_reimbursement_draft(
    database: Session,
    *,
    actor: DraftActor,
    draft_id: str,
    expected_revision: int,
    draft_input: ReimbursementDraftInput,
    max_items: int,
    now: datetime | None = None,
) -> dict[str, object]:
    changed_at = now or utc_now()
    draft = require_owned_draft(
        database,
        draft_id=draft_id,
        actor=actor,
        mutable=True,
        now=changed_at,
    )
    catalog = require_submission_ready_catalog(database)
    _require_catalog_binding(draft, CatalogBinding.from_catalog(catalog))
    calculation = validate_and_calculate_input(
        database,
        catalog=catalog,
        draft_input=draft_input,
        max_items=max_items,
        validate_project=True,
    )
    try:
        new_revision = bump_owned_draft_revision(
            database,
            draft_id=draft_id,
            actor=actor,
            expected_revision=expected_revision,
            now=changed_at,
        )
        database.execute(
            update(ReimbursementDraft)
            .where(
                ReimbursementDraft.id == draft_id,
                ReimbursementDraft.revision == new_revision,
            )
            .values(input_json=calculation.canonical_json)
            .execution_options(synchronize_session=False)
        )
        database.commit()
        database.expire_all()
    except Exception:
        database.rollback()
        raise
    updated = require_owned_draft(database, draft_id=draft_id, actor=actor)
    return draft_data(database, updated, calculation=calculation, now=changed_at)


def mark_reimbursement_draft_review_ready(
    database: Session,
    *,
    actor: DraftActor,
    draft_id: str,
    expected_revision: int,
    max_items: int,
    now: datetime | None = None,
) -> dict[str, object]:
    """Validate a complete local snapshot and atomically mark it review-ready."""

    changed_at = now or utc_now()
    draft = require_owned_draft(
        database,
        draft_id=draft_id,
        actor=actor,
        mutable=True,
        now=changed_at,
    )
    if draft.revision != expected_revision:
        raise _revision_conflict_error()
    catalog = require_submission_ready_catalog(database)
    binding = CatalogBinding.from_catalog(catalog)
    _require_catalog_binding(draft, binding)
    draft_input = _stored_input(draft)
    calculation = validate_and_calculate_input(
        database,
        catalog=catalog,
        draft_input=draft_input,
        max_items=max_items,
        validate_project=True,
    )
    related = database.scalars(
        select(ReimbursementDraftRelatedApproval)
        .where(ReimbursementDraftRelatedApproval.draft_id == draft.id)
        .order_by(ReimbursementDraftRelatedApproval.sort_order)
    ).all()
    _validate_related_snapshot(draft, related, catalog, draft_input=draft_input)
    _validate_file_snapshot(database, draft_id=draft.id)

    try:
        result = database.execute(
            update(ReimbursementDraft)
            .where(
                ReimbursementDraft.id == draft_id,
                ReimbursementDraft.corp_id == actor.corp_id,
                ReimbursementDraft.owner_user_id == actor.user_id,
                ReimbursementDraft.department_id == actor.department_id,
                ReimbursementDraft.department_name == actor.department_name,
                ReimbursementDraft.revision == expected_revision,
                ReimbursementDraft.status.in_(_MUTABLE_STATUSES),
                ReimbursementDraft.locked_at.is_(None),
                ReimbursementDraft.expires_at > changed_at,
            )
            .values(
                revision=expected_revision + 1,
                status=ReimbursementDraftStatus.REVIEW_READY.value,
                updated_at=changed_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            database.rollback()
            require_owned_draft(
                database,
                draft_id=draft_id,
                actor=actor,
                mutable=True,
                now=changed_at,
            )
            raise _revision_conflict_error()
        database.commit()
        database.expire_all()
    except Exception:
        database.rollback()
        raise
    updated = require_owned_draft(database, draft_id=draft_id, actor=actor)
    return draft_data(database, updated, calculation=calculation, now=changed_at)


async def replace_related_approvals(
    database: Session,
    workflow: DingTalkWorkflowClient,
    *,
    actor: DraftActor,
    draft_id: str,
    expected_revision: int,
    selections: list[RelatedApprovalSelectionInput],
    now: datetime | None = None,
) -> dict[str, object]:
    preflight_at = now or utc_now()
    draft = require_owned_draft(
        database,
        draft_id=draft_id,
        actor=actor,
        mutable=True,
        now=preflight_at,
    )
    if draft.revision != expected_revision:
        raise _revision_conflict_error()
    catalog = require_submission_ready_catalog(database)
    binding = CatalogBinding.from_catalog(catalog)
    _require_catalog_binding(draft, binding)
    domain_selections = tuple(
        TravelApprovalSelection(
            profile_key=item.profile_key,
            process_instance_id=item.process_instance_id,
            query_window=TravelApprovalQueryWindow.from_dates(
                item.query_window.from_date,
                item.query_window.to_date,
            ),
        )
        for item in selections
    )

    # CurrentSession, draft ownership and catalog are immutable snapshots now.
    # Release the shared auth/catalog read transaction before remote pagination.
    database.rollback()
    verified = (
        await reverify_travel_approval_selection(
            workflow,
            catalog,
            current_user_id=actor.user_id,
            selections=domain_selections,
        )
        if domain_selections
        else None
    )
    return _store_related_approvals(
        database,
        actor=actor,
        draft_id=draft_id,
        expected_revision=expected_revision,
        expected_binding=binding,
        verified=verified,
        changed_at=now or utc_now(),
    )


def validate_and_calculate_input(
    database: Session,
    *,
    catalog: OaTemplateCatalogContract,
    draft_input: ReimbursementDraftInput,
    max_items: int,
    validate_project: bool,
) -> DraftCalculation:
    if len(draft_input.items) > max_items:
        raise ApiError(
            "TOO_MANY_EXPENSE_LINES",
            f"当前部署每张报销单最多处理 {max_items} 条费用明细",
            422,
        )
    _require_exact_option(catalog, "company", draft_input.company_value)
    _require_exact_option(catalog, "budgetCode", draft_input.budget_code_value)
    if validate_project:
        _validate_project(database, draft_input)
    return _calculate_input(database, draft_input)


def _calculate_input(
    database: Session,
    draft_input: ReimbursementDraftInput,
) -> DraftCalculation:
    subsidy = None
    if draft_input.trip is not None:
        settings = get_expense_settings(database)
        trip_type = draft_input.trip.subsidy_trip_type()
        subsidy = calculate_subsidy(
            trip_type=trip_type,
            period=draft_input.trip.as_period(),
            configured_daily_rate=settings.daily_rate_for(trip_type),
            policy_confirmed=draft_input.trip.policy_confirmed,
            confirmed_effective_days=draft_input.trip.confirmed_effective_days,
            no_subsidy_exception=draft_input.trip.no_subsidy_exception,
        )
    totals = calculate_expense_totals(draft_input.items, subsidy).as_api_dict()
    totals["subsidy"] = subsidy.as_api_dict() if subsidy is not None else None
    input_data = _canonical_input_data(draft_input)
    return DraftCalculation(
        canonical_json=json.dumps(
            input_data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        input_data=input_data,
        totals_data=totals,
    )


def draft_data(
    database: Session,
    draft: ReimbursementDraft,
    *,
    calculation: DraftCalculation | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    if calculation is None:
        stored_input = _stored_input(draft)
        calculation = _calculate_input(database, stored_input)
    related = database.scalars(
        select(ReimbursementDraftRelatedApproval)
        .where(ReimbursementDraftRelatedApproval.draft_id == draft.id)
        .order_by(ReimbursementDraftRelatedApproval.sort_order)
    ).all()
    return {
        **_draft_summary(draft, now=now or utc_now()),
        "template": {
            "processCode": draft.template_process_code,
            "configVersion": draft.template_config_version,
            "schemaFingerprint": draft.schema_fingerprint,
        },
        "input": calculation.input_data,
        "totals": calculation.totals_data,
        "relatedApprovals": [_related_data(item) for item in related],
        "relatedApprovalSummary": _related_summary(related),
    }


def _store_related_approvals(
    database: Session,
    *,
    actor: DraftActor,
    draft_id: str,
    expected_revision: int,
    expected_binding: CatalogBinding,
    verified: VerifiedTravelSelection | None,
    changed_at: datetime,
) -> dict[str, object]:
    current_catalog = require_submission_ready_catalog(database)
    current_binding = CatalogBinding.from_catalog(current_catalog)
    if current_binding != expected_binding:
        raise _template_changed_error()
    current_draft = require_owned_draft(
        database,
        draft_id=draft_id,
        actor=actor,
        mutable=True,
        now=changed_at,
    )
    _require_catalog_binding(current_draft, current_binding)
    approvals = verified.approvals if verified is not None else ()
    instance_ids = [item.instance.instance_id for item in approvals]
    try:
        bump_owned_draft_revision(
            database,
            draft_id=draft_id,
            actor=actor,
            expected_revision=expected_revision,
            now=changed_at,
        )
        database.execute(
            delete(ReimbursementDraftRelatedApproval).where(
                ReimbursementDraftRelatedApproval.draft_id == draft_id
            )
        )
        for sort_order, approval in enumerate(approvals):
            database.add(
                ReimbursementDraftRelatedApproval(
                    draft_id=draft_id,
                    corp_id=actor.corp_id,
                    owner_user_id=actor.user_id,
                    sort_order=sort_order,
                    process_instance_id=approval.instance.instance_id,
                    travel_profile_key=approval.listed.profile_key,
                    process_code=approval.listed.source_process_code,
                    catalog_config_version=current_binding.config_version,
                    travel_schema_fingerprint=approval.listed.schema_fingerprint,
                    listed_from_ms=approval.listed.query_window.start_time_ms,
                    listed_to_ms=approval.listed.query_window.end_time_ms,
                    travel_start_date=approval.start_date,
                    travel_end_date=approval.end_date,
                    title=approval.instance.title,
                    business_id=approval.instance.business_id,
                    instance_created_at=_upstream_datetime(approval.instance.created_at),
                    verified_at=changed_at,
                    created_at=changed_at,
                    updated_at=changed_at,
                )
            )
        database.execute(
            update(ReimbursementDraft)
            .where(ReimbursementDraft.id == draft_id)
            .values(
                related_instance_ids_json=json.dumps(
                    instance_ids,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        )
        database.commit()
        database.expire_all()
    except Exception:
        database.rollback()
        raise
    updated = require_owned_draft(database, draft_id=draft_id, actor=actor)
    return draft_data(database, updated, now=changed_at)


def _canonical_input_data(draft_input: ReimbursementDraftInput) -> dict[str, object]:
    project = draft_input.project.model_dump(mode="json", by_alias=True)
    items = [item.model_dump(mode="json", by_alias=True) for item in draft_input.items]
    trip: dict[str, object] | None = None
    if draft_input.trip is not None:
        trip = draft_input.trip.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
            exclude_defaults=True,
        )
        trip["startTime"] = draft_input.trip.start_time.strftime("%H:%M")
        trip["endTime"] = draft_input.trip.end_time.strftime("%H:%M")
    return {
        "companyValue": draft_input.company_value,
        "budgetCodeValue": draft_input.budget_code_value,
        "project": project,
        "trip": trip,
        "items": items,
    }


def _stored_input(draft: ReimbursementDraft) -> ReimbursementDraftInput:
    try:
        return ReimbursementDraftInput.model_validate_json(draft.input_json)
    except ValueError:
        raise ApiError(
            "REIMBURSEMENT_DRAFT_CORRUPTED",
            "草稿数据损坏，请联系管理员",
            500,
        ) from None


def _validate_project(database: Session, draft_input: ReimbursementDraftInput) -> None:
    if isinstance(draft_input.project, ManualProjectInput):
        return
    if not isinstance(draft_input.project, SelectedProjectInput):
        raise ValueError("unsupported project input")
    project = database.get(Project, draft_input.project.id)
    if project is None or not project.enabled:
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在或已停用", 404)


def _require_exact_option(
    catalog: OaTemplateCatalogContract,
    logical_field: str,
    requested_value: str,
) -> FormOption:
    component_id = catalog.reimbursement.mappings.get(logical_field)
    component = next(
        (
            item
            for item in catalog.reimbursement.schema.components
            if item.component_id == component_id
        ),
        None,
    )
    option = (
        next(
            (item for item in component.options if item.value == requested_value),
            None,
        )
        if component is not None
        else None
    )
    if option is None:
        code = (
            "REIMBURSEMENT_COMPANY_OPTION_INVALID"
            if logical_field == "company"
            else "REIMBURSEMENT_BUDGET_OPTION_INVALID"
        )
        raise ApiError(code, "所选 OA 模板选项已失效，请刷新后重新选择", 422)
    return option


def _require_catalog_binding(
    draft: ReimbursementDraft,
    binding: CatalogBinding,
) -> None:
    if (
        draft.template_process_code != binding.process_code
        or draft.template_config_version != binding.config_version
        or draft.schema_fingerprint != binding.schema_fingerprint
    ):
        raise _template_changed_error()


def _validate_related_snapshot(
    draft: ReimbursementDraft,
    related: list[ReimbursementDraftRelatedApproval],
    catalog: OaTemplateCatalogContract,
    *,
    draft_input: ReimbursementDraftInput,
) -> None:
    if not related:
        raise _not_ready_error("请先关联至少一张已通过的出差审批单")

    expected_ids = [item.process_instance_id for item in related]
    try:
        stored_ids = json.loads(draft.related_instance_ids_json)
    except (TypeError, ValueError):
        raise _corrupted_error() from None
    if stored_ids != expected_ids:
        raise _corrupted_error()

    profiles = {profile.profile_key: profile for profile in catalog.travel_profiles}
    travel_type_values: set[str] = set()
    for item in related:
        profile = profiles.get(item.travel_profile_key)
        if (
            profile is None
            or item.process_code != profile.process_code
            or item.catalog_config_version != catalog.config_version
            or item.travel_schema_fingerprint != profile.schema.fingerprint
        ):
            raise _template_changed_error()
        travel_type_values.add(profile.travel_type_option.value)
    if len(travel_type_values) != 1:
        raise _template_changed_error()

    if draft_input.trip is not None:
        reimbursement_start = draft_input.trip.start_date
        reimbursement_end = draft_input.trip.end_date
    elif draft_input.items:
        reimbursement_start = min(item.date for item in draft_input.items)
        reimbursement_end = max(item.date for item in draft_input.items)
    else:
        return

    if any(
        item.travel_end_date < reimbursement_start
        or item.travel_start_date > reimbursement_end
        for item in related
    ):
        raise ApiError(
            "REIMBURSEMENT_TRAVEL_DATE_MISMATCH",
            "所选出差审批日期与本次报销日期不重叠，请重新选择",
            409,
        )


def _validate_file_snapshot(database: Session, *, draft_id: str) -> None:
    files = database.scalars(
        select(ReimbursementDraftFile).where(ReimbursementDraftFile.draft_id == draft_id)
    ).all()
    if not any(item.file_status == ReimbursementDraftFileStatus.ACTIVE.value for item in files):
        raise _not_ready_error("请先上传至少一个有效附件")
    if any(
        item.file_status
        in {
            ReimbursementDraftFileStatus.RESERVED.value,
            ReimbursementDraftFileStatus.WRITING.value,
            ReimbursementDraftFileStatus.DELETING.value,
        }
        or item.ocr_status == ReimbursementOcrStatus.RUNNING.value
        for item in files
    ):
        raise _not_ready_error("附件仍在上传、删除或识别中，请稍后重试")


def _require_department(draft: ReimbursementDraft, actor: DraftActor) -> None:
    if draft.department_id != actor.department_id or draft.department_name != actor.department_name:
        raise ApiError(
            "REIMBURSEMENT_DRAFT_DEPARTMENT_MISMATCH",
            "草稿所属部门与当前选择不同，请切换部门后重试",
            409,
        )


def _require_mutable(draft: ReimbursementDraft, *, now: datetime) -> None:
    if draft.expires_at <= now or draft.status == ReimbursementDraftStatus.EXPIRED.value:
        raise ApiError("REIMBURSEMENT_DRAFT_EXPIRED", "草稿已过期，请重新创建", 409)
    if draft.status not in _MUTABLE_STATUSES or draft.locked_at is not None:
        raise ApiError("REIMBURSEMENT_DRAFT_LOCKED", "草稿已锁定，不能继续修改", 409)


def _draft_summary(draft: ReimbursementDraft, *, now: datetime) -> dict[str, object]:
    status = ReimbursementDraftStatus.EXPIRED.value if draft.expires_at <= now else draft.status
    return {
        "id": draft.id,
        "status": status,
        "revision": draft.revision,
        "department": {"id": draft.department_id, "name": draft.department_name},
        "templateConfigVersion": draft.template_config_version,
        "relatedApprovalCount": _related_id_count(draft.related_instance_ids_json),
        "expiresAt": _timestamp(draft.expires_at),
        "createdAt": _timestamp(draft.created_at),
        "updatedAt": _timestamp(draft.updated_at),
        "lockedAt": _timestamp(draft.locked_at),
    }


def _related_data(item: ReimbursementDraftRelatedApproval) -> dict[str, object]:
    return {
        "processInstanceId": item.process_instance_id,
        "profileKey": item.travel_profile_key,
        "sourceProcessCode": item.process_code,
        "title": item.title,
        "businessId": item.business_id,
        "startDate": item.travel_start_date.isoformat(),
        "endDate": item.travel_end_date.isoformat(),
        "queryWindow": {
            "startTimeMs": item.listed_from_ms,
            "endTimeMs": item.listed_to_ms,
        },
        "verifiedAt": _timestamp(item.verified_at),
    }


def _related_summary(
    related: list[ReimbursementDraftRelatedApproval],
) -> dict[str, object] | None:
    if not related:
        return None
    return {
        "count": len(related),
        "startDate": min(item.travel_start_date for item in related).isoformat(),
        "endDate": max(item.travel_end_date for item in related).isoformat(),
    }


def _related_id_count(value: str) -> int:
    try:
        decoded = json.loads(value)
        return len(decoded) if isinstance(decoded, list) else 0
    except (TypeError, ValueError):
        return 0


def _upstream_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(UTC).replace(tzinfo=None)
    except (AttributeError, ValueError):
        raise ApiError(
            "TRAVEL_APPROVAL_DETAIL_INVALID",
            "钉钉返回的出差审批时间格式无效，请稍后重试",
            502,
        ) from None


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _not_found_error() -> ApiError:
    return ApiError("REIMBURSEMENT_DRAFT_NOT_FOUND", "草稿不存在", 404)


def _revision_conflict_error() -> ApiError:
    return ApiError(
        "REIMBURSEMENT_DRAFT_REVISION_CONFLICT",
        "草稿已在其他页面更新，请刷新后重试",
        409,
    )


def _template_changed_error() -> ApiError:
    return ApiError(
        "REIMBURSEMENT_DRAFT_TEMPLATE_CHANGED",
        "OA 审批模板已更新，请重新创建草稿",
        409,
    )


def _not_ready_error(message: str) -> ApiError:
    return ApiError("REIMBURSEMENT_DRAFT_NOT_READY", message, 409)


def _corrupted_error() -> ApiError:
    return ApiError(
        "REIMBURSEMENT_DRAFT_CORRUPTED",
        "草稿数据损坏，请联系管理员",
        500,
    )
