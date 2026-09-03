from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.integrations.dingtalk.workflow import DingTalkWorkflowClient
from app.schemas.common import success
from app.services.oa_template_profiles import require_submission_ready_catalog
from app.services.sessions import CurrentSession, get_current_session
from app.services.travel_approvals import (
    list_current_user_travel_approvals,
    requested_query_window,
    runtime_options,
)

router = APIRouter(tags=["oa-reimbursements"])


@router.get("/oa/reimbursements/options")
def get_reimbursement_options(
    database: Annotated[Session, Depends(get_db)],
    _current: Annotated[CurrentSession, Depends(get_current_session)],
) -> dict[str, object]:
    catalog = require_submission_ready_catalog(database)
    return success(runtime_options(catalog))


@router.get("/oa/travel-approvals")
async def get_current_user_travel_approvals(
    request: Request,
    database: Annotated[Session, Depends(get_db)],
    current: Annotated[CurrentSession, Depends(get_current_session)],
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    query: Annotated[str, Query(alias="q", max_length=100)] = "",
) -> dict[str, object]:
    catalog = require_submission_ready_catalog(database)
    query_window = requested_query_window(from_date, to_date)
    workflow: DingTalkWorkflowClient = request.app.state.dingtalk_workflow
    current_user_id = current.record.dingtalk_user_id
    # The catalog and identity are immutable snapshots from this point. End the
    # shared authentication/catalog read transaction before remote pagination.
    database.rollback()
    candidates = await list_current_user_travel_approvals(
        workflow,
        catalog,
        current_user_id=current_user_id,
        query_window=query_window,
        query=query,
    )
    return success(
        {
            "templateConfigVersion": catalog.config_version,
            "queryWindow": query_window.as_dict(),
            "items": [candidate.as_dict() for candidate in candidates],
        }
    )
