from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from enum import StrEnum

from app.core.errors import ApiError
from app.domain.money import money_string, quantize_money

HALF_DAY = Decimal("0.5")
NOON = time(12, 0)
MAX_DAILY_SUBSIDY = Decimal("10000.00")


class TripType(StrEnum):
    BUSINESS = "business"
    SHORT_TERM_PROJECT = "short_term_project"
    LONG_TERM_PROJECT = "long_term_project"
    SAME_CITY_PROJECT = "same_city_project"
    INTERNAL = "internal"
    OVERSEAS = "overseas"

    @property
    def requires_confirmation(self) -> bool:
        return self is TripType.SAME_CITY_PROJECT


@dataclass(frozen=True, slots=True)
class TripPeriod:
    start_date: date
    start_time: time
    end_date: date
    end_time: time


@dataclass(frozen=True, slots=True)
class SubsidyCalculation:
    trip_type: TripType
    calendar_days: int
    effective_days: Decimal
    daily_rate: Decimal
    total: Decimal
    related_approval_id: str | None = None

    def as_api_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "tripType": self.trip_type.value,
            "calendarDays": self.calendar_days,
            "effectiveDays": format(self.effective_days, ".1f"),
            "dailyRate": money_string(self.daily_rate),
            "total": money_string(self.total),
        }
        if self.related_approval_id is not None:
            data["relatedApprovalId"] = self.related_approval_id
        return data


def automatic_effective_days(period: TripPeriod) -> Decimal:
    if period.end_date < period.start_date or (
        period.end_date == period.start_date and period.end_time < period.start_time
    ):
        raise ApiError("INVALID_DATE_RANGE", "结束时间不能早于开始时间", 400)

    if period.start_date == period.end_date:
        crosses_noon = period.start_time < NOON <= period.end_time
        return Decimal("1.0") if crosses_noon else HALF_DAY

    date_difference = (period.end_date - period.start_date).days
    middle_days = max(date_difference - 1, 0)
    departure = Decimal("1.0") if period.start_time < NOON else HALF_DAY
    returned = HALF_DAY if period.end_time < NOON else Decimal("1.0")
    return departure + Decimal(middle_days) + returned


def calculate_subsidy(
    *,
    trip_type: TripType,
    period: TripPeriod,
    configured_daily_rate: Decimal,
    policy_confirmed: bool = False,
    confirmed_effective_days: Decimal | None = None,
    no_subsidy_exception: bool = False,
    manual_subsidy_amount: Decimal | None = None,
    related_approval_id: str | None = None,
) -> SubsidyCalculation:
    calculated_days = automatic_effective_days(period)
    calendar_days = (period.end_date - period.start_date).days + 1

    if manual_subsidy_amount is not None:
        raise ApiError("UNEXPECTED_POLICY_OVERRIDE", "出差补助由管理员设置的每日标准计算", 422)

    if trip_type is TripType.SHORT_TERM_PROJECT and calendar_days > 30:
        raise ApiError(
            "INVALID_TRIP_DURATION",
            "市外项目短期必须为连续 30 个自然日以内（含），请改选市外项目长期",
            422,
        )
    if trip_type is TripType.LONG_TERM_PROJECT and calendar_days <= 30:
        raise ApiError(
            "INVALID_TRIP_DURATION",
            "市外项目长期必须超过 30 个自然日，请改选市外项目短期",
            422,
        )

    if trip_type is TripType.OVERSEAS:
        raise ApiError("SUBSIDY_NOT_SUPPORTED", "境外出差不在当前补助范围内", 422)

    if trip_type.requires_confirmation:
        if not policy_confirmed:
            raise ApiError(
                "POLICY_CONFIRMATION_REQUIRED",
                "境内同市项目需要勾选已按公司制度确认",
                422,
            )
        if confirmed_effective_days is not None or no_subsidy_exception:
            raise ApiError(
                "UNEXPECTED_POLICY_OVERRIDE",
                "有效天数由系统按出发和返回时段自动计算",
                422,
            )
        effective_days = calculated_days
        daily_rate = quantize_money(configured_daily_rate)
    else:
        if policy_confirmed or confirmed_effective_days is not None or no_subsidy_exception:
            raise ApiError(
                "UNEXPECTED_POLICY_OVERRIDE",
                "该出差类型由系统自动计算，不能覆盖天数或标准",
                422,
            )
        effective_days = calculated_days
        daily_rate = quantize_money(configured_daily_rate)

    if trip_type is TripType.INTERNAL and calendar_days > 30:
        effective_days = Decimal("0.0")
        daily_rate = Decimal("0.00")

    total = quantize_money(effective_days * daily_rate)
    return SubsidyCalculation(
        trip_type=trip_type,
        calendar_days=calendar_days,
        effective_days=effective_days,
        daily_rate=daily_rate,
        total=total,
        related_approval_id=related_approval_id,
    )
