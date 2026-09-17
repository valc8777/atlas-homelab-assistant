#!/usr/bin/env python3
"""H3 tests: a compound request must not silently lose an eligible exact/
current/private intent, and answer_directly must not bypass one either.

This exercises only the deterministic detection/validation core
(split_discovery_clauses, discover_required_capabilities,
check_mandatory_tool_coverage). It is deliberately NOT wired into the live
planner call path yet -- see DEC-2026-09-11-09 -- so these tests operate on
decision dicts directly, the same way semantic_step_result() and
ensure_readonly_result_contract() were tested before any live capability
read from them.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atlas_orchestrator_v2 as v2

FAILURES = []


def check(name, condition):
    if not condition:
        FAILURES.append(name)
        print(f"FAIL: {name}")
    else:
        print(f"ok:   {name}")


def test_split_discovery_clauses():
    check(
        "splits an 'and'-joined compound into two clauses",
        v2.split_discovery_clauses("Convert 10 miles to kilometers and what is the capital of Japan?")
        == ["Convert 10 miles to kilometers", "what is the capital of Japan?"],
    )
    check(
        "a single-intent question is one clause, unchanged",
        v2.split_discovery_clauses("What is the capital of Japan?") == ["What is the capital of Japan?"],
    )
    three_intent = v2.split_discovery_clauses(
        "What is 17 times 23, what is the weather in Chicago, "
        "and what does our homelab run Jellyfin on?"
    )
    check("a three-intent question splits into exactly three clauses", len(three_intent) == 3)
    five_intent = v2.split_discovery_clauses("a, b, c, d, e")
    check("more than 4 raw clauses are capped, merging the overflow into the last", len(five_intent) == 4)


def test_shortlist_capabilities_for_compound_recovers_a_truncated_clause():
    # H6 finding: shortlist_capabilities()'s own limit=4 default can truncate
    # a real capability out of a many-clause compound's whole-question
    # shortlist entirely -- the model then has no way to choose it, because
    # it was never offered as a tool. Real, not contrived: a genuine
    # 5-intent question with this exact shape dropped weather.forecast.
    five_way = (
        "Calculate 17 times 23, convert 5 miles to km, what date is 10 days "
        "after October 1st, what is the weather in Chicago, and which VM ID "
        "runs our Jellyfin on the homelab."
    )
    whole_question_only = {item.name for item in v2.shortlist_capabilities(five_way, "operations", limit=4)}
    check("whole-question shortlist alone drops weather.forecast (the bug)", "weather.forecast" not in whole_question_only)
    unioned = {item.name for item in v2.shortlist_capabilities_for_compound(five_way, "operations")}
    check(
        "shortlist_capabilities_for_compound() recovers the truncated capability",
        {"math.calculate", "units.convert", "datetime.resolve", "weather.forecast", "homelab.knowledge"} <= unioned,
    )
    # Never loses anything the whole-question shortlist already found.
    check(
        "never removes what the whole-question shortlist already found",
        whole_question_only <= unioned,
    )


def test_discover_required_capabilities_named_combinations():
    # The three combinations the plan itself calls out by name.
    required = v2.discover_required_capabilities(
        "Search the public web for the latest news and tell me what homelab documentation says about it.",
        "operations",
    )
    check(
        "public.search + homelab.knowledge combination is discovered",
        {"public.search", "homelab.knowledge"} <= required,
    )
    required = v2.discover_required_capabilities(
        "Convert 10 miles to kilometers and look up the ISBN reference for that book.",
        "operations",
    )
    check(
        "units.convert + public.reference combination is discovered",
        {"units.convert", "public.reference"} <= required,
    )
    required = v2.discover_required_capabilities(
        "What is 17 times 23, what is the weather in Chicago, "
        "and what does our homelab run Jellyfin on?",
        "operations",
    )
    check(
        "a three-intent combination discovers all three mandatory capabilities",
        {"math.calculate", "weather.forecast", "homelab.knowledge"} <= required,
    )


def test_discover_required_capabilities_regressions():
    check(
        "answer.general-only question discovers nothing mandatory",
        v2.discover_required_capabilities("Explain how photosynthesis works.", "personal") == frozenset(),
    )
    # Real false positive found while building this: shortlist_capabilities()'s
    # generous fuzzy scoring counted incidental token overlap ("times" shared
    # with datetime.resolve's own description; "what" shared with its "what
    # date" term) and wrongly included datetime.resolve for a pure math
    # question with no date sub-intent at all.
    required = v2.discover_required_capabilities("What is 17 times 23?", "personal")
    check(
        "a pure math question does not spuriously require datetime.resolve",
        required == {"math.calculate"},
    )
    check(
        "personal scope never discovers operations-only homelab.knowledge",
        "homelab.knowledge" not in v2.discover_required_capabilities(
            "What does our homelab run Jellyfin on?", "personal"
        ),
    )


def test_discover_required_capabilities_def040_phrasings():
    # DEF-040: real weather-containing compound questions silently dropped
    # a whole clause because this detector's word-matching was too narrow
    # to recognize common phrasings ("raining" != "rain", "how cold is it"
    # has no bare weather keyword, "population of X"/"who wrote X" weren't
    # covered by public.reference at all).
    cases = {
        "What is the weather in Denver? What is the population of Tokyo?":
            {"weather.forecast", "public.reference"},
        "Is it raining in Seattle right now? What is the capital of France?":
            {"weather.forecast", "public.reference"},
        "How cold is it in Chicago today? Who wrote Moby Dick?":
            {"weather.forecast", "public.reference"},
    }
    for question, expected_subset in cases.items():
        required = v2.discover_required_capabilities(question, "personal")
        check(f"DEF-040 phrasing discovers both capabilities: {question[:50]!r}",
              expected_subset <= required)

    # Regression: the historical false-positive case must still discover
    # only the one real capability, not be over-broadened by the new terms.
    required = v2.discover_required_capabilities("What is 17 times 23?", "personal")
    check("DEF-040 additions do not reintroduce the math/datetime false positive",
          required == {"math.calculate"})
    # "hot take"/"cold case"-style bare adjectives must not spuriously
    # trigger weather.forecast -- only the narrow "how cold/hot/warm"
    # phrases were added, not the bare words.
    check("bare temperature adjectives outside the narrow phrase do not misfire",
          "weather.forecast" not in v2.discover_required_capabilities(
              "What is the hot new restaurant downtown?", "personal"
          ))


def test_discover_required_capabilities_def044_phrasings():
    # DEF-044: found live via A5's classify_question() false-negative scan --
    # "director" (noun) didn't match "who directed X" (verb form), and
    # neither "wrote"/"written by" matched "when was X published". A mixed-
    # capability compound with a "directed"/"published" clause now correctly
    # discovers both capabilities instead of silently missing the reference
    # one.
    cases = {
        "Who directed Dune Part Two, and what is the weather in Tokyo?":
            {"public.reference", "weather.forecast"},
        "What year was the first Dune novel published, and what is 9 times 9?":
            {"public.reference", "math.calculate"},
    }
    for question, expected_subset in cases.items():
        required = v2.discover_required_capabilities(question, "personal")
        check(f"DEF-044 phrasing discovers both capabilities: {question[:50]!r}",
              expected_subset <= required)

    # Known, still-open limitation (not fixed by this widening, not a new
    # DEF): when BOTH clauses of a compound need the SAME capability for
    # DIFFERENT entities, discover_required_capabilities() still reports
    # just one capability name, since it returns a union of capability
    # names, not a per-entity count. run_structured_lookup() (the legacy
    # single-domain "book"/"movie"/"tv"/"music" route) does one lookup on
    # the whole question and cannot answer both entities either way. This
    # is the same class of accepted limitation as the documented compound-
    # question single-intent drop (DEC-2026-09-15-12) -- a real fix would
    # need genuine multi-entity decomposition for one capability, not a
    # word-list change.
    required = v2.discover_required_capabilities(
        "Tell me who directed dune part two. Also what year was the first dune novel published?",
        "personal",
    )
    check(
        "DEF-044: same-capability two-entity compound is a known, still-open gap",
        required == {"public.reference"},
    )


def test_mandatory_tool_capabilities_never_includes_action_authority():
    # H4 explicitly requires keeping device/action compounds fail-closed and
    # separate: this fallback path must never be able to reach a write-
    # capable capability, by construction, not by a runtime scope check.
    for scope in ("personal", "operations", "admin"):
        for item in v2.capability_registry(scope):
            if item.name in v2.MANDATORY_TOOL_CAPABILITIES:
                check(f"{item.name} ({scope}) is read-only", item.read_only)


def test_check_mandatory_tool_coverage():
    question = "What is 17 times 23, what is the weather in Chicago, and what does our homelab run Jellyfin on?"

    check(
        "answer_directly while a mandatory signal exists is rejected",
        v2.check_mandatory_tool_coverage(question, {"mode": "direct", "capabilities": []}, "operations")
        == "mandatory_tool_bypass",
    )
    check(
        "answer_directly with no mandatory signal at all passes",
        v2.check_mandatory_tool_coverage(
            "Explain how photosynthesis works.", {"mode": "direct", "capabilities": []}, "personal"
        )
        is None,
    )
    check(
        "a plan covering every required capability passes",
        v2.check_mandatory_tool_coverage(
            question,
            {"mode": "deep", "capabilities": ["math.calculate", "weather.forecast", "homelab.knowledge"]},
            "operations",
        )
        is None,
    )
    check(
        "a plan silently missing one required capability is incomplete_plan",
        v2.check_mandatory_tool_coverage(
            question, {"mode": "deep", "capabilities": ["math.calculate", "weather.forecast"]}, "operations"
        )
        == "incomplete_plan",
    )
    check(
        "a single-intent plan with no compound requirement passes",
        v2.check_mandatory_tool_coverage(
            "What is the weather in Chicago?", {"mode": "tool", "capabilities": ["weather.forecast"]}, "personal"
        )
        is None,
    )


def run_all():
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
    print(f"\n{len(FAILURES)} failing" if FAILURES else "\nAll tests passed")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
