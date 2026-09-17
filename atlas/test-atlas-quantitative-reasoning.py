#!/usr/bin/env python3
"""Focused DEF-025b checks for local quantitative reasoning."""

from __future__ import annotations

import importlib.util
import pathlib
import sys


MODULE_PATH = pathlib.Path(__file__).with_name("run-atlas-readonly.py")
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("atlas_runner", MODULE_PATH)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def assert_ok(prompt: str, expected_fragments: list[str]) -> None:
    assert RUNNER.classify_question(prompt) == "quantitative_reasoning", prompt
    result = RUNNER.run_quantitative_reasoning(prompt)
    assert result["route"] == "quantitative_reasoning", result
    assert result["tool"] == "local-quantitative-reasoning", result
    assert result["result_status"] == "ok", result
    answer = result["answer"].lower()
    for fragment in expected_fragments:
        assert fragment.lower() in answer, (fragment, answer)


def main() -> None:
    assert_ok(
        "A backup is 556 GB and the real upload speed averages 50 Mbps after overhead; roughly how many hours should the upload take? If the backup window is 8 hours, is that enough?",
        ["24.71 hours", "does not fit", "16.71 hours"],
    )
    assert_ok(
        "A backup is 18 GB and the real upload speed averages 100 Mbps after overhead; roughly how many hours should the upload take? If the backup window is 8 hours, is that enough?",
        ["0.4 hours", "fits", "7.6 hours"],
    )
    assert_ok(
        "A trip through Petaluma is 77 miles at about 45 mph, while a trip through Berkeley is 52 miles at about 30 mph; which trip should take less driving time? How many minutes shorter is it?",
        ["Petaluma", "102.67 minutes", "104 minutes", "1.33 minutes shorter"],
    )
    assert_ok(
        "A trip through Concord is 69 miles at about 45 mph, while a trip through Napa is 59 miles at about 30 mph; which trip should take less driving time? How many minutes shorter is it?",
        ["Concord", "92 minutes", "118 minutes", "26 minutes shorter"],
    )
    assert RUNNER.question_needs_live_verification(
        "A trip through Martinez is 43 miles at about 45 mph, while a trip through Concord is 42 miles at about 30 mph; which trip should take less driving time? How many minutes shorter is it?"
    ) is True
    print("PASS: DEF-025b quantitative reasoning routes to local arithmetic")


if __name__ == "__main__":
    main()
