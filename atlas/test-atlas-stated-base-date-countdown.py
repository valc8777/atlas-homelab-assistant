#!/usr/bin/env python3
"""Focused DEF-025a checks for stated-base-date calendar math."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from unittest import mock
import datetime as dt


MODULE_PATH = pathlib.Path(__file__).with_name("run-atlas-readonly.py")
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("atlas_runner", MODULE_PATH)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class FixedDateTime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 15, 10, 0, tzinfo=tz)


def main() -> None:
    stated = (
        "Suppose today is November 11, 2026 and my doctor appointment is 31 days later; "
        "what exact date should I put on the calendar? If I want a reminder 3 days before that event, what date should the reminder use?"
    )
    assert RUNNER.classify_question(stated) == "countdown"
    result = RUNNER.run_countdown(stated)
    assert result["result_status"] == "ok", result
    assert result["result"]["event_date"] == "2026-12-12", result
    assert result["result"]["reminder_date"] == "2026-12-09", result
    assert "December 12, 2026" in result["answer"], result
    assert "December 9, 2026" in result["answer"], result

    another = (
        "Suppose today is September 19, 2026 and my backup drill is 37 days later; "
        "what exact date should I put on the calendar? If I want a reminder 3 days before that event, what date should the reminder use?"
    )
    assert RUNNER.classify_question(another) == "countdown"
    result = RUNNER.run_countdown(another)
    assert result["result"]["event_date"] == "2026-10-26", result
    assert result["result"]["reminder_date"] == "2026-10-23", result

    with mock.patch.object(RUNNER.dt, "datetime", FixedDateTime):
        assert RUNNER.classify_question("How far from Halloween?") == "countdown"
        halloween = RUNNER.run_countdown("How far from Halloween?")
    assert halloween["result_status"] == "ok", halloween
    assert halloween["result"]["target_date"] == "2026-10-31", halloween
    assert halloween["result"]["days"] == 46, halloween
    assert halloween["answer"] == "46 days.", halloween

    print("PASS: DEF-025a stated-base-date and how-far countdown routing")


if __name__ == "__main__":
    main()
