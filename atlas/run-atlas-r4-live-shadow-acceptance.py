#!/usr/bin/env python3
"""Bounded, privacy-minimized live acceptance for the ATLAS R4 shadow.

Run inside atlas-shortcuts. Synthetic client tags keep these requests out of
organic A3/A4 observation. Output contains only structural resolver metrics.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
import urllib.request

import atlas_app_store


BASE_URL = os.environ.get("ATLAS_R4_ACCEPTANCE_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
TOKEN = os.environ["ATLAS_SHORTCUTS_TOKEN"]
APP_DB = pathlib.Path(os.environ.get("ATLAS_APP_DB", "/app/atlas-app/atlas-app.db"))
CLIENT = "r4-live-shadow-synthetic"


def post_message(text: str, conversation_id: str | None = None) -> dict:
    payload = {
        "text": text,
        "context_scope": "operations",
        "client": CLIENT,
        "input_mode": "typed",
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    request = urllib.request.Request(
        f"{BASE_URL}/api/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)


def wait_for_event(conversation_id: str, expected_status: str, timeout: int = 90) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event in atlas_app_store.list_audit_events(APP_DB, limit=250):
            details = event.get("details") or {}
            if (
                event.get("event_type") == "reference_resolution.shadow"
                and details.get("conversation_id") == conversation_id
                and details.get("status") == expected_status
            ):
                return details
        time.sleep(0.25)
    raise RuntimeError(f"r4_shadow_event_timeout:{expected_status}")


def public_metrics(details: dict) -> dict:
    allowed = {
        "status", "reference_count", "reference_kinds", "reference_methods",
        "temporal_count", "temporal_precisions", "ambiguity_count",
        "ambiguity_kinds", "resolver_version", "context_scope",
    }
    return {key: details[key] for key in sorted(allowed) if key in details}


def assert_privacy(details: dict) -> None:
    serialized = json.dumps(details, sort_keys=True)
    for forbidden in (
        "Jellyfin", "Navidrome", "tomorrow", "Is it online",
        "Compare ", "Check ", "homelab.service", "2026-",
    ):
        assert forbidden not in serialized
    forbidden_keys = {
        "question", "resolved_question", "text", "canonical_id",
        "display_name", "normalized_date", "options",
    }
    assert forbidden_keys.isdisjoint(details)


def main() -> None:
    resolved_seed = post_message("Check Jellyfin.")
    resolved_conversation = resolved_seed["conversation_id"]
    post_message("Is it online tomorrow?", resolved_conversation)
    resolved = wait_for_event(resolved_conversation, "resolved")
    assert resolved.get("reference_count") == 1
    assert resolved.get("reference_kinds") == ["canonical_entity"]
    assert resolved.get("temporal_count") == 1
    assert resolved.get("temporal_precisions") == ["day"]
    assert resolved.get("ambiguity_count") == 0
    assert_privacy(resolved)

    ambiguous_seed = post_message("Compare Jellyfin and Navidrome.")
    ambiguous_conversation = ambiguous_seed["conversation_id"]
    post_message("Is it online?", ambiguous_conversation)
    ambiguous = wait_for_event(ambiguous_conversation, "ambiguous")
    assert ambiguous.get("reference_count") == 0
    assert ambiguous.get("temporal_count") == 0
    assert ambiguous.get("ambiguity_count") == 1
    assert ambiguous.get("ambiguity_kinds") == ["entity_reference"]
    assert_privacy(ambiguous)

    print(json.dumps({
        "ok": True,
        "synthetic_client": CLIENT,
        "cases": [
            {"case": "unique_entity_plus_relative_day", **public_metrics(resolved)},
            {"case": "multiple_entity_ambiguity", **public_metrics(ambiguous)},
        ],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
