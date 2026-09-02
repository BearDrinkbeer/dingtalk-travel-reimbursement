#!/usr/bin/env python3
"""Evaluate the full local PaddleOCR-VL pipeline on receipt examples."""

from __future__ import annotations

import argparse
import html
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import psutil
from paddleocr import PaddleOCRVL


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.ocr.extractors import extract_amount  # noqa: E402
from app.ocr.types import OcrLine  # noqa: E402


@dataclass(frozen=True, slots=True)
class Example:
    relative_path: str
    expected_category: str
    expected_amount: Decimal | None
    is_blank_sample: bool = False


EXAMPLES = (
    Example("advertising/marketing_promotion.png", "advertising", Decimal("5000.00")),
    Example("airfare/airfare_official.png", "airfare", None, True),
    Example("communications/telecom_fee.png", "communications", Decimal("139.00")),
    Example("hospitality/catering_service.png", "hospitality", Decimal("2139.00")),
    Example("labor_service/labor_service.jpg", "labor_service", Decimal("400.00")),
    Example("leasing/rental_service.png", "leasing", Decimal("264000.00")),
    Example("lodging/hotel_uipath.jpg", "lodging", Decimal("2749.99")),
    Example("office/office_supplies.png", "office", Decimal("56.43")),
    Example("utilities/water_fee.jpg", "utilities", Decimal("51.70")),
)

PREFERRED_AMOUNT_LABELS = (
    "价税合计（小写）",
    "价税合计(小写)",
    "价税合计",
    "小写",
    "合计",
)
_HTML_TAG = re.compile(r"<[^>]+>")


class _TableRowParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[str] = []
        self._row_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() == "tr":
            self._row_parts = []

    def handle_data(self, data: str) -> None:
        if self._row_parts is not None:
            value = " ".join(data.split())
            if value:
                self._row_parts.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "tr" and self._row_parts is not None:
            row = " ".join(self._row_parts)
            if row:
                self.rows.append(row)
            self._row_parts = None


def result_payload(result: Any) -> dict[str, Any]:
    value = result.json
    if callable(value):
        value = value()
    if not isinstance(value, dict):
        raise RuntimeError("PaddleOCR-VL result is not a JSON object")
    nested = value.get("res")
    return nested if isinstance(nested, dict) else value


def extract_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_blocks = payload.get("parsing_res_list")
    if not isinstance(raw_blocks, list):
        return []
    blocks: list[dict[str, Any]] = []
    for block in raw_blocks:
        if not isinstance(block, dict):
            continue
        content = block.get("block_content")
        if not isinstance(content, str) or not content.strip():
            continue
        blocks.append(
            {
                "label": block.get("block_label"),
                "content": content,
                "bbox": block.get("block_bbox"),
            }
        )
    return blocks


def plain_text(blocks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in blocks:
        content = html.unescape(str(block["content"]))
        parts.append(" ".join(_HTML_TAG.sub(" ", content).split()))
    return "\n".join(part for part in parts if part)


def deterministic_amount(blocks: list[dict[str, Any]]) -> Decimal | None:
    texts: list[str] = []
    for block in blocks:
        content = html.unescape(str(block["content"]))
        if "<tr" in content.lower():
            parser = _TableRowParser()
            parser.feed(content)
            texts.extend(parser.rows)
        else:
            texts.extend(line for line in content.splitlines() if line.strip())
    lines = [OcrLine(text=text, confidence=1.0) for text in texts]
    return extract_amount(lines, PREFERRED_AMOUNT_LABELS)


def rss_mebibytes() -> float:
    return round(psutil.Process().memory_info().rss / (1024 * 1024), 1)


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评测完整 PaddleOCR-VL-1.6 票据解析")
    parser.add_argument(
        "--examples-dir",
        type=Path,
        default=Path("data/ocr-eval-private/examples"),
    )
    parser.add_argument("--limit", type=int, default=len(EXAMPLES))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--recompute-from",
        type=Path,
        help="Reuse saved PaddleOCR-VL blocks and only recompute deterministic fields",
    )
    parser.add_argument("--max-new-tokens", type=int, default=2048)
    return parser.parse_args(argv)


