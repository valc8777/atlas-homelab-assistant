#!/usr/bin/env python3
"""Fixture tests for the ATLAS R2 canonical entity registry and resolver.

Covers the R2 gate directly: 100% on exact IDs and unique aliases, 100%
clarification on intentionally colliding aliases, and zero entity leakage
between context scopes. Also exercises the real generated registry (not
just synthetic fixtures) so the resolver's collision handling is proven
against real seed data, not only hand-built test cases.
"""

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from atlas_entity_registry import normalize_alias, resolve, validate_entity, validate_registry

FAILURES = []


def check(name, condition):
    if not condition:
        FAILURES.append(name)
        print(f"FAIL: {name}")
    else:
        print(f"ok:   {name}")


BASE_ENTITY = {
    "entity_id": "homelab.service.example",
    "entity_type": "service",
    "canonical_name": "Example Service",
    "aliases": ["example", "example service"],
    "context_scope": "operations",
    "source": "test-fixture",
    "status": "active",
    "updated_at": "2026-09-11T00:00:00Z",
}


def entity(**overrides):
    record = dict(BASE_ENTITY)
    record.update(overrides)
    return record


def test_valid_entity_round_trips():
    validated = validate_entity(entity())
    check("valid entity round-trips unchanged", validated["entity_id"] == "homelab.service.example")


def test_schema_rejects_invalid_entity_type():
    try:
        validate_entity(entity(entity_type="widget"))
        check("rejects invalid entity_type", False)
    except ValueError as exc:
        check("rejects invalid entity_type", str(exc) == "invalid_entity_type")


def test_schema_rejects_invalid_scope():
    try:
        validate_entity(entity(context_scope="public"))
        check("rejects invalid context_scope", False)
    except ValueError as exc:
        check("rejects invalid context_scope", str(exc) == "invalid_context_scope")


def test_schema_rejects_invalid_status():
    try:
        validate_entity(entity(status="deleted"))
        check("rejects invalid status", False)
    except ValueError as exc:
        check("rejects invalid status", str(exc) == "invalid_status")


def test_schema_rejects_missing_aliases_list():
    try:
        validate_entity(entity(aliases="example"))
        check("rejects non-list aliases", False)
    except ValueError as exc:
        check("rejects non-list aliases", str(exc) == "invalid_aliases")


def test_schema_rejects_credential_looking_values():
    try:
        validate_entity(entity(aliases=["example", "api_token_value"]))
        check("rejects credential-looking alias", False)
    except ValueError as exc:
        check("rejects credential-looking alias", str(exc) == "disallowed_value_in_alias")


def test_registry_rejects_duplicate_ids():
    try:
        validate_registry([entity(), entity()])
        check("rejects duplicate entity_id", False)
    except ValueError as exc:
        check("rejects duplicate entity_id", str(exc).startswith("duplicate_entity_id"))


def test_normalize_alias_folds_punctuation_and_case():
    check(
        "normalize_alias folds punctuation and case",
        normalize_alias("VM-202") == normalize_alias("vm 202") == "vm 202",
    )


REGISTRY = [
    entity(
        entity_id="proxmox.vm.202",
        entity_type="virtual_machine",
        canonical_name="Windows",
        aliases=["gaming VM", "VM 202", "Windows guest"],
    ),
    entity(
        entity_id="proxmox.vm.201",
        entity_type="virtual_machine",
        canonical_name="ai-stack",
        aliases=["ai-stack", "VM 201"],
    ),
    entity(
        entity_id="homelab.service.retired-thing",
        canonical_name="Old Service",
        aliases=["old service"],
        status="retired",
    ),
    entity(
        entity_id="personal.reminder.example",
        canonical_name="Grocery List",
        aliases=["the list"],
        context_scope="personal",
    ),
]

# Two entities that deliberately share one alias, to prove collisions are
# reported as ambiguous rather than guessed toward whichever sorts first.
COLLIDING_REGISTRY = REGISTRY + [
    entity(entity_id="homelab.service.collider-a", canonical_name="Collider A", aliases=["shared name"]),
    entity(entity_id="homelab.service.collider-b", canonical_name="Collider B", aliases=["shared name"]),
]


