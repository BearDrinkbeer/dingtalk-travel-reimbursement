from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.domain.subsidy import MAX_DAILY_SUBSIDY, TripType
from app.schemas.common import success
from app.schemas.primitives import DecimalString
from app.services.application_settings import get_expense_settings, update_expense_settings
from app.services.sessions import CurrentSession, get_current_session, require_admin_csrf

router = APIRouter(tags=["settings"])


class SubsidyRatesWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business: DecimalString = Field(gt=0, le=MAX_DAILY_SUBSIDY)
    short_term_project: DecimalString = Field(gt=0, le=MAX_DAILY_SUBSIDY)
    long_term_project: DecimalString = Field(gt=0, le=MAX_DAILY_SUBSIDY)
    same_city_project: DecimalString = Field(gt=0, le=MAX_DAILY_SUBSIDY)
    internal: DecimalString = Field(gt=0, le=MAX_DAILY_SUBSIDY)

    @field_validator(
        "business",
        "short_term_project",
        "long_term_project",
        "same_city_project",
        "internal",
    )
    @classmethod
    def validate_money_scale(cls, value: Decimal) -> Decimal:
        if value.as_tuple().exponent < -2:
            raise ValueError("subsidy rates support at most two decimal places")
        return value.quantize(Decimal("0.01"))

    def as_domain_mapping(self) -> dict[TripType, Decimal]:
        return {
            TripType.BUSINESS: self.business,
            TripType.SHORT_TERM_PROJECT: self.short_term_project,
            TripType.LONG_TERM_PROJECT: self.long_term_project,
            TripType.SAME_CITY_PROJECT: self.same_city_project,
            TripType.INTERNAL: self.internal,
        }


class SettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    subsidy_rates: SubsidyRatesWrite = Field(alias="subsidyRates")
    calculation_mode: Literal["half_day_12"] = Field(alias="calculationMode")


@router.get("/settings")
def get_application_settings(
    database: Annotated[Session, Depends(get_db)],
    _current: Annotated[CurrentSession, Depends(get_current_session)],
) -> dict[str, object]:
    return success(get_expense_settings(database).as_api_dict())


@router.put("/admin/settings")
def update_application_settings(
    body: SettingsWrite,
    database: Annotated[Session, Depends(get_db)],
    _current: Annotated[CurrentSession, Depends(require_admin_csrf)],
) -> dict[str, object]:
    settings = update_expense_settings(
        database,
        daily_rates=body.subsidy_rates.as_domain_mapping(),
        calculation_mode=body.calculation_mode,
    )
    return success(settings.as_api_dict())
