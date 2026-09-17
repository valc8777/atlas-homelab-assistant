#!/usr/bin/env python3
"""Default-off, deterministic answer consumer for ATLAS R3 candidates.

Candidate IDs are locators, never answer content. Every note, preference, and
entity is fetched again from its canonical store under the caller's exact
scope. Current-state answers accept only freshness-validated structured
candidates produced by atlas_current_state_retrieval.
"""

from __future__ import annotations

import pathlib
import re

import atlas_app_store
import atlas_hybrid_retrieval


SCOPES = frozenset({"personal", "operations", "admin"})
MAX_EVIDENCE = 3
_MEMORY_CUE = re.compile(
    r"\b(?:remember|saved|note|notes|on file|prefer|preference|did i (?:say|mention))\b",
    re.IGNORECASE,
)


def retrieval_requested(query: str, context_scope: str, current_state_result: dict | None = None) -> bool:
    """Conservatively identify questions for which an honest no-match is useful."""
    if context_scope not in SCOPES or not isinstance(query, str):
        return False
    if current_state_result and current_state_result.get("status") != "not_requested":
        return True
    if _MEMORY_CUE.search(query):
        return True
    # Operations/admin questions enter this canary only after R3 found an
    # authorized candidate; a bare miss must continue to the existing route.
    return False


def _canonical_note(db_path: pathlib.Path, candidate: dict, context_scope: str) -> dict | None:
    prefix = "note:"
    candidate_id = candidate.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.startswith(prefix):
        return None
    note_id = candidate_id[len(prefix):]
    # Scope is part of the canonical SQL selection. This is the required
    # second authorization check for both lexical and ID-only semantic hits.
    with atlas_app_store.connect(db_path) as connection:
        row = connection.execute(
            """
            select id, title, text, context_scope, updated_at
            from notes
            where id = ? and context_scope = ? and archived_at is null
            """,
            (note_id, context_scope),
        ).fetchone()
    if not row:
        return None
    return {
        "source_type": "note",
        "title": row["title"],
        "text": row["text"],
        "citation": f"ATLAS explicit note — {row['title']} (updated {row['updated_at']})",
    }


def _canonical_preference(db_path: pathlib.Path, candidate: dict, context_scope: str) -> dict | None:
    prefix = "preference:"
    candidate_id = candidate.get("candidate_id")
    if context_scope != "personal" or not isinstance(candidate_id, str) or not candidate_id.startswith(prefix):
        return None
    key = candidate_id[len(prefix):]
    with atlas_app_store.connect(db_path) as connection:
        row = connection.execute(
            "select key, value, updated_at from preferences where key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    return {
        "source_type": "preference",
        "title": row["key"],
        "text": row["value"],
        "citation": f"ATLAS explicit preference — {row['key']} (updated {row['updated_at']})",
    }


def _canonical_entity(candidate: dict, context_scope: str, registry: list[dict]) -> dict | None:
    prefix = "entity:"
    candidate_id = candidate.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.startswith(prefix):
        return None
    entity_id = candidate_id[len(prefix):]
    matches = [
        entity for entity in registry
        if entity.get("entity_id") == entity_id
        and entity.get("context_scope") == context_scope
        and entity.get("status") in {"active", "renamed"}
    ]
    if len(matches) != 1:
        return None
    entity = matches[0]
    return {
        "source_type": "canonical_entity",
        "title": entity["canonical_name"],
        "text": entity["canonical_name"],
        "citation": f"ATLAS canonical entity registry — {entity['source']} (updated {entity['updated_at']})",
    }


def _current_state_evidence(candidate: dict, context_scope: str) -> dict | None:
    if (
        context_scope != "operations"
        or candidate.get("context_scope") != context_scope
        or candidate.get("source_type") != "structured_current_state"
        or candidate.get("match_method") != "structured_current_state"
        or candidate.get("freshness") != "current"
        or not isinstance(candidate.get("text"), str)
        or not candidate["text"].strip()
    ):
        return None
    return {
        "source_type": "structured_current_state",
        "title": candidate.get("title") or "Current ATLAS observation",
        "text": candidate["text"].strip(),
        "citation": f"ATLAS Phase 2b observation (observed {candidate.get('updated_at') or 'recently'})",
    }


