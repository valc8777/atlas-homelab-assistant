#!/usr/bin/env python3

"""Adversarial tests for the comic-bot media-request confirmation/executor
path (`media.comic_request`, DEC-2026-09-10-04).

Unlike the four executors covered in test-atlas-action-confirmation-execution.py,
this action has no local database row to write and verify -- the real effect
(a downloaded file landing in /opt/stacks/media/media/comics on docker-main)
lives on a different host, reached over HTTP via atlas_comic_bot.py. These
tests mock atlas_comic_bot.trigger_comic_request and exercise
atlas_app_store.execute_confirmed_action() directly against a real temp
SQLite DB (same idiom as the existing confirmation-execution suite) to prove:

- Nothing is triggered on the real site until the confirmation is approved.
- A trigger response that reports ok but names no confirmed file is treated
  as a verification failure, not a success -- "the HTTP call succeeded"
  must never be conflated with "a file actually landed."
- A trigger response naming a file that already existed BEFORE this run
  (comic-bot's own "don't overwrite/re-download a same-named file" guard,
  see DEC-2026-09-10-05) is a legitimate success, not a "no new file"
  failure -- this is what stops a second identical request from retrying
  forever, unable to ever detect a "new" file that a prior request already
  produced.
- A trigger response that reports failure but somehow names a file is
  also treated as a failure (the "ok" flag governs, not file presence alone).
- A raised exception (network error, missing config) is a durable, retriable
  failure -- not a crash that loses the confirmation or double-runs the bot.
- At-most-once execution still holds for this capability specifically.
- A denied confirmation, or one hand-crafted with the wrong context_scope,
  is refused before the real trigger is ever called.
- An optional `result_index` in a confirmation's details (for the rare case
  a series' search results rank an unwanted match, e.g. a TPB collection,
  ahead of the intended single issue -- see DEC-2026-09-10-07) is forwarded
  to the trigger call verbatim; when absent, no override is passed at all.
"""

import pathlib
import tempfile
from unittest import mock

import atlas_app_store
import atlas_comic_bot


def with_temp_db(fn):
    def wrapper():
        with tempfile.TemporaryDirectory() as tmp:
            db = pathlib.Path(tmp) / "test.db"
            atlas_app_store.init_db(db)
            fn(db)

    return wrapper


def _request_comic_confirmation(db, text="Batman 2026-09"):
    return atlas_app_store.request_confirmation(
        db,
        action_type="action.media.comic_request",
        summary=f"Run comic-bot for: {text}",
        risk_level="high_impact",
        details={"capability": "media.comic_request", "context_scope": "personal", "text": text},
        execution_status="pending",
    )


@with_temp_db
def test_unapproved_confirmation_never_calls_trigger(db):
    confirmation = _request_comic_confirmation(db)
    with mock.patch.object(atlas_comic_bot, "trigger_comic_request") as trigger:
        result = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    trigger.assert_not_called()
    assert result["ok"] is False
    assert result["error"] == "not_approved"
    row = atlas_app_store.list_confirmations(db)[0]
    assert row["execution_status"] == "pending"


@with_temp_db
def test_denied_confirmation_never_calls_trigger(db):
    confirmation = _request_comic_confirmation(db)
    atlas_app_store.decide_confirmation(db, confirmation["id"], "denied")
    with mock.patch.object(atlas_comic_bot, "trigger_comic_request") as trigger:
        result = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    trigger.assert_not_called()
    assert result["ok"] is False
    assert result["error"] == "not_approved"


@with_temp_db
def test_successful_download_marks_executed(db):
    confirmation = _request_comic_confirmation(db, text="Saga #1")
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")
    payload = {
        "ok": True,
        "result_file": "Saga 001.cbz",
        "files_before": ["existing.cbz"],
        "files_after": ["existing.cbz", "Saga 001.cbz"],
        "output": "Saved: /downloads/Saga 001.cbz\nRESULT_FILE:Saga 001.cbz",
    }
    with mock.patch.object(atlas_comic_bot, "trigger_comic_request", return_value=payload) as trigger:
        result = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    trigger.assert_called_once()
    assert trigger.call_args.args[2] == "Saga #1", "the owner-typed text must be forwarded verbatim"
    assert result["ok"] is True
    assert result["row"]["result_file"] == "Saga 001.cbz"
    assert result["row"]["already_had_file"] is False, "this was a genuinely new download"
    row = atlas_app_store.list_confirmations(db)[0]
    assert row["execution_status"] == "executed"


