from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.subsidy import SubsidyCalculation, calculate_subsidy
from app.schemas.expenses import TripInput, TripPurpose
from app.services.application_settings import get_expense_settings


def subsidy_purpose_for_travel_type(label: str) -> TripPurpose | None:
    """Map the configured OA travel-category label to the subsidy policy family."""

    normalized = "".join(label.split())
    if "境外" in normalized:
        return TripPurpose.OVERSEAS
    if "同市" in normalized or "市内项目" in normalized:
        return TripPurpose.SAME_CITY_PROJECT
    if "公司内部" in normalized:
        return TripPurpose.INTERNAL
    if "市外项目" in normalized:
        return TripPurpose.PROJECT
    if "商务" in normalized:
        return TripPurpose.BUSINESS
    return None


def calculate_trip_subsidies(
    database: Session,
    trips: list[TripInput],
) -> list[SubsidyCalculation]:
    """Calculate one authoritative subsidy line for every non-overlapping period."""

    if not trips:
        return []
    settings = get_expense_settings(database)
    results: list[SubsidyCalculation] = []
    for trip in merge_overlapping_subsidy_trips(trips):
        trip_type = trip.subsidy_trip_type()
        if trip.trip_type is TripPurpose.OVERSEAS:
            # Overseas approvals remain linkable but never create subsidy lines.
            continue
        results.append(
            calculate_subsidy(
                trip_type=trip_type,
                period=trip.as_period(),
                configured_daily_rate=settings.daily_rate_for(trip_type),
                policy_confirmed=trip.policy_confirmed,
                confirmed_effective_days=trip.confirmed_effective_days,
                no_subsidy_exception=trip.no_subsidy_exception,
                manual_subsidy_amount=trip.manual_subsidy_amount,
                related_approval_id=trip.related_approval_id,
            )
        )
    return results


def merge_overlapping_subsidy_trips(trips: list[TripInput]) -> list[TripInput]:
    """Merge inclusive overlaps without filling gaps or adjacent trips.

    Drafts retain one input per approval for auditing.  This derived list is used
    for calculation and workbook rows so overlapping calendar days are paid once.
    """

    grouped_by_type: dict[TripPurpose, list[TripInput]] = {}
    for trip in trips:
        grouped_by_type.setdefault(trip.trip_type, []).append(trip)

    merged: list[TripInput] = []
    for typed_trips in grouped_by_type.values():
        ordered = sorted(
            typed_trips,
            key=lambda item: (
                item.start_date,
                item.end_date,
                item.related_approval_id or "",
            ),
        )
        groups: list[list[TripInput]] = []
        covered_end = None
        for trip in ordered:
            if not groups or covered_end is None or trip.start_date > covered_end:
                groups.append([trip])
                covered_end = trip.end_date
                continue
            groups[-1].append(trip)
            covered_end = max(covered_end, trip.end_date)

        for group in groups:
            start_date = min(item.start_date for item in group)
            end_date = max(item.end_date for item in group)
            start_time = min(item.start_time for item in group if item.start_date == start_date)
            end_time = max(item.end_time for item in group if item.end_date == end_date)
            first = group[0]
            merged.append(
                first.model_copy(
                    update={
                        "start_date": start_date,
                        "start_time": start_time,
                        "end_date": end_date,
                        "end_time": end_time,
                        "policy_confirmed": all(item.policy_confirmed for item in group),
                        "confirmed_effective_days": next(
                            (
                                item.confirmed_effective_days
                                for item in group
                                if item.confirmed_effective_days is not None
                            ),
                            None,
                        ),
                        "no_subsidy_exception": any(item.no_subsidy_exception for item in group),
                        "manual_subsidy_amount": next(
                            (
                                item.manual_subsidy_amount
                                for item in group
                                if item.manual_subsidy_amount is not None
                            ),
                            None,
                        ),
                    }
                )
            )
    return sorted(
        merged,
        key=lambda item: (
            item.start_date,
            item.end_date,
            item.related_approval_id or "",
        ),
    )


def request_trips(*, trip: TripInput | None, trips: list[TripInput]) -> list[TripInput]:
    return trips if trips else ([trip] if trip is not None else [])
