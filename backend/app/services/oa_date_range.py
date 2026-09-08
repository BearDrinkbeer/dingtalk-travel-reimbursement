"""Date-only OA ranges; official FormComponentValues uses a two-string JSON array.

https://open.dingtalk.com/document/development/oa-formcomponent-message#h4-uia-ttd-h7r
"""

import json
import re
from datetime import date


def parse_date_range(raw: str | None) -> tuple[date, date] | None:
    if not isinstance(raw, str):
        return None
    try:
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) not in (2, 3):
            return None
        if any(
            not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in values[:2]
        ):
            return None
        start, end = (date.fromisoformat(value) for value in values[:2])
        # Observed readback uses either inclusive string days (travel) or
        # elapsed numeric days (reimbursement). Neither changes the endpoints.
        # Only accept these bounded conventions, not arbitrary metadata.
        if len(values) == 3:
            duration = values[2]
            elapsed = (end - start).days
            if type(duration) is int:
                valid = duration in (elapsed, elapsed + 1)
            elif isinstance(duration, str):
                valid = duration in (str(elapsed), str(elapsed + 1))
            else:
                valid = False
            if not valid:
                return None
        return (start, end) if end >= start else None
    except (ValueError, TypeError):
        return None
