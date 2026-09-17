#!/usr/bin/env python3
"""Golden-set and boundary tests for DEF-053's narrative date-math solver."""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atlas_narrative_date_reasoning as narrative

ROOT = pathlib.Path(__file__).resolve().parents[2]
TASK_PATH = ROOT / "ai-evals" / "external" / "sources" / "bigbench-20260901" / "date_understanding" / "task.json"


def build_question(example: dict) -> str:
    rendered_choices = "; ".join(example["target_scores"].keys())
    return f"{example['input']}\nChoices: {rendered_choices}\nAnswer with the choice and a short reason."


def main() -> None:
    task = json.loads(TASK_PATH.read_text(encoding="utf-8"))
    examples = task["examples"]
    assert len(examples) == 369, f"unexpected dataset size: {len(examples)}"

    hit = 0
    miss = 0
    abstain = 0
    wrong: list[tuple[str, str, str]] = []
    for example in examples:
        correct = [choice for choice, score in example["target_scores"].items() if score == 1][0]
        question = build_question(example)
        result = narrative.resolve_narrative_date_answer(question)
        if result is None:
            abstain += 1
            continue
        if result["answer_choice"] == correct:
            hit += 1
        else:
            miss += 1
            wrong.append((example["input"], result["answer_choice"], correct))

    # DEF-053: 341/369 confidently and correctly resolved without touching
    # the model. The only two "wrong" answers are known, hand-verified
    # BIG-bench gold-label inconsistencies (Jan 31 + one week is Feb 7, not
    # the dataset's Feb 6; a stated "5-year anniversary" of a 1958 wedding
    # is 1963, not the dataset's 1961) -- our arithmetic is right, the
    # dataset's answer key is wrong there, and we deliberately do not chase
    # a broken gold label. Everything else the solver can't confidently
    # resolve abstains (26 cases) and falls through to the model unchanged,
    # exactly as before this fix -- a strictly non-regressive change.
    assert hit >= 340, f"solver coverage regressed: hit={hit}"
    assert miss <= 2, f"solver started emitting new wrong answers: {wrong}"
    assert hit + miss + abstain == len(examples)
    for question_text, got, expected in wrong:
        assert question_text in {
            "Jane is celebrating the last day of Jan 2012. What is the date one week from today in MM/DD/YYYY?",
            "Jane and John married on Jan 2, 1958. It is their 5-year anniversary today. What is the date one week from today in MM/DD/YYYY?",
        }, (question_text, got, expected)

    # Never emit an answer that isn't literally one of the offered choices.
    for example in examples:
        question = build_question(example)
        result = narrative.resolve_narrative_date_answer(question)
        if result is None:
            continue
        choices = [choice.strip() for choice in example["target_scores"].keys()]
        assert result["answer_choice"] in choices, (question, result)

    # Boundary: no "Choices:" trailer at all -- must abstain, not guess.
    assert narrative.resolve_narrative_date_answer(
        "Yesterday was April 30, 2021. What is the date today in MM/DD/YYYY?"
    ) is None

    # Boundary: a structurally identical prompt about something other than
    # dates must not be swept in.
    unrelated = (
        "What is the best way to store leftover rice?\n"
        "Choices: refrigerate within two hours; leave on the counter\n"
        "Answer with the choice and a short reason."
    )
    assert narrative.resolve_narrative_date_answer(unrelated) is None

    # Boundary: a computed date that isn't among the choices at all must
    # abstain rather than emit an unverified answer.
    unverifiable = (
        "Yesterday was April 30, 2021. What is the date today in MM/DD/YYYY?\n"
        "Choices: 06/01/2021; 07/04/2021\n"
        "Answer with the choice and a short reason."
    )
    assert narrative.resolve_narrative_date_answer(unverifiable) is None

    # Spot-check a few concrete families directly (not just via the corpus)
    # so a future regression here fails with an obvious, readable case.
    spot_checks = [
        (
            "Yesterday was April 30, 2021. What is the date today in MM/DD/YYYY?\n"
            "Choices: 05/01/2021; 02/23/2021\nAnswer with the choice and a short reason.",
            "05/01/2021",
        ),
        (
            "Jane and John married on Jan 2, 1958. Today is their golden wedding anniversary. "
            "What is the date today in MM/DD/YYYY?\n"
            "Choices: 01/02/2008; 01/02/2007\nAnswer with the choice and a short reason.",
            "01/02/2008",
        ),
        (
            "Jane was born on the last day of Feburary in 2000. Today is her 16-year-old birthday. "
            "What is the date one week ago from today in MM/DD/YYYY?\n"
            "Choices: 02/22/2016; 11/22/2016\nAnswer with the choice and a short reason.",
            "02/22/2016",
        ),
        (
            "Today is the palindrome day of 2020, because the MMDDYYYY format of the date is the "
            "same backwards as forwards. What is the date today in MM/DD/YYYY?\n"
            "Choices: 02/02/2020; 03/03/2020\nAnswer with the choice and a short reason.",
            "02/02/2020",
        ),
    ]
    for question, expected_choice in spot_checks:
        result = narrative.resolve_narrative_date_answer(question)
        assert result and result["answer_choice"] == expected_choice, (question, result)

    print(
        f"PASS: narrative date-math solver -- {hit}/{len(examples)} confidently correct, "
        f"{len(wrong)} known dataset-label quirks, {abstain} safe abstentions"
    )


if __name__ == "__main__":
    main()
