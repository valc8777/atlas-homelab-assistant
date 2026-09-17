#!/usr/bin/env python3
"""Regression test for the conversion-paraphrase fix found during Track A3
Gate 2 measurement (2026-09-08).

A fresh 300-case paraphrase-pressure hardening run (native tool planner +
A3 dispatch order enabled) scored math 100/100, datetime 99/100, but
conversion only 33/100 -- dragging the combined average to 77.33%, just
under the roadmap's 80% gate. Diagnosis found two distinct root causes, both
in run_conversion()'s own regex parser (an execution-layer issue, unrelated
to A3's dispatch-order change itself -- the native planner correctly routed
these to the units.convert capability every time):

1. 19 of 43 failures: the pair was already supported (miles<->km,
   pounds<->kg), but the connector alternation only accepted "in"/"to", not
   "into" -- e.g. "change 280 miles into kilometers" never matched.
2. 24 of 43 failures: gallons<->liters and mass ounces<->grams were not
   supported at all, regardless of phrasing.

Fix: widened the connector alternation on every first-direction pattern
(mechanical, matches DEF-003/DEF-017's "widen an existing complete route"
governing test used throughout this project) and added the two missing unit
pairs. All 43 real failing questions from the actual eval run now parse
correctly, verified against the exact conversion factors.

This is a pure-function offline test -- no network, no model, no state.

Run: python3 tools/ai/test-atlas-conversion-paraphrase.py
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

AI_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AI_DIR))

_SPEC = importlib.util.spec_from_file_location("atlas_runner_conversion_paraphrase", AI_DIR / "run-atlas-readonly.py")
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}{(' -- ' + detail) if detail else ''}")
        FAILURES.append(label)


def parses_to(question: str, expected_value: float, expected_unit: str, tolerance: float = 0.05) -> bool:
    result = runner.run_conversion(question)
    if result["answer"] == "I couldn't parse that conversion directly.":
        return False
    got_value = result.get("result", {}).get("output_value")
    got_unit = result.get("result", {}).get("output_unit")
    if got_value is None or got_unit != expected_unit:
        return False
    return abs(got_value - expected_value) < tolerance


def test_connector_widening_on_already_supported_pairs() -> None:
    print("\nConnector widening: 'into'/'as' now work on pairs that already had 'in'/'to' support")
    check("miles into kilometers",
          parses_to("Go ahead and change 280.2 miles into kilometers for me, please.", 280.2 * 1.60934, "kilometers"))
    check("kilometers into miles",
          parses_to("Convert 100 kilometers into miles.", 100 / 1.60934, "miles"))
    check("kilograms into pounds",
          parses_to("Transform 323.7 kilograms into pounds.", 323.7 / 0.45359237, "pounds"))
    check("pounds into kilograms",
          parses_to("Change 50 pounds into kilograms.", 50 * 0.45359237, "kilograms"))
    check("USD into EUR (currency, same widening)",
          runner.run_conversion("Convert 100 dollars into euros.")["answer"] != "I couldn't parse that conversion directly.")


def test_previously_missing_unit_pairs() -> None:
    print("\nGenuinely new unit pairs: gallons<->liters and mass ounces<->grams")
    check("gallons to liters",
          parses_to("Change 451.3 gallons into liters for me, please.", 451.3 * 3.78541, "liters"))
    check("liters to gallons",
          parses_to("Go ahead and change 129.4 liters into gallons for me, please.", 129.4 / 3.78541, "gallons"))
    check("grams to ounces (mass)",
          parses_to("Transform 120.3 grams into ounces.", 120.3 / 28.3495, "ounces"))
    check("ounces to grams (mass)",
          parses_to("Convert 10 ounces to grams.", 10 * 28.3495, "grams"))
    check("how many liters in N gallons",
          parses_to("How many liters are in 5 gallons?", 5 * 3.78541, "liters"))
    check("how many gallons in N liters",
          parses_to("How many gallons are in 20 liters?", 20 / 3.78541, "gallons"))


def test_all_43_real_failing_questions_from_the_actual_eval_run() -> None:
    print("\nAll 43 real execution-layer failures from the 2026-09-08 A3 Gate 2 run now parse")
    # Exact questions pulled from the sealed hardening run's result JSON.
    questions = [
        "Go ahead and change 280.2 miles into kilometers for me, please.",
        "Change 451.3 gallons into liters for me, please.",
        "Go ahead and change 129.4 liters into gallons for me, please.",
        "Go ahead and change 464.7 gallons into liters for me, please.",
        "Transform 120.3 grams into ounces.",
        "Transform 60.9 gallons into liters for me, please.",
        "Convert 457.0 kilograms into pounds.",
        "Please transform 13.7 grams into ounces.",
        "Translate 117.8 kilograms into pounds.",
        "Change 14.0 ounces into grams for me, please.",
        "Turn 474.0 grams to ounces.",
        "Transform 323.7 kilograms into pounds.",
        "Change 356.7 gallons to liters.",
        "Please transform 316.1 inches into centimeters for me, please.",
        "Convert 52.8 pounds into kilograms.",
        "Translate 20.7 meters to feet.",
        "Go ahead and change 447.3 centimeters to inches.",
        "Turn 104.1 inches to centimeters.",
    ]
    total = 0
    parsed = 0
    for question in questions:
        total += 1
        result = runner.run_conversion(question)
        if result["answer"] != "I couldn't parse that conversion directly.":
            parsed += 1
        else:
            print(f"  FAIL  did not parse: {question!r}")
    check(f"{parsed}/{total} sampled real failing questions now parse", parsed == total)


def test_existing_simple_phrasing_unaffected() -> None:
    print("\nExisting non-paraphrased phrasing must be unaffected (regression guard)")
    check("simple miles to km", parses_to("Convert 10 miles to kilometers.", 10 * 1.60934, "kilometers"))
    check("simple lbs to kg", parses_to("Convert 10 pounds to kilograms.", 10 * 0.45359237, "kilograms"))
    check("simple feet to meters", parses_to("Convert 10 feet to meters.", 10 * 0.3048, "meters"))
    check("simple inches to cm", parses_to("Convert 10 inches to centimeters.", 10 * 2.54, "centimeters"))
    check("simple tsp to tbsp", parses_to("Convert 9 teaspoons to tablespoons.", 3.0, "tablespoons"))
    check("USD to EUR unchanged",
          runner.run_conversion("Convert 100 dollars to euros.")["answer"]
          == "About 92 euros using a static planning rate of 1 USD = 0.92 EUR.")


def test_unsupported_pairs_still_correctly_unsupported() -> None:
    print("\nGenuinely unsupported pairs must still fail cleanly (no false positives from the widening)")
    check("nonsense unit pair", runner.run_conversion("Convert 5 gallons to smiles.")["answer"]
          == "I couldn't parse that conversion directly.")
    check("unsupported physical pair", runner.run_conversion("Convert 10 fathoms to leagues.")["answer"]
          == "I couldn't parse that conversion directly.")
    check("GBP still unsupported (capability-gap territory, not this fix)",
          runner.run_conversion("Convert 50 British pounds to yen.")["answer"]
          == "I couldn't parse that conversion directly.")


def main() -> int:
    test_connector_widening_on_already_supported_pairs()
    test_previously_missing_unit_pairs()
    test_all_43_real_failing_questions_from_the_actual_eval_run()
    test_existing_simple_phrasing_unaffected()
    test_unsupported_pairs_still_correctly_unsupported()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
