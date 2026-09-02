from __future__ import annotations

from typing import Any


def success(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data}
