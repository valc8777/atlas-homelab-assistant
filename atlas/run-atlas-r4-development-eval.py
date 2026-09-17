#!/usr/bin/env python3
"""Repeatable open development gate for ATLAS R4's first shadow slice."""

from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atlas_entity_registry
import atlas_reference_resolution


ROOT = pathlib.Path(__file__).resolve().parents[2]
PACIFIC = ZoneInfo("America/Los_Angeles")
ANCHOR = datetime(2026, 9, 12, 11, 30, tzinfo=PACIFIC)


def message(text: str, scope: str) -> dict:
    return {"role": "user", "text": text, "context_scope": scope}


def run(registry_path: pathlib.Path) -> dict:
    registry = atlas_entity_registry.load_registry_file(registry_path)
    totals = {
        "unambiguous": [0, 0],
        "ambiguous": [0, 0],
        "temporal": [0, 0],
        "event_relative": [0, 0],
        "scope_isolation": [0, 0],
        "unchanged_controls": [0, 0],
    }

    def score(category: str, passed: bool) -> None:
        totals[category][1] += 1
        totals[category][0] += int(bool(passed))

    entities = [
        "Jellyfin", "Navidrome", "Kavita", "Vaultwarden", "Windows",
        "ai-stack", "docker-main", "Open WebUI",
    ]
    followups = ["Is it online?", "Is it healthy?", "What is its status?", "Is that one reachable?", "What about this one?"]
    for entity in entities:
        for followup in followups:
            result = atlas_reference_resolution.resolve(
                followup, "operations", [message(f"Check {entity}.", "operations")],
                registry, now_value=ANCHOR,
            )
            score(
                "unambiguous",
                result["status"] == "resolved"
                and result["references"]
                and result["references"][0]["display_name"].lower() in result["resolved_question"].lower(),
            )

    places = ["San Francisco", "Medford", "Seattle", "Tokyo", "London"]
    for place in places:
        history = [message(f"What is the weather in {place}?", "personal")]
        there = atlas_reference_resolution.resolve(
            "Will it rain there tomorrow?", "personal", history, registry, now_value=ANCHOR
        )
        omitted = atlas_reference_resolution.resolve(
            "What about tomorrow?", "personal", history, registry, now_value=ANCHOR
        )
        score("unambiguous", there["status"] == "resolved" and place in there["resolved_question"])
        score("unambiguous", omitted["status"] == "resolved" and place in omitted["resolved_question"])

    pairs = [
        ("Jellyfin", "Navidrome"), ("Kavita", "Vaultwarden"),
        ("Windows", "ai-stack"), ("docker-main", "Open WebUI"),
        ("Prowlarr", "Sonarr"),
    ]
    ambiguous_followups = ["Is it online?", "Is it healthy?", "Is that one reachable?", "What about this one?"]
    for left, right in pairs:
        for followup in ambiguous_followups:
            result = atlas_reference_resolution.resolve(
                followup, "operations",
                [message(f"Compare {left} and {right}.", "operations")],
                registry, now_value=ANCHOR,
            )
            score(
                "ambiguous",
                result["status"] == "ambiguous"
                and result["ambiguities"][0]["option_count"] >= 2,
            )

    temporal = {
        "today": "2026-09-12", "tomorrow": "2026-09-13",
        "the day after tomorrow": "2026-09-14", "yesterday": "2026-09-11",
        "in 10 days": "2026-09-22", "in 2 weeks": "2026-09-26",
        "next week": "2026-09-19", "next Monday": "2026-09-14",
        "next Friday": "2026-09-18", "next Saturday": "2026-09-19",
    }
    wrappers = ["What happens {x}?", "Show me {x}.", "Check again {x}.", "Use {x} for the date."]
    for phrase, expected in temporal.items():
        for wrapper in wrappers:
            result = atlas_reference_resolution.resolve(
                wrapper.format(x=phrase), "personal", [], registry, now_value=ANCHOR
            )
            score(
                "temporal",
                len(result["temporal"]) == 1
                and result["temporal"][0]["normalized_date"] == expected
                and result["temporal"][0]["timezone"] == "America/Los_Angeles",
            )

    event_records = [
        {
            "summary": "Biology class",
            "location": "SFSU",
            "dtstart": "2026-09-13T10:00:00-07:00",
            "dtend": "2026-09-13T11:15:00-07:00",
        },
        {
            "summary": "Chemistry lab",
            "location": "SFSU",
            "dtstart": "2026-09-14T14:00:00-07:00",
            "dtend": "2026-09-14T16:00:00-07:00",
        },
        {
            "summary": "Advising appointment",
            "location": "Zoom",
            "dtstart": "2026-09-15T09:00:00-07:00",
            "dtend": "2026-09-15T09:30:00-07:00",
        },
    ]
    event_cases = [
        ("What should I review after my class?", "after", "2026-09-13T11:15:00-07:00"),
        ("What should I prep before my lab?", "before", "2026-09-14T14:00:00-07:00"),
        ("Remind me what to bring before my appointment.", "before", "2026-09-15T09:00:00-07:00"),
    ]
    for prompt, relation, expected in event_cases:
        result = atlas_reference_resolution.resolve(
            prompt, "personal", [], registry, event_records=event_records, now_value=ANCHOR
        )
        score(
            "event_relative",
            result["status"] == "resolved"
            and result["temporal"]
            and result["temporal"][0]["relation"] == relation
            and result["temporal"][0]["normalized_datetime"] == expected
            and result["temporal"][0]["precision"] == "datetime",
        )
    ambiguous_events = [
        {"summary": "Biology class", "dtstart": "2026-09-13T10:00:00-07:00"},
        {"summary": "Chemistry class", "dtstart": "2026-09-14T10:00:00-07:00"},
    ]
    for prompt in ("What should I review after my class?", "What should I review before my class?"):
        result = atlas_reference_resolution.resolve(
            prompt, "personal", [], registry, event_records=ambiguous_events, now_value=ANCHOR
        )
        score("event_relative", result["status"] == "ambiguous" and result["ambiguities"][0]["kind"] == "event_reference")

    for followup in followups * 4:
        crossed = atlas_reference_resolution.resolve(
            followup, "personal", [message("Check Jellyfin.", "operations")],
            registry, now_value=ANCHOR,
        )
        score("scope_isolation", crossed["references"] == [])

    controls = [
        "Explain photosynthesis.", "Is Jellyfin online?", "What is 15% of 240?",
        "Write a short poem.", "How does DNS work?", "Tell me about backups.",
        "What happened on 13/4?", "List three privacy principles.",
    ]
    for control in controls:
        result = atlas_reference_resolution.resolve(
            control, "operations", [message("Check Navidrome.", "operations")],
            registry, now_value=ANCHOR,
        )
        score("unchanged_controls", result["references"] == [] and result["status"] == "unchanged")

    # Ambiguous numeric month/day ordering must always clarify.
    for numeric in ("3/4", "4/3", "1/2", "2/1", "11/12", "12/11"):
        result = atlas_reference_resolution.resolve(
            f"What happens on {numeric}?", "personal", [], registry, now_value=ANCHOR
        )
        score("ambiguous", result["status"] == "ambiguous" and result["ambiguities"][0]["kind"] == "numeric_date")

    return {
        name: {"passed": values[0], "total": values[1], "rate": values[0] / values[1] if values[1] else 0.0}
        for name, values in totals.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=pathlib.Path, default=ROOT / "atlas-entity-registry-2026-09-11.json")
    parser.add_argument("--report", type=pathlib.Path)
    args = parser.parse_args()
    result = run(args.registry)
    passed = sum(item["passed"] for item in result.values())
    total = sum(item["total"] for item in result.values())
    gate_passed = (
        result["unambiguous"]["rate"] >= 0.95
        and result["ambiguous"]["rate"] == 1.0
        and result["temporal"]["rate"] >= 0.95
        and result["event_relative"]["rate"] == 1.0
        and result["scope_isolation"]["rate"] == 1.0
        and result["unchanged_controls"]["rate"] == 1.0
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# ATLAS R4 reference-resolution development gate", "",
            "> Repeatable open development evidence for R4 resolver semantics.", "",
            f"- Overall: `{passed}/{total}`", f"- Gate: `{'pass' if gate_passed else 'fail'}`", "",
            "| Category | Passed | Total | Rate |", "|---|---:|---:|---:|",
        ]
        for name, item in result.items():
            lines.append(f"| `{name}` | {item['passed']} | {item['total']} | {item['rate']:.1%} |")
        lines.extend([
            "", "## Boundaries", "",
            "- Exact-scope user turns only; no implicit personal/operations/admin hierarchy.",
            "- Resolved questions, entity IDs/names, locations, and normalized dates are absent from audit metrics.",
            "- Event-relative cases use already-authorized event records passed into the resolver.",
            "- Answer-path consumption remains separately flagged and default-off.", "",
        ])
        args.report.write_text("\n".join(lines), encoding="utf-8")
    print(f"{'PASS' if gate_passed else 'FAIL'}: R4 development gate {passed}/{total}")
    if not gate_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
