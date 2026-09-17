#!/usr/bin/env python3
"""Adversarial tests for the dormant ATLAS R3 scope-first retriever."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atlas_app_store
import atlas_entity_registry
import atlas_hybrid_retrieval as retrieval
import atlas_shortcuts_server as server


ROOT = pathlib.Path(__file__).resolve().parents[2]


def main() -> None:
    registry = atlas_entity_registry.load_registry_file(ROOT / "atlas-entity-registry-2026-09-11.json")
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = pathlib.Path(temp_dir) / "atlas.db"
        atlas_app_store.init_db(db_path)
        personal_note = atlas_app_store.create_note(
            db_path, "Coffee grinder", "The preferred grinder setting is eighteen.", "personal"
        )
        operations_note = atlas_app_store.create_note(
            db_path, "Jellyfin storage", "Jellyfin media lives on the atlas-media dataset.", "operations"
        )
        admin_note = atlas_app_store.create_note(
            db_path, "Private admin detail", "The maintenance codename is violet.", "admin"
        )
        atlas_app_store.set_preference(db_path, "coffee", "dark roast")

        exact = retrieval.retrieve_candidates("Check prowlarr", "operations", db_path, registry)
        assert exact["status"] == "matched"
        assert exact["candidates"][0]["source_type"] == "canonical_entity"

        exact_id = retrieval.retrieve_candidates(
            "Tell me about proxmox.vm.202", "operations", db_path, registry
        )
        assert exact_id["candidates"][0]["candidate_id"] == "entity:proxmox.vm.202"
        assert exact_id["candidates"][0]["match_method"] == "exact_id"

        alias = retrieval.retrieve_candidates("Is gaming VM online?", "operations", db_path, registry)
        assert alias["candidates"][0]["candidate_id"] == "entity:proxmox.vm.202"

        collision = retrieval.retrieve_candidates("Tell me about Proxmox", "operations", db_path, registry)
        assert collision["status"] == "ambiguous"
        assert collision["candidates"] == []
        assert collision["notices"] == [{"status": "ambiguous", "candidate_count": 2}]

        note = retrieval.retrieve_candidates("Where is Jellyfin media?", "operations", db_path, registry)
        note_ids = [item["candidate_id"] for item in note["candidates"]]
        assert f"note:{operations_note['id']}" in note_ids
        assert f"note:{personal_note['id']}" not in note_ids
        assert f"note:{admin_note['id']}" not in note_ids

        personal = retrieval.retrieve_candidates("What coffee do I prefer?", "personal", db_path, registry)
        assert personal["candidates"][0]["candidate_id"] == "preference:coffee"
        assert all(item["context_scope"] == "personal" for item in personal["candidates"])

        # DEF-048: found live via Track A3 Gate 1 -- a real preference keyed
        # "general" (an ordinary English adjective, not a distinctive topic)
        # matched "Which scientist developed the theory of general
        # relativity?" via single-token overlap and returned a coffee order
        # instead of answering. Must not match on that generic key alone,
        # but a preference with an actually distinctive key ("coffee") must
        # keep matching exactly as before.
        atlas_app_store.set_preference(db_path, "general", "black coffee, no sugar")
        relativity = retrieval.retrieve_candidates(
            "Which scientist developed the theory of general relativity?", "personal", db_path, registry
        )
        assert all(item["candidate_id"] != "preference:general" for item in relativity["candidates"]), relativity
        still_matches_coffee = retrieval.retrieve_candidates("What coffee do I prefer?", "personal", db_path, registry)
        assert still_matches_coffee["candidates"][0]["candidate_id"] == "preference:coffee"

        # Preferences are personal-only and must not even be loaded in other
        # scopes. A matching operations query therefore remains no-match.
        operations_preference = retrieval.retrieve_candidates(
            "What coffee do I prefer?", "operations", db_path, registry
        )
        assert all(item["source_type"] != "preference" for item in operations_preference["candidates"])

        # The registry's personal bedside lamp can never cross into operations.
        lamp_wrong_scope = retrieval.retrieve_candidates(
            "What about the bedside lamp?", "operations", db_path, registry
        )
        assert all(item["candidate_id"] != "entity:homeassistant.light.bedside-table-lamp" for item in lamp_wrong_scope["candidates"])

        no_match = retrieval.retrieve_candidates(
            "quasar xylophone nebula", "personal", db_path, registry
        )
        assert no_match == {"status": "no_match", "candidates": [], "notices": []}
        assert retrieval.retrieve_candidates("", "personal", db_path, registry)["status"] == "no_match"

        semantic = retrieval.retrieve_candidates(
            "streaming collection", "operations", db_path, registry,
            semantic_matches=[{
                "candidate_id": f"note:{operations_note['id']}",
                "source_type": "note",
                "source_key": operations_note["id"],
                "context_scope": "operations",
                "updated_at": operations_note["updated_at"],
                "match_method": "semantic_cosine",
                "similarity": 0.82,
            }],
        )
        assert semantic["candidates"][0]["candidate_id"] == f"note:{operations_note['id']}"
        assert semantic["candidates"][0]["match_method"] == "semantic_cosine"
        assert semantic["candidates"][0]["text"] == ""

        try:
            retrieval.retrieve_candidates(
                "private detail", "personal", db_path, registry,
                semantic_matches=[{
                    "candidate_id": f"note:{admin_note['id']}", "source_type": "note",
                    "context_scope": "admin", "similarity": 0.99,
                }],
            )
        except ValueError as exc:
            assert str(exc) == "semantic_scope_mismatch"
        else:
            raise AssertionError("cross-scope semantic match accepted")

        current_candidate = {
            "candidate_id": "current_state:service_reachability",
            "subject_id": "current_state:service_reachability",
            "source_type": "structured_current_state",
            "context_scope": "operations",
            "title": "Current monitored-service reachability",
            "text": "0 of 10 monitored services are unreachable.",
            "source": "ATLAS Phase 2b signal observation",
            "updated_at": "2026-09-12T12:00:00-07:00",
            "lifecycle": "active",
            "freshness": "current",
            "match_method": "structured_current_state",
            "score": 950.0,
        }
        current = retrieval.retrieve_candidates(
            "Are services reachable now?", "operations", db_path, registry,
            current_state_candidates=[current_candidate],
        )
        assert current["candidates"][0]["candidate_id"] == "current_state:service_reachability"
        assert current["candidates"][0]["match_method"] == "structured_current_state"

        try:
            retrieval.retrieve_candidates(
                "Are services reachable now?", "personal", db_path, registry,
                current_state_candidates=[current_candidate],
            )
        except ValueError as exc:
            assert str(exc) == "current_state_scope_mismatch"
        else:
            raise AssertionError("cross-scope current-state candidate accepted")

        try:
            retrieval.retrieve_candidates("anything", "invalid", db_path, registry)
        except ValueError as exc:
            assert str(exc) == "invalid_context_scope"
        else:
            raise AssertionError("invalid scope accepted")

        # Retired evidence is never eligible to outrank an active candidate.
        reranked = retrieval.deterministic_rerank([
            {"candidate_id": "retired", "lifecycle": "retired", "match_method": "exact_id", "score": 9999},
            {"candidate_id": "active", "lifecycle": "active", "match_method": "fts5_bm25", "score": 1},
        ])
        assert [item["candidate_id"] for item in reranked] == ["active"]

        # Bounds are deterministic even with an excessive caller limit.
        bounded = retrieval.deterministic_rerank([
            {"candidate_id": f"c{i}", "lifecycle": "active", "match_method": "lexical_overlap", "score": i}
            for i in range(40)
        ], limit=999)
        assert len(bounded) == retrieval.MAX_CANDIDATES
        assert bounded[0]["candidate_id"] == "c39"

        metrics = retrieval.privacy_metrics(personal)
        serialized = json.dumps(metrics).lower()
        assert metrics["candidate_count"] >= 1
        expected_preference_fingerprint = retrieval.usage_fingerprint("preference:coffee", "personal")
        assert expected_preference_fingerprint in metrics["record_usage_fingerprints"]
        assert all(":" not in item for item in metrics["record_usage_fingerprints"])
        assert "dark roast" not in serialized
        assert personal["candidates"][0]["candidate_id"].lower() not in serialized
        assert "coffee" not in serialized

        # The only deployed consumer is a separate background audit. It
        # persists structure/latency only, never retrieved content or IDs.
        with mock.patch.object(server, "HYBRID_RETRIEVAL_SHADOW_ENABLED", True), mock.patch.object(
            server, "ENTITY_REGISTRY_PATH", ROOT / "atlas-entity-registry-2026-09-11.json"
        ), mock.patch.object(server, "_ENTITY_REGISTRY_CACHE", None), mock.patch.object(
            server, "APP_DB", db_path
        ):
            server._record_hybrid_retrieval_shadow({
                "question": "What coffee do I prefer?",
                "conversation_id": "r3-shadow-test",
                "context_scope": "personal",
            })
        event = next(
            item for item in atlas_app_store.list_audit_events(db_path, limit=20)
            if item["event_type"] == "hybrid_retrieval.shadow"
        )
        persisted = json.dumps(event["details"]).lower()
        assert event["details"]["status"] == "matched"
        assert event["details"]["candidate_count"] >= 1
        assert expected_preference_fingerprint in event["details"]["record_usage_fingerprints"]
        assert all(":" not in item for item in event["details"]["record_usage_fingerprints"])
        assert "dark roast" not in persisted
        assert "preference:coffee" not in persisted
        assert "what coffee" not in persisted

        with mock.patch.object(server, "HYBRID_RETRIEVAL_SHADOW_ENABLED", False), mock.patch.object(
            server.atlas_app_store, "add_audit_event"
        ) as audit_mock:
            server._record_hybrid_retrieval_shadow({"question": "secret"})
        audit_mock.assert_not_called()

        fake_semantic_match = [{
            "candidate_id": f"note:{operations_note['id']}",
            "source_type": "note",
            "source_key": operations_note["id"],
            "context_scope": "operations",
            "updated_at": operations_note["updated_at"],
            "match_method": "semantic_cosine",
            "similarity": 0.82,
        }]
        with mock.patch.object(server, "HYBRID_RETRIEVAL_SHADOW_ENABLED", True), mock.patch.object(
            server, "SEMANTIC_RETRIEVAL_SHADOW_ENABLED", True
        ), mock.patch.object(server, "ENTITY_REGISTRY_PATH", ROOT / "atlas-entity-registry-2026-09-11.json"), mock.patch.object(
            server, "_ENTITY_REGISTRY_CACHE", None
        ), mock.patch.object(server, "APP_DB", db_path), mock.patch.object(
            server.atlas_semantic_index, "authorized_records", return_value=[]
        ), mock.patch.object(
            server.atlas_semantic_index, "index_is_current", return_value=True
        ), mock.patch.object(
            server.atlas_semantic_index, "semantic_matches", return_value=fake_semantic_match
        ), mock.patch.object(server, "SEMANTIC_INDEX_PATH", pathlib.Path(temp_dir) / "semantic.db"):
            server._record_hybrid_retrieval_shadow({
                "question": "Which service streams the collection?",
                "conversation_id": "r3-semantic-shadow-test",
                "context_scope": "operations",
            })
        semantic_event = next(
            item for item in atlas_app_store.list_audit_events(db_path, limit=20)
            if item["event_type"] == "hybrid_retrieval.shadow"
            and item["details"].get("conversation_id") == "r3-semantic-shadow-test"
        )
        assert semantic_event["details"]["semantic_status"] == "completed"
        assert semantic_event["details"]["semantic_match_count"] == 1
        assert "semantic_cosine" in semantic_event["details"]["match_methods"]
        assert semantic_event["details"]["record_usage_fingerprints"] == [
            retrieval.usage_fingerprint(f"note:{operations_note['id']}", "operations")
        ]
        semantic_persisted = json.dumps(semantic_event["details"]).lower()
        assert operations_note["id"].lower() not in semantic_persisted
        assert "streams the collection" not in semantic_persisted

        with mock.patch.object(server, "HYBRID_RETRIEVAL_SHADOW_ENABLED", True), mock.patch.object(
            server, "SEMANTIC_RETRIEVAL_SHADOW_ENABLED", False
        ), mock.patch.object(server, "CURRENT_STATE_RETRIEVAL_SHADOW_ENABLED", True), mock.patch.object(
            server, "ENTITY_REGISTRY_PATH", ROOT / "atlas-entity-registry-2026-09-11.json"
        ), mock.patch.object(server, "_ENTITY_REGISTRY_CACHE", None), mock.patch.object(
            server, "APP_DB", db_path
        ), mock.patch.object(
            server.atlas_current_state_retrieval,
            "retrieve_current_state",
            return_value={"status": "matched", "candidates": [current_candidate], "notices": []},
        ):
            server._record_hybrid_retrieval_shadow({
                "question": "Are services reachable now?",
                "conversation_id": "r3-current-state-shadow-test",
                "context_scope": "operations",
            })
        current_event = next(
            item for item in atlas_app_store.list_audit_events(db_path, limit=30)
            if item["event_type"] == "hybrid_retrieval.shadow"
            and item["details"].get("conversation_id") == "r3-current-state-shadow-test"
        )
        assert current_event["details"]["current_state_status"] == "matched"
        assert current_event["details"]["current_state_candidate_count"] == 1
        assert "structured_current_state" in current_event["details"]["match_methods"]
        current_persisted = json.dumps(current_event["details"]).lower()
        assert "0 of 10" not in current_persisted
        assert "service_reachability" not in current_persisted

    print("PASS: R3 scope-first entity/note/preference retrieval, deterministic ranking, no-match, and privacy metrics")


if __name__ == "__main__":
    main()
