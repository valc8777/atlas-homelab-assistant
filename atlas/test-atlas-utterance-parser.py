#!/usr/bin/env python3
"""R0/R1/R2 typed utterance parser contract and privacy tests."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atlas_app_store
import atlas_shortcuts_server as server
import atlas_utterance_parser as parser


def main() -> None:
    decision = {
        "mode": "deep",
        "context_scope": "personal",
        "capabilities": ["weather.forecast", "math.calculate"],
        "steps": [
            {"capability": "weather.forecast", "request": "Weather in Sacramento tomorrow."},
            {"capability": "math.calculate", "request": "Calculate 17 times 23."},
        ],
        "confidence": 0.91,
    }
    proposal = parser.parse_validated_decision("private compound text", decision, "personal")
    parsed = parser.validate_parsed_utterance(
        proposal, "personal", {"weather.forecast", "math.calculate"}
    )
    assert [item["capability"] for item in parsed["intents"]] == ["weather.forecast", "math.calculate"]
    assert parsed["intents"][0]["slots"]["relative_time"] == ["tomorrow"]
    assert parsed["intents"][1]["slots"]["number"] == ["17", "23"]
    assert parsed["entities"][0]["type"] == "location"

    metrics = parser.privacy_metrics(parsed)
    serialized = json.dumps(metrics).lower()
    assert metrics["intent_count"] == 2
    assert metrics["slot_types"] == ["number", "relative_time"]
    assert "sacramento" not in serialized
    assert "17" not in serialized
    assert "private compound text" not in serialized

    bad_scope = dict(proposal, context_scope="operations")
    try:
        parser.validate_parsed_utterance(bad_scope, "personal", {"weather.forecast", "math.calculate"})
    except ValueError as exc:
        assert str(exc) == "scope_mismatch"
    else:
        raise AssertionError("cross-scope parse accepted")

    bad_capability = dict(proposal)
    bad_capability["intents"] = [dict(proposal["intents"][0], capability="notes.manage")]
    try:
        parser.validate_parsed_utterance(bad_capability, "personal", {"weather.forecast"})
    except ValueError as exc:
        assert str(exc) == "unknown_or_disallowed_capability"
    else:
        raise AssertionError("unallowed capability accepted")

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = pathlib.Path(temp_dir) / "atlas.db"
        atlas_app_store.init_db(db_path)
        job = {
            "question": "Weather in Sacramento tomorrow and calculate 17 times 23.",
            "conversation_id": "conversation-test",
            "context_scope": "personal",
        }
        with mock.patch.object(server, "TYPED_UTTERANCE_SHADOW_ENABLED", True), mock.patch.object(
            server, "APP_DB", db_path
        ):
            server._record_typed_utterance_shadow(job, decision)
        events = atlas_app_store.list_audit_events(db_path, limit=10)
        event = events[0]
        assert event["event_type"] == "utterance_parser.shadow"
        # Check only the typed metadata (`details`, i.e. privacy_metrics'
        # own output), not the whole row: `id` is random hex and `created_at`
        # is a live timestamp, either of which can coincidentally contain a
        # digit substring like "17" with no privacy meaning whatsoever.
        persisted = json.dumps(event["details"]).lower()
        assert "sacramento" not in persisted
        assert "17" not in persisted
        assert "times 23" not in persisted
        assert event["details"]["intent_count"] == 2

    with mock.patch.object(server, "TYPED_UTTERANCE_SHADOW_ENABLED", False), mock.patch.object(
        server.atlas_app_store, "add_audit_event"
    ) as audit_mock:
        server._record_typed_utterance_shadow({"question": "secret"}, decision)
    audit_mock.assert_not_called()

    # R2: omitting `registry` (the existing deployed shadow call path) must
    # remain byte-identical to R1 -- no canonical_id/canonical_resolution
    # keys appear at all.
    assert "canonical_id" not in parsed["entities"][0]
    assert "canonical_resolution" not in parsed["entities"][0]
    assert metrics["canonical_resolved_entity_count"] == 0
    assert metrics["canonical_resolution_methods"] == []

    # R2: passing a registry enriches a clean match with a canonical_id,
    # but the canonical_id value itself must never reach privacy_metrics --
    # only the aggregate count and the resolution *method* name may.
    fixture_registry = [{
        "entity_id": "proxmox.vm.202",
        "entity_type": "virtual_machine",
        "canonical_name": "Windows",
        "aliases": ["gaming VM", "VM 202"],
        "context_scope": "operations",
        "source": "test-fixture",
        "status": "active",
        "updated_at": "2026-09-11T00:00:00Z",
    }]
    vm_decision = {
        "mode": "deep",
        "context_scope": "operations",
        "capabilities": ["homelab.knowledge"],
        "steps": [{"capability": "homelab.knowledge", "request": "Is VM 202 running?"}],
        "confidence": 0.9,
    }
    vm_proposal = parser.parse_validated_decision(
        "is vm 202 running", vm_decision, "operations", registry=fixture_registry
    )
    vm_parsed = parser.validate_parsed_utterance(vm_proposal, "operations", {"homelab.knowledge"})
    vm_entity = vm_parsed["entities"][0]
    assert vm_entity["canonical_id"] == "proxmox.vm.202"
    assert vm_entity["canonical_resolution"] == "exact_alias"

    vm_metrics = parser.privacy_metrics(vm_parsed)
    vm_serialized = json.dumps(vm_metrics).lower()
    assert vm_metrics["canonical_resolved_entity_count"] == 1
    assert vm_metrics["canonical_resolution_methods"] == ["exact_alias"]
    assert "proxmox.vm.202" not in vm_serialized
    assert "windows" not in vm_serialized

    # R2's first deployed consumer is the background-only shadow path. With
    # its independent flag on, the real registry loader is used and the
    # persisted event contains aggregate resolution evidence but no ID/value.
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = pathlib.Path(temp_dir)
        db_path = temp_path / "atlas.db"
        registry_path = temp_path / "registry.json"
        atlas_app_store.init_db(db_path)
        registry_path.write_text(json.dumps({"entities": fixture_registry}))
        job = {
            "question": "Is VM 202 running?",
            "conversation_id": "conversation-r2-shadow",
            "context_scope": "operations",
        }
        with mock.patch.object(server, "TYPED_UTTERANCE_SHADOW_ENABLED", True), mock.patch.object(
            server, "ENTITY_REGISTRY_SHADOW_ENABLED", True
        ), mock.patch.object(server, "ENTITY_REGISTRY_PATH", registry_path), mock.patch.object(
            server, "_ENTITY_REGISTRY_CACHE", None
        ), mock.patch.object(server, "APP_DB", db_path):
            server._record_typed_utterance_shadow(job, vm_decision)
        event = atlas_app_store.list_audit_events(db_path, limit=1)[0]
        assert event["details"]["status"] == "completed"
        assert event["details"]["canonical_resolved_entity_count"] == 1
        assert event["details"]["canonical_resolution_methods"] == ["exact_alias"]
        persisted = json.dumps(event["details"]).lower()
        assert "proxmox.vm.202" not in persisted
        assert "windows" not in persisted

    # An entity the registry cannot cleanly resolve keeps its plain R1 tag --
    # no canonical fields invented from an ambiguous or absent match.
    unresolved_decision = dict(vm_decision, steps=[
        {"capability": "homelab.knowledge", "request": "Is VM 999 running?"}
    ])
    unresolved_proposal = parser.parse_validated_decision(
        "is vm 999 running", unresolved_decision, "operations", registry=fixture_registry
    )
    unresolved_entity = unresolved_proposal["entities"][0]
    assert "canonical_id" not in unresolved_entity
    assert unresolved_entity["resolution"] == "exact_id"  # unchanged R1 regex-level tag

    print("PASS: typed utterance validation, exact slots, scope/capability gates, R2 canonical enrichment, and privacy-minimized shadow audit")


if __name__ == "__main__":
    main()
