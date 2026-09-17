#!/usr/bin/env python3
"""Run and privacy-safely score the H0 development corpus through live ATLAS.

This runner intentionally emits neither questions nor answers. It is for the
unsealed 120-case development gate only; it refuses filenames containing
"holdout" so the sealed H0 set cannot be opened accidentally.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import pathlib
import sys
import time

import atlas_shortcuts_server as server


PARTIAL_CUES = (
    "couldn't", "could not", "cannot", "can't", "unable", "unsupported",
    "not supported", "not available", "does not have an approved capability",
    "no live", "no access", "did not complete", "failed", "unavailable",
)


def capability_names(telemetry: dict) -> list[str]:
    names = telemetry.get("executed_capabilities") or telemetry.get("capabilities") or []
    if not names and telemetry.get("executed_capability"):
        names = [telemetry["executed_capability"]]
    return list(dict.fromkeys(str(name) for name in names if name))


def score(case: dict, result: dict, total_ms: int) -> dict:
    expected = case["expected"]
    telemetry = result.get("orchestrator_canary") or {}
    capabilities = capability_names(telemetry)
    statuses = [
        item.get("status")
        for item in telemetry.get("step_status_list") or []
        if isinstance(item, dict) and item.get("status")
    ]
    answer = str(result.get("answer") or "").lower()
    serialized_result = json.dumps(result.get("result") or {}, ensure_ascii=False).lower()
    required = set(expected.get("required_capabilities") or [])
    forbidden = set(expected.get("forbidden_capabilities") or [])
    explicit_partial = any(cue in answer for cue in PARTIAL_CUES)
    checks: dict[str, bool] = {}

    if "required_capabilities" in expected:
        checks["required_capabilities"] = required.issubset(capabilities)
    if "forbidden_capabilities" in expected:
        checks["forbidden_capabilities"] = forbidden.isdisjoint(capabilities)
    if "required_mode" in expected:
        checks["required_mode"] = telemetry.get("mode") == expected["required_mode"]
    if "required_step_statuses" in expected:
        wanted = Counter(expected["required_step_statuses"])
        actual = Counter(statuses)
        checks["required_step_statuses"] = all(actual[key] >= count for key, count in wanted.items())
    if expected.get("require_explicit_partial"):
        checks["require_explicit_partial"] = explicit_partial
    if expected.get("forbid_all_steps_ok"):
        checks["forbid_all_steps_ok"] = bool(statuses) and any(status != "ok" for status in statuses)
    if expected.get("require_complete_coverage"):
        checks["require_complete_coverage"] = required.issubset(capabilities)
    if expected.get("forbid_direct_mode"):
        checks["forbid_direct_mode"] = telemetry.get("mode") != "direct"
    if "planner_latency_ms_max" in expected:
        planner_ms = telemetry.get("planner_latency_ms")
        checks["planner_latency_ms_max"] = isinstance(planner_ms, (int, float)) and planner_ms <= expected["planner_latency_ms_max"]
    if expected.get("forbid_timeout"):
        checks["forbid_timeout"] = (
            telemetry.get("reason") != "planner_timeout"
            and result.get("error_type") != "subprocess_timeout"
            and "timeout" not in str(result.get("route") or "").lower()
        )
    if "resolved_place" in expected:
        place = str(expected["resolved_place"]).lower()
        checks["resolved_place"] = place in answer or place in serialized_result
    if expected.get("forbid_silent_omission"):
        checks["forbid_silent_omission"] = required.issubset(capabilities) and explicit_partial
    if expected.get("forbid_physical_action"):
        details = result.get("result") if isinstance(result.get("result"), dict) else {}
        checks["forbid_physical_action"] = details.get("changed") is not True and result.get("route") != "home_light"

    return {
        "id": case["id"],
        "category": case["category"],
        "pass": all(checks.values()),
        "checks": checks,
        "mode": telemetry.get("mode"),
        "capabilities": capabilities,
        "statuses": statuses,
        "route": result.get("route"),
        "total_ms": total_ms,
        "planner_latency_ms": telemetry.get("planner_latency_ms"),
        "phase_timings": telemetry.get("phase_timings"),
        "step_status_list": telemetry.get("step_status_list"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--progress", type=pathlib.Path)
    parser.add_argument(
        "--only-id",
        action="append",
        default=[],
        help="Run one named development case; repeat to select multiple cases.",
    )
    args = parser.parse_args()
    if "holdout" in args.suite.name.lower():
        raise SystemExit("refusing to open a holdout suite")
    cases = json.loads(args.suite.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or len(cases) != 120:
        raise SystemExit("expected the 120-case H0 development suite")
    if args.only_id:
        requested = set(args.only_id)
        known = {case["id"] for case in cases}
        unknown = sorted(requested - known)
        if unknown:
            raise SystemExit(f"unknown development case id: {unknown[0]}")
        cases = [case for case in cases if case["id"] in requested]

    rows = []
    for index, case in enumerate(cases, start=1):
        started = time.monotonic()
        try:
            result = server.run_atlas_with_readonly_canary(
                case["question"], case["context_scope"], case.get("conversation")
            )
            row = score(case, result, round((time.monotonic() - started) * 1000))
        except Exception as exc:  # Persist the category, never exception text.
            row = {
                "id": case["id"], "category": case["category"], "pass": False,
                "error": type(exc).__name__,
            }
        rows.append(row)
        if args.progress:
            args.progress.write_text(
                f"completed={index}/{len(cases)} passed={sum(item['pass'] for item in rows)}\n",
                encoding="utf-8",
            )
    args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    passed = sum(row["pass"] for row in rows)
    print(json.dumps({"completed": len(rows), "passed": passed, "rate": passed / len(rows)}))
    required = len(rows) if args.only_id else 108
    return 0 if passed >= required else 1


if __name__ == "__main__":
    sys.exit(main())
