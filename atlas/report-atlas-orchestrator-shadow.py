#!/usr/bin/env python3

"""Summarize privacy-safe ATLAS Orchestration v2 shadow traces."""

import argparse
import json
import pathlib
import statistics

import atlas_app_store


def percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))]


def counts(items: list[dict], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "none")
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items(), key=lambda pair: (-pair[1], pair[0])))


def build_report(db_path: pathlib.Path, limit: int) -> dict:
    traces = atlas_app_store.list_orchestration_shadow_traces(db_path, limit=limit)
    completed = [item for item in traces if item.get("status") == "completed"]
    latencies = [int(item["planner_latency_ms"]) for item in completed if item.get("planner_latency_ms") is not None]
    total = len(traces)
    return {
        "window": total,
        "completed": len(completed),
        "valid_rate": round(len(completed) / total, 4) if total else None,
        "statuses": counts(traces, "status"),
        "modes": counts(completed, "decision_mode"),
        "decision_sources": counts(completed, "decision_source"),
        "scopes": counts(completed, "decision_scope"),
        "current_routes": counts(traces, "current_route"),
        "errors": counts([item for item in traces if item.get("error_type")], "error_type"),
        "planner_latency_ms": {
            "mean": round(statistics.fmean(latencies)) if latencies else None,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": max(latencies) if latencies else None,
        },
        "privacy": {
            "raw_question_stored": False,
            "raw_model_output_stored": False,
            "fingerprint": "owner-keyed HMAC-SHA256",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=pathlib.Path, default=pathlib.Path("/app/atlas-app/atlas-app.db"))
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = build_report(args.db, max(1, min(args.limit, 10000)))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"Window: {report['window']} traces; valid: {report['completed']} ({report['valid_rate']})")
    print(f"Statuses: {report['statuses']}")
    print(f"Modes: {report['modes']}")
    print(f"Errors: {report['errors']}")
    print(f"Planner latency ms: {report['planner_latency_ms']}")


if __name__ == "__main__":
    main()
