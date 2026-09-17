#!/usr/bin/env python3
"""Regression coverage for the DEF-028 / DEF-030 Phase 3 fixes.

Every case here is deliberately NOT the exact wording that originally
exposed each bug (those live in DEC-2026-09-16-06/-08) -- these are
independent phrasings plus fresh regression checks, so a future change
can't silently re-narrow a fix back down to one known string.
"""

import datetime as dt
import importlib.util
import pathlib
import sys
from zoneinfo import ZoneInfo


MODULE_PATH = pathlib.Path(__file__).with_name("run-atlas-readonly.py")
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("atlas_readonly", MODULE_PATH)
atlas_readonly = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(atlas_readonly)


def test_def028_weather_no_location_fallback() -> None:
    # Original repro shapes (DEC-2026-09-16-06), plus fresh paraphrases that
    # also have no "in X"/"for X" clause at all.
    no_location_questions = [
        "will it rain this weekend",
        "hows the weather where im at",
        "idk if i shld bring an umbrella 2day",
        "is it gonna rain and should i water the plants or not",
        # fresh paraphrases, never used in any prior test or probe
        "do i need a coat today",
        "should i expect any storms this week",
        "is it cold outside right now",
    ]
    for question in no_location_questions:
        place = atlas_readonly.infer_weather_place(question)
        assert place != question.strip(), (
            f"no-location question still fell back to the raw question text: {question!r} -> {place!r}"
        )
        assert place, f"no-location question resolved to an empty place: {question!r}"

    # Regression: a real, explicit location must still be extracted
    # correctly -- this fix must never make a genuinely-scoped question
    # fall back to the default.
    assert atlas_readonly.infer_weather_place("whats the weather in Chicago tomorrow") == "Chicago"
    assert atlas_readonly.infer_weather_place("is it gonna rain in Martinez right now") == "Martinez"
    print("PASS: DEF-028 no-location fallback uses the default, not the raw question")


def test_def028_bare_place_name_not_defaulted() -> None:
    # DEC-2026-09-16-14: a real "compare Oakland to San Francisco" question
    # decomposes (correctly, by the model) into two deep-plan steps whose
    # request text is just the bare place name, e.g. "Oakland" -- no "in
    # X"/"for X" framing at all. The first version of the DEF-028 fix
    # defaulted both steps to the same place, silently breaking the
    # comparison. A short, letters-only phrase with no sentence-shaped word
    # must be treated as the place itself, not defaulted.
    for place in ["Oakland", "San Francisco", "New York", "Chicago", "Martinez"]:
        assert atlas_readonly.infer_weather_place(place) == place, place

    # But a short, genuinely no-location fragment must still default.
    for fragment in ["weather now", "is it raining", "rain today"]:
        result = atlas_readonly.infer_weather_place(fragment)
        assert result != fragment, f"short no-location fragment kept as a place: {fragment!r} -> {result!r}"
    print("PASS: DEF-028 preserves a bare place name instead of defaulting it away")


def test_def032_using_clause_boundary() -> None:
    # Fresh paraphrase, not the exact real question that exposed this.
    place = atlas_readonly.infer_weather_place("Tell me the forecast in Denver using the app")
    assert place == "Denver", place
    print("PASS: DEF-032 'using' is a recognized clause boundary after the place name")


def test_def031_city_abbreviation_aliases() -> None:
    # Confirmed empirically against Open-Meteo's live geocoding API: these
    # either return no result at all, or (for "la") a wrong, differently-
    # named place instead of Los Angeles.
    cases = {
        "what is the weather in sf": "San Francisco",
        "weather in la": "Los Angeles",
        "weather in dc": "Washington, DC",
        "weather in philly": "Philadelphia",
        "weather in nola": "New Orleans",
    }
    for question, expected in cases.items():
        assert atlas_readonly.infer_weather_place(question) == expected, question

    # Regression: a real place that happens to share letters with an alias
    # key must not be rewritten.
    assert atlas_readonly.infer_weather_place("weather in Atlanta") == "Atlanta"
    print("PASS: DEF-031 common city abbreviations resolve to their full name before geocoding")


