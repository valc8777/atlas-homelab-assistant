#!/usr/bin/env python3
"""Generate fresh synthetic R1 typed-parser development fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
from datetime import datetime


CITIES = [
    "Davis", "Monterey", "Truckee", "Modesto", "Auburn", "Chico", "Napa", "Berkeley",
    "Petaluma", "Sonoma", "Merced", "Folsom", "Lodi", "Mendocino", "Arcata", "Redding",
    "Fremont", "Vallejo", "Sausalito", "Woodland",
]
UNIT_PAIRS = [
    ("kilometers", "miles"), ("pounds", "ounces"), ("liters", "gallons"),
    ("celsius", "fahrenheit"), ("hours", "minutes"), ("meters", "feet"),
]
TIME_PHRASES = [
    "today", "tomorrow", "tonight", "next Monday", "next Tuesday", "next Wednesday",
    "next Thursday", "next Friday", "next Saturday", "next Sunday", "in 3 days",
    "in 5 weeks", "in 45 minutes", "in 6 hours", "next month", "next year",
]


def source(seed: int) -> dict:
    return {
        "dataset": "ATLAS R1 typed-parser synthetic development",
        "license_status": "locally_authored_synthetic",
        "seed": seed,
        "eval_only": True,
    }


def case(case_id: str, category: str, question: str, decision: dict, expected: dict, seed: int) -> dict:
    return {
        "id": case_id,
        "category": category,
        "question": question,
        "context_scope": decision["context_scope"],
        "decision": decision,
        "expected": expected,
        "source": source(seed),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument("--seed", type=int, default=2026090809)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    cases = []

    for index in range(20):
        left = rng.randint(11, 98)
        right = rng.randint(3, 49)
        question = f"Calculate {left} times {right}."
        decision = {"mode": "tool", "context_scope": "personal", "capabilities": ["math.calculate"]}
        cases.append(case(f"r1-{args.seed}-math-{index:02d}", "number_slots", question, decision, {
            "capabilities": ["math.calculate"], "slots": [{"number": [str(left), str(right)]}], "entities": [],
        }, args.seed))

    for index in range(20):
        value = rng.randint(2, 240)
        source_unit, target_unit = UNIT_PAIRS[index % len(UNIT_PAIRS)]
        question = f"Convert {value} {source_unit} to {target_unit}."
        decision = {"mode": "tool", "context_scope": "personal", "capabilities": ["units.convert"]}
        cases.append(case(f"r1-{args.seed}-units-{index:02d}", "unit_slots", question, decision, {
            "capabilities": ["units.convert"],
            "slots": [{"number": [str(value)], "unit": [source_unit, target_unit]}], "entities": [],
        }, args.seed))

    for index in range(20):
        phrase = TIME_PHRASES[index % len(TIME_PHRASES)]
        question = f"What date or time is {phrase}?"
        decision = {"mode": "tool", "context_scope": "personal", "capabilities": ["datetime.resolve"]}
        numbers = [value for value in phrase.split() if value.isdigit()]
        slots = {"relative_time": [phrase.lower()]}
        if numbers:
            slots["number"] = numbers
            slots["unit"] = [phrase.split()[-1].lower()]
        cases.append(case(f"r1-{args.seed}-time-{index:02d}", "relative_time_slots", question, decision, {
            "capabilities": ["datetime.resolve"], "slots": [slots], "entities": [],
        }, args.seed))

    for index, city in enumerate(CITIES):
        timing = "tomorrow" if index % 2 == 0 else "tonight"
        question = f"What is the weather in {city} {timing}?"
        decision = {"mode": "retrieve", "context_scope": "personal", "capabilities": ["weather.forecast"]}
        cases.append(case(f"r1-{args.seed}-weather-{index:02d}", "location_entities", question, decision, {
            "capabilities": ["weather.forecast"], "slots": [{"relative_time": [timing]}],
            "entities": [{"surface": city, "type": "location", "resolution": "syntax_exact"}],
        }, args.seed))

    for index in range(20):
        vm_id = 310 + index
        question = f"Find the documented status of VM {vm_id}."
        decision = {"mode": "retrieve", "context_scope": "operations", "capabilities": ["homelab.knowledge"]}
        cases.append(case(f"r1-{args.seed}-vm-{index:02d}", "exact_id_entities", question, decision, {
            "capabilities": ["homelab.knowledge"], "slots": [{"number": [str(vm_id)]}],
            "entities": [{"surface": f"VM {vm_id}", "type": "virtual_machine", "resolution": "exact_id"}],
        }, args.seed))

    for index, city in enumerate(CITIES):
        left = rng.randint(12, 80)
        right = rng.randint(4, 35)
        weather_request = f"Weather in {city} tomorrow."
        math_request = f"Calculate {left} times {right}."
        question = f"{weather_request} Also, {math_request}"
        decision = {
            "mode": "deep", "context_scope": "personal",
            "capabilities": ["weather.forecast", "math.calculate"],
            "steps": [
                {"capability": "weather.forecast", "request": weather_request},
                {"capability": "math.calculate", "request": math_request},
            ],
        }
        cases.append(case(f"r1-{args.seed}-compound-{index:02d}", "compound_intents", question, decision, {
            "capabilities": ["weather.forecast", "math.calculate"],
            "slots": [{"relative_time": ["tomorrow"]}, {"number": [str(left), str(right)]}],
            "entities": [{"surface": city, "type": "location", "resolution": "syntax_exact"}],
        }, args.seed))

    rng.shuffle(cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    counts = {category: sum(item["category"] == category for item in cases) for category in sorted({item["category"] for item in cases})}
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "development_unrun",
        "seed": args.seed,
        "count": len(cases),
        "suite_file": args.output.name,
        "suite_sha256": digest,
        "category_counts": counts,
        "source": "locally authored synthetic exact-parser fixtures",
        "eval_only": True,
    }
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