def _render_answer(evidence: list[dict]) -> str:
    statements = []
    citations = []
    for index, item in enumerate(evidence, start=1):
        if item["source_type"] == "preference":
            statements.append(f"{item['title']}: {item['text']} [{index}]")
        elif item["source_type"] == "canonical_entity":
            statements.append(f"Matched canonical entity: {item['text']}. [{index}]")
        else:
            statements.append(f"{item['title']}: {item['text']} [{index}]")
        citations.append(f"[{index}] {item['citation']}")
    return "\n".join(statements + ["", "Sources:"] + citations)


def answer_from_candidates(
    query: str,
    context_scope: str,
    db_path: pathlib.Path,
    registry: list[dict],
    retrieval_result: dict,
    *,
    current_state_result: dict | None = None,
) -> dict | None:
    """Return a grounded canary result, or None when the canary is inapplicable."""
    if context_scope not in SCOPES:
        raise ValueError("invalid_context_scope")
    current_status = (current_state_result or {}).get("status", "not_requested")
    requested = retrieval_requested(query, context_scope, current_state_result)

    # A current-state request never degrades to notes/preferences. This check
    # runs before any memory candidate is re-fetched or rendered.
    if current_status == "unavailable":
        statuses = sorted({notice.get("status", "unavailable") for notice in (current_state_result or {}).get("notices", [])})
        reason = ", ".join(statuses) if statuses else "unavailable"
        return {
            "route": "r3_retrieval_canary",
            "tool": "atlas-r3-answer-canary",
            "answer": f"I can't verify the current status from fresh observations right now ({reason}). I won't substitute an older note as current truth.",
            "source": "ATLAS R3 scoped retrieval",
            "result": {"status": "current_state_unavailable", "evidence_count": 0, "citation_count": 0},
        }

    status = retrieval_result.get("status")
    if status == "ambiguous" and requested:
        return {
            "route": "r3_retrieval_canary", "tool": "atlas-r3-answer-canary",
            "answer": "I found more than one authorized match. Please name the specific item you mean.",
            "source": "ATLAS R3 scoped retrieval",
            "result": {"status": "ambiguous", "evidence_count": 0, "citation_count": 0},
        }

    evidence = []
    candidates = retrieval_result.get("candidates") or []
    if current_status == "matched":
        # A current-state request consumes only the freshness-gated adapter's
        # own candidates. Even a highly ranked note/entity cannot supplement
        # or replace current truth.
        allowed_current = {
            (item.get("candidate_id"), item.get("updated_at"), item.get("text"))
            for item in (current_state_result or {}).get("candidates") or []
        }
        candidates = [
            item for item in candidates
            if item.get("source_type") == "structured_current_state"
            and (item.get("candidate_id"), item.get("updated_at"), item.get("text")) in allowed_current
        ]
    for candidate in candidates:
        source_type = candidate.get("source_type")
        if source_type == "structured_current_state":
            item = _current_state_evidence(candidate, context_scope)
        elif source_type == "note":
            item = _canonical_note(db_path, candidate, context_scope)
        elif source_type == "preference":
            item = _canonical_preference(db_path, candidate, context_scope)
        elif source_type == "canonical_entity":
            # Registry records establish identity/aliases, not operational
            # relationships or descriptive facts. They may rank/clarify other
            # evidence but are never sufficient to answer by themselves.
            item = None
        else:
            item = None
        if item:
            evidence.append(item)
        if len(evidence) >= MAX_EVIDENCE:
            break

    if evidence:
        return {
            "route": "r3_retrieval_canary",
            "tool": "atlas-r3-answer-canary",
            "answer": _render_answer(evidence),
            "source": "ATLAS R3 scoped retrieval",
            "result": {
                "status": "matched",
                "evidence_count": len(evidence),
                "citation_count": len(evidence),
                "source_types": sorted({item["source_type"] for item in evidence}),
            },
        }

    if requested:
        return {
            "route": "r3_retrieval_canary", "tool": "atlas-r3-answer-canary",
            "answer": "I couldn't find an authorized saved fact that answers that. I won't guess.",
            "source": "ATLAS R3 scoped retrieval",
            "result": {"status": "no_match", "evidence_count": 0, "citation_count": 0},
        }
    return None
