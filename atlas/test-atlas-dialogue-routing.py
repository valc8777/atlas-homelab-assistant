#!/usr/bin/env python3
"""Seeded generalized checks for ambiguity and short-follow-up routing."""

from __future__ import annotations

import importlib.util
import pathlib
import random
import sys


MODULE_PATH = pathlib.Path(__file__).with_name("run-atlas-readonly.py")
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("atlas_runner", MODULE_PATH)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def main() -> None:
    rng = random.Random(20260902)
    adjectives = ["best", "most reliable", "most efficient"]
    subjects = ["telescope", "ferry", "archive", "battery", "museum", "compiler", "bridge", "orchard"]
    fragments = ["by operating cost", "for a student", "within Europe", "using intrinsic output", "among local options"]

    for _ in range(120):
        adjective = rng.choice(adjectives)
        subject = rng.choice(subjects)
        prompt = f"What is the {adjective} {subject}?"
        answer = RUNNER.deterministic_comparison_clarification(prompt)
        assert answer and "criterion" in answer and "measurement" in answer, prompt

        scoped = f"What is the {adjective} {subject} by annual usage?"
        assert RUNNER.deterministic_comparison_clarification(scoped) is None, scoped

        fragment = rng.choice(fragments)
        history = [
            {"role": "user", "text": prompt},
            {"role": "assistant", "text": "What criterion should I use?"},
        ]
        rewrite = RUNNER.rewrite_followup_from_history(fragment, history)
        assert rewrite and subject in rewrite and fragment in rewrite, (prompt, fragment, rewrite)

    assert RUNNER.is_weather_question("What is the fastest train?") is False
    assert RUNNER.is_weather_question("Will it rain near the train station?") is True
    # Found live 2026-09-05: "rain" is correctly word-bounded (not a
    # substring-match bug), but "purple rain" (Prince reference) isn't about
    # precipitation -- a real question ("give me a purple rain like effect
    # on my lamp") was misrouted to weather, which then failed trying to
    # look up weather for "effect on my lamp" as if it were a place. Fixed
    # with a narrow exclusion, not a broad heuristic, so legitimate weather
    # phrasing that happens to include "light" (e.g. "light rain") still works.
    assert RUNNER.is_weather_question("give me a purple rain like effect on my lamp") is False
    assert RUNNER.is_weather_question("is there a chance of light rain today") is True
    assert RUNNER.is_weather_question("Should I expect fog near Half Moon Bay tomorrow?") is True
    assert RUNNER.is_weather_question("Could it be overcast in Alameda this evening?") is True
    assert RUNNER.is_weather_question("Explain the Cold War in plain language") is False
    assert RUNNER.is_weather_question("Think outside the box and explain the metaphor") is False
    conventional_facts = [
        "Which organ is largest inside a mammal?",
        "Which bone is longest in a person's body?",
        "Which planet has the shortest orbital year?",
        "Which ocean is deepest on Earth?",
    ]
    for prompt in conventional_facts:
        assert RUNNER.deterministic_comparison_clarification(prompt) is None, prompt
        assert RUNNER.question_needs_live_verification(prompt) is False, prompt
    def025e_prompts = [
        "For the local weather adapter, imagine we optimize source freshness aggressively and accept a little extra latency; what is the most likely benefit and the most likely downside? If the owner complains that it feels slower, what measurement would tell us whether the tradeoff is actually worth keeping?",
        "An answer about private notifications can cite a local audit event and a README, but they disagree on the current state and the user cares most about privacy exposure; which source should get more weight and why? What should the assistant say if neither source proves the current answer?",
    ]
    for prompt in def025e_prompts:
        assert RUNNER.may_need_comparison_clarification(prompt) is False, prompt
        assert RUNNER.deterministic_comparison_clarification(prompt) is None, prompt
    assert RUNNER.question_needs_live_verification("Which company currently has the largest market value?") is True

    # DEF-050: found live via an overnight unattended stress run -- the exact
    # real Valve-ownership repro DEF-038 was supposed to have fixed still
    # produced a confidently fabricated answer ("Valve is owned by
    # Microsoft") whenever dispatch reached run_general_reference() through
    # the legacy path, since this function's own keyword list was never
    # updated when DEF-038 added ownership/leadership/price coverage to the
    # orchestrator's separate decision gate. Now reuses that same pattern.
    ownership_questions = [
        "Is valve owned by a different company? Who are the majority shareholders?",
        "Who owns Twitter now?",
        "Who is the CEO of Tesla?",
        "What is the current price of Bitcoin?",
    ]
    for prompt in ownership_questions:
        assert RUNNER.question_needs_live_verification(prompt) is True, prompt
    stable_facts = [
        "What year did the civil war start?",
        "What is the largest state?",
        "Who wrote Romeo and Juliet?",
    ]
    for prompt in stable_facts:
        assert RUNNER.question_needs_live_verification(prompt) is False, prompt

    # DEF-052: population rankings are the same "changes over time, don't
    # trust static knowledge" class DEF-038/DEF-050 already cover for
    # ownership/leadership/price. Found via the overnight stress run's
    # actual-answer grading: a real population-ranking question answered
    # "China" from stale knowledge (the real answer has been India since a
    # well-established 2023 shift). A historical population question must
    # not be swept in just for containing the word.
    population_ranking_questions = [
        "Which country has the largest population in the world?",
        "What is the most populous country in the world?",
    ]
    for prompt in population_ranking_questions:
        assert RUNNER.question_needs_live_verification(prompt) is True, prompt
    historical_population_questions = [
        "What was the population of Rome in 500 BC?",
        "What is the population of Japan?",
    ]
    for prompt in historical_population_questions:
        assert RUNNER.question_needs_live_verification(prompt) is False, prompt

    # DEF-042: found live 2026-09-16 -- a short (<=6 word), grammatically
    # complete, standalone question immediately following an unrelated
    # exchange used to trigger the model-based follow-up rewrite regardless
    # of topic, corrupting downstream retrieval with irrelevant prior-turn
    # context. The gate now requires the previous turn to actually need
    # comparison clarification, matching rewrite_followup_from_history's own
    # documented scope ("a short criterion fragment").
    unrelated_history = [
        {"role": "user", "text": "What is the weather in San Francisco"},
        {"role": "assistant", "text": "San Francisco, United States: Overcast, 16.6 C, feels like 15.7 C."},
    ]
    assert RUNNER.should_rewrite_followup("How many people live in Europe?", unrelated_history) is False
    assert RUNNER.should_rewrite_followup("What is the best school in San Francisco", unrelated_history) is False
    assert RUNNER.rewrite_followup_from_history("How many people live in Europe?", unrelated_history) is None

    comparison_history = [
        {"role": "user", "text": "What is the best telescope?"},
        {"role": "assistant", "text": "What criterion, measurement, or scope should I use for “best”?"},
    ]
    assert RUNNER.should_rewrite_followup("by operating cost", comparison_history) is True
    rewrite = RUNNER.rewrite_followup_from_history("by operating cost", comparison_history)
    assert rewrite and "telescope" in rewrite and "operating cost" in rewrite, rewrite

    # DEF-043: found live via A5's classify_question() false-negative scan --
    # is_conversion_question()'s "feet"/"foot" tokens were bare substring
    # checks (unlike every other token in that list), matching inside
    # "football". Two real sports-trivia questions ("...ever played? What
    # about football?" and "...football's longest match?") were wrongly
    # classified as unit conversion. Word-bounded now, matching this file's
    # own established fix pattern for the same class of bug.
    assert RUNNER.is_conversion_question(
        "What is the longest game of foosball ever played? What about football?"
    ) is False
    assert RUNNER.is_conversion_question(
        "What is the longest game of soccer ever played? How much difference was "
        "it between that and football's longest match?"
    ) is False
    assert RUNNER.is_conversion_question("What is the football schedule this week?") is False
    assert RUNNER.is_conversion_question("How tall is the Eiffel Tower in feet?") is True
    assert RUNNER.is_conversion_question("Convert 6 feet to meters") is True
    assert RUNNER.is_conversion_question("How many feet are in a mile?") is True
    assert RUNNER.is_conversion_question("What size shoe fits my foot?") is True

    # DEF-045: found via A5's classifier audit -- ambiguous_role_clarification()
    # had zero real firings across 360 real messages, but the underlying
    # ambiguity (a role question naming no country/state/org/city) is common
    # real phrasing; the classifier's exact-phrase matching was the actual
    # bug, not rarity of the ambiguity. Widened to real phrasing variance
    # while keeping scope-qualified questions correctly unmatched.
    role_positives = [
        "Who is the president?",
        "Who is the current president?",
        "Who is president?",
        "Who is our president?",
        "Can you tell me who is the president?",
        "Do you know who is the president?",
        "Who is the president right now?",
        "Who is the mayor?",
        "Who is the CEO?",
        "Who is the governor?",
        "Who is the prime minister?",
    ]
    for prompt in role_positives:
        assert RUNNER.ambiguous_role_clarification(prompt) is not None, prompt
    role_negatives = [
        "Who is the president of the United States?",
        "Who is the mayor of San Francisco?",
        "Who is the CEO of Tesla?",
        "Who won the presidential election?",
        "Who was the first president?",
        "Who is the president and what did he say today?",
    ]
    for prompt in role_negatives:
        assert RUNNER.ambiguous_role_clarification(prompt) is None, prompt

    # Chat-quality review cluster 1: a message disputing a prior claim must
    # reach general_reference even when it contains an incidental domain
    # keyword like "book" -- found live 2026-09-17: "That is not true, in
    # the book the character was also named Louis de Pointe du Lac" was
    # hijacked by the bare word "book" into a structured-media lookup
    # ("No match. Source: Open Library.") instead of being reconsidered.
    dispute_positives = [
        "That is not true, in the book the character was also named Louis de Pointe du Lac",
        "That's wrong.",
        "No, that's not true.",
        "I don't think that's true.",
        "That is incorrect.",
    ]
    for prompt in dispute_positives:
        assert RUNNER.is_disputing_prior_claim(prompt), prompt
        assert RUNNER.classify_question(prompt) == "general_reference", prompt
    dispute_negatives = [
        "Is it true that the movie changed the ending?",
        "What book should I read next?",
        "Tell me about that book.",
    ]
    for prompt in dispute_negatives:
        assert not RUNNER.is_disputing_prior_claim(prompt), prompt

    print("PASS: 120 randomized ambiguity pairs, scoped controls, follow-ups, and weather boundaries")


if __name__ == "__main__":
    main()
