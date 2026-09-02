#!/usr/bin/env python3
"""Evaluate a local Ollama model as a constrained receipt-text classifier."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


MODEL_DEFAULT = "qwen3-vl:2b-instruct-q4_K_M"
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
CATEGORY_GUIDANCE = (
    "airfare=航空运输电子客票、飞机票；rail_fare=铁路客票、火车票；"
    "local_transport=出租车、网约车、市内交通；lodging=住宿、酒店；"
    "office=办公用品、文具；hospitality=餐饮、餐费、业务招待；"
    "communications=电话、通信、网络服务；employee_welfare=员工福利；"
    "consulting=咨询、顾问服务；advertising=广告、宣传、市场推广；"
    "leasing=房租、设备或场地租赁；property_management=物业服务；"
    "utilities=水费、电费、燃气费；labor_service=劳务服务、劳务费；"
    "conference=会议服务；other=没有足够依据归入以上类别。"
)
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": sorted(CATEGORY_IDS)},
    },
    "required": ["category"],
    "additionalProperties": False,
}


def prompt(text: str) -> str:
    category_text = "、".join(f"{category_id}={name}" for category_id, name in CATEGORIES)
    return f"""你是公司费用票据分类器。只根据下方 OCR 原文分类，不提取或修改金额日期。

可选类别只有：{category_text}。
类别含义：{CATEGORY_GUIDANCE}

规则：
1. category 必须从上述英文 ID 中选择。
2. 优先依据“货物或应税劳务、服务名称”、项目名称和票据类型。
3. 无法确定时选择 other，禁止猜测。
4. 只输出 category，不要复述 OCR 原文。

OCR 原文：
---
{text[:24000]}
---
"""


def call_model(endpoint: str, model: str, text: str, timeout: int) -> tuple[dict[str, Any], float]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt(text)}],
        "format": RESULT_SCHEMA,
        "think": False,
        "stream": False,
        "keep_alive": "10m",
        "options": {
            "temperature": 0,
            "top_p": 0.1,
            "num_ctx": 8192,
            "num_predict": 32,
            "seed": 7,
        },
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"local Ollama request failed: {type(exc).__name__}") from exc
    wall_seconds = round(time.monotonic() - started, 3)
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise RuntimeError("local Ollama response has no message content")
    try:
        candidate = json.loads(message["content"])
    except json.JSONDecodeError as exc:
        raw_excerpt = " ".join(message["content"][:300].split())
        raise RuntimeError(
            f"model did not return valid JSON: {raw_excerpt!r}"
        ) from exc
    if not isinstance(candidate, dict) or candidate.get("category") not in CATEGORY_IDS:
        raise RuntimeError("model returned an invalid category")
    return candidate, wall_seconds


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评测 OCR 文本后的本地小模型费用分类")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=MODEL_DEFAULT)
    parser.add_argument("--endpoint", default=ENDPOINT_DEFAULT)
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    source = json.loads(args.input.read_text(encoding="utf-8"))
    source_results = source.get("results")
    if not isinstance(source_results, list):
        print("输入评测文件缺少 results", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    for index, source_result in enumerate(source_results, start=1):
        sample = source_result.get("sample")
        print(f"[{index}/{len(source_results)}] {sample}", file=sys.stderr, flush=True)
        text = source_result.get("text")
        if not isinstance(text, str):
            results.append(
                {
                    "sample": sample,
                    "expectedCategory": source_result.get("expectedCategory"),
                    "blankSample": bool(source_result.get("blankSample")),
                    "error": "missing OCR text",
                }
            )
            continue
        try:
            candidate, wall_seconds = call_model(args.endpoint, args.model, text, args.timeout)
            results.append(
                {
                    "sample": sample,
                    "expectedCategory": source_result.get("expectedCategory"),
                    "blankSample": bool(source_result.get("blankSample")),
                    "categoryMatch": (
                        candidate["category"] == source_result.get("expectedCategory")
                    ),
                    "candidate": candidate,
                    "timing": {"wallSeconds": wall_seconds},
                }
            )
        except RuntimeError as exc:
            results.append(
                {
                    "sample": sample,
                    "expectedCategory": source_result.get("expectedCategory"),
                    "blankSample": bool(source_result.get("blankSample")),
                    "error": str(exc),
                }
            )

    successful = [result for result in results if "candidate" in result]
    expected = [result for result in results if not result.get("blankSample", False)]
    scored = [result for result in successful if not result["blankSample"]]
    durations = [result["timing"]["wallSeconds"] for result in successful]
    document = {
        "summary": {
            "model": args.model,
            "inputPipeline": source.get("summary", {}).get("pipeline"),
            "sampleCount": len(results),
            "successfulCount": len(successful),
            "scoredCount": len(expected),
            "categoryMatches": sum(result["categoryMatch"] for result in scored),
            "categoryAccuracy": (
                round(sum(result["categoryMatch"] for result in scored) / len(expected), 4)
                if expected
                else None
            ),
            "meanWallSeconds": round(statistics.mean(durations), 3) if durations else None,
            "medianWallSeconds": round(statistics.median(durations), 3) if durations else None,
        },
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(document["summary"], ensure_ascii=False, indent=2))
    return 0 if len(successful) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
