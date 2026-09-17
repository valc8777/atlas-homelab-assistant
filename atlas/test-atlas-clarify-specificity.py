#!/usr/bin/env python3
"""Regression coverage for DEF-034: clarify_request's own "missing" detail
must be surfaced in the owner-facing message instead of always falling back
to a generic template that ignores what the model actually said was
ambiguous.
"""

import pathlib
import sys

AI_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AI_DIR))

import atlas_orchestrator_v2 as orch  # noqa: E402
import atlas_shortcuts_server as server  # noqa: E402


def test_deterministic_gate_names_the_specific_superlative() -> None:
    # deterministic_clarification_plan() is a regex match, not a model
    # call, so it never has a model-generated "missing" detail -- but it
    # already captures the specific superlative word, so it should name
    # that instead of falling all the way back to the fully generic
    # template. Fresh questions, not the exact real one that exposed this.
    cases = {
        "What is the largest us government base": "largest",
        "What is the best pizza place": "best",
        "What is the most popular programming language": "most popular",
    }
    for question, comparison in cases.items():
        hard_decision = orch.deterministic_clarification_plan(question, "personal")
        assert hard_decision is not None, question
        assert hard_decision.get("missing") == f"the criterion or scope for '{comparison}'", hard_decision

        result = server.clarification_result_from_plan(question, hard_decision, {})
        assert comparison in result["answer"], result["answer"]
        assert result["answer"] != (
            "I need one clarification before I can answer that reliably. What criterion, scope, or target should I use?"
        ), result["answer"]
    print("PASS: DEF-034 the deterministic clarify gate names the specific superlative, not just a generic template")


def main() -> None:
    # A specific "missing" detail from the model is used verbatim, phrased
    # as a question regardless of whether the model's own text ended with
    # punctuation.
    result = server.clarification_result_from_plan(
        "What is the largest US government base",
        {"mode": "clarify", "missing": "the criterion for 'largest' (land area, population, or budget)"},
        {},
    )
    assert result["route"] == "clarification", result
    assert "the criterion for 'largest' (land area, population, or budget)?" in result["answer"], result["answer"]

    # A "missing" detail that already ends with a question mark or period
    # doesn't end up with doubled punctuation.
    result = server.clarification_result_from_plan(
        "What's the best show?",
        {"mode": "clarify", "missing": "what genre or platform?"},
        {},
    )
    assert result["answer"].count("?") == 1, result["answer"]

    # No "missing" detail at all -- falls back to the generic template, not
    # a crash or an empty clarify message.
    result = server.clarification_result_from_plan(
        "Highest player count",
        {"mode": "clarify"},
        {},
    )
    assert result["answer"] == (
        "I need one clarification before I can answer that reliably. What criterion, scope, or target should I use?"
    ), result["answer"]

    # A blank/whitespace-only "missing" also falls back to the generic
    # template rather than producing a malformed "...reliably: ?" message.
    result = server.clarification_result_from_plan(
        "Highest player count",
        {"mode": "clarify", "missing": "   "},
        {},
    )
    assert "reliably: ?" not in result["answer"], result["answer"]
    assert "What criterion, scope, or target should I use?" in result["answer"], result["answer"]

    # The pre-existing Daytona special case must still take priority over
    # any "missing" detail, unchanged.
    result = server.clarification_result_from_plan(
        "Who won the first Daytona?",
        {"mode": "clarify", "missing": "which Daytona"},
        {},
    )
    assert "Daytona 500" in result["answer"], result["answer"]

    print("PASS: DEF-034 clarify responses use the model's own 'missing' detail when present")

    test_deterministic_gate_names_the_specific_superlative()


if __name__ == "__main__":
    main()
