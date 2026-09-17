#!/usr/bin/env python3
"""Typed, read-only utterance parsing primitives for ATLAS R0/R1/R2.

This module does not execute capabilities or write memory. It converts an
already server-validated orchestration decision into a bounded intent skeleton
and adds deterministic exact slots that are safe to measure in shadow mode.

Optionally, when an R2 canonical entity registry is supplied, exact-pattern
entities (VM IDs, weather locations) are also checked against it so shadow
telemetry can report a real resolution method (e.g. `exact_alias`) instead of
only the regex-level tag. This is purely additive: with no registry supplied
(the existing deployed call path), behavior is byte-identical to before R2.
"""

from __future__ import annotations

import re

import atlas_entity_registry


SCOPES = {"personal", "operations", "admin"}
MAX_INTENTS = 4
MAX_ENTITIES = 12
MAX_SLOTS_PER_INTENT = 12

OPERATION_BY_CAPABILITY = {
    "answer.general": "answer",
    "math.calculate": "calculate",
    "units.convert": "convert",
    "datetime.resolve": "resolve_time",
    "weather.forecast": "retrieve",
    "public.reference": "retrieve",
    "public.search": "retrieve",
    "homelab.knowledge": "recall",
    "memory.recall": "recall",
    "reminders.manage": "propose_action",
    "tasks.manage": "propose_action",
    "shopping.manage": "propose_action",
    "notes.manage": "propose_action",
}

NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?!\w|\.\d)")
RELATIVE_TIME_RE = re.compile(
    r"\b(?:today|tomorrow|yesterday|tonight|next\s+(?:week|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"in\s+\d+\s+(?:minutes?|hours?|days?|weeks?|months?))\b",
    flags=re.IGNORECASE,
)
UNIT_RE = re.compile(
    r"\b(?:kilometers?|kilometres?|miles?|meters?|metres?|feet|foot|inches?|centimeters?|centimetres?|"
    r"kilograms?|pounds?|ounces?|liters?|litres?|gallons?|celsius|fahrenheit|minutes?|hours?|days?|weeks?)\b",
    flags=re.IGNORECASE,
)
VM_ID_RE = re.compile(r"\bvm\s*(?:id\s*)?(\d{1,6})\b", flags=re.IGNORECASE)
WEATHER_LOCATION_RE = re.compile(
    r"\b(?:weather|forecast|temperature|conditions)\s+(?:for|in|at)\s+([A-Za-z][A-Za-z .'-]{1,80}?)(?=\s+(?:today|tomorrow|tonight|next\b)|[,.?!]|$)",
    flags=re.IGNORECASE,
)


def _resolve_canonical(surface: str, context_scope: str, registry: list[dict] | None) -> dict:
    """Best-effort R2 canonical lookup. Returns {} unless cleanly resolved.

    Never attaches a canonical_id on "ambiguous" or "no_match" -- an entity
    without a clean registry match keeps exactly its R1 regex-level tag,
    unchanged from before R2 existed.
    """
    if not registry:
        return {}
    result = atlas_entity_registry.resolve(surface, context_scope, registry)
    if result["status"] == "resolved":
        return {"canonical_id": result["entity_id"], "canonical_resolution": result["resolution"]}
    return {}


def _exact_slots(
    text: str, context_scope: str = "personal", registry: list[dict] | None = None
) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
    slots: dict[str, list[str]] = {}
    entities: list[dict[str, str]] = []
    numbers = NUMBER_RE.findall(text)
    if numbers:
        slots["number"] = numbers[:8]
    units = [match.group(0).lower() for match in UNIT_RE.finditer(text)]
    if units:
        slots["unit"] = list(dict.fromkeys(units))[:4]
    times = [match.group(0).lower() for match in RELATIVE_TIME_RE.finditer(text)]
    if times:
        slots["relative_time"] = list(dict.fromkeys(times))[:4]
    for match in VM_ID_RE.finditer(text):
        entity = {"surface": match.group(0), "type": "virtual_machine", "resolution": "exact_id"}
        entity.update(_resolve_canonical(entity["surface"], context_scope, registry))
        entities.append(entity)
    for match in WEATHER_LOCATION_RE.finditer(text):
        surface = match.group(1).strip()
        entity = {"surface": surface, "type": "location", "resolution": "syntax_exact"}
        entity.update(_resolve_canonical(surface, context_scope, registry))
        entities.append(entity)
    return slots, entities[:MAX_ENTITIES]


