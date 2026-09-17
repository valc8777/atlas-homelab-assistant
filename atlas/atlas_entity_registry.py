#!/usr/bin/env python3
"""Canonical entity registry and resolver for ATLAS R2.

Read-only reference data: canonical IDs and owner/source-approved aliases
for homelab entities (VMs, hosts, services, Home Assistant devices). This
module has no write authority and never executes a capability -- it only
maps a surface phrase to a canonical entity ID (or reports "ambiguous" /
"no_match" / "retired") so downstream capabilities can stop re-parsing
free text themselves. Aliases are owner- or source-approved records, not
model-created truth: this module never invents or scores a fuzzy alias
match, it only compares against records already in the registry.
"""

from __future__ import annotations

import re

ENTITY_TYPES = {"virtual_machine", "host", "service", "home_entity"}
SCOPES = {"personal", "operations", "admin"}
STATUSES = {"active", "renamed", "retired", "ambiguous"}

# Defense in depth: the registry stores identifiers and display names only.
# Reject anything that looks like a credential value at validation time so a
# bad seed record cannot smuggle a secret into recall-adjacent reference data.
_FORBIDDEN_VALUE_PATTERN = re.compile(r"(token|password|secret|api[_-]?key|credential)", re.IGNORECASE)

_PUNCT_RE = re.compile(r"[^a-z0-9]+")
_LETTER_DIGIT_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=[0-9])|(?<=[0-9])(?=[a-z])")


def normalize_alias(text: str) -> str:
    """Fold an alias/surface string to a comparison-safe canonical form.

    Lowercases, inserts a boundary between a letter and a digit that are
    directly adjacent (so "vm202" and "VM 202" normalize the same way),
    then collapses any remaining punctuation/whitespace run to one space.
    """
    lowered = text.lower()
    spaced = _LETTER_DIGIT_BOUNDARY_RE.sub(" ", lowered)
    return _PUNCT_RE.sub(" ", spaced).strip()


def validate_entity(record: dict) -> dict:
    """Validate one canonical-entity record against the R2 schema.

    Raises ValueError with a short machine-readable reason on any violation.
    """
    if not isinstance(record, dict):
        raise ValueError("invalid_entity_record")
    entity_id = record.get("entity_id")
    entity_type = record.get("entity_type")
    canonical_name = record.get("canonical_name")
    aliases = record.get("aliases")
    context_scope = record.get("context_scope")
    source = record.get("source")
    status = record.get("status")
    updated_at = record.get("updated_at")

    if not isinstance(entity_id, str) or not entity_id:
        raise ValueError("invalid_entity_id")
    if entity_type not in ENTITY_TYPES:
        raise ValueError("invalid_entity_type")
    if not isinstance(canonical_name, str) or not canonical_name:
        raise ValueError("invalid_canonical_name")
    if not isinstance(aliases, list) or not all(isinstance(alias, str) and alias for alias in aliases):
        raise ValueError("invalid_aliases")
    if context_scope not in SCOPES:
        raise ValueError("invalid_context_scope")
    if not isinstance(source, str) or not source:
        raise ValueError("invalid_source")
    if status not in STATUSES:
        raise ValueError("invalid_status")
    if not isinstance(updated_at, str) or not updated_at:
        raise ValueError("invalid_updated_at")

    for field_name, value in (("entity_id", entity_id), ("canonical_name", canonical_name), ("source", source)):
        if _FORBIDDEN_VALUE_PATTERN.search(value):
            raise ValueError(f"disallowed_value_in_{field_name}")
    for alias in aliases:
        if _FORBIDDEN_VALUE_PATTERN.search(alias):
            raise ValueError("disallowed_value_in_alias")

    return {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "canonical_name": canonical_name,
        "aliases": list(aliases),
        "context_scope": context_scope,
        "source": source,
        "status": status,
        "updated_at": updated_at,
    }


def validate_registry(records: list) -> list:
    """Validate a full registry, rejecting duplicate entity IDs."""
    if not isinstance(records, list):
        raise ValueError("invalid_registry")
    validated = [validate_entity(record) for record in records]
    seen_ids: set[str] = set()
    for entity in validated:
        if entity["entity_id"] in seen_ids:
            raise ValueError(f"duplicate_entity_id:{entity['entity_id']}")
        seen_ids.add(entity["entity_id"])
    return validated


def _resolve_hits(hits: list[dict], resolution_method: str) -> dict:
    distinct_ids = {entity["entity_id"] for entity in hits}
    if len(distinct_ids) > 1:
        return {
            "status": "ambiguous",
            "resolution": resolution_method,
            "candidates": sorted(distinct_ids),
        }
    entity = hits[0]
    if entity["status"] == "retired":
        return {
            "status": "retired",
            "resolution": resolution_method,
            "entity_id": entity["entity_id"],
            "canonical_name": entity["canonical_name"],
        }
    if entity["status"] == "ambiguous":
        return {
            "status": "ambiguous",
            "resolution": resolution_method,
            "candidates": [entity["entity_id"]],
        }
    return {
        "status": "resolved",
        "resolution": resolution_method,
        "entity_id": entity["entity_id"],
        "entity_type": entity["entity_type"],
        "canonical_name": entity["canonical_name"],
        "context_scope": entity["context_scope"],
        "lifecycle": entity["status"],
    }


def resolve(surface: str, context_scope: str, registry: list[dict]) -> dict:
    """Resolve a surface phrase to a canonical entity within one scope.

    Priority order: exact canonical entity_id, then exact alias (or exact
    canonical_name) string, then normalized alias. A collision at any tier
    -- the same surface matching more than one distinct entity -- is
    reported as ambiguous rather than guessed. Entities are only ever
    considered within the caller's own context_scope: this function does
    not implement any cross-scope fallback or admin override.
    """
    if context_scope not in SCOPES:
        raise ValueError("invalid_context_scope")
    if not isinstance(surface, str) or not surface.strip():
        return {"status": "no_match", "candidates": []}

    scoped = [entity for entity in registry if entity["context_scope"] == context_scope]

    exact_id_hits = [entity for entity in scoped if entity["entity_id"] == surface]
    if exact_id_hits:
        return _resolve_hits(exact_id_hits, "exact_id")

    exact_alias_hits = [
        entity for entity in scoped
        if surface in entity["aliases"] or surface == entity["canonical_name"]
    ]
    if exact_alias_hits:
        return _resolve_hits(exact_alias_hits, "exact_alias")

    normalized_surface = normalize_alias(surface)
    normalized_hits = [
        entity for entity in scoped
        if normalized_surface and (
            normalized_surface == normalize_alias(entity["canonical_name"])
            or any(normalized_surface == normalize_alias(alias) for alias in entity["aliases"])
        )
    ]
    if normalized_hits:
        return _resolve_hits(normalized_hits, "normalized_alias")

    return {"status": "no_match", "candidates": []}


def load_registry_file(path) -> list[dict]:
    """Load and validate a registry JSON file (a list of entity records)."""
    import json
    import pathlib

    data = json.loads(pathlib.Path(path).read_text())
    records = data["entities"] if isinstance(data, dict) and "entities" in data else data
    return validate_registry(records)
