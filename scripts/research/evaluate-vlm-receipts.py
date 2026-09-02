#!/usr/bin/env python3
"""Evaluate a local Ollama vision model on the private receipt examples.

The script deliberately sends each image straight to the local model and only
persists structured candidates and timing metrics. It does not print or save a
full OCR transcript.
"""

from __future__ import annotations

import argparse
import base64
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


MODEL_DEFAULT = "minicpm-v4.6:q4_K_M"
ENDPOINT_DEFAULT = "http://127.0.0.1:11434/api/chat"

CATEGORIES = (
    ("airfare", "飞机票"),
    ("rail_fare", "火车票"),
    ("local_transport", "市内交通费"),
    ("lodging", "住宿费"),
    ("office", "办公费"),
    ("hospitality", "招待费"),
    ("communications", "通讯费"),
    ("employee_welfare", "福利费"),
    ("consulting", "咨询费"),
    ("advertising", "广告费"),
    ("leasing", "租赁费"),
    ("property_management", "物业费"),
    ("utilities", "水电费"),
    ("labor_service", "劳务费"),
    ("conference", "会议费"),
    ("other", "其他"),
)
CATEGORY_IDS = {category_id for category_id, _name in CATEGORIES}


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

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": sorted(CATEGORY_IDS)},
        "event_date": {"type": ["string", "null"]},
        "invoice_date": {"type": ["string", "null"]},
        "amount": {"type": ["string", "null"]},
        "description": {"type": ["string", "null"]},
        "evidence": {
            "type": "object",
            "properties": {
                "category": {"type": ["string", "null"]},
                "event_date": {"type": ["string", "null"]},
                "amount": {"type": ["string", "null"]},
            },
            "required": ["category", "event_date", "amount"],
            "additionalProperties": False,
        },
    },
    "required": [
        "category",
        "event_date",
        "invoice_date",
        "amount",
        "description",
        "evidence",
    ],
    "additionalProperties": False,
}


def prompt() -> str:
    category_text = "、".join(f"{category_id}={name}" for category_id, name in CATEGORIES)
    return f"""你是公司费用票据识别器。只根据图片中可见内容返回 JSON，不解释，不猜测缺失字段。

可选类别只有：{category_text}。

规则：
1. category 必须从上述英文 ID 中选择。优先依据“货物或应税劳务、服务名称”、项目名称和票据类型。
2. event_date 是费用实际发生日期，例如乘车日期、入住/服务日期；不要把开票日期误当成明确标注的乘车日期或服务日期。没有独立发生日期时，可使用开票日期。
3. invoice_date 只填写明确标注的开票日期。
4. amount 取价税合计小写、票价或实际总支付金额，保留两位小数；不要取税额、单价、发票号码。
5. description 简短填写服务/商品名称，交通票据填写起点-终点。
6. evidence 中逐项复制支持结论的简短可见原文；看不到就填 null。禁止填写姓名、证件号、票号或完整地址。
7. 空白票样可以识别类别，但不存在的日期和金额必须为 null。
"""


def request_payload(model: str, image_path: Path) -> dict[str, Any]:
    image = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt(), "images": [image]}],
        "format": RESULT_SCHEMA,
        "think": False,
        "stream": False,
        "keep_alive": "10m",
        "options": {
            "temperature": 0,
            "top_p": 0.1,
            "num_ctx": 8192,
            "num_predict": 320,
            "seed": 7,
        },
    }


def call_model(endpoint: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"local Ollama request failed: {type(exc).__name__}") from exc
    if not isinstance(body, dict):
        raise RuntimeError("local Ollama response is not an object")
    return body


def parse_candidate(response: dict[str, Any]) -> dict[str, Any]:
    message = response.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise RuntimeError("local Ollama response has no message content")
    try:
        candidate = json.loads(message["content"])
    except json.JSONDecodeError as exc:
        raise RuntimeError("model did not return valid JSON") from exc
    if not isinstance(candidate, dict):
        raise RuntimeError("model candidate is not an object")
    if candidate.get("category") not in CATEGORY_IDS:
        raise RuntimeError("model candidate contains an unknown category")
    return candidate


