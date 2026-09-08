"""Bounded native-PDF fallback for itinerary tables with explicit column headings."""

from __future__ import annotations

import re
from collections.abc import Sequence
from contextlib import ExitStack
from datetime import date

import pypdfium2 as pdfium

_HEADERS = {
    "sequence": r"序号|No\.",
    "type": r"车型",
    "date": r"上车时间|用车时间|Date",
    "city": r"城市",
    "origin": r"起点|Origin",
    "destination": r"终点|Destination",
    "mileage": r"里程[\[（(]公里[\]）)]|Mileage",
    "amount": r"可开票金额[\[（(]元[\]）)]|Amount",
    "note": r"备注",
}
_REQUIRED = {"sequence", "date", "origin", "destination", "mileage", "amount"}
_Character = tuple[str, float, float, float, float]


def _text_lines(characters: Sequence[_Character]) -> list[list[_Character]]:
    rows: list[list[_Character]] = []
    for character in sorted(characters, key=lambda item: (-item[2], item[1])):
        if not rows or abs(rows[-1][0][2] - character[2]) > 1:
            rows.append([])
        rows[-1].append(character)
    return [sorted(row, key=lambda item: item[1]) for row in rows]


def positioned_itinerary_layout(path: str, page_index: int, *, max_characters: int) -> str | None:
    """Recover columns/row wraps, never strip a number guessed to be mileage.

    Only accept an unambiguous left-aligned table with numbered rows, date,
    endpoints, a separate mileage column and amount. Other layouts retain OCR
    fallback/manual review. The caller must still check totals and page coverage.
    """
    try:
        with ExitStack() as resources:
            document = pdfium.PdfDocument(path)
            resources.callback(document.close)
            page = document[page_index]
            resources.callback(page.close)
            if page.get_rotation():
                return None
            textpage = page.get_textpage()
            resources.callback(textpage.close)
            count = textpage.count_chars()
            if count > max_characters:
                return None
            characters = []
            for index in range(count):
                character = textpage.get_text_range(index, 1)
                if not character.strip():
                    continue
                left, bottom, right, top = textpage.get_charbox(index, loose=True)
                characters.append((character, left, bottom, right, top))
            headers = []
            for row in _text_lines(characters):
                text = "".join(item[0] for item in row)
                matches = {
                    field: list(re.finditer(pattern, text, re.I))
                    for field, pattern in _HEADERS.items()
                }
                if not all(len(matches[field]) == 1 for field in _REQUIRED):
                    continue
                if any(len(found) > 1 for found in matches.values()):
                    return None
                columns = sorted(
                    (row[found[0].start()][1], field) for field, found in matches.items() if found
                )
                headers.append((min(item[2] for item in row), columns))
            if len(headers) != 1:
                return None
            header_bottom, columns = headers[0]
            if columns[0][1] != "sequence":
                return None
            below = [item for item in characters if item[4] < header_bottom]
            # A crop through a character would manufacture a shortened address.
            # Such overlapping/centered layouts need the existing OCR fallback.
            if any(
                item[1] + 0.2 < boundary < item[3] - 0.2
                for boundary, _ in columns[1:]
                for item in below
            ):
                return None
            number_rows = _text_lines(
                [item for item in below if columns[0][0] - 0.2 <= item[1] < columns[1][0] - 0.2]
            )
            numbers = ["".join(item[0] for item in row) for row in number_rows]
            if not numbers or any(not re.fullmatch(r"\d{1,4}", number) for number in numbers):
                return None
            if [int(number) for number in numbers] != list(
                range(int(numbers[0]), int(numbers[0]) + len(numbers))
            ):
                return None
            centers = [
                sum((item[2] + item[4]) / 2 for item in row) / len(row) for row in number_rows
            ]
            boundaries = (
                [header_bottom - 0.2]
                + [(upper + lower) / 2 for upper, lower in zip(centers, centers[1:], strict=False)]
                + [0]
            )
            layout = ["序号  日期  起点  终点  金额"]
            for index, number in enumerate(numbers):
                cells = {}
                for column_index, (left, field) in enumerate(columns):
                    right = (
                        columns[column_index + 1][0] - 0.2
                        if column_index + 1 < len(columns)
                        else None
                    )
                    value = textpage.get_text_bounded(
                        left=left - 0.2,
                        right=right,
                        top=boundaries[index],
                        bottom=boundaries[index + 1],
                    )
                    cells[field] = re.sub(r"\s+", "", value)
                if cells["sequence"] != number:
                    return None
                if not re.fullmatch(
                    r"20\d{2}-\d{2}-\d{2}(?:\d{2}:\d{2}(?::\d{2})?)?", cells["date"]
                ):
                    return None
                date.fromisoformat(cells["date"][:10])
                if not re.fullmatch(r"\d+(?:\.\d+)?", cells["mileage"]):
                    return None
                if not re.fullmatch(r"[¥￥]?\d[\d,]*(?:\.\d{1,2})?", cells["amount"]):
                    return None
                if not cells["origin"] or not cells["destination"]:
                    return None
                layout.append(
                    "  ".join(
                        cells[field]
                        for field in (
                            "sequence",
                            "date",
                            "origin",
                            "destination",
                            "amount",
                        )
                    )
                )
            return "\n".join(layout)
    except (pdfium.PdfiumError, OSError, ValueError, IndexError):
        return None
