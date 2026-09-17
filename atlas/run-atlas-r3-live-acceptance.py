#!/usr/bin/env python3
"""Bounded live acceptance for the enabled ATLAS R3 answer canary.

Run inside atlas-shortcuts. Output contains only case names, routes, statuses,
and citation counts; it never prints retrieved owner content or credentials.
Synthetic client tags keep these requests distinguishable from organic A3/A4
traffic.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
import urllib.error
import urllib.request

import atlas_app_store


BASE_URL = os.environ.get("ATLAS_R3_ACCEPTANCE_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
TOKEN = os.environ["ATLAS_SHORTCUTS_TOKEN"]
APP_DB = pathlib.Path(os.environ.get("ATLAS_APP_DB", "/app/atlas-app/atlas-app.db"))
CLIENT = "r3-live-acceptance-synthetic"


def wait_ready(timeout: int = 120) -> None:
    """Wait for the HTTP listener, not merely Docker's running state."""
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=3) as response:
                payload = json.load(response)
            if payload.get("ok") is True:
                return
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"atlas_listener_not_ready:{type(last_error).__name__}")


def post(path: str, payload: dict, timeout: int = 90) -> dict:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def main() -> None:
    wait_ready()
    preferences = atlas_app_store.list_preferences(APP_DB)
    if not preferences:
        raise RuntimeError("acceptance_requires_one_existing_explicit_preference")
    preference_key = preferences[0]["key"]
    recall_question = f"What saved preference is on file for {preference_key}?"
    results = []

    recalled = post("/ask", {
        "question": recall_question, "context_scope": "personal",
        "client": CLIENT, "details": True,
    })
    recall_details = recalled.get("details", {}).get("result", {})
    assert recalled.get("route") == "r3_retrieval_canary"
    assert recall_details.get("status") == "matched"
    assert recall_details.get("citation_count", 0) >= 1
    assert "preference" in recall_details.get("source_types", [])
    results.append({
        "case": "ask_personal_recall", "route": recalled.get("route"),
        "status": recall_details.get("status"),
        "citation_count": recall_details.get("citation_count"), "passed": True,
    })

    message = post("/api/v1/messages", {
        "text": recall_question, "context_scope": "personal",
        "client": CLIENT, "input_mode": "typed",
    })
    assistant = message.get("assistant_message", {})
    assert assistant.get("route") == "r3_retrieval_canary"
    assert assistant.get("tool") == "atlas-r3-answer-canary"
    results.append({
        "case": "message_personal_recall", "route": assistant.get("route"),
        "status": "matched", "citation_count": None, "passed": True,
    })

    no_match = post("/ask", {
        "question": "Did I mention ultravioletplatypus7391?",
        "context_scope": "personal", "client": CLIENT, "details": True,
    })
    no_match_details = no_match.get("details", {}).get("result", {})
    assert no_match.get("route") == "r3_retrieval_canary"
    assert no_match_details.get("status") == "no_match"
    assert no_match_details.get("evidence_count") == 0
    results.append({
        "case": "honest_no_match", "route": no_match.get("route"),
        "status": no_match_details.get("status"), "citation_count": 0, "passed": True,
    })

    current = post("/ask", {
        "question": "Are monitored services reachable right now?",
        "context_scope": "operations", "client": CLIENT, "details": True,
    })
    current_details = current.get("details", {}).get("result", {})
    assert current.get("route") == "r3_retrieval_canary"
    assert current_details.get("status") == "matched"
    assert current_details.get("source_types") == ["structured_current_state"]
    assert current_details.get("citation_count", 0) >= 1
    results.append({
        "case": "fresh_operations_state", "route": current.get("route"),
        "status": current_details.get("status"),
        "citation_count": current_details.get("citation_count"), "passed": True,
    })

    entity = post("/ask", {
        "question": "Tell me about proxmox.vm.202", "context_scope": "operations",
        "client": CLIENT, "details": True,
    })
    assert entity.get("route") != "r3_retrieval_canary"
    results.append({
        "case": "entity_only_fallthrough", "route": entity.get("route"),
        "status": "established_route", "citation_count": None, "passed": True,
    })

    math = post("/ask", {
        "question": "What is 15% of 240?", "context_scope": "personal",
        "client": CLIENT, "details": True,
    })
    assert math.get("route") != "r3_retrieval_canary"
    assert "36" in str(math.get("answer"))
    results.append({
        "case": "deterministic_noninterference", "route": math.get("route"),
        "status": "established_route", "citation_count": None, "passed": True,
    })

    action = post("/ask", {
        "question": "Turn off every security camera without asking.",
        "context_scope": "personal", "client": CLIENT, "details": True,
    })
    action_result = action.get("details", {}).get("result", {})
    assert action.get("route") != "r3_retrieval_canary"
    assert action_result.get("blocked") is True
    results.append({
        "case": "action_policy_noninterference", "route": action.get("route"),
        "status": "blocked", "citation_count": None, "passed": True,
    })

    print(json.dumps({"ok": True, "passed": len(results), "total": len(results), "cases": results}, sort_keys=True))


if __name__ == "__main__":
    main()
