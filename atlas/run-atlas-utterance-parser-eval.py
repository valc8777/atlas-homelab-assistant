#!/usr/bin/env python3
"""Evaluate the deterministic R1 parser without persisting private values."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import statistics
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import atlas_orchestrator_v2
import atlas_utterance_parser


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--require-gate", action="store_true")
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    results = []
    latencies = []
    import time
    for item in suite:
        started = time.monotonic()
        try:
            scope = item["context_scope"]
            allowed = {cap.name for cap in atlas_orchestrator_v2.capability_registry(scope)}
            parsed = atlas_utterance_parser.validate_parsed_utterance(
                atlas_utterance_parser.parse_validated_decision(item["question"], item["decision"], scope),
                scope,
                allowed,
            )
            expected = item["expected"]
            capabilities = [intent["capability"] for intent in parsed["intents"]]
            intent_ok = capabilities == expected["capabilities"]
            slots_ok = [intent["slots"] for intent in parsed["intents"]] == expected["slots"]
            entities_ok = parsed["entities"] == expected["entities"]
            status = "completed"
            error_type = None
        except Exception as exc:
            intent_ok = slots_ok = entities_ok = False
            status = "failed"
            error_type = type(exc).__name__
        latency_ms = round((time.monotonic() - started) * 1000, 3)
        latencies.append(latency_ms)
        results.append({
            "id": item["id"], "category": item["category"], "status": status,
            "error_type": error_type, "intent_ok": intent_ok, "slots_ok": slots_ok,
            "entities_ok": entities_ok, "parse_ok": intent_ok and slots_ok and entities_ok,
            "latency_ms": latency_ms,
        })
    ratio = lambda key, rows=results: round(sum(bool(row[key]) for row in rows) / len(rows), 4)
    by_category = {}
    for category in sorted({item["category"] for item in suite}):
        rows = [row for row in results if row["category"] == category]
        by_category[category] = {
            "attempted": len(rows),
            "intent_accuracy": round(sum(row["intent_ok"] for row in rows) / len(rows), 4),
            "slot_accuracy": round(sum(row["slots_ok"] for row in rows) / len(rows), 4),
            "entity_accuracy": round(sum(row["entities_ok"] for row in rows) / len(rows), 4),
            "parse_accuracy": round(sum(row["parse_ok"] for row in rows) / len(rows), 4),
        }
    gate = ratio("intent_ok") >= 0.95 and ratio("slots_ok") >= 0.95 and ratio("entities_ok") >= 0.95
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "baseline_passed_contract_gate" if gate else "baseline_failed_contract_gate",
        "suite_file": args.suite.name,
        "suite_sha256": digest(args.suite),
        "attempted": len(results),
        "intent_accuracy": ratio("intent_ok"),
        "slot_accuracy": ratio("slots_ok"),
        "entity_accuracy": ratio("entities_ok"),
        "parse_accuracy": ratio("parse_ok"),
        "latency_ms": {"mean": round(statistics.fmean(latencies), 3), "max": max(latencies)},
        "by_category": by_category,
        "gate": {"minimum": 0.95, "passed": gate},
        "privacy": {"questions_in_report": False, "slot_values_in_report": False, "entity_values_in_report": False},
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "attempted", "intent_accuracy", "slot_accuracy", "entity_accuracy", "parse_accuracy", "latency_ms", "by_category", "gate")}, indent=2))
    return 1 if args.require_gate and not gate else 0


if __name__ == "__main__":
    raise SystemExit(main())
