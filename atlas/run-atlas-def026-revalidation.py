#!/usr/bin/env python3
"""Focused live revalidation for ATLAS DEF-026 fixes, at DEF-025 scale.

Forked from run-atlas-def025-revalidation.py's structure. The key
difference: DEF-026's original problem was that DEF-025's fixes only
generalized to their exact seed phrasing, not the underlying question
shape. So this runner grades against each case's precomputed `expect`
value (an exact date, hour count, or place name) baked in by
generate-atlas-def026-paraphrase-suite.py, not just "did it hit a route" --
a route match with a wrong computed answer must still fail here.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import random
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime


CATEGORIES = [
    "countdown_base_clause",
    "event_reminder_offset",
    "backup_upload_window",
    "causal_tradeoff",
    "weather_place_extraction",
    "compound_countdown_weather",
]

EXPECTED_ROUTES = {
    "countdown_base_clause": {"countdown"},
    "event_reminder_offset": {"countdown"},
    "backup_upload_window": {"quantitative_reasoning"},
    "causal_tradeoff": {"causal_tradeoff_reasoning"},
    "weather_place_extraction": {"weather"},
    "compound_countdown_weather": {"countdown"},
}


def load_cases(path: pathlib.Path, per_category: int, seed: int) -> list[dict]:
    all_cases = json.loads(path.read_text(encoding="utf-8"))
    rng = random.Random(seed)
    selected: list[dict] = []
    for category in CATEGORIES:
        rows = [item for item in all_cases if item.get("category") == category]
        if len(rows) < per_category:
            raise SystemExit(f"{category}: only {len(rows)} cases available, need {per_category}")
        selected.extend(rng.sample(rows, per_category))
    selected.sort(key=lambda item: (item["category"], item["id"]))
    return selected


def token_from_args(args: argparse.Namespace) -> str:
    if args.token:
        return args.token
    if args.token_env_file:
        for line in pathlib.Path(args.token_env_file).read_text(encoding="utf-8").splitlines():
            if line.startswith("ATLAS_SHORTCUTS_TOKEN="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("ATLAS_SHORTCUTS_TOKEN", "")


def contract_pass(category: str, route: str | None, answer: str, expect: dict, transport_error: str | None) -> tuple[bool, list[str]]:
    failures: list[str] = []
    lowered = answer.lower()
    if transport_error:
        failures.append("transport_error")
    if not answer.strip():
        failures.append("empty_answer")
    if route not in EXPECTED_ROUTES[category]:
        failures.append(f"route:{route}")
        return not failures, failures

    if category in ("countdown_base_clause", "compound_countdown_weather"):
        days = expect["days"]
        if f"{days} days" not in lowered and f"{days} day" not in lowered:
            failures.append(f"missing_day_count:{days}")
    elif category == "event_reminder_offset":
        if expect["event_date"].lower() not in lowered:
            failures.append(f"missing_event_date:{expect['event_date']}")
        if expect["reminder_date"].lower() not in lowered:
            failures.append(f"missing_reminder_date:{expect['reminder_date']}")
    elif category == "backup_upload_window":
        hours_int = int(expect["hours_ceil"])
        if str(hours_int) not in lowered and f"{expect['hours_ceil']}" not in lowered:
            failures.append(f"missing_hours:{expect['hours_ceil']}")
        fits_word = "fit" in lowered
        if not fits_word:
            failures.append("missing_fit_statement")
    elif category == "causal_tradeoff":
        for term in expect["contains"]:
            if term not in lowered:
                failures.append(f"missing_term:{term}")
    elif category == "weather_place_extraction":
        if expect["place_contains"].lower() not in lowered:
            failures.append(f"missing_place:{expect['place_contains']}")

    return not failures, failures


def post_message(base_url: str, token: str, client: str, item: dict, timeout: int) -> dict:
    payload = json.dumps({
        "text": item["question"],
        "input_mode": "typed",
        "client": client,
        "context_scope": "personal",
    }).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/v1/messages",
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=payload,
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        assistant = body.get("assistant_message") or {}
        answer = assistant.get("text") or ""
        route = assistant.get("route")
        tool = assistant.get("tool")
        transport_error = None
    except TimeoutError:
        answer, route, tool, transport_error = "", None, None, "timeout"
    except urllib.error.URLError as exc:
        answer, route, tool, transport_error = "", None, None, f"URLError: {exc}"
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        answer, route, tool, transport_error = "", None, None, f"{type(exc).__name__}: {exc}"
    latency_ms = round((time.monotonic() - started) * 1000)
    passed, failures = contract_pass(item["category"], route, answer, item["expect"], transport_error)
    return {
        "id": item["id"],
        "category": item["category"],
        "question": item["question"],
        "expect": item["expect"],
        "route": route,
        "tool": tool,
        "answer": answer,
        "latency_ms": latency_ms,
        "transport_error": transport_error,
        "contract_pass": passed,
        "contract_failures": failures,
    }


def percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1)))
    return ordered[index]


def summarize(results: list[dict]) -> dict:
    by_category: dict[str, list[dict]] = defaultdict(list)
    for result in results:
        by_category[result["category"]].append(result)
    categories = {}
    for category in CATEGORIES:
        rows = by_category.get(category, [])
        latencies = [r["latency_ms"] for r in rows if not r.get("transport_error")]
        categories[category] = {
            "count": len(rows),
            "contract_pass": sum(1 for r in rows if r["contract_pass"]),
            "transport_errors": sum(1 for r in rows if r["transport_error"]),
            "routes": dict(Counter(r.get("route") or "none" for r in rows)),
            "failure_counts": dict(Counter(f for r in rows for f in r["contract_failures"])),
            "latency_ms": {
                "p50": percentile(latencies, 50),
                "p95": percentile(latencies, 95),
                "max": max(latencies) if latencies else None,
            },
        }
    latencies = [r["latency_ms"] for r in results if not r.get("transport_error")]
    return {
        "count": len(results),
        "contract_pass": sum(1 for r in results if r["contract_pass"]),
        "transport_errors": sum(1 for r in results if r["transport_error"]),
        "pass_rate": round(sum(1 for r in results if r["contract_pass"]) / len(results) * 100, 1) if results else None,
        "failure_counts": dict(Counter(f for r in results for f in r["contract_failures"])),
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "max": max(latencies) if latencies else None,
            "mean": round(statistics.mean(latencies), 1) if latencies else None,
        },
        "categories": categories,
    }


def write_outputs(results: list[dict], summary: dict, output_json: pathlib.Path, output_md: pathlib.Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": summary,
        "results": results,
    }
    output_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# ATLAS DEF-026 Focused Revalidation (DEF-025 scale)",
        "",
        f"- Cases: {summary['count']}",
        f"- Contract pass: {summary['contract_pass']} / {summary['count']} ({summary['pass_rate']}%)",
        f"- Transport errors: {summary['transport_errors']}",
        f"- Latency: p50 {summary['latency_ms']['p50']}ms, p95 {summary['latency_ms']['p95']}ms, max {summary['latency_ms']['max']}ms",
        "",
        "| Category | Count | Contract pass | Transport errors | p95 latency | Failure counts |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for category, data in summary["categories"].items():
        failures = ", ".join(f"{k}:{v}" for k, v in sorted(data["failure_counts"].items())) or "-"
        lines.append(
            f"| `{category}` | {data['count']} | {data['contract_pass']} | {data['transport_errors']} | "
            f"{data['latency_ms']['p95']} | {failures} |"
        )
    lines.extend(["", "## Failures", ""])
    failures = [r for r in results if not r["contract_pass"]]
    if not failures:
        lines.append("None.")
    else:
        for row in failures[:50]:
            lines.extend([
                f"### {row['id']}",
                "",
                f"- Category: `{row['category']}`",
                f"- Route/tool: `{row.get('route')}` / `{row.get('tool')}`",
                f"- Expected: {row['expect']}",
                f"- Failures: {', '.join(row['contract_failures'])}",
                f"- Question: {row['question']}",
                f"- Answer: {' '.join(row['answer'].split())[:500]}",
                "",
            ])
    output_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=pathlib.Path, required=True)
    parser.add_argument("--base-url", default=os.environ.get("ATLAS_SCHEDULER_BASE_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--token", default="")
    parser.add_argument("--token-env-file")
    parser.add_argument("--client", default="atlas-def026-focused-revalidation")
    parser.add_argument("--per-category", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260916)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--output-json", required=True, type=pathlib.Path)
    parser.add_argument("--output-md", required=True, type=pathlib.Path)
    args = parser.parse_args()

    token = token_from_args(args)
    if not token:
        raise SystemExit("ATLAS_SHORTCUTS_TOKEN, --token, or --token-env-file is required")
    items = load_cases(args.suite, args.per_category, args.seed)
    print(f"Loaded {len(items)} DEF-026 cases ({args.per_category} per category)")
    started = time.monotonic()
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(post_message, args.base_url, token, args.client, item, args.timeout)
            for item in items
        ]
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            results.append(future.result())
            if index % 10 == 0 or index == len(futures):
                elapsed = max(time.monotonic() - started, 1)
                print(f"[{index}/{len(futures)}] elapsed={round(elapsed)}s", flush=True)
    results.sort(key=lambda row: row["id"])
    summary = summarize(results)
    write_outputs(results, summary, args.output_json, args.output_md)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_md}")
    return 0 if summary["contract_pass"] == summary["count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
