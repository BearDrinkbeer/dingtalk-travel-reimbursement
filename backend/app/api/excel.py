from __future__ import annotations

import logging
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.database.session import get_db
from app.domain.expenses import calculate_expense_totals
from app.domain.subsidy import calculate_subsidy
from app.schemas.excel import ExcelGenerateRequest
from app.services.application_settings import get_expense_settings
from app.services.excel_generator import (
    XLSX_MEDIA_TYPE,
    ResolvedProject,
    content_disposition,
    generate_expense_workbook,
)
from app.services.sessions import CurrentSession, require_csrf, require_selected_department

logger = logging.getLogger(__name__)
router = APIRouter(tags=["excel"])


def _resolve_project(body: ExcelGenerateRequest) -> ResolvedProject:
    return ResolvedProject(
        display_text=body.project.text,
        filename_component=body.project.text,
    )


@router.post("/excel/generate")
def generate_excel(
    body: ExcelGenerateRequest,
    request: Request,
    database: Annotated[Session, Depends(get_db)],
    current: Annotated[CurrentSession, Depends(require_csrf)],
) -> StreamingResponse:
    started_at = perf_counter()
    max_items = request.app.state.settings.expense_max_items
    if len(body.items) > max_items:
        raise ApiError(
            "TOO_MANY_EXPENSE_LINES",
            f"当前部署每张报销单最多处理 {max_items} 条费用明细",
            422,
        )
    department_name = require_selected_department(current)
    project = _resolve_project(body)
    subsidy = None
    if body.trip is not None:
        settings = get_expense_settings(database)
        subsidy_trip_type = body.trip.subsidy_trip_type()
        subsidy = calculate_subsidy(
            trip_type=subsidy_trip_type,
            period=body.trip.as_period(),
            configured_daily_rate=settings.daily_rate_for(subsidy_trip_type),
            policy_confirmed=body.trip.policy_confirmed,
            confirmed_effective_days=body.trip.confirmed_effective_days,
            no_subsidy_exception=body.trip.no_subsidy_exception,
            manual_subsidy_amount=body.trip.manual_subsidy_amount,
        )
    totals = calculate_expense_totals(body.items, subsidy)
    result = generate_expense_workbook(
        template_path=request.app.state.settings.excel_template_path,
        employee_name=current.record.name,
        department_name=department_name,
        project=project,
        trip=body.trip,
        items=body.items,
        subsidy=subsidy,
        totals=totals,
    )
    logger.info(
        "Expense workbook generated",
        extra={
            "line_count": len(body.items) + (1 if subsidy is not None else 0),
            "receipt_count": totals.receipt_count,
            "duration_ms": round((perf_counter() - started_at) * 1000, 2),
        },
    )
    return StreamingResponse(
        iter((result.content,)),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": content_disposition(result.filename)},
    )
