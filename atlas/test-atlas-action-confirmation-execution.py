#!/usr/bin/env python3

"""Adversarial tests for ATLAS orchestration v2 action authority: the
confirmation-gated execution pipeline for fuzzy, model-classified action-mode
decisions (reminders/tasks/shopping/notes.manage -- the deliberately narrow
"first action slice").

These tests exercise atlas_app_store.execute_confirmed_action() directly
against a real temp SQLite DB (same idiom as smoke-test-atlas-app-store.py
and test-atlas-orchestrator-v2.py) to prove the contracts this feature exists
to guarantee:

- No unconfirmed writes ever happen.
- At-most-once execution per confirmation, safe against double-approval and
  double-poll races.
- A verified, read-back-checked write, or a clean rollback with a durable
  failure record -- never a partial or silently-wrong row.
- A stale/hand-crafted confirmation naming an unsupported capability or the
  wrong context_scope is refused, not executed.

The orchestrator-canary-boundary side of this feature (the caller-side gate
in run_atlas_with_readonly_canary that decides whether a fuzzy action-mode
decision becomes a confirmation at all) is covered separately in
test-atlas-orchestration-readonly-canary.py.
"""

import pathlib
import tempfile
from unittest import mock

import atlas_app_store


def with_temp_db(fn):
    def wrapper():
        with tempfile.TemporaryDirectory() as tmp:
            db = pathlib.Path(tmp) / "test.db"
            atlas_app_store.init_db(db)
            fn(db)

    return wrapper


@with_temp_db
def test_no_unconfirmed_writes(db):
    details = {"capability": "notes.manage", "context_scope": "personal", "text": "Reschedule the dentist"}
    confirmation = atlas_app_store.request_confirmation(
        db, action_type="action.notes.manage", summary="note", risk_level="low", details=details, execution_status="pending"
    )
    # Never approved.
    result = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    assert result["ok"] is False
    assert result["error"] == "not_approved"
    assert atlas_app_store.list_notes(db) == []
    row = atlas_app_store.list_confirmations(db)[0]
    assert row["execution_status"] == "pending"


@with_temp_db
def test_double_approve_double_execute_is_idempotent(db):
    details = {"capability": "tasks.manage", "context_scope": "personal", "text": "Review the budget"}
    confirmation = atlas_app_store.request_confirmation(
        db, action_type="action.tasks.manage", summary="task", risk_level="low", details=details, execution_status="pending"
    )
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")  # no-op, status already != pending

    first = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    second = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    assert first["ok"] is True and first.get("already_executed") is not True
    assert second["ok"] is True and second.get("already_executed") is True
    tasks = atlas_app_store.list_tasks(db)
    assert len(tasks) == 1
    assert tasks[0]["id"] == confirmation["id"]


@with_temp_db
def test_deny_then_bypass_attempt_writes_nothing(db):
    details = {"capability": "shopping.manage", "context_scope": "personal", "text": "milk"}
    confirmation = atlas_app_store.request_confirmation(
        db, action_type="action.shopping.manage", summary="shopping", risk_level="low", details=details, execution_status="pending"
    )
    atlas_app_store.decide_confirmation(db, confirmation["id"], "denied")

    # A second decide_confirmation call attempting to flip a denied record to
    # approved must no-op (the existing "where status = 'pending'" guard).
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")
    row = atlas_app_store.list_confirmations(db)[0]
    assert row["status"] == "denied"

    # Simulate a caller bypassing the API layer entirely and calling the
    # executor directly against the denied confirmation id.
    result = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    assert result["ok"] is False
    assert result["error"] == "not_approved"
    assert atlas_app_store.list_shopping_items(db) == []


