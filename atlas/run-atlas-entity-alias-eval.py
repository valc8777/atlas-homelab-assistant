#!/usr/bin/env python3
"""Score the R2 entity-resolution development suite against real seed data."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from atlas_entity_registry import load_registry_file, resolve  # noqa: E402

# Per-category gates from ATLAS_RECALL_AND_PARSING_PLAN.md Phase R2.
CATEGORY_GATES = {
    "exact_id": 1.0,
    "exact_alias": 1.0,
    "colliding_alias": 1.0,
    "cross_scope_leakage": 1.0,
    "paraphrased_alias": 0.95,
}


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case_passed(result: dict, expected: dict) -> bool:
    if expected["status"] != result["status"]:
        return False
    if expected["status"] == "resolved":
        return result.get("entity_id") == expected["entity_id"]
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, type=pathlib.Path)
    parser.add_argument("--registry", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--require-gate", action="store_true")
    args = parser.parse_args()

    registry = load_registry_file(args.registry)
    suite = json.loads(args.suite.read_text(encoding="utf-8"))

    results = []
    for case in suite:
        result = resolve(case["surface"], case["context_scope"], registry)
        passed = case_passed(result, case["expected"])
        results.append({
            "id": case["id"],
            "category": case["category"],
            "passed": passed,
            "expected_status": case["expected"]["status"],
            "actual_status": result["status"],
        })

    by_category = {}
    for category in sorted({case["category"] for case in suite}):
        rows = [row for row in results if row["category"] == category]
        accuracy = round(sum(row["passed"] for row in rows) / len(rows), 4)
        gate_minimum = CATEGORY_GATES.get(category)
        by_category[category] = {
            "attempted": len(rows),
            "accuracy": accuracy,
            "gate_minimum": gate_minimum,
            "gate_passed": gate_minimum is None or accuracy >= gate_minimum,
        }

    overall_gate = all(entry["gate_passed"] for entry in by_category.values())
    overall_accuracy = round(sum(row["passed"] for row in results) / len(results), 4)

    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "development_suite_gate_passed" if overall_gate else "development_suite_gate_failed",
        "suite_file": args.suite.name,
        "suite_sha256": digest(args.suite),
        "registry_file": args.registry.name,
        "registry_sha256": digest(args.registry),
        "attempted": len(results),
        "overall_accuracy": overall_accuracy,
        "by_category": by_category,
        "gate": {"passed": overall_gate},
        "privacy": {"surfaces_are_public_alias_strings_not_private_text": True},
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "attempted", "overall_accuracy", "by_category", "gate")}, indent=2))
    return 1 if args.require_gate and not overall_gate else 0


if __name__ == "__main__":
    raise SystemExit(main())
