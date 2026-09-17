#!/usr/bin/env python3
"""ATLAS_RECALL_AND_PARSING_PLAN.md Phase R2: the canonical entity registry
becomes a real, answer-affecting consumer for the first time (previously
shadow-only, per the phase's own "deliberately not done" note).
`enhance_homelab_query_with_entities()` resolves a homelab question's
registry-known entity mentions and appends the canonical name(s) to the
retrieval query sent to Open WebUI, so a nickname the owner actually uses
("qbit", "the gaming VM", "docker heavy") matches documentation written
under the official name. Gated by ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED,
abstains (leaves the query unchanged) on ambiguous mentions, no match, or
the registry being unavailable -- same abstain-safe principle DEF-053
already established for narrative date math.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
from unittest import mock

AI_DIR = pathlib.Path(__file__).resolve().parent
ROOT = AI_DIR.parents[1]
sys.path.insert(0, str(AI_DIR))

import atlas_orchestrator_v2

_SPEC = importlib.util.spec_from_file_location("atlas_runner_entity_homelab_query", AI_DIR / "run-atlas-readonly.py")
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


def _reset_orchestrator_cache() -> None:
    atlas_orchestrator_v2._ENTITY_REGISTRY_DISCOVERY_CACHE = None


def _reset_cache() -> None:
    runner._HOMELAB_ENTITY_REGISTRY_CACHE = None


def main() -> None:
    registry_path = ROOT / "atlas-entity-registry-2026-09-11.json"
    assert registry_path.exists(), "real registry fixture must exist for this test"

    # Flag off (the deployed default until this is explicitly enabled) --
    # must be a complete no-op, matching every other gated rollout in this
    # project (R3's answer canary, R6's dry-run-first design).
    _reset_cache()
    with mock.patch.dict("os.environ", {"ATLAS_ENTITY_REGISTRY_PATH": str(registry_path)}, clear=False):
        os_environ_without_flag = dict(**__import__("os").environ)
        os_environ_without_flag.pop("ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED", None)
        with mock.patch.dict("os.environ", os_environ_without_flag, clear=True):
            unchanged = runner.enhance_homelab_query_with_entities("is qbit running ok", "operations")
    assert unchanged == "is qbit running ok", unchanged

    # Flag on: a real, distinctive nickname resolves and gets appended.
    _reset_cache()
    with mock.patch.dict(
        "os.environ",
        {"ATLAS_ENTITY_REGISTRY_PATH": str(registry_path), "ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED": "true"},
    ):
        enhanced = runner.enhance_homelab_query_with_entities("is qbit running ok", "operations")
    assert enhanced == "is qbit running ok (qBittorrent)", enhanced

    # A multi-word nickname across a real service with a compound name.
    _reset_cache()
    with mock.patch.dict(
        "os.environ",
        {"ATLAS_ENTITY_REGISTRY_PATH": str(registry_path), "ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED": "true"},
    ):
        enhanced = runner.enhance_homelab_query_with_entities("what's running on docker heavy", "operations")
    assert enhanced == "what's running on docker heavy (ai-stack)", enhanced

    # Ambiguous mention ("pve" resolves to both a service and the host) must
    # abstain, not guess -- same principle DEF-053 already established.
    _reset_cache()
    with mock.patch.dict(
        "os.environ",
        {"ATLAS_ENTITY_REGISTRY_PATH": str(registry_path), "ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED": "true"},
    ):
        ambiguous = runner.enhance_homelab_query_with_entities("is pve up", "operations")
    assert ambiguous == "is pve up", ambiguous

    # No entity mentioned at all.
    _reset_cache()
    with mock.patch.dict(
        "os.environ",
        {"ATLAS_ENTITY_REGISTRY_PATH": str(registry_path), "ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED": "true"},
    ):
        no_match = runner.enhance_homelab_query_with_entities("what is the weather like", "operations")
    assert no_match == "what is the weather like", no_match

    # Scope isolation: the personal-scope bedside lamp must not leak into an
    # operations-scoped query, matching R2's zero-cross-scope-leakage gate.
    _reset_cache()
    with mock.patch.dict(
        "os.environ",
        {"ATLAS_ENTITY_REGISTRY_PATH": str(registry_path), "ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED": "true"},
    ):
        wrong_scope = runner.enhance_homelab_query_with_entities("turn on the bedside lamp", "operations")
    assert wrong_scope == "turn on the bedside lamp", wrong_scope

    # Missing/invalid registry file must degrade to a no-op, never raise.
    _reset_cache()
    with tempfile.TemporaryDirectory() as temp_dir:
        missing_path = pathlib.Path(temp_dir) / "does-not-exist.json"
        with mock.patch.dict(
            "os.environ",
            {"ATLAS_ENTITY_REGISTRY_PATH": str(missing_path), "ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED": "true"},
        ):
            safe = runner.enhance_homelab_query_with_entities("is qbit running ok", "operations")
        assert safe == "is qbit running ok", safe

        invalid_path = pathlib.Path(temp_dir) / "invalid.json"
        invalid_path.write_text("not valid json", encoding="utf-8")
        _reset_cache()
        with mock.patch.dict(
            "os.environ",
            {"ATLAS_ENTITY_REGISTRY_PATH": str(invalid_path), "ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED": "true"},
        ):
            safe_invalid = runner.enhance_homelab_query_with_entities("is qbit running ok", "operations")
        assert safe_invalid == "is qbit running ok", safe_invalid

    # A retired entity in a fixture registry must not be surfaced (mirrors
    # atlas_hybrid_retrieval's own lifecycle handling) -- built as a small
    # synthetic fixture since the real registry currently has zero retired
    # entities.
    fixture_registry = [
        {
            "entity_id": "homelab.service.example-retired",
            "entity_type": "service",
            "canonical_name": "Example Retired Service",
            "aliases": ["example retired service", "old service"],
            "context_scope": "operations",
            "source": "test-fixture",
            "status": "retired",
            "updated_at": "2026-09-11T00:00:00+00:00",
        }
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        fixture_path = pathlib.Path(temp_dir) / "fixture-registry.json"
        fixture_path.write_text(json.dumps(fixture_registry), encoding="utf-8")
        _reset_cache()
        with mock.patch.dict(
            "os.environ",
            {"ATLAS_ENTITY_REGISTRY_PATH": str(fixture_path), "ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED": "true"},
        ):
            retired_query = runner.enhance_homelab_query_with_entities("is old service still up", "operations")
        assert retired_query == "is old service still up", retired_query

    # classify_question() routing: without this, a bare nickname alone often
    # never reaches run_homelab_chat() at all (confirmed live: "is qbit
    # running ok" fell through to general_reference before this fix), which
    # would make the query enhancement above moot for exactly the phrasings
    # it's meant to help most.
    _reset_cache()
    with mock.patch.dict(
        "os.environ",
        {"ATLAS_ENTITY_REGISTRY_PATH": str(registry_path), "ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED": "true"},
    ):
        assert runner.classify_question("is qbit running ok", "operations") == "homelab"
        assert runner.classify_question("whats on docker heavy", "operations") == "homelab"
        # Ambiguous ("pve") and no-match questions must not be swept in just
        # because the registry was consulted.
        assert runner.classify_question("is pve up", "operations") != "homelab"
        assert runner.classify_question("what is the weather", "operations") != "homelab"
        # Personal scope: the registry check itself is skipped entirely, so
        # a personal-scope mention of the one personal-scope entity (the
        # bedside lamp) is not redirected into homelab routing -- it's
        # already handled by the dedicated home_light/home_read routes.
        assert runner.classify_question("is qbit running ok", "personal") != "homelab"

    # Flag off: the classify_question() signal must also be a complete
    # no-op, not just the query-enhancement function.
    _reset_cache()
    with mock.patch.dict("os.environ", {"ATLAS_ENTITY_REGISTRY_PATH": str(registry_path)}, clear=False):
        os_environ_without_flag = dict(**__import__("os").environ)
        os_environ_without_flag.pop("ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED", None)
        with mock.patch.dict("os.environ", os_environ_without_flag, clear=True):
            assert runner.classify_question("is qbit running ok", "operations") != "homelab"

    # Native-planner discovery: without this, "docker heavy"/"pve"-shaped
    # questions never even offer homelab.knowledge as a tool option to the
    # model, so it cannot be picked regardless of the model's own judgment.
    # Confirmed live before this fix: "whats on docker heavy" and "is pve
    # up" both fell through to general_reference.
    _reset_orchestrator_cache()
    with mock.patch.dict(
        "os.environ",
        {"ATLAS_ENTITY_REGISTRY_PATH": str(registry_path), "ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED": "true"},
    ):
        shortlisted = {
            capability.name
            for capability in atlas_orchestrator_v2.shortlist_capabilities("whats on docker heavy", "operations")
        }
        assert "homelab.knowledge" in shortlisted, shortlisted

        shortlisted_qbit = {
            capability.name
            for capability in atlas_orchestrator_v2.shortlist_capabilities("is qbit running ok", "operations")
        }
        assert "homelab.knowledge" in shortlisted_qbit, shortlisted_qbit

        # Ambiguous ("pve") must not be swept in just because the registry
        # was consulted -- same abstain principle as everywhere else here.
        shortlisted_ambiguous = {
            capability.name
            for capability in atlas_orchestrator_v2.shortlist_capabilities("is pve up", "operations")
        }
        assert "homelab.knowledge" not in shortlisted_ambiguous, shortlisted_ambiguous

    # Flag off: this discovery signal must also be a complete no-op. Uses
    # "qbit" rather than "docker heavy" here -- the bare word "docker" is
    # already one of this capability's own pre-existing DISCOVERY_TERMS, so
    # that phrase would pass regardless of this fix and wouldn't isolate it.
    _reset_orchestrator_cache()
    with mock.patch.dict("os.environ", {"ATLAS_ENTITY_REGISTRY_PATH": str(registry_path)}, clear=False):
        os_environ_without_flag = dict(**__import__("os").environ)
        os_environ_without_flag.pop("ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED", None)
        with mock.patch.dict("os.environ", os_environ_without_flag, clear=True):
            shortlisted_off = {
                capability.name
                for capability in atlas_orchestrator_v2.shortlist_capabilities("is qbit running ok", "operations")
            }
    assert "homelab.knowledge" not in shortlisted_off, shortlisted_off
    _reset_orchestrator_cache()

    _reset_cache()
    print("PASS: R2 entity registry homelab-query enhancement (flag gate, resolution, ambiguity abstain, scope isolation, missing/invalid registry, retired-entity exclusion, classify_question routing signal, native-planner discovery)")


if __name__ == "__main__":
    main()
