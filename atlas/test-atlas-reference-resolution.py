#!/usr/bin/env python3
"""Adversarial tests for ATLAS R4 ephemeral reference resolution."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atlas_entity_registry
import atlas_app_store
import atlas_reference_resolution as resolver
import atlas_shortcuts_server as server


ROOT = pathlib.Path(__file__).resolve().parents[2]
PACIFIC = ZoneInfo("America/Los_Angeles")


def user(text: str, scope: str = "operations") -> dict:
    return {"role": "user", "text": text, "context_scope": scope}


def main() -> None:
    registry = atlas_entity_registry.load_registry_file(ROOT / "atlas-entity-registry-2026-09-11.json")
    anchor = datetime(2026, 9, 12, 23, 30, tzinfo=PACIFIC)

    single = resolver.resolve(
        "Is it online?", "operations", [user("Check whether Jellyfin is configured.")],
        registry, now_value=anchor,
    )
    assert single["status"] == "resolved"
    assert single["resolved_question"] == "Is Jellyfin online?"
    assert single["references"][0]["canonical_id"] == "homelab.service.jellyfin"

    possessive = resolver.resolve(
        "What is its status?", "operations", [user("Check Windows.")],
        registry, now_value=anchor,
    )
    assert possessive["resolved_question"] == "What is Windows' status?"

    # The latest entity-bearing user turn wins; an older entity does not
    # create false ambiguity with a newer exact antecedent.
    newest = resolver.resolve(
        "Is it healthy?", "operations",
        [user("Check Jellyfin."), {"role": "assistant", "text": "Okay."}, user("Now check Navidrome.")],
        registry, now_value=anchor,
    )
    assert newest["resolved_question"] == "Is Navidrome healthy?"

    ambiguous = resolver.resolve(
        "Is it online?", "operations", [user("Compare Jellyfin and Navidrome.")],
        registry, now_value=anchor,
    )
    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["ambiguities"][0]["option_count"] == 2
    assert set(ambiguous["ambiguities"][0]["options"]) == {"Jellyfin", "Navidrome"}

    collision = resolver.resolve(
        "Is it online?", "operations", [user("Tell me about Proxmox.")],
        registry, now_value=anchor,
    )
    assert collision["status"] == "ambiguous"
    assert collision["ambiguities"][0]["option_count"] == 2

    # No implicit scope hierarchy and no cross-conversation input exist in
    # the resolver. Wrong-scope turns are ignored before entity extraction.
    crossed = resolver.resolve(
        "Is it on?", "personal", [user("Check the bedside lamp.", "operations")],
        registry, now_value=anchor,
    )
    assert crossed["status"] == "unchanged"
    assert crossed["references"] == []

    personal = resolver.resolve(
        "Is it on?", "personal", [user("Check the bedside lamp.", "personal")],
        registry, now_value=anchor,
    )
    assert personal["resolved_question"] == "Is Bedside Table Lamp on?"

    explicit = resolver.resolve(
        "Is Jellyfin online or is it degraded?", "operations", [user("Check Navidrome.")],
        registry, now_value=anchor,
    )
    assert explicit["status"] == "unchanged"
    assert explicit["resolved_question"] == "Is Jellyfin online or is it degraded?"

    long_question = resolver.resolve(
        "Could you please investigate whether it might currently be reachable from outside the private network today?",
        "operations", [user("Check Jellyfin.")], registry, now_value=anchor,
    )
    assert long_question["references"] == []
    assert long_question["temporal"][0]["normalized_date"] == "2026-09-12"

    there = resolver.resolve(
        "Will it rain there tomorrow?", "personal",
        [user("What is the weather in San Francisco?", "personal")], registry, now_value=anchor,
    )
    assert "San Francisco" in there["resolved_question"]
    assert there["temporal"][0]["normalized_date"] == "2026-09-13"

    omitted = resolver.resolve(
        "What about tomorrow?", "personal",
        [user("What is the weather in Medford?", "personal")], registry, now_value=anchor,
    )
    assert omitted["resolved_question"] == "What is the weather in Medford tomorrow?"

    event = resolver.resolve(
        "What should I review after my class?", "personal", [],
        registry,
        event_records=[{
            "summary": "Biology class",
            "location": "SFSU",
            "dtstart": "2026-09-13T10:00:00-07:00",
            "dtend": "2026-09-13T11:15:00-07:00",
        }],
        now_value=anchor,
    )
    assert event["status"] == "resolved"
    assert event["temporal"][0]["relation"] == "after"
    assert event["temporal"][0]["normalized_datetime"] == "2026-09-13T11:15:00-07:00"
    assert event["temporal"][0]["precision"] == "datetime"

    event_ambiguous = resolver.resolve(
        "What should I review after my class?", "personal", [],
        registry,
        event_records=[
            {"summary": "Biology class", "dtstart": "2026-09-13T10:00:00-07:00"},
            {"summary": "Chemistry class", "dtstart": "2026-09-14T10:00:00-07:00"},
        ],
        now_value=anchor,
    )
    assert event_ambiguous["status"] == "ambiguous"
    assert event_ambiguous["ambiguities"] == [{"kind": "event_reference", "option_count": 2}]

    temporal_cases = {
        "today": "2026-09-12",
        "tomorrow": "2026-09-13",
        "the day after tomorrow": "2026-09-14",
        "yesterday": "2026-09-11",
        "in 10 days": "2026-09-22",
        "in 2 weeks": "2026-09-26",
        "next week": "2026-09-19",
        "next month": "2026-10-12",
        "next year": "2027-09-12",
        "next Monday": "2026-09-14",
    }
    for phrase, expected in temporal_cases.items():
        result = resolver.resolve(f"What happens {phrase}?", "personal", [], registry, now_value=anchor)
        assert result["temporal"][0]["normalized_date"] == expected, (phrase, result)
        assert result["temporal"][0]["timezone"] == "America/Los_Angeles"
        assert result["temporal"][0]["precision"] == "day"

    month_end = resolver.resolve(
        "What happens next month?", "personal", [], registry,
        now_value=datetime(2026, 1, 31, 12, 0, tzinfo=PACIFIC),
    )
    assert month_end["temporal"][0]["normalized_date"] == "2026-02-28"

    leap = resolver.resolve(
        "What happens next year?", "personal", [], registry,
        now_value=datetime(2028, 2, 29, 12, 0, tzinfo=PACIFIC),
    )
    assert leap["temporal"][0]["normalized_date"] == "2029-02-28"

    numeric = resolver.resolve("What happens on 3/4?", "personal", [], registry, now_value=anchor)
    assert numeric["status"] == "ambiguous"
    assert numeric["ambiguities"] == [{"kind": "numeric_date", "option_count": 2}]
    unambiguous_numeric = resolver.resolve("What happened on 13/4?", "personal", [], registry, now_value=anchor)
    assert unambiguous_numeric["status"] == "unchanged"

    # DEF-046: found live via Track A3 Gate 1 -- a self-contained structured-
    # choice reasoning prompt (the BIG-bench date_understanding template) is
    # stuffed with multiple embedded M/D-style dates as reasoning context,
    # none of which are a genuine "which format did you mean" request. Every
    # such case (100%, 0/20) was wrongly blocked before this fix.
    structured_prompt = (
        "Today is the second day of the third month of 1966. What is the date one year ago "
        "from today in MM/DD/YYYY?\nChoices: 03/02/1965; 03/02/2012; 11/02/1964; 03/01/1965; "
        "01/07/1965; 03/12/1965\nAnswer with the choice and a short reason."
    )
    structured_result = resolver.resolve(structured_prompt, "personal", [], registry, now_value=anchor)
    assert structured_result["status"] == "unchanged", structured_result
    assert structured_result["ambiguities"] == []

    # DEF-051: found via an overnight stress run -- collapsing the same
    # template's newlines into spaces (a test-harness bug, not a live
    # regression) alone defeated DEF-046's exemption, since it required a
    # literal newline before "Choices:". A stray formatting difference
    # should not be able to silently disable this. Widened to also accept
    # "Choices:" right after normal sentence-ending punctuation.
    collapsed_prompt = (
        "Today is the second day of the third month of 1966. What is the date one year ago "
        "from today in MM/DD/YYYY? Choices: 03/02/1965; 03/02/2012; 11/02/1964; 03/01/1965; "
        "01/07/1965; 03/12/1965 Answer with the choice and a short reason."
    )
    collapsed_result = resolver.resolve(collapsed_prompt, "personal", [], registry, now_value=anchor)
    assert collapsed_result["status"] == "unchanged", collapsed_result
    assert collapsed_result["ambiguities"] == []
    # Casual mid-sentence use of the word "choices" (no answer-instruction
    # phrase) must still be treated as an ordinary question, not exempted.
    assert resolver._is_self_contained_structured_prompt(
        "I have a few choices: 3/4 or 4/3, which do you think?"
    ) is False

    try:
        resolver.resolve("Is it online?", "invalid", [], registry, now_value=anchor)
    except ValueError as exc:
        assert str(exc) == "invalid_context_scope"
    else:
        raise AssertionError("invalid scope accepted")

    metrics = resolver.privacy_metrics(there)
    serialized = json.dumps(metrics)
    assert "San Francisco" not in serialized
    assert "2026-09-13" not in serialized
    assert "2026-09-12T23:30" not in serialized
    assert "11:15" not in json.dumps(resolver.privacy_metrics(event))
    assert "Will it rain" not in serialized
    assert metrics["reference_count"] == 1
    assert metrics["temporal_count"] == 1

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = pathlib.Path(temp_dir) / "atlas.db"
        atlas_app_store.init_db(db_path)
        job = {
            "question": "Is it online tomorrow?",
            "conversation_id": "r4-shadow-test",
            "context_scope": "operations",
            "conversation": [user("Check Jellyfin.")],
        }
        with mock.patch.object(server, "REFERENCE_RESOLUTION_SHADOW_ENABLED", True), mock.patch.object(
            server, "APP_DB", db_path
        ), mock.patch.object(server, "_load_entity_registry", return_value=registry):
            server._record_reference_resolution_shadow(job)
        event = next(
            item for item in atlas_app_store.list_audit_events(db_path)
            if item["event_type"] == "reference_resolution.shadow"
        )
        assert event["details"]["status"] == "resolved"
        assert event["details"]["reference_count"] == 1
        assert event["details"]["temporal_count"] == 1
        persisted = json.dumps(event["details"])
        for forbidden in ("Jellyfin", "tomorrow", "2026-", "homelab.service", "Is it online"):
            assert forbidden not in persisted

        with mock.patch.object(server, "REFERENCE_RESOLUTION_SHADOW_ENABLED", False), mock.patch.object(
            server.atlas_app_store, "add_audit_event"
        ) as audit_mock:
            server._record_reference_resolution_shadow(job)
        audit_mock.assert_not_called()

        with mock.patch.object(server, "REFERENCE_RESOLUTION_CONSUMER_ENABLED", False):
            assert server._resolve_reference_for_answer("Is it online?", "operations", [user("Check Jellyfin.")]) is None

        with mock.patch.object(server, "REFERENCE_RESOLUTION_CONSUMER_ENABLED", True), mock.patch.object(
            server, "_load_entity_registry", return_value=registry
        ), mock.patch.object(server, "_reference_resolution_events", return_value=[]):
            consumed = server._resolve_reference_for_answer("Is it online?", "operations", [user("Check Jellyfin.")])
        assert consumed["status"] == "resolved"
        assert consumed["resolved_question"] == "Is Jellyfin online?"

    print("PASS: R4 ephemeral exact-scope references, event-relative timing, ambiguity, Pacific temporal normalization, and privacy metrics")


if __name__ == "__main__":
    main()
