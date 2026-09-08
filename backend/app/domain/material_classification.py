from __future__ import annotations

import json

MATERIAL_CLASSIFICATION_KEY = "_materialClassification"
PENDING_CLASSIFICATION_STATUSES = frozenset({"pending", "needs_confirmation"})


def material_classification(raw_json: str | None) -> dict[str, object] | None:
    if not raw_json:
        return None
    try:
        payload = json.loads(raw_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or MATERIAL_CLASSIFICATION_KEY not in payload:
        return None
    value = payload[MATERIAL_CLASSIFICATION_KEY]
    return (
        value
        if isinstance(value, dict)
        else {
            "status": "needs_confirmation",
            "kind": "unknown",
            "reason": "请确认材料用途",
            "pageCount": None,
        }
    )
