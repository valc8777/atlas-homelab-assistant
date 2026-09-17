#!/usr/bin/env python3
"""Focused tests for ATLAS R6 non-destructive memory maintenance reports."""

from __future__ import annotations

import pathlib
import sys
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atlas_app_store
import atlas_hybrid_retrieval
import atlas_memory_maintenance
import atlas_shortcuts_server as server


LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = pathlib.Path(temp_dir) / "atlas.db"
        atlas_app_store.init_db(db_path)
        atlas_app_store.set_preference(db_path, "favorite color", "blue")
        note_1 = atlas_app_store.create_note(
            db_path,
            title="favorite color",
            text="My favorite color is green.",
            context_scope="personal",
            row_id="note-a",
        )
        note_2 = atlas_app_store.create_note(
            db_path,
            title="favorite color",
            text="My favorite color is green.",
            context_scope="personal",
            row_id="note-b",
        )
        expired_import = atlas_app_store.create_note(
            db_path,
            title="Old imported fact",
            text="Temporary imported fact.",
            context_scope="personal",
            row_id="note-expired-import",
            source_type="import",
            import_batch_id="batch-1",
            import_confidence=0.42,
            review_status="needs_review",
            expires_at=(datetime.now(LOCAL_TZ) - timedelta(days=1)).isoformat(),
            fact_lifecycle="active",
        )
        correction = atlas_app_store.correct_preference(
            db_path,
            "favorite color",
            "blue",
            "purple",
            source="owner_correction",
            row_id="history-1",
        )
        atlas_app_store.add_audit_event(
            db_path,
            "hybrid_retrieval.shadow",
            "Hybrid retrieval shadow matched",
            {
                "status": "matched",
                "candidate_count": 1,
                "source_types": ["preference"],
                "match_methods": ["exact"],
                "record_usage_fingerprints": [
                    atlas_hybrid_retrieval.usage_fingerprint("preference:favorite color", "personal")
                ],
            },
        )
        now = datetime.now(LOCAL_TZ)
        atlas_app_store.put_signal_observation(
            db_path,
            "backup_status",
            {"latest_ok": True, "last_success_age_hours": 6.5},
            observed_at=(now - timedelta(minutes=5)).isoformat(),
        )

        before_preferences = atlas_app_store.list_preferences(db_path)
        before_notes = atlas_app_store.list_notes(db_path, limit=500, context_scope="personal")
        before_audit = atlas_app_store.list_audit_events(db_path)
        report = atlas_memory_maintenance.build_maintenance_report(db_path)
        after_preferences = atlas_app_store.list_preferences(db_path)
        after_notes = atlas_app_store.list_notes(db_path, limit=500, context_scope="personal")
        after_audit = atlas_app_store.list_audit_events(db_path)

        assert before_preferences == after_preferences
        assert before_notes == after_notes
        assert before_audit == after_audit
        assert report["dry_run"] is True
        assert report["context_scope"] == "personal"
        assert report["counts"]["preferences"] == 1
        assert report["counts"]["notes"] == 3
        assert report["counts"]["exportable_records"] == 5
        assert report["counts"]["retrieval_events"] == 1
        assert report["counts"]["matched_retrieval_events"] == 1
        assert report["counts"]["fingerprinted_retrieval_events"] == 1
        assert report["counts"]["never_retrieved_records"] == 3
        assert report["counts"]["backup_policy_status"] == "ok"
        assert report["counts"]["expired_fact_candidates"] == 1
        assert report["counts"]["low_confidence_import_candidates"] == 1
        assert report["counts"]["history_count"] == 1
        assert report["counts"]["statuses"] == {"superseded": 1}
        assert report["counts"]["affected_ids"] == [correction["history_id"]]
        duplicate = next(item for item in report["findings"] if item["kind"] == "duplicate_notes")
        assert duplicate["affected_ids"] == sorted([note_1["id"], note_2["id"]])
        conflict = next(item for item in report["findings"] if item["kind"] == "unresolved_conflict")
        assert conflict["subject"] == "color"
        assert conflict["affected_ids"] == ["favorite color", "note-a", "note-b"]
        rehearsal = next(
            item for item in report["findings"] if item["kind"] == "embedding_index_rebuild_rehearsal"
        )
        assert rehearsal["records_indexed"] == 1 + 3
        assert rehearsal["canonical_memory_mutated"] is False
        export_plan = next(item for item in report["findings"] if item["kind"] == "export_dry_run_plan")
        assert export_plan["record_count"] == 5
        assert export_plan["by_scope"] == {"personal": 5}
        assert export_plan["by_source"] == {
            "import": 1,
            "manual_note": 2,
            "owner_correction": 1,
            "preference": 1,
        }
        assert export_plan["by_entity"] == {"unlinked": 5}
        assert export_plan["would_write_export"] is False
        assert export_plan["affected_ids"] == [
            "note:note-a",
            "note:note-b",
            "note:note-expired-import",
            "preference:favorite color",
            "preference_history:history-1",
        ]
        purge_plan = next(item for item in report["findings"] if item["kind"] == "purge_dry_run_plan")
        assert purge_plan["affected_ids"] == ["note-b"]
        assert purge_plan["would_delete"] is False
        assert purge_plan["would_archive"] is False
        assert "favorite color" in purge_plan["blocked_conflict_ids"]
        retrieval_use = next(item for item in report["findings"] if item["kind"] == "retrieval_use_tracking")
        assert retrieval_use["record_count"] == 5
        assert retrieval_use["trackable_record_count"] == 4
        assert retrieval_use["affected_ids"] == [
            "note:note-a",
            "note:note-b",
            "note:note-expired-import",
        ]
        assert retrieval_use["never_retrieved_count"] == 3
        assert retrieval_use["retrieval_event_count"] == 1
        assert retrieval_use["matched_retrieval_event_count"] == 1
        assert retrieval_use["fingerprinted_retrieval_event_count"] == 1
        assert retrieval_use["can_identify_never_retrieved_records"] is True
        assert retrieval_use["historical_coverage_complete"] is True
        backup = next(item for item in report["findings"] if item["kind"] == "backup_policy_check")
        assert backup["status"] == "ok"
        assert backup["latest_ok"] is True
        assert backup["last_success_age_hours"] == 6.5
        assert backup["would_trigger_backup"] is False
        expired = next(item for item in report["findings"] if item["kind"] == "expired_facts")
        assert expired["affected_ids"] == [f"note:{expired_import['id']}"]
        low_confidence = next(item for item in report["findings"] if item["kind"] == "low_confidence_imports")
        assert low_confidence["affected_ids"] == [f"note:{expired_import['id']}"]
        expired_gap = next(item for item in report["findings"] if item["kind"] == "expired_facts_schema_gap")
        assert expired_gap["record_count"] == 4
        assert expired_gap["affected_ids"] == [
            "note:note-a",
            "note:note-b",
            "preference:favorite color",
            "preference_history:history-1",
        ]
        assert expired_gap["can_identify_expired_facts"] is True
        assert "expires_at" in expired_gap["missing_fields"]
        low_confidence_gap = next(
            item for item in report["findings"] if item["kind"] == "low_confidence_imports_schema_gap"
        )
        assert low_confidence_gap["record_count"] == 4
        assert low_confidence_gap["affected_ids"] == expired_gap["affected_ids"]
        assert low_confidence_gap["can_identify_low_confidence_imports"] is True
        assert "import_confidence" in low_confidence_gap["missing_fields"]
        assert "embedding_index_rebuild" not in report["unsupported_checks"]
        assert "export_or_purge_plan" not in report["unsupported_checks"]
        assert "never_retrieved_records" not in report["unsupported_checks"]
        assert "record_level_retrieval_attribution" not in report["unsupported_checks"]
        assert "backup_policy_check" not in report["unsupported_checks"]
        assert "expired_facts" not in report["unsupported_checks"]
        assert "low_confidence_imports" not in report["unsupported_checks"]
        assert "expired_fact_classification" not in report["unsupported_checks"]
        assert "low_confidence_import_classification" not in report["unsupported_checks"]
        assert report["unsupported_checks"] == ["orphaned_aliases"]

        atlas_app_store.put_signal_observation(
            db_path,
            "backup_status",
            {"latest_ok": False, "last_success_age_hours": 72},
            observed_at=(now - timedelta(hours=25)).isoformat(),
        )
        stale_report = atlas_memory_maintenance.build_maintenance_report(db_path)
        stale_backup = next(item for item in stale_report["findings"] if item["kind"] == "backup_policy_check")
        assert stale_backup["status"] == "attention"
        assert "latest_backup_not_ok" in stale_backup["reason"]
        assert "backup_observation_stale_or_future" in stale_backup["reason"]
        assert "last_success_too_old_or_unknown" in stale_backup["reason"]

        with tempfile.TemporaryDirectory() as empty_dir:
            empty_db = pathlib.Path(empty_dir) / "atlas.db"
            atlas_app_store.init_db(empty_db)
            missing = atlas_memory_maintenance.build_maintenance_report(empty_db)
            missing_backup = next(item for item in missing["findings"] if item["kind"] == "backup_policy_check")
            assert missing_backup["status"] == "unavailable"
            assert missing_backup["reason"] == "backup_status_observation_missing"

        handler = object.__new__(server.AtlasHandler)
        original_db = server.APP_DB
        try:
            server.APP_DB = db_path
            answer = handler._answer_memory_maintenance_request("Run a memory maintenance report.", "personal")
            assert answer["route"] == "memory_maintenance"
            assert answer["result"]["dry_run"] is True
            assert "did not merge" in answer["answer"].lower()
            assert handler._answer_memory_maintenance_request("Run a memory maintenance report.", "operations") is None
        finally:
            server.APP_DB = original_db

    print("PASS: R6 memory maintenance dry run enumerates affected IDs without mutation")


if __name__ == "__main__":
    main()
