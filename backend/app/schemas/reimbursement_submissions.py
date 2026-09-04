from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SubmitReimbursementRequest(BaseModel):
    """Identify the exact reviewed draft revision the employee is submitting."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_revision: int = Field(alias="expectedRevision", ge=1, strict=True)
