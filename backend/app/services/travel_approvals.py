from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from app.core.errors import ApiError
from app.integrations.dingtalk.workflow import (
    DingTalkWorkflowClient,
    FormOption,
    FormSchema,
    WorkflowProcessInstance,
)

_DINGTALK_TIME_ZONE = ZoneInfo("Asia/Shanghai")
_PAGE_SIZE = 20
_MAX_LISTED_IDS = 200
_MAX_PAGES_PER_PROFILE = 50
_DETAIL_CONCURRENCY = 5
_MAX_WINDOW_DAYS = 120


class ReimbursementTemplateLike(Protocol):
    process_code: str
    schema: FormSchema
    mappings: dict[str, str]


class TravelTemplateLike(Protocol):
    profile_key: str
    display_name: str
    process_code: str
    schema: FormSchema
    start_date_component_id: str
    end_date_component_id: str
    travel_type_option: FormOption


class OaTemplateCatalogLike(Protocol):
    config_version: int
    reimbursement: ReimbursementTemplateLike
    travel_profiles: tuple[TravelTemplateLike, ...]


@dataclass(frozen=True, slots=True)
class TravelApprovalQueryWindow:
    from_date: date
    to_date: date
    start_time_ms: int
    end_time_ms: int

    @classmethod
    def from_dates(cls, from_date: date, to_date: date) -> TravelApprovalQueryWindow:
        if not isinstance(from_date, date) or not isinstance(to_date, date):
            raise ValueError("query window dates are required")
        if to_date < from_date or (to_date - from_date).days >= _MAX_WINDOW_DAYS:
            raise ApiError(
                "TRAVEL_APPROVAL_QUERY_WINDOW_INVALID",
                "出差审批查询范围必须为连续且不超过 120 天",
                422,
            )
        start = datetime.combine(from_date, time.min, tzinfo=_DINGTALK_TIME_ZONE)
        end_exclusive = datetime.combine(
            to_date + timedelta(days=1),
            time.min,
            tzinfo=_DINGTALK_TIME_ZONE,
        )
        return cls(
            from_date=from_date,
            to_date=to_date,
            start_time_ms=int(start.timestamp() * 1000),
            end_time_ms=int(end_exclusive.timestamp() * 1000) - 1,
        )

    @classmethod
    def latest(cls, *, today: date | None = None) -> TravelApprovalQueryWindow:
        end_date = today or datetime.now(_DINGTALK_TIME_ZONE).date()
        return cls.from_dates(
            end_date - timedelta(days=_MAX_WINDOW_DAYS - 1),
            end_date,
        )

    def as_dict(self) -> dict[str, str]:
        return {"from": self.from_date.isoformat(), "to": self.to_date.isoformat()}


@dataclass(frozen=True, slots=True)
class ListedTravelApproval:
    instance_id: str
    expected_originator_user_id: str
    profile_key: str
    profile_display_name: str
    source_process_code: str
    schema_fingerprint: str
    travel_type_option: FormOption
    start_date_component_id: str
    end_date_component_id: str
    query_window: TravelApprovalQueryWindow


@dataclass(frozen=True, slots=True)
class TravelApprovalCandidate:
    listed: ListedTravelApproval
    instance: WorkflowProcessInstance
    start_date: date
    end_date: date

    def as_dict(self) -> dict[str, object]:
        return {
            "processInstanceId": self.instance.instance_id,
            "profileKey": self.listed.profile_key,
            "profileDisplayName": self.listed.profile_display_name,
            "sourceProcessCode": self.listed.source_process_code,
            "travelTypeOption": self.listed.travel_type_option.as_dict(),
            "title": self.instance.title,
            "businessId": self.instance.business_id,
            "startDate": self.start_date.isoformat(),
            "endDate": self.end_date.isoformat(),
            "createdAt": self.instance.created_at,
            "finishedAt": self.instance.finished_at,
        }


@dataclass(frozen=True, slots=True)
class TravelApprovalSelection:
    profile_key: str
    process_instance_id: str
    query_window: TravelApprovalQueryWindow


@dataclass(frozen=True, slots=True)
class VerifiedTravelSelection:
    approvals: tuple[TravelApprovalCandidate, ...]
    travel_type_option: FormOption
    start_date: date
    end_date: date


def requested_query_window(
    from_date: date | None,
    to_date: date | None,
    *,
    today: date | None = None,
) -> TravelApprovalQueryWindow:
    if from_date is None and to_date is None:
        return TravelApprovalQueryWindow.latest(today=today)
    if from_date is None or to_date is None:
        raise ApiError(
            "TRAVEL_APPROVAL_QUERY_WINDOW_INVALID",
            "自定义查询范围必须同时填写开始和结束日期",
            422,
        )
    return TravelApprovalQueryWindow.from_dates(from_date, to_date)


