from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.integrations.dingtalk.workflow import DingTalkWorkflowClient
from app.schemas.common import success
from app.services.oa_template_profiles import (
    confirm_reimbursement_template,
    current_reimbursement_template,
    inspect_reimbursement_template,
)
from app.services.sessions import CurrentSession, require_admin, require_admin_csrf

router = APIRouter(tags=["oa-template-administration"])


class ProcessCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    process_code: str = Field(
        alias="processCode",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    @field_validator("process_code")
    @classmethod
    def normalize_process_code(cls, value: str) -> str:
        return value.strip()


class TemplateMappingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    company: str = Field(min_length=1, max_length=128)
    budget_code: str = Field(alias="budgetCode", min_length=1, max_length=128)
    travel_type: str = Field(alias="travelType", min_length=1, max_length=128)
    start_date: str = Field(alias="startDate", min_length=1, max_length=128)
    end_date: str = Field(alias="endDate", min_length=1, max_length=128)
    duration_days: str = Field(alias="durationDays", min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=128)
    reimbursement_amount: str = Field(alias="reimbursementAmount", min_length=1, max_length=128)
    related_approvals: str = Field(alias="relatedApprovals", min_length=1, max_length=128)
    attachments: str = Field(min_length=1, max_length=128)

    @field_validator("*")
    @classmethod
    def normalize_component_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("component id must not be blank")
        return normalized

    def as_mapping(self) -> dict[str, str]:
        return {
            "company": self.company,
            "budgetCode": self.budget_code,
            "travelType": self.travel_type,
            "startDate": self.start_date,
            "endDate": self.end_date,
            "durationDays": self.duration_days,
            "description": self.description,
            "reimbursementAmount": self.reimbursement_amount,
            "relatedApprovals": self.related_approvals,
            "attachments": self.attachments,
        }


class TemplateConfirmationRequest(ProcessCodeRequest):
    expected_config_version: int | None = Field(
        alias="expectedConfigVersion",
        ge=1,
    )
    schema_fingerprint: str = Field(
        alias="schemaFingerprint",
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    mappings: TemplateMappingsWrite
    allowed_travel_process_codes: list[str] = Field(
        alias="allowedTravelProcessCodes",
        min_length=1,
        max_length=20,
    )
    related_approval_smoke_test_confirmed: bool = Field(alias="relatedApprovalSmokeTestConfirmed")

    @field_validator("allowed_travel_process_codes")
    @classmethod
    def normalize_travel_process_codes(cls, value: list[str]) -> list[str]:
        return [process_code.strip() for process_code in value]


@router.post("/admin/oa/templates/inspect")
async def inspect_template(
    body: ProcessCodeRequest,
    request: Request,
    database: Annotated[Session, Depends(get_db)],
    _current: Annotated[CurrentSession, Depends(require_admin_csrf)],
) -> dict[str, object]:
    workflow: DingTalkWorkflowClient = request.app.state.dingtalk_workflow
    inspected = await inspect_reimbursement_template(database, workflow, body.process_code)
    return success(inspected)


@router.get("/admin/oa/templates/reimbursement")
def get_reimbursement_template(
    database: Annotated[Session, Depends(get_db)],
    _current: Annotated[CurrentSession, Depends(require_admin)],
) -> dict[str, object]:
    return success(current_reimbursement_template(database))


@router.put("/admin/oa/templates/reimbursement")
async def confirm_template(
    body: TemplateConfirmationRequest,
    request: Request,
    database: Annotated[Session, Depends(get_db)],
    current: Annotated[CurrentSession, Depends(require_admin_csrf)],
) -> dict[str, object]:
    workflow: DingTalkWorkflowClient = request.app.state.dingtalk_workflow
    profile = await confirm_reimbursement_template(
        database,
        workflow,
        process_code=body.process_code,
        expected_config_version=body.expected_config_version,
        inspected_fingerprint=body.schema_fingerprint,
        mappings=body.mappings.as_mapping(),
        allowed_travel_process_codes=body.allowed_travel_process_codes,
        related_approval_smoke_test_confirmed=(body.related_approval_smoke_test_confirmed),
        administrator_user_id=current.record.dingtalk_user_id,
    )
    return success(profile)