@with_temp_db
def test_result_index_override_is_forwarded_when_present(db):
    confirmation = atlas_app_store.request_confirmation(
        db,
        action_type="action.media.comic_request",
        summary="Run comic-bot for: Absolute Martian Manhunter #1",
        risk_level="high_impact",
        details={
            "capability": "media.comic_request",
            "context_scope": "personal",
            "text": "Absolute Martian Manhunter #1",
            "result_index": 1,
        },
        execution_status="pending",
    )
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")
    payload = {
        "ok": True, "result_file": "Absolute Martian Manhunter 001 (2025).cbz",
        "files_before": [], "files_after": ["Absolute Martian Manhunter 001 (2025).cbz"],
        "output": "Saved",
    }
    with mock.patch.object(atlas_comic_bot, "trigger_comic_request", return_value=payload) as trigger:
        result = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    assert result["ok"] is True
    assert trigger.call_args.kwargs.get("result_index") == 1, "the override must reach the adapter call"


@with_temp_db
def test_no_result_index_override_when_absent(db):
    # The common case (no override in details) must not silently inject one.
    confirmation = _request_comic_confirmation(db, text="Saga #1")
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")
    payload = {"ok": True, "result_file": "Saga 001.cbz", "files_before": [], "files_after": ["Saga 001.cbz"], "output": "Saved"}
    with mock.patch.object(atlas_comic_bot, "trigger_comic_request", return_value=payload) as trigger:
        atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    assert trigger.call_args.kwargs.get("result_index") is None


@with_temp_db
def test_already_downloaded_file_is_treated_as_success_not_a_retry(db):
    # The exact bug behind a real incident: a second request for something
    # already fetched must resolve as done, not loop forever unable to see
    # a "new" file. comic-bot itself won't re-download/overwrite a same-
    # named file (DEC-2026-09-10-05) -- it still reports RESULT_FILE for the
    # file it confirmed is already there, which files_before already lists.
    confirmation = _request_comic_confirmation(db, text="Saga #1")
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")
    payload = {
        "ok": True,
        "result_file": "Saga 001.cbz",
        "files_before": ["Saga 001.cbz"],
        "files_after": ["Saga 001.cbz"],
        "output": "Already have: /downloads/Saga 001.cbz\nRESULT_FILE:Saga 001.cbz",
    }
    with mock.patch.object(atlas_comic_bot, "trigger_comic_request", return_value=payload):
        result = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    assert result["ok"] is True, "already having the file is success, not a verification failure"
    assert result["row"]["already_had_file"] is True
    row = atlas_app_store.list_confirmations(db)[0]
    assert row["execution_status"] == "executed", "must not be left retriable forever"


@with_temp_db
def test_trigger_reports_ok_but_no_result_file_is_a_failure(db):
    # A 200 round-trip alone must never be treated as success -- e.g. the
    # bot ran, hit every selector, but the site's download never fired, so
    # comic-bot never printed a RESULT_FILE line at all.
    confirmation = _request_comic_confirmation(db)
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")
    payload = {
        "ok": True, "result_file": None,
        "files_before": ["a.cbz"], "files_after": ["a.cbz"],
        "output": "no download observed",
    }
    with mock.patch.object(atlas_comic_bot, "trigger_comic_request", return_value=payload):
        result = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    assert result["ok"] is False
    assert "verification_mismatch" in result["error"]
    row = atlas_app_store.list_confirmations(db)[0]
    assert row["execution_status"] == "failed"
    assert "missing_result_file" in row["execution_error"]


