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
from app.schemas.excel import ExcelGenerateRequest
from app.services.excel_generator import (
    XLSX_MEDIA_TYPE,
    ResolvedProject,
    content_disposition,
    generate_expense_workbook,
)
from app.services.sessions import CurrentSession, require_csrf, require_selected_department
from app.services.subsidy_calculation import (
    calculate_trip_subsidies,
    merge_overlapping_subsidy_trips,
    request_trips,
)

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
    trips = request_trips(trip=body.trip, trips=body.trips)
    output_trips = merge_overlapping_subsidy_trips(trips)
    subsidies = calculate_trip_subsidies(database, trips)
    subsidy = subsidies[0] if body.trip is not None and len(subsidies) == 1 else None
    totals = calculate_expense_totals(body.items, subsidies)
    result = generate_expense_workbook(
        template_path=request.app.state.settings.excel_template_path,
        employee_name=current.record.name,
        department_name=department_name,
        project=project,
        trip=body.trip,
        items=body.items,
        subsidy=subsidy,
        totals=totals,
        trips=output_trips,
        subsidies=subsidies,
    )
    logger.info(
        "Expense workbook generated",
        extra={
            "line_count": len(body.items) + len(subsidies),
            "receipt_count": totals.receipt_count,
            "duration_ms": round((perf_counter() - started_at) * 1000, 2),
        },
    )
    return StreamingResponse(
        iter((result.content,)),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": content_disposition(result.filename)},
    )
