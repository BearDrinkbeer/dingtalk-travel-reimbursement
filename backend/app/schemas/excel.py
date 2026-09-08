from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.categories import ExpenseCategory
from app.schemas.expenses import (
    MAX_EXPENSE_ITEMS_HARD_LIMIT,
    ExpenseLineBase,
    TripInput,
)
from app.schemas.primitives import StrictCalendarDate


class SelectedProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["selected"]
    id: int = Field(gt=0)


class ManualProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["manual"]
    text: str = Field(min_length=1, max_length=255)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("manual project must not be blank")
        return normalized


class ExcelExpenseItemInput(ExpenseLineBase):
    """Only the line fields needed for authoritative workbook recomputation."""

    date: StrictCalendarDate
    receipt_count: int = Field(alias="receiptCount", ge=1, le=10_000)

    @field_validator("category")
    @classmethod
    def reject_subsidy(cls, value: ExpenseCategory) -> ExpenseCategory:
        if value is ExpenseCategory.SUBSIDY:
            raise ValueError("subsidy is calculated by the server")
        return value


class ExcelGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ManualProjectInput
    # A missing trip explicitly means no travel subsidy is claimed.
    trip: TripInput | None = None
    # The deployment-configured technical limit is enforced by the endpoint.
    # This higher absolute ceiling bounds request parsing even if misconfigured.
    items: list[ExcelExpenseItemInput] = Field(max_length=MAX_EXPENSE_ITEMS_HARD_LIMIT)
