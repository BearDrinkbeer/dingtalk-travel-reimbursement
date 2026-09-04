from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.excel import ExcelGenerateRequest
from app.schemas.primitives import StrictCalendarDate

MAX_RELATED_APPROVALS = 20


class ReimbursementDraftInput(ExcelGenerateRequest):
    """Editable values whose authoritative result is recalculated by the server."""

    company_value: str = Field(alias="companyValue", min_length=1, max_length=2048)
    budget_code_value: str = Field(alias="budgetCodeValue", min_length=1, max_length=2048)

    @field_validator("company_value", "budget_code_value")
    @classmethod
    def normalize_oa_option_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("OA option value must not be blank")
        return normalized


class CreateReimbursementDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_revision: int = Field(alias="expectedRevision", ge=0, le=0, strict=True)
    input: ReimbursementDraftInput


class UpdateReimbursementDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_revision: int = Field(alias="expectedRevision", ge=1, strict=True)
    input: ReimbursementDraftInput


class DraftRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_revision: int = Field(alias="expectedRevision", ge=1, strict=True)


class TravelApprovalQueryWindowInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_date: StrictCalendarDate = Field(alias="from")
    to_date: StrictCalendarDate = Field(alias="to")


class RelatedApprovalSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    process_instance_id: str = Field(
        alias="processInstanceId",
        min_length=1,
        max_length=128,
    )
    profile_key: str = Field(alias="profileKey", min_length=1, max_length=64)
    query_window: TravelApprovalQueryWindowInput = Field(alias="queryWindow")

    @field_validator("process_instance_id", "profile_key")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized


class ReplaceRelatedApprovalsRequest(DraftRevisionRequest):
    selections: list[RelatedApprovalSelectionInput] = Field(
        max_length=MAX_RELATED_APPROVALS,
    )

    @model_validator(mode="after")
    def reject_duplicate_instances(self) -> ReplaceRelatedApprovalsRequest:
        instance_ids = [item.process_instance_id for item in self.selections]
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("related approval instances must be unique")
        return self
