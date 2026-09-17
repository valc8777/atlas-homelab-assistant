#!/usr/bin/env python3
"""Regression coverage for the DEF-026 paraphrase-robustness fixes.

Every case here is deliberately NOT the exact wording that originally
exposed the bug (those live in DEC-2026-09-15-10 and the paraphrase-audit
eval artifacts) -- these are a second, independent set of phrasings, so a
future change can't silently re-narrow a fix back down to one known string
without this file catching it.
"""

import importlib.util
import pathlib
import sys


MODULE_PATH = pathlib.Path(__file__).with_name("run-atlas-readonly.py")
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("atlas_readonly", MODULE_PATH)
atlas_readonly = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(atlas_readonly)


def main() -> None:
    # Case 1: "how many days ... until X" with a base-date clause in between.
    q = "How many days from today until New Year's Eve 2028?"
    assert atlas_readonly.classify_question(q, "personal") == "countdown", q
    result = atlas_readonly.run_countdown(q)
    assert result["result_status"] == "ok", result
    assert result["result"]["target_date"] == "2028-12-31", result

    # Case 1b: found via the 120-case DEF-026 revalidation, not the original
    # 16 or the first regression case above -- "how far away is X from
    # today?" put "from today" itself inside the captured target text (no
    # clause-boundary marker separates them), so parse_target_date() got
    # "independence day 2027 from today" and failed outright.
    q = "How far away is Independence Day 2027 from today?"
    result = atlas_readonly.run_countdown(q)
    assert result["result_status"] == "ok", result
    assert result["result"]["target_date"] == "2027-07-04", result

    # Case 2: event-offset-from-now with an explicit multi-day reminder.
    q = (
        "If today is October 1, 2026, and my conference is 40 days from now, what date is that? "
        "I'd like a reminder 5 days before the conference -- what date should that be?"
    )
    assert atlas_readonly.classify_question(q, "personal") == "countdown", q
    result = atlas_readonly.run_countdown(q)
    assert result["result_status"] == "ok", result
    assert result["result"]["event_date"] == "2026-11-10", result
    assert result["result"]["reminder_date"] == "2026-11-05", result

    # Case 3: rephrased backup/upload-window question, different numbers and
    # a hyphenated "N-hour window" instead of "window is N hours".
    q = "A transfer is 900 GB and averages 100 Mbps. Can it finish inside a 20-hour maintenance window?"
    assert atlas_readonly.classify_question(q, "personal") == "quantitative_reasoning", q
    result = atlas_readonly.run_quantitative_reasoning(q)
    assert result["result_status"] == "ok", result
    assert "20-hour" in result["answer"] or "20 hour" in result["answer"], result

    # Case 4: causal-tradeoff question with a criterion supplied, different
    # subject and wording from the original seed template.
    q = (
        "Suppose we compress database backups more aggressively; what's the benefit and the downside, "
        "and how would we know if it's worth it?"
    )
    assert atlas_readonly.classify_question(q, "personal") == "causal_tradeoff_reasoning", q
    result = atlas_readonly.run_causal_tradeoff_reasoning(q)
    assert result["result_status"] == "ok", result
    assert "benefit" in result["answer"].lower() and "downside" in result["answer"].lower(), result

    # Case 5: weather place extraction with trailing clauses and a city/state.
    place = atlas_readonly.infer_weather_place(
        "What's the weather like in Austin, Texas tomorrow, and should I bring a poncho?"
    )
    assert place == "Austin, Texas", place
    place2 = atlas_readonly.infer_weather_place("Is it going to snow in Denver today?")
    assert place2 == "Denver", place2

    # Case 6: source-freshness question phrased as "ran N minutes/hours ago"
    # instead of "cached X is N minutes old".
    q = "The last backup status check ran 90 minutes ago. Is the backup currently healthy?"
    assert atlas_readonly.classify_question(q, "personal") == "source_freshness_reasoning", q
    result = atlas_readonly.run_source_freshness_reasoning(q)
    assert result["result_status"] == "ok", result
    assert "stale" in result["answer"].lower(), result

    # Case 7: the evidence-sufficiency guard's canned text no longer presumes
    # every unsupported-absence question was about counting.
    assert "count" not in atlas_readonly.EVIDENCE_GUARD_TEXT.lower(), atlas_readonly.EVIDENCE_GUARD_TEXT

    # Case 8: found live during DEF-026 verification, not in the original 16
    # -- the countdown target-text extraction had the exact same greedy
    # capture-to-end-of-string bug as the weather-place extractor (case 5),
    # just not caught by the first paraphrase batch because none of those
    # put the countdown clause *before* a trailing clause in the same
    # message. A3 is single-intent, so this only fixes the date half; the
    # weather half is silently dropped -- known, documented gap, not solved
    # here (see DEC-2026-09-15-10 follow-up note).
    q = "How many days until Christmas 2026, and separately, what's the weather in Seattle right now?"
    result = atlas_readonly.run_countdown(q)
    assert result["result_status"] == "ok", result
    assert result["result"]["target_date"] == "2026-12-25", result

    print("PASS: DEF-026 paraphrase-robustness regressions")


if __name__ == "__main__":
    main()