def parse_validated_decision(
    question: str, decision: dict, context_scope: str, registry: list[dict] | None = None
) -> dict:
    """Build an untrusted parse proposal from a validated planner decision.

    `registry` is an optional R2 canonical entity registry (see
    atlas_entity_registry.load_registry_file). Omitting it (the existing
    deployed call path) leaves entity resolution exactly as R1 shipped it.
    """
    steps = decision.get("steps") if isinstance(decision.get("steps"), list) else []
    if steps:
        sources = [(step.get("capability"), step.get("request") or "") for step in steps]
    else:
        capabilities = decision.get("capabilities") or []
        sources = [(capability, question) for capability in capabilities[:MAX_INTENTS]]
        if not sources and decision.get("mode") in {"direct", "clarify", "refuse"}:
            sources = [(None, question)]
    intents = []
    entities = []
    for index, (capability, text) in enumerate(sources, start=1):
        slots, found_entities = _exact_slots(text, context_scope, registry)
        entities.extend(found_entities)
        intents.append({
            "intent_id": f"i{index}",
            "operation": OPERATION_BY_CAPABILITY.get(capability, decision.get("mode") or "unknown"),
            "capability": capability,
            "slots": slots,
            "missing_slots": [],
            "confidence": decision.get("confidence"),
            "method": "validated_plan_plus_exact_parser",
        })
    return {
        "context_scope": context_scope,
        "intents": intents,
        "entities": entities[:MAX_ENTITIES],
        "references": [],
        "temporal": [],
        "ambiguities": [],
    }


def validate_parsed_utterance(payload: dict, context_scope: str, allowed_capabilities: set[str]) -> dict:
    """Validate and normalize a parse proposal without granting authority."""
    if context_scope not in SCOPES or not isinstance(payload, dict):
        raise ValueError("invalid_parse")
    if payload.get("context_scope") != context_scope:
        raise ValueError("scope_mismatch")
    raw_intents = payload.get("intents")
    if not isinstance(raw_intents, list) or not 1 <= len(raw_intents) <= MAX_INTENTS:
        raise ValueError("invalid_intents")
    intents = []
    for index, intent in enumerate(raw_intents, start=1):
        if not isinstance(intent, dict):
            raise ValueError("invalid_intent")
        capability = intent.get("capability")
        if capability is not None and capability not in allowed_capabilities:
            raise ValueError("unknown_or_disallowed_capability")
        operation = intent.get("operation")
        slots = intent.get("slots")
        if not isinstance(operation, str) or not operation or not isinstance(slots, dict) or len(slots) > MAX_SLOTS_PER_INTENT:
            raise ValueError("invalid_intent")
        normalized_slots = {}
        for name, values in slots.items():
            if not isinstance(name, str) or not isinstance(values, list) or len(values) > 8 or not all(isinstance(value, str) for value in values):
                raise ValueError("invalid_slots")
            normalized_slots[name] = values
        intents.append({
            "intent_id": f"i{index}",
            "operation": operation,
            "capability": capability,
            "slots": normalized_slots,
            "missing_slots": [],
            "confidence": intent.get("confidence") if isinstance(intent.get("confidence"), (int, float)) and not isinstance(intent.get("confidence"), bool) else None,
            "method": "validated_plan_plus_exact_parser",
        })
    raw_entities = payload.get("entities") or []
    if not isinstance(raw_entities, list) or len(raw_entities) > MAX_ENTITIES:
        raise ValueError("invalid_entities")
    entities = []
    for entity in raw_entities:
        if not isinstance(entity, dict) or not all(isinstance(entity.get(key), str) and entity.get(key) for key in ("surface", "type", "resolution")):
            raise ValueError("invalid_entity")
        normalized_entity = {key: entity[key] for key in ("surface", "type", "resolution")}
        # Optional R2 canonical-resolution fields: only carried through when
        # both are present and non-empty strings, never invented here.
        canonical_id = entity.get("canonical_id")
        canonical_resolution = entity.get("canonical_resolution")
        if isinstance(canonical_id, str) and canonical_id and isinstance(canonical_resolution, str) and canonical_resolution:
            normalized_entity["canonical_id"] = canonical_id
            normalized_entity["canonical_resolution"] = canonical_resolution
        entities.append(normalized_entity)
    return {
        "context_scope": context_scope,
        "intents": intents,
        "entities": entities,
        "references": [],
        "temporal": [],
        "ambiguities": [],
    }


def privacy_metrics(parsed: dict) -> dict:
    """Return audit-safe structure metadata; never values or source text.

    R2 note: `canonical_id` values (which specific VM/host/service an entity
    resolved to) are deliberately never included here, only aggregate counts
    and the resolution *method* names -- the same privacy boundary R0/R1
    already applies to entity *type* names.
    """
    slot_types = sorted({name for intent in parsed["intents"] for name in intent["slots"]})
    canonical_resolutions = sorted({
        entity["canonical_resolution"] for entity in parsed["entities"] if entity.get("canonical_id")
    })
    return {
        "status": "completed",
        "intent_count": len(parsed["intents"]),
        "operations": [intent["operation"] for intent in parsed["intents"]],
        "capabilities": [intent["capability"] for intent in parsed["intents"] if intent["capability"]],
        "slot_types": slot_types,
        "entity_count": len(parsed["entities"]),
        "entity_types": sorted({entity["type"] for entity in parsed["entities"]}),
        "resolution_methods": sorted({entity["resolution"] for entity in parsed["entities"]}),
        "canonical_resolved_entity_count": sum(1 for entity in parsed["entities"] if entity.get("canonical_id")),
        "canonical_resolution_methods": canonical_resolutions,
        "ambiguity_count": len(parsed["ambiguities"]),
        "parser_version": "atlas-utterance-parser-r1-shadow-1",
    }
