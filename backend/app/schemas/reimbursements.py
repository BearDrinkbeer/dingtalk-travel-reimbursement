from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.excel import ExcelExpenseItemInput, ExcelGenerateRequest
from app.schemas.expenses import MAX_EXPENSE_ITEMS_HARD_LIMIT
from app.schemas.primitives import StrictCalendarDate

MAX_RELATED_APPROVALS = 20
CURRENT_OCR_DISPOSITION_VERSION = 1


class ReimbursementDraftExpenseItemInput(ExcelExpenseItemInput):
    """Draft-only receipt provenance; workbook consumers intentionally omit it."""

    source_file_id: str | None = Field(
        default=None,
        alias="sourceFileId",
        min_length=1,
        max_length=36,
    )

    @field_validator("source_file_id")
    @classmethod
    def normalize_source_file_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("sourceFileId must not be blank")
        return normalized


class ReimbursementDraftInput(ExcelGenerateRequest):
    """Editable values whose authoritative result is recalculated by the server."""

    # Stored drafts created before receipt provenance existed have no version field.
    # Keep that absence observable as v0 so the UI/server can migrate them safely
    # instead of treating every terminal OCR result as a newly recovered line.
    ocr_disposition_version: int = Field(
        default=0,
        alias="ocrDispositionVersion",
        ge=0,
        le=CURRENT_OCR_DISPOSITION_VERSION,
        strict=True,
    )
    company_value: str = Field(alias="companyValue", min_length=1, max_length=2048)
    budget_code_value: str = Field(alias="budgetCodeValue", min_length=1, max_length=2048)
    items: list[ReimbursementDraftExpenseItemInput] = Field(
        max_length=MAX_EXPENSE_ITEMS_HARD_LIMIT,
    )
    dismissed_ocr_file_ids: list[str] = Field(
        default_factory=list,
        alias="dismissedOcrFileIds",
        max_length=MAX_EXPENSE_ITEMS_HARD_LIMIT,
    )

    @field_validator("company_value", "budget_code_value")
    @classmethod
    def normalize_oa_option_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("OA option value must not be blank")
        return normalized

    @field_validator("dismissed_ocr_file_ids")
    @classmethod
    def normalize_dismissed_ocr_file_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 36 for value in normalized):
            raise ValueError("dismissedOcrFileIds contains an invalid file id")
        return normalized

    @model_validator(mode="after")
    def validate_ocr_file_dispositions(self) -> ReimbursementDraftInput:
        source_ids = [
            item.source_file_id
            for item in self.items
            if item.source_file_id is not None
        ]
        dismissed_ids = self.dismissed_ocr_file_ids
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("sourceFileId values must be unique within a draft")
        if len(dismissed_ids) != len(set(dismissed_ids)):
            raise ValueError("dismissedOcrFileIds values must be unique within a draft")
        if set(source_ids).intersection(dismissed_ids):
            raise ValueError("an OCR file cannot be both linked and dismissed")
        return self


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