def recompute_saved_document(source: Path, output: Path) -> int:
    document = json.loads(source.read_text(encoding="utf-8"))
    results = document.get("results")
    if not isinstance(results, list):
        raise RuntimeError("saved evaluation has no results list")
    expected_by_path = {example.relative_path: example for example in EXAMPLES}
    scored: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict) or "error" in result:
            continue
        example = expected_by_path.get(result.get("sample"))
        if example is not None:
            result["expectedCategory"] = example.expected_category
        blocks = result.get("blocks")
        if not isinstance(blocks, list):
            blocks = []
        parsed_amount = deterministic_amount(blocks)
        expected_raw = result.get("expectedAmount")
        expected = Decimal(expected_raw) if isinstance(expected_raw, str) else None
        result["amount"] = str(parsed_amount) if parsed_amount is not None else None
        result["amountMatch"] = parsed_amount == expected if expected is not None else None
        if not result.get("blankSample"):
            scored.append(result)
    summary = document.get("summary")
    if not isinstance(summary, dict):
        raise RuntimeError("saved evaluation has no summary object")
    summary["amountMatches"] = sum(result["amountMatch"] is True for result in scored)
    summary["amountAccuracy"] = (
        round(summary["amountMatches"] / len(scored), 4) if scored else None
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    if args.recompute_from is not None:
        return recompute_saved_document(args.recompute_from, args.output)
    selected = EXAMPLES[: max(0, min(args.limit, len(EXAMPLES)))]
    if not selected:
        print("至少需要选择一张测试票据", file=sys.stderr)
        return 2

    init_started = time.monotonic()
    pipeline = PaddleOCRVL(
        pipeline_version="v1.6",
        device="cpu",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_layout_detection=True,
        use_chart_recognition=False,
        use_seal_recognition=False,
    )
    init_seconds = round(time.monotonic() - init_started, 3)
    init_rss = rss_mebibytes()

    results: list[dict[str, Any]] = []
    try:
        for index, example in enumerate(selected, start=1):
            print(f"[{index}/{len(selected)}] {example.relative_path}", file=sys.stderr, flush=True)
            path = args.examples_dir / example.relative_path
            started = time.monotonic()
            try:
                result = next(
                    iter(pipeline.predict(str(path), max_new_tokens=args.max_new_tokens))
                )
                seconds = round(time.monotonic() - started, 3)
                payload = result_payload(result)
                blocks = extract_blocks(payload)
                parsed_amount = deterministic_amount(blocks)
                results.append(
                    {
                        "sample": example.relative_path,
                        "expectedCategory": example.expected_category,
                        "expectedAmount": (
                            str(example.expected_amount)
                            if example.expected_amount is not None
                            else None
                        ),
                        "blankSample": example.is_blank_sample,
                        "amount": str(parsed_amount) if parsed_amount is not None else None,
                        "amountMatch": (
                            parsed_amount == example.expected_amount
                            if example.expected_amount is not None
                            else None
                        ),
                        "text": plain_text(blocks),
                        "blocks": blocks,
                        "timing": {"wallSeconds": seconds},
                        "rssMiB": rss_mebibytes(),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - preserve the batch result
                results.append(
                    {
                        "sample": example.relative_path,
                        "expectedCategory": example.expected_category,
                        "expectedAmount": (
                            str(example.expected_amount)
                            if example.expected_amount is not None
                            else None
                        ),
                        "blankSample": example.is_blank_sample,
                        "amountMatch": False if example.expected_amount is not None else None,
                        "error": f"{type(exc).__name__}: {exc}",
                        "timing": {"wallSeconds": round(time.monotonic() - started, 3)},
                        "rssMiB": rss_mebibytes(),
                    }
                )
    finally:
        pipeline.close()

    successful = [result for result in results if "error" not in result]
    scored = [result for result in successful if not result["blankSample"]]
    durations = [result["timing"]["wallSeconds"] for result in successful]
    document = {
        "summary": {
            "pipeline": "PaddleOCR-VL-1.6",
            "sampleCount": len(results),
            "successfulCount": len(successful),
            "scoredCount": len(scored),
            "amountMatches": sum(result["amountMatch"] is True for result in scored),
            "amountAccuracy": (
                round(sum(result["amountMatch"] is True for result in scored) / len(scored), 4)
                if scored
                else None
            ),
            "initSeconds": init_seconds,
            "initRssMiB": init_rss,
            "meanWallSeconds": round(statistics.mean(durations), 3) if durations else None,
            "medianWallSeconds": round(statistics.median(durations), 3) if durations else None,
            "maxObservedRssMiB": max(
                (float(result["rssMiB"]) for result in results),
                default=init_rss,
            ),
        },
        "results": results,
    }
    encoded = json.dumps(document, ensure_ascii=False, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded + "\n", encoding="utf-8")
    print(json.dumps(document["summary"], ensure_ascii=False, indent=2))
    return 0 if len(successful) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