def runtime_options(catalog: OaTemplateCatalogLike) -> dict[str, object]:
    reimbursement = catalog.reimbursement
    company_options = _mapped_options(reimbursement, "company")
    budget_options = _mapped_options(reimbursement, "budgetCode")
    return {
        "templateConfigVersion": catalog.config_version,
        "reimbursementProcessCode": reimbursement.process_code,
        "companyOptions": [option.as_dict() for option in company_options],
        "budgetCodeOptions": [option.as_dict() for option in budget_options],
        "travelProfiles": [
            {
                "profileKey": profile.profile_key,
                "displayName": profile.display_name,
                "processCode": profile.process_code,
                "schemaFingerprint": profile.schema.fingerprint,
                "travelTypeOption": profile.travel_type_option.as_dict(),
            }
            for profile in catalog.travel_profiles
        ],
    }


async def list_current_user_travel_approvals(
    workflow: DingTalkWorkflowClient,
    catalog: OaTemplateCatalogLike,
    *,
    current_user_id: str,
    query_window: TravelApprovalQueryWindow,
    query: str = "",
) -> tuple[TravelApprovalCandidate, ...]:
    user_id = _required_text(current_user_id, field="current_user_id")
    _validate_window(query_window)
    listed = await _list_approval_references(
        workflow,
        catalog.travel_profiles,
        current_user_id=user_id,
        query_window=query_window,
    )
    resolved = await _resolve_details(workflow, tuple(listed.values()))
    eligible = tuple(
        candidate
        for candidate in resolved
        if candidate is not None and _matches_query(candidate, query)
    )
    return tuple(
        sorted(
            eligible,
            key=lambda item: (
                item.start_date,
                item.instance.created_at,
                item.instance.instance_id,
            ),
            reverse=True,
        )
    )


async def reverify_travel_approval_selection(
    workflow: DingTalkWorkflowClient,
    catalog: OaTemplateCatalogLike,
    *,
    current_user_id: str,
    selections: tuple[TravelApprovalSelection, ...],
) -> VerifiedTravelSelection:
    """Re-prove list membership before trusting selected instance details."""

    if not selections:
        raise ApiError(
            "TRAVEL_APPROVAL_SELECTION_REQUIRED",
            "请至少选择一张已通过的出差审批单",
            422,
        )
    user_id = _required_text(current_user_id, field="current_user_id")
    seen_ids: set[str] = set()
    selections_by_window: dict[TravelApprovalQueryWindow, list[TravelApprovalSelection]] = {}
    for selection in selections:
        _validate_window(selection.query_window)
        instance_id = _required_text(
            selection.process_instance_id,
            field="process_instance_id",
        )
        if instance_id in seen_ids:
            raise ApiError(
                "TRAVEL_APPROVAL_SELECTION_DUPLICATE",
                "同一张出差审批单不能重复选择",
                422,
            )
        seen_ids.add(instance_id)
        selections_by_window.setdefault(selection.query_window, []).append(selection)

    proven: list[ListedTravelApproval] = []
    for query_window, window_selections in selections_by_window.items():
        listed = await _list_approval_references(
            workflow,
            catalog.travel_profiles,
            current_user_id=user_id,
            query_window=query_window,
        )
        for selection in window_selections:
            reference = listed.get(selection.process_instance_id)
            if reference is None or reference.profile_key != selection.profile_key:
                raise ApiError(
                    "TRAVEL_APPROVAL_MEMBERSHIP_CHANGED",
                    "所选出差审批已不在当前用户可关联的审批列表中，请重新选择",
                    409,
                )
            proven.append(reference)

    resolved = await _resolve_details(workflow, tuple(proven))
    if any(item is None for item in resolved):
        raise ApiError(
            "TRAVEL_APPROVAL_MEMBERSHIP_CHANGED",
            "所选出差审批状态或日期已变化，请重新选择",
            409,
        )
    approvals = tuple(item for item in resolved if item is not None)
    travel_type_values = {item.listed.travel_type_option.value for item in approvals}
    if len(travel_type_values) != 1:
        raise ApiError(
            "TRAVEL_APPROVAL_TYPE_MISMATCH",
            "所选出差审批的出差类别不同，不能放在同一张报销单中",
            422,
        )
    return VerifiedTravelSelection(
        approvals=approvals,
        travel_type_option=approvals[0].listed.travel_type_option,
        start_date=min(item.start_date for item in approvals),
        end_date=max(item.end_date for item in approvals),
    )


