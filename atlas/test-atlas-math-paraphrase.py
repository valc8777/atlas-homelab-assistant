#!/usr/bin/env python3

"""Regression test for DEF-003: math paraphrase extraction.

Confirmed on a real overnight hardening run at 648/650 (99.7%) failure --
is_math_question() correctly routed these to `math`, but
normalize_math_expression()'s prefix-stripping only ever handled 4 fixed
strings ("what is "/"what's "/"calculate "/"compute "), and none of the
downstream re.fullmatch() shape checks tolerated the trailing result phrase
("...is.", "...leaves you with.", etc.) every paraphrase template ends with.

This is a pure-function offline test -- no network, no model, no state.

Run: python3 tools/ai/test-atlas-math-paraphrase.py
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

AI_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AI_DIR))

_SPEC = importlib.util.spec_from_file_location("atlas_runner_math", AI_DIR / "run-atlas-readonly.py")
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}{(' -- ' + detail) if detail else ''}")
        FAILURES.append(label)


def solves(question: str, expected: float) -> bool:
    expr = runner.normalize_math_expression(question)
    if not expr:
        return False
    try:
        got = runner.evaluate_math_expression(expr)
    except Exception:
        return False
    return abs(got - expected) < 0.01


# The suite generator's own canonical verb list
# (ai-evals/hardening/generate-atlas-hardening-suite.py's MATH_PARAPHRASE_VERBS),
# kept in sync deliberately -- this is exactly what a fresh hardening sample
# exercises.
CANONICAL_VERBS = [
    "Work out", "Figure out", "Determine", "Calculate", "Compute", "Tally up",
    "Total up", "Add up", "Please work out", "Could you work out",
    "Can you determine", "Please calculate", "See if you can work out",
    "Go ahead and figure out",
]

CANONICAL_TEMPLATES = {
    "percent": ("{v} what 23% of 640 is.", 147.2),
    "add": ("{v} what 690 plus 111 equals.", 801),
    "subtract": ("{v} what 690 minus 111 leaves you with.", 579),
    "multiply": ("{v} what 8 times 12 comes out to.", 96),
    "divide": ("{v} what 513 divided by 9 works out to.", 57),
}


def test_all_canonical_combinations() -> None:
    print("\nAll 5 templates x all 14 canonical verbs (70 combinations)")
    total = 0
    passed = 0
    for _op, (template, expected) in CANONICAL_TEMPLATES.items():
        for verb in CANONICAL_VERBS:
            total += 1
            question = template.format(v=verb)
            if solves(question, expected):
                passed += 1
            else:
                print(f"  FAIL  {question!r}")
    check(f"{passed}/{total} canonical combinations solve correctly", passed == total)


def test_exact_real_failures() -> None:
    print("\nExact real confirmed failures from the 2026-09-06/07 overnight run")
    check("Total up ... leaves you with",
          solves("Total up what 690 minus 111 leaves you with.", 579))
    check("Go ahead and figure out ... leaves you with",
          solves("Go ahead and figure out what 551 minus 317 leaves you with.", 234))
    check("Please work out ... works out to",
          solves("Please work out what 513 divided by 9 works out to.", 57))
    check("Can you determine ... equals",
          solves("Can you determine what 880 plus 78 equals.", 958))
    check("Please calculate ... leaves you with",
          solves("Please calculate what 700 minus 490 leaves you with.", 210))


def test_existing_simple_phrasing_unaffected() -> None:
    print("\nExisting simple phrasing must be unaffected (regression guard)")
    check("what is X% of Y", solves("What is 15% of 240?", 36))
    check("what's X percent of Y", solves("What's 15 percent of 200?", 30))
    check("calculate X + Y", solves("Calculate 45 + 55", 100))
    check("compute X / Y", solves("Compute 100 / 4", 25))
    check("word fraction", solves("What is half of 84?", 42))
    check("tip calculation", solves("What is a 20% tip on $50?", 10))
    check("word-percent tip calculation", solves("What is a 17.5 percent tip on $47.60?", 8.33))
    check("meal-first decimal tip calculation",
          solves("A meal is $47.60. What is a 17.5 percent tip?", 8.33))
    check("split bill", solves("Split a $120 bill between 4 people", 30))
    check("square root", solves("What is the square root of 144?", 12))
    check("squared", solves("What is 9 squared?", 81))
    check("repeated-change word problem",
          solves("A box starts with 30 lanterns. Exactly 3 are removed each day for 2 days. "
                 "How many remain?", 24))


def test_generalizes_beyond_canonical_list() -> None:
    print("\nGeneralizes to natural variants not in the canonical verb list")
    check("help me figure out", solves("Help me figure out what 45 times 3 comes out to.", 135))
    check("tell me what X works out to", solves("Tell me what 90 divided by 5 works out to.", 18))


def test_explicit_date_offset() -> None:
    print("\nExplicit date offsets use the named base date, not today's clock")
    result = runner.run_datetime("What calendar date is 45 days after October 17, 2026?")
    check("45 days after October 17, 2026",
          result.get("result", {}).get("resolved_date") == "2026-12-01",
          result.get("answer", ""))


def test_indirect_weather_place_cleanup() -> None:
    print("\nIndirect weather advice keeps only the real place")
    check("jacket phrasing", runner.infer_weather_place(
        "Will I need a jacket in Oakland tomorrow morning?") == "Oakland")
    check("umbrella phrasing", runner.infer_weather_place(
        "Do I need an umbrella in Daly City tomorrow?") == "Daly City")


def main() -> int:
    test_all_canonical_combinations()
    test_exact_real_failures()
    test_existing_simple_phrasing_unaffected()
    test_generalizes_beyond_canonical_list()
    test_explicit_date_offset()
    test_indirect_weather_place_cleanup()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
