#!/usr/bin/env python3
"""Freshness, scope, and value-shape tests for R3 structured state."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atlas_app_store
import atlas_current_state_retrieval as current_state


LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def put(db_path: pathlib.Path, name: str, value, observed_at: datetime, status: str = "ok") -> None:
    atlas_app_store.put_signal_observation(
        db_path, name, value, status=status, observed_at=observed_at.isoformat()
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = pathlib.Path(temp_dir) / "atlas.db"
        atlas_app_store.init_db(db_path)
        now = datetime(2026, 9, 12, 12, 0, tzinfo=LOCAL_TZ)
        put(db_path, "service_reachability", {"total": 10, "unreachable_count": 1}, now - timedelta(minutes=2))
        put(db_path, "host_health", {"total": 3, "unhealthy_count": 0}, now - timedelta(minutes=3))
        put(db_path, "backup_status", {"latest_ok": True, "last_success_age_hours": 5.5}, now - timedelta(minutes=4))

        services = current_state.retrieve_current_state(
            "Are any services unreachable right now?", "operations", db_path, now_value=now
        )
        assert services["status"] == "matched"
        assert [item["candidate_id"] for item in services["candidates"]] == ["current_state:service_reachability"]
        assert services["candidates"][0]["text"] == "1 of 10 monitored services are unreachable."

        hosts = current_state.retrieve_current_state(
            "Are the hosts healthy?", "operations", db_path, now_value=now
        )
        assert hosts["candidates"][0]["candidate_id"] == "current_state:host_health"

        backup = current_state.retrieve_current_state(
            "Did the latest backup succeed?", "operations", db_path, now_value=now
        )
        assert backup["candidates"][0]["candidate_id"] == "current_state:backup_status"
        assert "5.5 hours" in backup["candidates"][0]["text"]

        combined = current_state.retrieve_current_state(
            "What is the current Proxmox host and backup status?", "operations", db_path, now_value=now
        )
        assert {item["candidate_id"] for item in combined["candidates"]} == {
            "current_state:service_reachability", "current_state:host_health", "current_state:backup_status"
        }

        # Scope filtering occurs before any observation read/candidate build.
        assert current_state.retrieve_current_state(
            "Are services currently reachable?", "personal", db_path, now_value=now
        )["status"] == "not_requested"
        assert current_state.retrieve_current_state(
            "Are services currently reachable?", "admin", db_path, now_value=now
        )["candidates"] == []

        # No current-state cue means ordinary historical/reference questions
        # cannot accidentally receive a live-state candidate.
        assert current_state.retrieve_current_state(
            "Explain the Proxmox architecture", "operations", db_path, now_value=now
        )["status"] == "not_requested"

        put(db_path, "service_reachability", {"total": 10, "unreachable_count": 0}, now - timedelta(minutes=16))
        stale = current_state.retrieve_current_state(
            "Are services reachable now?", "operations", db_path, now_value=now
        )
        assert stale["status"] == "unavailable"
        assert stale["candidates"] == []
        assert stale["notices"] == [{"source": "service_reachability", "status": "stale"}]

        put(db_path, "service_reachability", {"total": 10, "unreachable_count": 0}, now + timedelta(seconds=1))
        future = current_state.retrieve_current_state(
            "Are services reachable now?", "operations", db_path, now_value=now
        )
        assert future["notices"][0]["status"] == "future"

        put(db_path, "service_reachability", None, now, status="error")
        failed = current_state.retrieve_current_state(
            "Are services reachable now?", "operations", db_path, now_value=now
        )
        assert failed["notices"][0]["status"] == "error"

        put(db_path, "service_reachability", {"total": 2, "unreachable_count": 3}, now)
        malformed = current_state.retrieve_current_state(
            "Are services reachable now?", "operations", db_path, now_value=now
        )
        assert malformed["candidates"] == []
        assert malformed["notices"][0]["status"] == "invalid_value"

        metrics = current_state.privacy_metrics(services)
        serialized = json.dumps(metrics).lower()
        assert metrics["current_state_candidate_count"] == 1
        assert "1 of 10" not in serialized
        assert "service_reachability" not in serialized

        try:
            current_state.retrieve_current_state("status", "invalid", db_path, now_value=now)
        except ValueError as exc:
            assert str(exc) == "invalid_context_scope"
        else:
            raise AssertionError("invalid scope accepted")

    print("PASS: R3 structured current-state freshness, scope, value validation, and privacy metrics")


if __name__ == "__main__":
    main()