@with_temp_db
def test_trigger_reports_failure_despite_naming_a_file_is_still_a_failure(db):
    # The listener's own "ok" flag governs even if a result_file happens to
    # be present -- a reported failure must not be second-guessed into a pass.
    confirmation = _request_comic_confirmation(db)
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")
    payload = {
        "ok": False, "result_file": "partial.cbz",
        "files_before": ["a.cbz"], "files_after": ["a.cbz", "partial.tmp"],
        "output": "error: timeout",
    }
    with mock.patch.object(atlas_comic_bot, "trigger_comic_request", return_value=payload):
        result = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    assert result["ok"] is False
    row = atlas_app_store.list_confirmations(db)[0]
    assert row["execution_status"] == "failed"
    assert "trigger_reported_failure" in row["execution_error"]


@with_temp_db
def test_network_error_is_retriable_and_retry_succeeds(db):
    confirmation = _request_comic_confirmation(db)
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")
    success_payload = {
        "ok": True, "result_file": "Saga 001.cbz",
        "files_before": [], "files_after": ["Saga 001.cbz"], "output": "Saved",
    }
    with mock.patch.object(
        atlas_comic_bot,
        "trigger_comic_request",
        side_effect=[TimeoutError("docker-main trigger listener unreachable"), success_payload],
    ) as trigger:
        first = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
        assert first["ok"] is False
        assert "unreachable" in first["error"]
        row = atlas_app_store.list_confirmations(db)[0]
        assert row["execution_status"] == "failed"

        second = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    assert trigger.call_count == 2
    assert second["ok"] is True
    row = atlas_app_store.list_confirmations(db)[0]
    assert row["execution_status"] == "executed"


@with_temp_db
def test_double_approve_double_execute_is_idempotent(db):
    confirmation = _request_comic_confirmation(db)
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")  # no-op, already decided
    payload = {
        "ok": True, "result_file": "Saga 001.cbz",
        "files_before": [], "files_after": ["Saga 001.cbz"], "output": "Saved",
    }
    with mock.patch.object(atlas_comic_bot, "trigger_comic_request", return_value=payload) as trigger:
        first = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
        second = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    assert trigger.call_count == 1, "the real bot/site must never be triggered twice for one approval"
    assert first["ok"] is True and first.get("already_executed") is not True
    assert second["ok"] is True and second.get("already_executed") is True


@with_temp_db
def test_wrong_context_scope_fails_closed_before_trigger(db):
    # media.comic_request is registered personal-only in atlas_orchestrator_v2;
    # a hand-crafted confirmation claiming "operations" must be refused.
    confirmation = atlas_app_store.request_confirmation(
        db,
        action_type="action.media.comic_request",
        summary="bad scope",
        risk_level="high_impact",
        details={"capability": "media.comic_request", "context_scope": "operations", "text": "Saga #1"},
        execution_status="pending",
    )
    atlas_app_store.decide_confirmation(db, confirmation["id"], "approved")
    with mock.patch.object(atlas_comic_bot, "trigger_comic_request") as trigger:
        result = atlas_app_store.execute_confirmed_action(db, confirmation["id"])
    trigger.assert_not_called()
    assert result["ok"] is False
    assert result["error"] == "scope_not_allowed"


def main() -> None:
    test_unapproved_confirmation_never_calls_trigger()
    test_denied_confirmation_never_calls_trigger()
    test_successful_download_marks_executed()
    test_result_index_override_is_forwarded_when_present()
    test_no_result_index_override_when_absent()
    test_already_downloaded_file_is_treated_as_success_not_a_retry()
    test_trigger_reports_ok_but_no_result_file_is_a_failure()
    test_trigger_reports_failure_despite_naming_a_file_is_still_a_failure()
    test_network_error_is_retriable_and_retry_succeeds()
    test_double_approve_double_execute_is_idempotent()
    test_wrong_context_scope_fails_closed_before_trigger()
    print("ATLAS comic-bot confirmation/executor tests passed")


if __name__ == "__main__":
    main()