def test_resolves_exact_canonical_id():
    result = resolve("proxmox.vm.202", "operations", REGISTRY)
    check("resolves exact canonical entity_id", result == {
        "status": "resolved",
        "resolution": "exact_id",
        "entity_id": "proxmox.vm.202",
        "entity_type": "virtual_machine",
        "canonical_name": "Windows",
        "context_scope": "operations",
        "lifecycle": "active",
    })


def test_resolves_exact_unique_alias():
    result = resolve("gaming VM", "operations", REGISTRY)
    check("resolves exact unique alias", result["status"] == "resolved" and result["entity_id"] == "proxmox.vm.202")


def test_resolves_normalized_alias_variant():
    result = resolve("vm202", "operations", REGISTRY)
    check("resolves normalized alias variant (vm202 -> VM 202)", result["status"] == "resolved"
          and result["entity_id"] == "proxmox.vm.202" and result["resolution"] == "normalized_alias")


def test_colliding_alias_is_ambiguous_not_guessed():
    result = resolve("shared name", "operations", COLLIDING_REGISTRY)
    check("colliding alias reports ambiguous, not a guess", result["status"] == "ambiguous"
          and set(result["candidates"]) == {"homelab.service.collider-a", "homelab.service.collider-b"})


def test_retired_entity_reports_retired_not_silent_no_match():
    result = resolve("old service", "operations", REGISTRY)
    check("retired entity reports retired status", result == {
        "status": "retired",
        "resolution": "exact_alias",
        "entity_id": "homelab.service.retired-thing",
        "canonical_name": "Old Service",
    })


def test_unknown_surface_is_no_match():
    result = resolve("something never seeded", "operations", REGISTRY)
    check("unknown surface is no_match", result == {"status": "no_match", "candidates": []})


def test_zero_leakage_operations_entity_invisible_in_personal_scope():
    result = resolve("VM 201", "personal", REGISTRY)
    check("operations entity invisible under personal scope", result["status"] == "no_match")


def test_zero_leakage_personal_entity_invisible_in_operations_scope():
    result = resolve("the list", "operations", REGISTRY)
    check("personal entity invisible under operations scope", result["status"] == "no_match")


def test_zero_leakage_admin_scope_has_no_implicit_visibility():
    result = resolve("VM 201", "admin", REGISTRY)
    check("operations entity invisible under admin scope (no implicit hierarchy)", result["status"] == "no_match")


def test_invalid_context_scope_raises():
    try:
        resolve("VM 201", "bogus", REGISTRY)
        check("resolve rejects invalid context_scope", False)
    except ValueError as exc:
        check("resolve rejects invalid context_scope", str(exc) == "invalid_context_scope")


def test_real_generated_registry_loads_and_validates():
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    seed_path = repo_root / "atlas-entity-registry-2026-09-11.json"
    if not seed_path.exists():
        subprocess.run([sys.executable, str(repo_root / "tools/ai/generate-atlas-entity-registry.py")], check=True)
    import json
    data = json.loads(seed_path.read_text())
    real_registry = validate_registry(data["entities"])
    check("real generated registry validates cleanly", len(real_registry) > 0)

    proxmox_ve_result = resolve("proxmox ve", "operations", real_registry)
    check(
        "real seed data: 'proxmox ve' collides across the Proxmox VE service "
        "entity and the zola-cerebro host entity, and is correctly ambiguous",
        proxmox_ve_result["status"] == "ambiguous"
        and set(proxmox_ve_result["candidates"]) == {"homelab.service.proxmox-ve", "proxmox.host.zola-cerebro"},
    )

    windows_result = resolve("gaming VM", "operations", real_registry)
    check("real seed data: 'gaming VM' resolves to VM 202 (Windows)",
          windows_result.get("entity_id") == "proxmox.vm.202")

    lamp_result = resolve("the bedside lamp", "personal", real_registry)
    check("real seed data: 'the bedside lamp' resolves under personal scope",
          lamp_result.get("entity_id") == "home_assistant.entity.bedside_table_lamp")
    lamp_wrong_scope = resolve("the bedside lamp", "operations", real_registry)
    check("real seed data: bedside lamp invisible under operations scope",
          lamp_wrong_scope["status"] == "no_match")


def run_all():
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
    print(f"\n{len(FAILURES)} failing" if FAILURES else "\nAll tests passed")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