async def _list_approval_references(
    workflow: DingTalkWorkflowClient,
    profiles: tuple[TravelTemplateLike, ...],
    *,
    current_user_id: str,
    query_window: TravelApprovalQueryWindow,
) -> dict[str, ListedTravelApproval]:
    listed: dict[str, ListedTravelApproval] = {}
    for profile in profiles:
        next_token = 0
        for _page_number in range(_MAX_PAGES_PER_PROFILE):
            page = await workflow.list_process_instance_ids(
                process_code=profile.process_code,
                start_time=query_window.start_time_ms,
                end_time=query_window.end_time_ms,
                next_token=next_token,
                max_results=_PAGE_SIZE,
                user_ids=(current_user_id,),
                statuses=("COMPLETED",),
            )
            for instance_id in page.instance_ids:
                existing = listed.get(instance_id)
                if existing is not None:
                    code = (
                        "TRAVEL_APPROVAL_SOURCE_AMBIGUOUS"
                        if existing.source_process_code != profile.process_code
                        else "TRAVEL_APPROVAL_LIST_INVALID"
                    )
                    raise ApiError(
                        code,
                        "钉钉返回的出差审批来源不唯一，请联系管理员检查模板配置",
                        409,
                    )
                if len(listed) >= _MAX_LISTED_IDS:
                    raise _result_limit_error()
                listed[instance_id] = ListedTravelApproval(
                    instance_id=instance_id,
                    expected_originator_user_id=current_user_id,
                    profile_key=profile.profile_key,
                    profile_display_name=profile.display_name,
                    source_process_code=profile.process_code,
                    schema_fingerprint=profile.schema.fingerprint,
                    travel_type_option=profile.travel_type_option,
                    start_date_component_id=profile.start_date_component_id,
                    end_date_component_id=profile.end_date_component_id,
                    query_window=query_window,
                )
            if page.next_token is None:
                break
            next_token = page.next_token
        else:
            raise _result_limit_error()
    return listed


async def _resolve_details(
    workflow: DingTalkWorkflowClient,
    listed: tuple[ListedTravelApproval, ...],
) -> tuple[TravelApprovalCandidate | None, ...]:
    if not listed:
        return ()
    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def resolve(reference: ListedTravelApproval) -> TravelApprovalCandidate | None:
        async with semaphore:
            instance = await workflow.get_process_instance(reference.instance_id)
        return _eligible_candidate(reference, instance)

    tasks = [asyncio.create_task(resolve(reference)) for reference in listed]
    try:
        done, _pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in done:
            if task.cancelled():
                raise asyncio.CancelledError
            failure = task.exception()
            if failure is not None:
                raise failure
        return tuple(task.result() for task in tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _eligible_candidate(
    listed: ListedTravelApproval,
    instance: WorkflowProcessInstance,
) -> TravelApprovalCandidate | None:
    if (
        instance.instance_id != listed.instance_id
        or instance.originator_user_id != listed.expected_originator_user_id
        or instance.status != "COMPLETED"
        or instance.result != "agree"
    ):
        return None
    start_date = _component_date(instance, listed.start_date_component_id)
    end_date = _component_date(instance, listed.end_date_component_id)
    if start_date is None or end_date is None or end_date < start_date:
        return None
    return TravelApprovalCandidate(
        listed=listed,
        instance=instance,
        start_date=start_date,
        end_date=end_date,
    )


def _component_date(instance: WorkflowProcessInstance, component_id: str) -> date | None:
    value = next(
        (
            form_value.value
            for form_value in instance.form_values
            if form_value.component_id == component_id
        ),
        None,
    )
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _mapped_options(
    reimbursement: ReimbursementTemplateLike,
    logical_field: str,
) -> tuple[FormOption, ...]:
    component_id = reimbursement.mappings.get(logical_field)
    component = next(
        (item for item in reimbursement.schema.components if item.component_id == component_id),
        None,
    )
    if component is None or not component.options:
        raise ApiError(
            "OA_TEMPLATE_CONFIRMATION_REQUIRED",
            "审批模板选项配置已经失效，请管理员重新检查并确认",
            409,
        )
    return component.options


def _validate_window(query_window: TravelApprovalQueryWindow) -> None:
    expected = TravelApprovalQueryWindow.from_dates(
        query_window.from_date,
        query_window.to_date,
    )
    if query_window != expected:
        raise ApiError(
            "TRAVEL_APPROVAL_QUERY_WINDOW_INVALID",
            "出差审批查询时间范围无效",
            422,
        )


def _matches_query(candidate: TravelApprovalCandidate, query: str) -> bool:
    normalized = query.strip().casefold()
    if not normalized:
        return True
    return normalized in candidate.instance.title.casefold() or normalized in (
        candidate.instance.business_id.casefold()
    )


def _required_text(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _result_limit_error() -> ApiError:
    return ApiError(
        "TRAVEL_APPROVAL_RESULT_LIMIT_EXCEEDED",
        "出差审批数量过多，请缩小查询日期范围",
        422,
    )
