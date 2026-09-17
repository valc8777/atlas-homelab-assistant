#!/usr/bin/env python3
"""Focused tests for ATLAS R5 personal-memory conflict detection."""

from __future__ import annotations

import pathlib
import sys
import tempfile
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atlas_app_store
import atlas_memory_conflicts
import atlas_shortcuts_server as server


def main() -> None:
    preferences = [
        {"key": "favorite color", "value": "blue", "updated_at": "2026-09-01T00:00:00-07:00"},
    ]
    notes = [
        {
            "id": "note-1",
            "title": "favorite color",
            "text": "My favorite color is green.",
            "updated_at": "2026-09-02T00:00:00-07:00",
        }
    ]
    conflicts = atlas_memory_conflicts.detect_memory_conflicts(
        "Do you remember my favorite color?", preferences, notes
    )
    assert len(conflicts) == 1
    assert conflicts[0]["subject"] == "color"
    assert conflicts[0]["value_count"] == 2
    assert atlas_memory_conflicts.conflict_metrics(conflicts) == {
        "conflict_count": 1,
        "record_count": 2,
        "subjects": 1,
    }
    assert atlas_memory_conflicts.detect_memory_conflicts(
        "Do you remember my favorite snack?", preferences, notes
    ) == []
    assert atlas_memory_conflicts.parse_owner_correction(
        "That's wrong, my favorite color is green."
    ) == {"subject": "color", "value": "green"}
    assert atlas_memory_conflicts.parse_owner_correction("That is wrong.") == {
        "needs_replacement": True
    }

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = pathlib.Path(temp_dir) / "atlas.db"
        atlas_app_store.init_db(db_path)
        atlas_app_store.set_preference(db_path, "favorite color", "blue")
        atlas_app_store.create_note(
            db_path,
            title="favorite color",
            text="My favorite color is green.",
            context_scope="personal",
        )
        handler = object.__new__(server.AtlasHandler)
        original_db = server.APP_DB
        try:
            server.APP_DB = db_path
            result = handler._answer_personal_memory_request(
                "Do you remember my favorite color?", "personal"
            )
            assert result["route"] == "personal_memory_conflict"
            assert result["result"] == {"conflict_count": 1, "record_count": 2, "subjects": 1}
            assert "blue" in result["answer"]
            assert "green" in result["answer"]

            non_personal = handler._answer_personal_memory_request(
                "Do you remember my favorite color?", "operations"
            )
            assert non_personal is None

            listed = handler._answer_personal_memory_request("What are my preferences?", "personal")
            assert listed["route"] == "personal_memory"

            # Chat-quality review cluster 5, found live 2026-09-17: a bare
            # "That is wrong."/"No, ..."-shaped correction with NO
            # established personal-memory context in the conversation must
            # not presumptuously ask for "the exact replacement fact" --
            # that ambiguous shape matched real corrections of ATLAS's own
            # immediately preceding weather answer and general-knowledge
            # claims, neither of which is a stored personal fact. With no
            # conversation history at all (a fresh, never-seen conversation
            # id), this must defer to the normal answer pipeline instead.
            with mock.patch.object(server, "send_ntfy_feedback", return_value=None):
                no_context = handler._propose_memory_correction("That is wrong.", "personal", "conv-1")
            assert no_context is None
            assert atlas_app_store.list_preferences(db_path)[0]["value"] == "blue"

            # The same ambiguous shape must still work once there IS a real
            # personal-memory turn to be correcting -- the legitimate use
            # case this feedback message exists for.
            conv_id = atlas_app_store.save_exchange(
                db_path,
                "What is my favorite color?",
                {"answer": "Blue.", "route": "personal_memory", "tool": "atlas-app-store"},
            )["conversation_id"]
            with mock.patch.object(server, "send_ntfy_feedback", return_value=None):
                missing = handler._propose_memory_correction("That is wrong.", "personal", conv_id)
            assert missing["route"] == "memory_correction_feedback"
            assert atlas_app_store.list_preferences(db_path)[0]["value"] == "blue"

            # Live-verified 2026-09-17: a real preference recall in
            # production actually answers through R3's retrieval canary,
            # not the legacy `personal_memory` route -- must be recognized
            # too, or a genuine correction silently stops working.
            r3_conv_id = atlas_app_store.save_exchange(
                db_path,
                "What is my favorite color?",
                {"answer": "favorite color: blue [1]", "route": "r3_retrieval_canary", "tool": "atlas-r3-answer-canary"},
            )["conversation_id"]
            with mock.patch.object(server, "send_ntfy_feedback", return_value=None):
                r3_missing = handler._propose_memory_correction("That is wrong.", "personal", r3_conv_id)
            assert r3_missing["route"] == "memory_correction_feedback"

            with mock.patch.object(server, "send_ntfy_feedback", return_value=None):
                preview = handler._propose_memory_correction(
                    "That's wrong, my favorite color is green.", "personal", "conv-1"
                )
            assert preview["route"] == "memory_correction_preview"
            confirmation = preview["result"]["confirmation"]
            details = confirmation["details"]
            assert details["capability"] == "memory.correct"
            assert details["record_type"] == "preference"
            assert details["key"] == "favorite color"
            assert details["old_value"] == "blue"
            assert details["new_value"] == "green"
            assert details["context_scope"] == "personal"
            assert details["affected_aliases"] == ["favorite color", "color"]
            assert atlas_app_store.list_preferences(db_path)[0]["value"] == "blue"

            atlas_app_store.decide_confirmation(db_path, confirmation["id"], "denied")
            denied = atlas_app_store.execute_confirmed_action(db_path, confirmation["id"])
            assert denied == {"ok": False, "executed": False, "error": "not_approved"}
            assert atlas_app_store.list_preferences(db_path)[0]["value"] == "blue"

            with mock.patch.object(server, "send_ntfy_feedback", return_value=None):
                approved_preview = handler._propose_memory_correction(
                    "Actually, my favorite color is purple.", "personal", "conv-1"
                )
            approved = approved_preview["result"]["confirmation"]
            atlas_app_store.decide_confirmation(db_path, approved["id"], "approved")
            applied = atlas_app_store.execute_confirmed_action(db_path, approved["id"])
            assert applied["ok"] is True
            assert applied["row"]["preference"]["value"] == "purple"
            assert applied["row"]["history"]["old_value"] == "blue"
            assert applied["row"]["history"]["new_value"] == "purple"
            assert applied["row"]["history"]["status"] == "superseded"

            rollback = atlas_app_store.rollback_preference_correction(
                db_path, applied["row"]["history"]["id"]
            )
            assert rollback["status"] == "rolled_back"
            assert atlas_app_store.list_preferences(db_path)[0]["value"] == "blue"

            stale = atlas_app_store.request_confirmation(
                db_path,
                action_type="action.memory.correct",
                summary="stale",
                risk_level="low",
                details={
                    "capability": "memory.correct",
                    "context_scope": "personal",
                    "record_type": "preference",
                    "key": "favorite color",
                    "old_value": "green",
                    "new_value": "orange",
                    "source": "owner_correction",
                },
                execution_status="pending",
            )
            atlas_app_store.decide_confirmation(db_path, stale["id"], "approved")
            stale_result = atlas_app_store.execute_confirmed_action(db_path, stale["id"])
            assert stale_result["ok"] is False
            assert stale_result["executed"] is False
            assert "stale_preference_correction" in stale_result["error"]
            assert atlas_app_store.list_preferences(db_path)[0]["value"] == "blue"
        finally:
            server.APP_DB = original_db

    print("PASS: R5 memory conflicts, correction previews, confirmation-gated supersession, and rollback")


if __name__ == "__main__":
    main()
