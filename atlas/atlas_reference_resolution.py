#!/usr/bin/env python3
"""Ephemeral, scope-exact reference and temporal resolution for ATLAS R4.

This module has no storage or execution authority. It resolves only exact
canonical entities and syntactically explicit weather locations from recent
same-conversation, same-scope user turns. Resolved text is returned to the
caller for ephemeral use and must never be written as durable memory.
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import atlas_entity_registry


SCOPES = frozenset({"personal", "operations", "admin"})
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
MAX_HISTORY_MESSAGES = 8
MAX_FOLLOWUP_WORDS = 12

_PRONOUN = re.compile(r"\b(?:it|that one|this one)\b", re.IGNORECASE)
_POSSESSIVE_PRONOUN = re.compile(r"\bits\b", re.IGNORECASE)
_THERE = re.compile(r"\bthere\b", re.IGNORECASE)
_WEATHER_LOCATION = re.compile(
    r"\b(?:weather|forecast|temperature|conditions)\s+(?:for|in|at)\s+"
    r"([A-Za-z][A-Za-z .'-]{1,80}?)(?=\s+(?:today|tomorrow|tonight|next\b)|[,.?!]|$)",
    re.IGNORECASE,
)
_WEATHER_WORD = re.compile(r"\b(?:weather|forecast|temperature|conditions|rain|snow)\b", re.IGNORECASE)
_OMITTED_WEATHER_SUBJECT = re.compile(
    r"^\s*(?:and\s+)?(?:what\s+about\s+)?(?:today|tomorrow|the day after tomorrow|"
    r"next\s+(?:week|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_AMBIGUOUS_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?!\d)")
# DEF-046: a self-contained structured-choice reasoning prompt (the
# "Choices: A; B; C\nAnswer with the choice..." template used by the
# BIG-bench-derived eval categories, e.g. date_understanding) already
# supplies every fact needed to answer -- any bare M/D-style date inside it
# is scaffolding for the reasoning, not a genuine request that needs its
# own format disambiguated. Found live via Track A3 Gate 1 (DEC-2026-09-16-29):
# every date_understanding case (100%, 0/20) was blocked with "Which date
# format did you mean?" before the question was ever answered, because
# these prompts are stuffed with multiple embedded M/D dates as reasoning
# context. Matches the identical exemption already used for the same
# template shape elsewhere in this codebase (classify_question()'s
# is_structured_reasoning_question()).
#
# DEF-051: the original pattern required "Choices:" be preceded by a literal
# newline or start-of-string. An overnight stress-test harness happened to
# collapse the template's newlines into spaces before sending it (its own
# bug, not a live production regression), and that alone was enough to
# defeat the exemption and drop date_understanding to 0/126 -- every other
# structured-choice task in the same run was unaffected only because they
# don't happen to contain M/D-shaped dates in their own text at all. A
# stray formatting difference should not be able to silently disable a
# safety-relevant exemption like this; widened to also accept "Choices:"
# right after normal sentence-ending punctuation (". "/"? "/"! "), which
# covers both the intended multi-line template and a single-line collapse
# of the same content, without loosening it into matching "choices:"
# appearing mid-sentence in ordinary prose.
_STRUCTURED_CHOICE_PROMPT_RE = re.compile(r"(?:^|\n|[.?!]\s)choices:\s*\S+", re.IGNORECASE)
_STRUCTURED_ANSWER_INSTRUCTION_RE = re.compile(r"answer\s+with\s+(?:the\s+)?choice\b", re.IGNORECASE)


def _is_self_contained_structured_prompt(question: str) -> bool:
    return bool(
        _STRUCTURED_CHOICE_PROMPT_RE.search(question)
        and _STRUCTURED_ANSWER_INSTRUCTION_RE.search(question)
    )
_RELATIVE_DATE = re.compile(
    r"\b(?:the day after tomorrow|today|tomorrow|yesterday|"
    r"in\s+\d{1,4}\s+(?:days?|weeks?)|"
    r"next\s+(?:week|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
    re.IGNORECASE,
)
_EVENT_RELATIVE = re.compile(
    r"\b(?P<relation>after|before)\s+my\s+(?P<subject>[A-Za-z][A-Za-z0-9 .'-]{1,60}?)(?=[?.!,]|$)",
    re.IGNORECASE,
)
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _normalized_contains(text: str, surface: str) -> bool:
    normalized_text = f" {atlas_entity_registry.normalize_alias(text)} "
    normalized_surface = atlas_entity_registry.normalize_alias(surface)
    return bool(normalized_surface and f" {normalized_surface} " in normalized_text)


def _entities_in_text(text: str, context_scope: str, registry: list[dict]) -> list[dict]:
    hits = {}
    for entity in registry:
        if entity.get("context_scope") != context_scope or entity.get("status") == "retired":
            continue
        surfaces = [entity.get("entity_id"), entity.get("canonical_name"), *(entity.get("aliases") or [])]
        if any(isinstance(surface, str) and _normalized_contains(text, surface) for surface in surfaces):
            hits[entity["entity_id"]] = entity
    return sorted(hits.values(), key=lambda item: item["entity_id"])


def _same_scope_user_turns(messages: list[dict], context_scope: str) -> list[dict]:
    return [
        message for message in messages[-MAX_HISTORY_MESSAGES:]
        if message.get("role") == "user"
        and message.get("context_scope") == context_scope
        and isinstance(message.get("text"), str)
        and message["text"].strip()
    ]


def _latest_entity_antecedents(messages: list[dict], context_scope: str, registry: list[dict]) -> list[dict]:
    for message in reversed(_same_scope_user_turns(messages, context_scope)):
        entities = _entities_in_text(message["text"], context_scope, registry)
        if entities:
            return entities
    return []


def _latest_weather_location(messages: list[dict], context_scope: str) -> str | None:
    for message in reversed(_same_scope_user_turns(messages, context_scope)):
        match = _WEATHER_LOCATION.search(message["text"])
        if match:
            return " ".join(match.group(1).split())
    return None


def _add_months(value, months: int):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _possessive(name: str) -> str:
    return f"{name}'" if name.lower().endswith("s") else f"{name}'s"


def _normalize_relative_date(surface: str, anchor: datetime) -> str:
    lowered = " ".join(surface.lower().split())
    base = anchor.astimezone(LOCAL_TZ).date()
    if lowered == "today":
        resolved = base
    elif lowered == "tomorrow":
        resolved = base + timedelta(days=1)
    elif lowered == "the day after tomorrow":
        resolved = base + timedelta(days=2)
    elif lowered == "yesterday":
        resolved = base - timedelta(days=1)
    elif lowered == "next week":
        resolved = base + timedelta(days=7)
    elif lowered == "next month":
        resolved = _add_months(base, 1)
    elif lowered == "next year":
        resolved = _add_months(base, 12)
    elif lowered.startswith("in "):
        amount, unit = re.fullmatch(r"in\s+(\d{1,4})\s+(days?|weeks?)", lowered).groups()
        resolved = base + timedelta(days=int(amount) * (7 if unit.startswith("week") else 1))
    else:
        weekday = lowered.removeprefix("next ")
        target = _WEEKDAYS[weekday]
        delta = (target - base.weekday()) % 7
        resolved = base + timedelta(days=delta or 7)
    return resolved.isoformat()


def _parse_event_datetime(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def _event_matches_subject(event: dict, subject: str) -> bool:
    wanted = atlas_entity_registry.normalize_alias(subject)
    if not wanted:
        return False
    haystack = atlas_entity_registry.normalize_alias(
        " ".join(str(event.get(key) or "") for key in ("summary", "location"))
    )
    return bool(haystack and f" {wanted} " in f" {haystack} ")


def _resolve_event_reference(cleaned: str, event_records: list[dict] | None) -> tuple[list[dict], list[dict]]:
    temporal = []
    ambiguities = []
    for match in _EVENT_RELATIVE.finditer(cleaned):
        relation = match.group("relation").lower()
        subject = " ".join(match.group("subject").split())
        matches = [
            event for event in event_records or []
            if _event_matches_subject(event, subject)
        ]
        if len(matches) != 1:
            ambiguities.append({"kind": "event_reference", "option_count": len(matches)})
            continue
        event = matches[0]
        dtstart = _parse_event_datetime(event.get("dtstart"))
        dtend = _parse_event_datetime(event.get("dtend")) or dtstart
        anchor = dtend if relation == "after" else dtstart
        if not anchor:
            ambiguities.append({"kind": "event_reference", "option_count": 0})
            continue
        temporal.append({
            "surface": match.group(0),
            "relation": relation,
            "normalized_datetime": anchor.isoformat(),
            "timezone": str(LOCAL_TZ),
            "precision": "datetime",
            "source": "authorized_calendar_event",
        })
    return temporal, ambiguities


def resolve(
    question: str,
    context_scope: str,
    recent_messages: list[dict],
    registry: list[dict],
    *,
    event_records: list[dict] | None = None,
    now_value: datetime | None = None,
) -> dict:
    """Resolve bounded references into an ephemeral standalone question."""
    if context_scope not in SCOPES:
        raise ValueError("invalid_context_scope")
    if not isinstance(question, str) or not question.strip():
        return {"status": "unchanged", "resolved_question": question, "references": [], "temporal": [], "ambiguities": []}
    if not isinstance(recent_messages, list) or not isinstance(registry, list):
        raise ValueError("invalid_resolution_input")
    if _is_self_contained_structured_prompt(question):
        return {"status": "unchanged", "resolved_question": question, "references": [], "temporal": [], "ambiguities": []}

    cleaned = " ".join(question.split())[:500]
    anchor = now_value or datetime.now(LOCAL_TZ)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=LOCAL_TZ)
    ambiguities = []
    for match in _AMBIGUOUS_NUMERIC_DATE.finditer(cleaned):
        left, right = int(match.group(1)), int(match.group(2))
        if 1 <= left <= 12 and 1 <= right <= 12:
            ambiguities.append({"kind": "numeric_date", "option_count": 2})
    if ambiguities:
        return {
            "status": "ambiguous", "resolved_question": cleaned,
            "references": [], "temporal": [], "ambiguities": ambiguities,
        }

    temporal = []
    for match in _RELATIVE_DATE.finditer(cleaned):
        temporal.append({
            "surface": match.group(0),
            "normalized_date": _normalize_relative_date(match.group(0), anchor),
            "anchor_timestamp": anchor.astimezone(LOCAL_TZ).isoformat(),
            "timezone": str(LOCAL_TZ),
            "precision": "day",
            "uncertain": False,
        })
    event_temporal, event_ambiguities = _resolve_event_reference(cleaned, event_records)
    if event_ambiguities:
        return {
            "status": "ambiguous", "resolved_question": cleaned,
            "references": [], "temporal": temporal,
            "ambiguities": event_ambiguities,
        }
    temporal.extend(event_temporal)

    references = []
    resolved_question = cleaned
    words = re.findall(r"[A-Za-z0-9']+", cleaned)
    short_followup = len(words) <= MAX_FOLLOWUP_WORDS
    current_entities = _entities_in_text(cleaned, context_scope, registry)

    has_entity_reference = _PRONOUN.search(cleaned) or _POSSESSIVE_PRONOUN.search(cleaned)
    if short_followup and has_entity_reference and not current_entities:
        antecedents = _latest_entity_antecedents(recent_messages, context_scope, registry)
        if len(antecedents) > 1:
            return {
                "status": "ambiguous", "resolved_question": cleaned,
                "references": [], "temporal": temporal,
                "ambiguities": [{
                    "kind": "entity_reference", "option_count": len(antecedents),
                    "options": [item["canonical_name"] for item in antecedents],
                }],
            }
        if len(antecedents) == 1:
            entity = antecedents[0]
            if _POSSESSIVE_PRONOUN.search(resolved_question):
                resolved_question = _POSSESSIVE_PRONOUN.sub(
                    _possessive(entity["canonical_name"]), resolved_question, count=1
                )
            else:
                resolved_question = _PRONOUN.sub(entity["canonical_name"], resolved_question, count=1)
            references.append({
                "kind": "canonical_entity", "canonical_id": entity["entity_id"],
                "display_name": entity["canonical_name"], "resolution": "recent_exact_scope",
            })

    location = _latest_weather_location(recent_messages, context_scope)
    if short_followup and location and _THERE.search(resolved_question):
        resolved_question = _THERE.sub(location, resolved_question, count=1)
        references.append({"kind": "location", "display_name": location, "resolution": "recent_syntax_exact"})
    elif location and _OMITTED_WEATHER_SUBJECT.match(resolved_question) and not _WEATHER_WORD.search(resolved_question):
        relative = temporal[0]["surface"] if temporal else resolved_question.strip(" ?.!")
        resolved_question = f"What is the weather in {location} {relative}?"
        references.append({"kind": "location", "display_name": location, "resolution": "recent_syntax_exact"})

    status = "resolved" if references or temporal else "unchanged"
    return {
        "status": status,
        "resolved_question": resolved_question,
        "references": references,
        "temporal": temporal,
        "ambiguities": [],
    }


def privacy_metrics(result: dict) -> dict:
    """Return structure-only metrics, excluding raw/resolved text and values."""
    return {
        "status": result.get("status"),
        "reference_count": len(result.get("references") or []),
        "reference_kinds": sorted({item.get("kind") for item in result.get("references") or [] if item.get("kind")}),
        "reference_methods": sorted({item.get("resolution") for item in result.get("references") or [] if item.get("resolution")}),
        "temporal_count": len(result.get("temporal") or []),
        "temporal_precisions": sorted({item.get("precision") for item in result.get("temporal") or [] if item.get("precision")}),
        "ambiguity_count": len(result.get("ambiguities") or []),
        "ambiguity_kinds": sorted({item.get("kind") for item in result.get("ambiguities") or [] if item.get("kind")}),
        "resolver_version": "atlas-reference-resolution-r4-shadow-1",
    }
