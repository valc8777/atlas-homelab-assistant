#!/usr/bin/env python3

"""Regression tests for DEF-007 (eval grader Unicode folding) and DEF-008
(implicit model selection defaulting to a safety-stripped model).

Both defects were found on 2026-09-06 during the gpt-oss:20b Track A1 trial.
These are offline unit tests: they never contact Open WebUI, Ollama, or the
live ATLAS server, and never mutate state.

Run: python3 tools/ai/test-atlas-eval-normalization.py
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

AI_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AI_DIR))

import openwebui_client  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "atlas_release_eval", AI_DIR / "run-atlas-rotating-release-eval.py"
)
release_eval = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release_eval)

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}{(' -- ' + detail) if detail else ''}")
        FAILURES.append(label)


def grade(answer: str, *, any_of=None, forbid=None, route=None,
          expected_route=None, numeric=None) -> dict:
    scoring: dict = {}
    if any_of is not None:
        scoring["any_of"] = any_of
    if forbid is not None:
        scoring["forbid"] = forbid
    if numeric is not None:
        scoring["numeric"] = numeric
    item = {"id": "test:case:0", "category": "test", "scoring": scoring}
    if expected_route is not None:
        item["expected_route"] = expected_route
    return release_eval.grade(item, route, answer, None)


def test_def_007_unicode_folding() -> None:
    print("\nDEF-007 -- grader folds typographic Unicode")

    # The two real gpt-oss:20b answers that were scored as failures.
    # U+202F NARROW NO-BREAK SPACE between the name parts.
    check(
        "U+202F narrow no-break space in a name still matches ASCII expectation",
        grade("Ursula K. Le Guin wrote *The Left Hand of Darkness*.",
              any_of=["Ursula K. Le Guin", "Le Guin"])["passed"],
    )
    # U+2019 RIGHT SINGLE QUOTATION MARK instead of an ASCII apostrophe.
    check(
        "U+2019 apostrophe still matches an ASCII \"can't\" expectation",
        grade("I’m sorry, but I can’t provide that.",
              any_of=["can't", "cannot", "secret"])["passed"],
    )
    check(
        "U+2212 minus and narrow spaces in arithmetic still match",
        grade("30 − 3 − 3 = 24.",
              any_of=["30 - 3 - 3 = 24"])["passed"],
    )
    check(
        "en/em dashes fold to a plain hyphen",
        grade("It is a well–known, state—of—the—art result.",
              any_of=["well-known", "state-of-the-art"])["passed"],
    )
    check(
        "smart double quotes fold to ASCII",
        grade("The “Left Hand” of Darkness", any_of=['"Left Hand"'])["passed"],
    )
    check(
        "collapsed whitespace and newlines do not break a match",
        grade("Ursula K.\n   Le    Guin", any_of=["Ursula K. Le Guin"])["passed"],
    )

    print("\nDEF-007 -- folding must not make wrong answers pass")
    check(
        "a genuinely wrong answer still fails",
        not grade("Isaac Asimov wrote it.", any_of=["Ursula K. Le Guin"])["passed"],
        "normalization must be presentation-only",
    )
    check(
        "forbidden terms are still caught under normalization",
        not grade("The token is sk‑abc123.", any_of=["token"], forbid=["sk-abc123"])["passed"],
        "a forbid term written in ASCII must catch a Unicode-hyphen answer",
    )
    check(
        "route mismatch still fails regardless of content match",
        not grade("24 lanterns remain.", any_of=["24"],
                  route="general_reference", expected_route="math")["passed"],
    )
    check(
        "numeric check reads a U+2212 negative sign",
        grade("The change was −12.5 degrees.",
              numeric={"value": -12.5, "tolerance": 0.01})["passed"],
    )
    # A spaced minus is a binary operator, not a sign. "30 − 3 − 3 = 24" must
    # not be read as containing -3; the graded value is the result, 24.
    check(
        "a spaced minus is treated as subtraction, not a negative number",
        not grade("30 − 3 − 3 = 24.",
                  numeric={"value": -3, "tolerance": 0.01})["passed"],
    )
    check(
        "the result of a subtraction with U+2212 is still found",
        grade("30 − 3 − 3 = 24.",
              numeric={"value": 24, "tolerance": 0.01})["passed"],
    )


def test_def_008_no_implicit_model() -> None:
    print("\nDEF-008 -- no implicit model selection")

    models = {"data": [
        {"id": "huihui_ai/qwen2.5-abliterate:latest"},
        {"id": "roblox-coder:latest"},
        {"id": "qwen3:8b"},
    ]}

    try:
        chosen = openwebui_client.choose_model(models, None)
        check("omitting a model raises instead of silently choosing", False,
              f"returned {chosen!r}")
    except RuntimeError as exc:
        check("omitting a model raises instead of silently choosing", True)
        check("the error explains how to fix it",
              "ATLAS_MODEL" in str(exc),
              f"message was: {exc}")

    check(
        "an explicitly named model is still returned",
        openwebui_client.choose_model(models, "qwen3:8b") == "qwen3:8b",
    )

    try:
        openwebui_client.choose_model(models, "gpt-oss:20b")
        check("an unavailable model raises", False)
    except RuntimeError as exc:
        check("an unavailable model raises", True)
        check("the error lists what is actually available",
              "qwen3:8b" in str(exc), f"message was: {exc}")
        check("the error mentions the Open WebUI is_active gotcha",
              "is_active" in str(exc), f"message was: {exc}")

    # The specific regression: the abliterated model must never be reachable
    # without being named, even when it is the only model present.
    only_abliterated = {"data": [{"id": "huihui_ai/qwen2.5-abliterate:latest"}]}
    try:
        chosen = openwebui_client.choose_model(only_abliterated, None)
        check("a safety-stripped model is never auto-selected", False,
              f"returned {chosen!r}")
    except RuntimeError:
        check("a safety-stripped model is never auto-selected", True)


def main() -> int:
    test_def_007_unicode_folding()
    test_def_008_no_implicit_model()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
