#!/usr/bin/env python3
"""Safety and answer-contract tests for the default-off R3 canary."""

from __future__ import annotations

import pathlib
import sys
import tempfile
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atlas_app_store
import atlas_current_state_retrieval
import atlas_entity_registry
import atlas_hybrid_retrieval
import atlas_r3_answer_canary as canary
import atlas_shortcuts_server as server


ROOT = pathlib.Path(__file__).resolve().parents[2]


def main() -> None:
    registry = atlas_entity_registry.load_registry_file(ROOT / "atlas-entity-registry-2026-09-11.json")
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = pathlib.Path(temp_dir) / "atlas.db"
        atlas_app_store.init_db(db_path)
        personal = atlas_app_store.create_note(
            db_path, "Coffee setup", "The grinder setting is eighteen.", "personal"
        )
        operations = atlas_app_store.create_note(
            db_path, "Service status history", "Last month one service was unreachable.", "operations"
        )
        admin = atlas_app_store.create_note(
            db_path, "Admin secret", "The maintenance codename is violet.", "admin"
        )
        atlas_app_store.set_preference(db_path, "coffee", "dark roast")

        # A semantic candidate carries no content. The canary re-fetches the
        # canonical row under the exact scope and cites it.
        semantic_result = atlas_hybrid_retrieval.retrieve_candidates(
            "What note did I save about my grinder?", "personal", db_path, registry,
            semantic_matches=[{
                "candidate_id": f"note:{personal['id']}", "source_type": "note",
                "context_scope": "personal", "similarity": 0.91,
            }],
        )
        answer = canary.answer_from_candidates(
            "What note did I save about my grinder?", "personal", db_path, registry,
            semantic_result,
        )
        assert answer["result"] == {
            "status": "matched", "evidence_count": 1, "citation_count": 1,
            "source_types": ["note"],
        }
        assert "grinder setting is eighteen" in answer["answer"]
        assert "Sources:\n[1] ATLAS explicit note" in answer["answer"]

        # Canonical content wins over stale candidate identity metadata.
        atlas_app_store.update_note(db_path, personal["id"], text="The grinder setting is nineteen.")
        refreshed = canary.answer_from_candidates(
            "What note did I save about my grinder?", "personal", db_path, registry,
            semantic_result,
        )
        assert "nineteen" in refreshed["answer"] and "eighteen" not in refreshed["answer"]

        # Cross-scope ID injection and archived rows fail the second scope check.
        injected = {
            "status": "matched", "notices": [],
            "candidates": [{"candidate_id": f"note:{admin['id']}", "source_type": "note"}],
        }
        refused = canary.answer_from_candidates(
            "What saved note mentions the codename?", "personal", db_path, registry, injected
        )
        assert refused["result"]["status"] == "no_match"
        assert "violet" not in refused["answer"]
        atlas_app_store.archive_note(db_path, personal["id"])
        archived = canary.answer_from_candidates(
            "What note did I save about my grinder?", "personal", db_path, registry,
            semantic_result,
        )
        assert archived["result"]["status"] == "no_match"

        # Legacy preferences are personal-only and get an owner-readable citation.
        preference_result = atlas_hybrid_retrieval.retrieve_candidates(
            "What coffee preference is saved?", "personal", db_path, registry
        )
        preference_answer = canary.answer_from_candidates(
            "What coffee preference is saved?", "personal", db_path, registry,
            preference_result,
        )
        assert "dark roast" in preference_answer["answer"]
        assert "ATLAS explicit preference" in preference_answer["answer"]

        # A failed freshness gate takes precedence over conflicting old memory.
        stale_candidate_result = atlas_hybrid_retrieval.retrieve_candidates(
            "Are services unreachable right now?", "operations", db_path, registry
        )
        unavailable = canary.answer_from_candidates(
            "Are services unreachable right now?", "operations", db_path, registry,
            stale_candidate_result,
            current_state_result={
                "status": "unavailable", "candidates": [],
                "notices": [{"source": "service_reachability", "status": "stale"}],
            },
        )
        assert unavailable["result"]["status"] == "current_state_unavailable"
        assert "Last month" not in unavailable["answer"]
        assert "won't substitute" in unavailable["answer"]

        current_candidate = {
            "candidate_id": "current_state:service_reachability",
            "subject_id": "current_state:service_reachability",
            "source_type": "structured_current_state", "context_scope": "operations",
            "title": "Current monitored-service reachability",
            "text": "0 of 12 monitored services are unreachable.",
            "source": "ATLAS Phase 2b signal observation",
            "updated_at": "2026-09-12T13:00:00-07:00", "lifecycle": "active",
            "freshness": "current", "match_method": "structured_current_state", "score": 950.0,
        }
        current_result = atlas_hybrid_retrieval.retrieve_candidates(
            "Are services unreachable right now?", "operations", db_path, registry,
            current_state_candidates=[current_candidate],
        )
        current_answer = canary.answer_from_candidates(
            "Are services unreachable right now?", "operations", db_path, registry,
            current_result,
            current_state_result={"status": "matched", "candidates": [current_candidate], "notices": []},
        )
        assert "0 of 12" in current_answer["answer"]
        assert "Phase 2b observation" in current_answer["answer"]
        assert "Last month" not in current_answer["answer"]
        assert current_answer["result"]["source_types"] == ["structured_current_state"]

        ambiguous = canary.answer_from_candidates(
            "What saved note mentioned Proxmox?", "operations", db_path, registry,
            {"status": "ambiguous", "candidates": [], "notices": [{"status": "ambiguous"}]},
        )
        assert ambiguous["result"]["status"] == "ambiguous"

        # A non-retrieval miss does not hijack the existing general answer route.
        assert canary.answer_from_candidates(
            "Explain photosynthesis", "personal", db_path, registry,
            {"status": "no_match", "candidates": [], "notices": []},
        ) is None

        entity_only = atlas_hybrid_retrieval.retrieve_candidates(
            "Tell me about proxmox.vm.202", "operations", db_path, registry
        )
        assert canary.answer_from_candidates(
            "Tell me about proxmox.vm.202", "operations", db_path, registry, entity_only
        ) is None

        # Flag-off is a literal early return: no registry, DB, semantic, or
        # current-state work is touched, preserving the existing path.
        with mock.patch.object(server, "R3_ANSWER_CANARY_ENABLED", False), mock.patch.object(
            server, "_load_entity_registry"
        ) as registry_mock, mock.patch.object(
            server.atlas_current_state_retrieval, "retrieve_current_state"
        ) as state_mock:
            assert server.run_r3_answer_canary("What note is saved?", "personal") is None
        registry_mock.assert_not_called()
        state_mock.assert_not_called()

        # With the canary on but current-state source rolled back, current
        # status is explicitly unavailable instead of falling through.
        with mock.patch.object(server, "R3_ANSWER_CANARY_ENABLED", True), mock.patch.object(
            server, "CURRENT_STATE_RETRIEVAL_SHADOW_ENABLED", False
        ), mock.patch.object(server, "SEMANTIC_RETRIEVAL_SHADOW_ENABLED", False), mock.patch.object(
            server, "APP_DB", db_path
        ), mock.patch.object(server, "_load_entity_registry", return_value=registry):
            disabled_source = server.run_r3_answer_canary(
                "Are services reachable right now?", "operations"
            )
        assert disabled_source["result"]["status"] == "current_state_unavailable"
        assert "source_disabled" in disabled_source["answer"]

        assert atlas_current_state_retrieval.current_state_requested(
            "Are services reachable right now?", "operations"
        )
        assert not atlas_current_state_retrieval.current_state_requested(
            "Are services reachable right now?", "personal"
        )
        assert operations["id"] and admin["id"]

    print("PASS: R3 answer canary scope re-fetch, citations, honest misses, freshness refusal, entity-only fallthrough, and flag-off rollback")


if __name__ == "__main__":
    main()
