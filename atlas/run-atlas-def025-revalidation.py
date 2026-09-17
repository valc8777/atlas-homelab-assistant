#!/usr/bin/env python3
"""Focused live revalidation for ATLAS DEF-025 slices.

This is intentionally narrower than the general two-question runner: it
replays the original DEF-025 category families and scores the contract each
slice was built to satisfy.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import random
import re
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime


CATEGORIES = [
    "calendar_math",
    "unit_and_rate_reasoning",
    "comparative_quantitative",
    "temporal_state_tracking",
    "counterfactual_planning",
    "causal_tradeoff",
    "evidence_quality",
]


EXPECTED_ROUTES = {
    "calendar_math": {"countdown"},
    "unit_and_rate_reasoning": {"quantitative_reasoning"},
    "comparative_quantitative": {"quantitative_reasoning"},
    "temporal_state_tracking": {"closed_world_reasoning"},
    "counterfactual_planning": {"general_reference", "counterfactual_boundary_reasoning"},
    "causal_tradeoff": {"general_reference", "causal_tradeoff_reasoning"},
    "evidence_quality": {"evidence_quality_reasoning"},
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


def post_message(base_url: str, token: str, client: str, item: dict, timeout: int) -> dict:
    payload = json.dumps({
        "text": item["question"],
        "input_mode": "typed",
        "client": client,
        "context_scope": item.get("context_scope", "personal"),
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
    return grade(item, answer, route, tool, latency_ms, transport_error)


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _two_part(text: str) -> bool:
    lowered = text.lower()
    if "put " in lowered and "reminder" in lowered and " should be on " in lowered:
        return True
    if "takes about" in lowered and ("fits in" in lowered or "does not fit" in lowered or "too tight" in lowered):
        return True
    markers = (
        "reminder", "fits", "too tight", "safe", "minutes", "shorter",
        "benefit", "downside", "measurement", "avoid", "first",
        "where", "under", "on the desk", "neither source", "don't have enough evidence",
    )
    return sum(1 for marker in markers if marker in lowered) >= 2


def contract_pass(category: str, route: str | None, answer: str, transport_error: str | None) -> tuple[bool, list[str]]:
    failures: list[str] = []
    lowered = answer.lower()
    if transport_error:
        failures.append("transport_error")
    if not answer.strip():
        failures.append("empty_answer")
    if route not in EXPECTED_ROUTES[category]:
        failures.append(f"route:{route}")
    if route in {"clarification", "authority_required", "legacy_fallback_unavailable"}:
        failures.append(f"bad_route:{route}")
    if _has_any(lowered, ("i need one clarification", "what criterion", "what scope")):
        failures.append("unneeded_clarification")
    if category in {"counterfactual_planning"} and _has_any(lowered, ("i can't perform", "not authorize writes")):
        failures.append("false_authority_refusal")
    if category in {"calendar_math", "unit_and_rate_reasoning", "comparative_quantitative", "temporal_state_tracking", "causal_tradeoff"} and not _two_part(answer):
        failures.append("missing_two_part_signal")
    if category == "evidence_quality":
        if "don't have enough evidence" not in lowered and "do not have enough evidence" not in lowered:
            failures.append("missing_insufficient_evidence_refusal")
        if "should not present" not in lowered and "not present" not in lowered:
            failures.append("missing_no-confirmation_boundary")
    return not failures, failures


def grade(item: dict, answer: str, route: str | None, tool: str | None, latency_ms: int, transport_error: str | None) -> dict:
    passed, failures = contract_pass(item["category"], route, answer, transport_error)
    return {
        "id": item["id"],
        "category": item["category"],
        "question": item["question"],
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
        "# ATLAS DEF-025 Focused Revalidation",
        "",
        f"- Cases: {summary['count']}",
        f"- Contract pass: {summary['contract_pass']} / {summary['count']}",
        f"- Transport errors: {summary['transport_errors']}",
        f"- Latency: p50 {summary['latency_ms']['p50']}ms, p95 {summary['latency_ms']['p95']}ms, max {summary['latency_ms']['max']}ms",
        "",
        "| Category | Count | Contract pass | Transport errors | p95 latency | Routes | Failure counts |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for category, data in summary["categories"].items():
        routes = ", ".join(f"{k}:{v}" for k, v in sorted(data["routes"].items())) or "-"
        failures = ", ".join(f"{k}:{v}" for k, v in sorted(data["failure_counts"].items())) or "-"
        lines.append(
            f"| `{category}` | {data['count']} | {data['contract_pass']} | {data['transport_errors']} | "
            f"{data['latency_ms']['p95']} | {routes} | {failures} |"
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
                f"- Failures: {', '.join(row['contract_failures'])}",
                f"- Question: {row['question']}",
                f"- Answer: {' '.join(row['answer'].split())[:500]}",
                "",
            ])
    output_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=pathlib.Path, default=pathlib.Path("ai-evals/reasoning/atlas-two-question-reasoning-2026-09-def025-a3-reasoning-1000-seed-20260913.json"))
    parser.add_argument("--base-url", default=os.environ.get("ATLAS_SCHEDULER_BASE_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--token", default="")
    parser.add_argument("--token-env-file")
    parser.add_argument("--client", default="atlas-def025-focused-revalidation")
    parser.add_argument("--per-category", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260915)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--output-json", required=True, type=pathlib.Path)
    parser.add_argument("--output-md", required=True, type=pathlib.Path)
    args = parser.parse_args()

    token = token_from_args(args)
    if not token:
        raise SystemExit("ATLAS_SHORTCUTS_TOKEN, --token, or --token-env-file is required")
    items = load_cases(args.suite, args.per_category, args.seed)
    print(f"Loaded {len(items)} DEF-025 cases ({args.per_category} per category)")
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
