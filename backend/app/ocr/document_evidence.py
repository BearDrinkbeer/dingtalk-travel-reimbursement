from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from app.ocr.types import OcrLine

RailType = Literal["high_speed", "emu", "regular", "unknown"]
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{3,63}$")
_NUMBER_LABELS = {
    "invoice": re.compile(r"发票(?:号码|号)|\binvoice\s*(?:number|no\.?|#)(?![A-Za-z])", re.I),
    "order": re.compile(r"订单(?:编号|号码|号)|\border\s*(?:number|no\.?|id|#)(?![A-Za-z])", re.I),
}
_TRAIN = re.compile(r"(?<![A-Za-z0-9])([GDCZTK]\d{1,4})(?![A-Za-z0-9])", re.I)
_NUMBERED_TRAIN = re.compile(r"车次\s*[:：]?\s*(\d{1,5})(?!\d)")


def extract_document_numbers(lines: Sequence[OcrLine]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Read only explicitly labelled identifiers, retaining leading zeros.

    Phone/tax identifiers and filenames are not alternative sources. A split
    value is accepted only when the immediately following line is one ID.
    """

    values: dict[str, list[str]] = {"invoice": [], "order": []}
    for kind, pattern in _NUMBER_LABELS.items():
        for index, line in enumerate(lines):
            for match in pattern.finditer(line.text):
                suffix = line.text[match.end() :].lstrip(" \t:：#")
                if not suffix and index + 1 < len(lines):
                    suffix = lines[index + 1].text.strip()
                    if not _ID.fullmatch(suffix):
                        continue
                parts = re.split(r"[\s,，;；。]+", suffix, maxsplit=1)
                token = parts[0]
                if len(parts) > 1 and re.match(r"[A-Za-z0-9-]+(?:\s|$)", parts[1]):
                    # Never silently truncate a split identifier into a matchable ID.
                    continue
                if _ID.fullmatch(token) and any(character.isdigit() for character in token):
                    normalized = token.upper()
                    if normalized not in values[kind] and len(values[kind]) < 100:
                        values[kind].append(normalized)
    return tuple(values["invoice"]), tuple(values["order"])


def extract_rail_type(lines: Sequence[OcrLine], *, railway_evidence: bool) -> RailType | None:
    if not railway_evidence:
        return None
    candidates: set[RailType] = set()
    for line in lines:
        # Identifiers can contain G123 too; they are not train-number fields.
        if any(label in line.text for label in ("发票号", "订单", "纳税", "电话")):
            continue
        for match in _TRAIN.finditer(line.text):
            prefix = match.group(1)[0].upper()
            candidates.add(
                "high_speed" if prefix == "G" else "emu" if prefix in "DC" else "regular"
            )
        if _NUMBERED_TRAIN.search(line.text):
            candidates.add("regular")
    if len(candidates) != 1:
        return "unknown"
    return candidates.pop()