def test_def030_today_as_countdown_target() -> None:
    now = dt.datetime(2026, 9, 16, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert atlas_readonly.parse_target_date("today", now) == now.date()
    assert atlas_readonly.parse_target_date("now", now) == now.date()

    result = atlas_readonly.run_countdown("how many days until today")
    assert result["result_status"] == "ok", result
    assert result["result"]["days"] == 0, result
    assert result["answer"] == "Today.", result

    # Regression: a real future target date must still parse correctly.
    real_target = atlas_readonly.parse_target_date("december 25", now)
    assert real_target == dt.date(2026, 12, 25), real_target
    print("PASS: DEF-030 'today'/'now' as a countdown target resolves to a zero-day countdown")


def test_def030_percent_math_parsing() -> None:
    # Digit form: the real bug was "whats" (no apostrophe) never being
    # stripped as a recognized prefix, so the otherwise-working digit
    # percent-of regex never got a clean expression to match against.
    digit_cases = [
        ("whats 15 percent of 200", 30.0),
        ("whats 50 percent of 80", 40.0),
        ("what is 15 percent of 200", 30.0),  # regression: must still work
    ]
    for question, expected in digit_cases:
        expr = atlas_readonly.normalize_math_expression(question)
        assert expr is not None, f"failed to parse: {question!r}"
        assert atlas_readonly.evaluate_math_expression(expr) == expected, (question, expr)

    # Spelled-out form, narrow scope per ATLAS_A3_RELIABILITY_PLAN.md Phase 3
    # (0-999, no thousands/fractions). Fresh number combinations, not just
    # the original repro string.
    word_cases = [
        ("whats twenty five percent of two hundred", 50.0),
        ("what is ten percent of fifty", 5.0),
        ("whats twenty percent of one hundred fifty", 30.0),
    ]
    for question, expected in word_cases:
        expr = atlas_readonly.normalize_math_expression(question)
        assert expr is not None, f"failed to parse: {question!r}"
        assert atlas_readonly.evaluate_math_expression(expr) == expected, (question, expr)
    print("PASS: DEF-030 percent-of math parses in both digit and spelled-out form")


def test_def035_discount_math_parsing() -> None:
    # Found via a final end-to-end verification probe, not real user text --
    # "N% off (of) $X" wants the amount AFTER the discount, a different
    # shape than "N percent of M" (which the DEF-030 fix already handles).
    cases = [
        ("whats 15% off of 84 bucks", 71.4),
        ("whats 20% off 50 dollars", 40.0),
        ("whats 10 percent off of 200", 180.0),
        ("what is 15% off of 84 bucks", 71.4),  # regression: apostrophe form
    ]
    for question, expected in cases:
        expr = atlas_readonly.normalize_math_expression(question)
        assert expr is not None, f"failed to parse: {question!r}"
        assert atlas_readonly.evaluate_math_expression(expr) == expected, (question, expr)

    # Regression: plain "percent of" must not be misparsed as a discount.
    expr = atlas_readonly.normalize_math_expression("whats 15 percent of 200")
    assert atlas_readonly.evaluate_math_expression(expr) == 30.0, expr

    # is_math_question() has its own separate word list -- a discount
    # question must actually classify as math end-to-end, not just parse
    # correctly if it somehow reached run_math().
    for question in ["whats 15% off of 84 bucks", "whats 20% off 50 dollars"]:
        assert atlas_readonly.is_math_question(question), question
        assert atlas_readonly.classify_question(question, "personal") == "math", question

    # Live routing depends on the native planner actually being OFFERED
    # math.calculate as a tool -- classify_question() alone is not the live
    # dispatch path when the native planner is enabled. Found by testing
    # live and discovering the discount question routed through general
    # LLM reasoning (got the right answer by luck) because none of
    # math.calculate's DISCOVERY_TERMS matched a bare "%" with no literal
    # "percent" word.
    import atlas_orchestrator_v2 as orch
    caps = orch.shortlist_capabilities_for_compound("whats 15% off of 84 bucks", "personal", None)
    assert "math.calculate" in [c.name for c in caps], [c.name for c in caps]
    print("PASS: DEF-035 'N% off of $X' discount questions parse to the post-discount amount")


def test_def036_countdown_like_date_fallback() -> None:
    # Found via the same verification probe: a vague description followed
    # by a concrete example ("a date thats already passed, like march 1")
    # stopped the clause-boundary capture at the comma, dropping the
    # actually-usable date entirely.
    now = dt.datetime(2026, 9, 16, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    result = atlas_readonly.run_countdown("how many days until a date thats already passed, like march 1")
    assert result["result_status"] == "ok", result
    assert result["result"]["target_date"] == "2027-03-01", result

    # Regression: a plain target with no "like" clause must still work.
    result = atlas_readonly.run_countdown("how many days until december 25")
    assert result["result_status"] == "ok", result
    print("PASS: DEF-036 a concrete date after 'like' is used when the primary capture fails to parse")


def test_def037_legacy_gate_compound_exemption() -> None:
    # Same fix as the orchestrator's deterministic_clarification_plan(),
    # applied to the legacy pre-native-planner ambiguity gate
    # (deterministic_comparison_clarification()), which real questions can
    # still reach when the orchestrator path falls back.
    independent_compound = "Whats the most popular flavor at baskin robins? What is the tunnel near Sausalito after the Golden Gate Bridge called?"
    assert atlas_readonly.deterministic_comparison_clarification(independent_compound) is None, independent_compound

    # A single ambiguous question must still clarify.
    assert atlas_readonly.deterministic_comparison_clarification("whats the best show") is not None

    # A linked reasoning question (pronoun referring back to the first
    # clause) must still clarify, not be treated as independent topics.
    linked = "What is the best show? How does it compare to the worst one?"
    assert atlas_readonly.deterministic_comparison_clarification(linked) is not None, linked
    print("PASS: DEF-037 legacy gate exempts independent compounds, not linked reasoning")


def test_words_to_number_narrow_scope() -> None:
    cases = {
        "zero": 0, "five": 5, "fifteen": 15, "twenty": 20, "twenty five": 25,
        "one hundred": 100, "two hundred": 200, "two hundred five": 205,
        "two hundred twenty five": 225, "ninety nine": 99,
    }
    for words, expected in cases.items():
        assert atlas_readonly._words_to_number(words) == expected, words
    # Out of narrow scope -- must fail closed (None), not guess.
    for words in ["a thousand", "one million", "twenty-twenty", "", "the"]:
        assert atlas_readonly._words_to_number(words) is None, words
    print("PASS: _words_to_number covers 0-999 and fails closed outside that")


def main() -> None:
    test_def028_weather_no_location_fallback()
    test_def028_bare_place_name_not_defaulted()
    test_def032_using_clause_boundary()
    test_def031_city_abbreviation_aliases()
    test_def030_today_as_countdown_target()
    test_def030_percent_math_parsing()
    test_def035_discount_math_parsing()
    test_def036_countdown_like_date_fallback()
    test_def037_legacy_gate_compound_exemption()
    test_words_to_number_narrow_scope()


if __name__ == "__main__":
    main()