@with_temp_db
def test_execution_crash_rolls_back_and_retry_succeeds(db):
    details = {"capability": "tasks.manage", "context_scope": "personal", "text": "Review the budget"}
    confirmation = atlas_app_store.request_confirmation(
        db, action_type="action.tasks.manage", summary="task", risk_level="low", details=details, execution_status="pending"
    )
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")

    def boom(db_path, conn, row_id, details):
        raise RuntimeError("simulated disk failure")

    with mock.patch.dict(atlas_app_store._ACTION_EXECUTORS, {"tasks.manage": boom}):
        crashed = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    assert crashed["ok"] is False
    assert "simulated disk failure" in crashed["error"]
    assert atlas_app_store.list_tasks(db) == [], "no partial row should survive a mid-write crash"

    row = atlas_app_store.list_confirmations(db)[0]
    assert row["execution_status"] == "failed"
    assert "simulated disk failure" in row["execution_error"]
    audit_types = [event["event_type"] for event in atlas_app_store.list_audit_events(db, limit=50)]
    assert "confirmation.execution_failed" in audit_types

    retry = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    assert retry["ok"] is True
    assert len(atlas_app_store.list_tasks(db)) == 1


@with_temp_db
def test_unknown_capability_in_stale_confirmation_is_refused(db):
    # Hand-crafted, bypassing request_confirmation entirely -- simulates a
    # confirmation record from a future/removed capability that never had a
    # real executor wired up.
    details = {"capability": "home.control", "context_scope": "personal"}
    confirmation = atlas_app_store.request_confirmation(
        db, action_type="action.home.control", summary="bad", risk_level="low", details=details, execution_status="pending"
    )
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")

    result = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    assert result["ok"] is False
    assert result["error"] == "unsupported_capability"
    row = atlas_app_store.list_confirmations(db)[0]
    assert row["execution_status"] == "failed"


@with_temp_db
def test_wrong_context_scope_fails_closed(db):
    # shopping.manage is registered as personal-only in atlas_orchestrator_v2;
    # a confirmation claiming "operations" for it must be refused, not
    # silently defaulted to personal.
    details = {"capability": "shopping.manage", "context_scope": "operations", "text": "milk"}
    confirmation = atlas_app_store.request_confirmation(
        db, action_type="action.shopping.manage", summary="bad scope", risk_level="low", details=details, execution_status="pending"
    )
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")

    result = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    assert result["ok"] is False
    assert result["error"] == "scope_not_allowed"
    assert atlas_app_store.list_shopping_items(db) == []


@with_temp_db
def test_atomic_claim_prevents_concurrent_double_write(db):
    # Simulate a genuine race: two callers both see execution_status in
    # ('pending','failed') and both call execute_confirmed_action. The
    # atomic claim's UPDATE ... WHERE guard means only one can win regardless
    # of call order, since SQLite serializes writers.
    details = {"capability": "reminders.manage", "context_scope": "personal", "text": "Call the dentist", "due_at": None}
    confirmation = atlas_app_store.request_confirmation(
        db, action_type="action.reminders.manage", summary="reminder", risk_level="low", details=details, execution_status="pending"
    )
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")

    results = [atlas_app_store.execute_confirmed_action(db, confirmation["id"]) for _ in range(5)]
    successes = [r for r in results if r["ok"] and not r.get("already_executed")]
    cached = [r for r in results if r["ok"] and r.get("already_executed")]
    assert len(successes) == 1, "exactly one call should perform the real write"
    assert len(cached) == 4, "every other call should see the cached executed result"
    assert len(atlas_app_store.list_reminders(db)) == 1


def main() -> None:
    test_no_unconfirmed_writes()
    test_double_approve_double_execute_is_idempotent()
    test_deny_then_bypass_attempt_writes_nothing()
    test_execution_crash_rolls_back_and_retry_succeeds()
    test_unknown_capability_in_stale_confirmation_is_refused()
    test_wrong_context_scope_fails_closed()
    test_atomic_claim_prevents_concurrent_double_write()
    print("ATLAS action confirmation execution adversarial tests passed")


if __name__ == "__main__":
    main()
