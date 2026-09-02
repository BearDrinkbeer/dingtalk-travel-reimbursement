from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.domain.money import MONEY_QUANTUM
from app.domain.subsidy import MAX_DAILY_SUBSIDY, TripType
from app.models.setting import Setting

logger = logging.getLogger(__name__)

LEGACY_SUBSIDY_KEY = "subsidy_per_day"
CALCULATION_MODE_KEY = "calculation_mode"
DEFAULT_CALCULATION_MODE = "half_day_12"

RATE_KEYS: dict[TripType, str] = {
    TripType.BUSINESS: "subsidy_business_per_day",
    TripType.SHORT_TERM_PROJECT: "subsidy_short_term_project_per_day",
    TripType.LONG_TERM_PROJECT: "subsidy_long_term_project_per_day",
    TripType.SAME_CITY_PROJECT: "subsidy_same_city_project_per_day",
    TripType.INTERNAL: "subsidy_internal_per_day",
}
DEFAULT_RATES: dict[TripType, Decimal] = {
    TripType.BUSINESS: Decimal("100.00"),
    TripType.SHORT_TERM_PROJECT: Decimal("100.00"),
    TripType.LONG_TERM_PROJECT: Decimal("150.00"),
    TripType.SAME_CITY_PROJECT: Decimal("50.00"),
    TripType.INTERNAL: Decimal("100.00"),
}


@dataclass(frozen=True, slots=True)
class ExpenseSettings:
    daily_rates: Mapping[TripType, Decimal]
    calculation_mode: str

    def daily_rate_for(self, trip_type: TripType) -> Decimal:
        return self.daily_rates[trip_type]

    def as_api_dict(self) -> dict[str, object]:
        return {
            "subsidyRates": {
                trip_type.value: format(self.daily_rates[trip_type], ".2f")
                for trip_type in TripType
            },
            "calculationMode": self.calculation_mode,
        }


def ensure_expense_setting_rows(database: Session) -> dict[str, Setting]:
    """Seed per-type rates while preserving the legacy automatic rate safely.

    Existing installations may only have ``subsidy_per_day``. Its raw value is
    copied to the two formerly automatic types and then validated normally; an
    invalid legacy value therefore fails closed instead of silently changing a
    company's configured amount.
    """

    legacy = database.get(Setting, LEGACY_SUBSIDY_KEY)
    changed = False
    if legacy is None:
        legacy = Setting(key=LEGACY_SUBSIDY_KEY, value="100.00")
        database.add(legacy)
        changed = True

    rows: dict[str, Setting] = {LEGACY_SUBSIDY_KEY: legacy}
    for trip_type, key in RATE_KEYS.items():
        row = database.get(Setting, key)
        if row is None:
            if trip_type in {TripType.BUSINESS, TripType.SHORT_TERM_PROJECT}:
                initial_value = legacy.value
            else:
                initial_value = format(DEFAULT_RATES[trip_type], ".2f")
            row = Setting(key=key, value=initial_value)
            database.add(row)
            changed = True
        rows[key] = row

    mode = database.get(Setting, CALCULATION_MODE_KEY)
    if mode is None:
        mode = Setting(key=CALCULATION_MODE_KEY, value=DEFAULT_CALCULATION_MODE)
        database.add(mode)
        changed = True
    rows[CALCULATION_MODE_KEY] = mode
    if changed:
        database.commit()
    return rows


def _configuration_error(key: str) -> ApiError:
    logger.error("Invalid stored expense setting: %s", key)
    return ApiError(
        "INVALID_SYSTEM_CONFIGURATION",
        "系统补助配置无效，请联系管理员",
        500,
    )


def _validated_rate(row: Setting, key: str) -> Decimal:
    try:
        amount = Decimal(row.value)
    except (InvalidOperation, ValueError):
        raise _configuration_error(key) from None
    if (
        not amount.is_finite()
        or amount <= 0
        or amount > MAX_DAILY_SUBSIDY
        or amount.as_tuple().exponent < -2
    ):
        raise _configuration_error(key)
    return amount.quantize(MONEY_QUANTUM)


def get_expense_settings(database: Session) -> ExpenseSettings:
    rows = ensure_expense_setting_rows(database)
    rates = {trip_type: _validated_rate(rows[key], key) for trip_type, key in RATE_KEYS.items()}
    mode = rows[CALCULATION_MODE_KEY]
    if mode.value != DEFAULT_CALCULATION_MODE:
        raise _configuration_error(CALCULATION_MODE_KEY)
    return ExpenseSettings(daily_rates=rates, calculation_mode=mode.value)


def update_expense_settings(
    database: Session,
    *,
    daily_rates: Mapping[TripType, Decimal],
    calculation_mode: str,
) -> ExpenseSettings:
    rows = ensure_expense_setting_rows(database)
    for trip_type, key in RATE_KEYS.items():
        rows[key].value = format(daily_rates[trip_type], ".2f")
    rows[CALCULATION_MODE_KEY].value = calculation_mode
    database.commit()
    return get_expense_settings(database)