def seconds(response: dict[str, Any], key: str) -> float:
    value = response.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return round(float(value) / 1_000_000_000, 3)


def amount_matches(candidate: Any, expected: Decimal | None) -> bool | None:
    if expected is None:
        return None
    if isinstance(candidate, bool) or not isinstance(candidate, (str, int, float)):
        return False
    try:
        return Decimal(str(candidate).replace(",", "").strip()) == expected
    except InvalidOperation:
        return False


def evaluate_example(
    example: Example,
    *,
    root: Path,
    endpoint: str,
    model: str,
    timeout: int,
) -> dict[str, Any]:
    path = root / example.relative_path
    if not path.is_file():
        raise RuntimeError(f"missing example: {example.relative_path}")
    started = time.monotonic()
    response = call_model(endpoint, request_payload(model, path), timeout)
    wall_seconds = round(time.monotonic() - started, 3)
    candidate = parse_candidate(response)
    return {
        "sample": example.relative_path,
        "expectedCategory": example.expected_category,
        "expectedAmount": str(example.expected_amount) if example.expected_amount else None,
        "blankSample": example.is_blank_sample,
        "categoryMatch": candidate.get("category") == example.expected_category,
        "amountMatch": amount_matches(candidate.get("amount"), example.expected_amount),
        "candidate": candidate,
        "timing": {
            "wallSeconds": wall_seconds,
            "totalSeconds": seconds(response, "total_duration"),
            "loadSeconds": seconds(response, "load_duration"),
            "promptSeconds": seconds(response, "prompt_eval_duration"),
            "generationSeconds": seconds(response, "eval_duration"),
            "generatedTokens": response.get("eval_count", 0),
        },
    }


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评测本地 Ollama 视觉模型的票据字段与类别识别")
    parser.add_argument("--model", default=MODEL_DEFAULT)
    parser.add_argument("--endpoint", default=ENDPOINT_DEFAULT)
    parser.add_argument(
        "--examples-dir",
        type=Path,
        default=Path("data/ocr-eval-private/examples"),
    )
    parser.add_argument("--limit", type=int, default=len(EXAMPLES))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    selected = EXAMPLES[: max(0, min(args.limit, len(EXAMPLES)))]
    if not selected:
        print("至少需要选择一张测试票据", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    for index, example in enumerate(selected, start=1):
        print(f"[{index}/{len(selected)}] {example.relative_path}", file=sys.stderr, flush=True)
        try:
            results.append(
                evaluate_example(
                    example,
                    root=args.examples_dir,
                    endpoint=args.endpoint,
                    model=args.model,
                    timeout=args.timeout,
                )
            )
        except RuntimeError as exc:
            results.append(
                {
                    "sample": example.relative_path,
                    "expectedCategory": example.expected_category,
                    "expectedAmount": (
                        str(example.expected_amount) if example.expected_amount else None
                    ),
                    "blankSample": example.is_blank_sample,
                    "categoryMatch": False,
                    "amountMatch": False if example.expected_amount is not None else None,
                    "error": str(exc),
                }
            )

    successful = [result for result in results if "candidate" in result]
    scored = [result for result in successful if not result["blankSample"]]
    durations = [result["timing"]["wallSeconds"] for result in successful]
    summary = {
        "model": args.model,
        "sampleCount": len(results),
        "successfulCount": len(successful),
        "scoredCount": len(scored),
        "categoryMatches": sum(result["categoryMatch"] for result in scored),
        "categoryAccuracy": (
            round(sum(result["categoryMatch"] for result in scored) / len(scored), 4)
            if scored
            else None
        ),
        "amountMatches": sum(result["amountMatch"] is True for result in scored),
        "amountAccuracy": (
            round(sum(result["amountMatch"] is True for result in scored) / len(scored), 4)
            if scored
            else None
        ),
        "meanWallSeconds": round(statistics.mean(durations), 3) if durations else None,
        "medianWallSeconds": round(statistics.median(durations), 3) if durations else None,
    }
    document = {"summary": summary, "results": results}
    encoded = json.dumps(document, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0 if len(successful) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
