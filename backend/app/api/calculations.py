from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.database.session import get_db
from app.domain.categories import EXPENSE_CATEGORIES
from app.domain.expenses import calculate_expense_totals
from app.domain.subsidy import calculate_subsidy
from app.schemas.common import success
from app.schemas.expenses import TotalsRequest, TripInput
from app.services.application_settings import get_expense_settings
from app.services.sessions import (
    CurrentSession,
    get_current_session,
    require_csrf,
    require_selected_department,
)

router = APIRouter(tags=["reimbursement"])


def _calculate(body: TripInput, database: Session):
    settings = get_expense_settings(database)
    subsidy_trip_type = body.subsidy_trip_type()
    return calculate_subsidy(
        trip_type=subsidy_trip_type,
        period=body.as_period(),
        configured_daily_rate=settings.daily_rate_for(subsidy_trip_type),
        policy_confirmed=body.policy_confirmed,
        confirmed_effective_days=body.confirmed_effective_days,
        no_subsidy_exception=body.no_subsidy_exception,
    )


@router.get("/expense-categories")
def expense_categories(
    _current: Annotated[CurrentSession, Depends(get_current_session)],
) -> dict[str, object]:
    return success([item.as_api_dict() for item in EXPENSE_CATEGORIES])


@router.post("/calculate/subsidy")
def subsidy(
    body: TripInput,
    database: Annotated[Session, Depends(get_db)],
    current: Annotated[CurrentSession, Depends(require_csrf)],
) -> dict[str, object]:
    require_selected_department(current)
    return success(_calculate(body, database).as_api_dict())


@router.post("/calculate/totals")
def totals(
    body: TotalsRequest,
    request: Request,
    database: Annotated[Session, Depends(get_db)],
    current: Annotated[CurrentSession, Depends(require_csrf)],
) -> dict[str, object]:
    require_selected_department(current)
    max_items = request.app.state.settings.expense_max_items
    if len(body.items) > max_items:
        raise ApiError(
            "TOO_MANY_EXPENSE_LINES",
            f"当前部署每张报销单最多处理 {max_items} 条费用明细",
            422,
        )
    subsidy_calculation = _calculate(body.trip, database) if body.trip is not None else None
    result = calculate_expense_totals(body.items, subsidy_calculation).as_api_dict()
    result["subsidy"] = (
        subsidy_calculation.as_api_dict() if subsidy_calculation is not None else None
    )
    return success(result)
