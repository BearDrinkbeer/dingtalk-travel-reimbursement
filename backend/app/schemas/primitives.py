from __future__ import annotations

import re
from datetime import date, time
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BeforeValidator

_DECIMAL_STRING_PATTERN = re.compile(r"(?:0|[1-9]\d*)(?:\.\d+)?")
_MINUTE_TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_CALENDAR_DATE_PATTERN = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])")


def _decimal_string(value: object) -> Decimal:
    """Parse exact, unsigned decimal JSON strings while retaining Decimal internally."""
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, str) and _DECIMAL_STRING_PATTERN.fullmatch(value):
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:  # pragma: no cover - guarded by the regex
            raise ValueError("invalid decimal string") from exc
    else:
        raise ValueError("decimal values must be JSON strings")
    if not parsed.is_finite():
        raise ValueError("decimal values must be finite")
    return parsed


def _minute_time(value: object) -> time:
    """Parse request times only from the exact local 24-hour HH:MM format."""
    if isinstance(value, time):
        if value.second or value.microsecond or value.tzinfo is not None:
            raise ValueError("times must use local HH:MM minute precision")
        return value
    if not isinstance(value, str) or not _MINUTE_TIME_PATTERN.fullmatch(value):
        raise ValueError("times must use exact local HH:MM format")
    return time(hour=int(value[:2]), minute=int(value[3:]))


def _calendar_date(value: object) -> date:
    """Parse JSON dates only from exact, zero-padded YYYY-MM-DD strings."""

    if type(value) is date:
        return value
    if not isinstance(value, str) or not _CALENDAR_DATE_PATTERN.fullmatch(value):
        raise ValueError("dates must use exact YYYY-MM-DD format")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date must be a valid calendar date") from exc


DecimalString = Annotated[Decimal, BeforeValidator(_decimal_string)]
MinuteTime = Annotated[time, BeforeValidator(_minute_time)]
StrictCalendarDate = Annotated[date, BeforeValidator(_calendar_date)]
