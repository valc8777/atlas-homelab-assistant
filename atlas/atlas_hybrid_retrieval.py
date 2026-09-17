#!/usr/bin/env python3
"""Scope-first local candidate retrieval for ATLAS Phase R3.

This module is deliberately not connected to a live answer path. It produces
bounded, provenance-labelled candidates from the R2 canonical registry,
explicitly saved notes, and personal-only preferences. Scope filtering happens
before candidate generation; raw conversation messages are not searched because
R3 authorizes conversation summaries, not arbitrary conversation history.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import atlas_app_store
import atlas_entity_registry


SCOPES = frozenset({"personal", "operations", "admin"})
MAX_QUERY_CHARS = 500
MAX_CANDIDATES = 20
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOPWORDS = frozenset({
    "a", "about", "an", "and", "are", "did", "do", "does", "for", "from",
    "have", "i", "in", "is", "it", "me", "my", "of", "on", "or", "the",
    "this", "to", "was", "what", "where", "which", "who", "with", "you",
})
# DEF-048: found live via Track A3 Gate 1 (DEC-2026-09-16-30). "Which
# scientist developed the theory of general relativity?" returned a stored
# personal coffee-order preference instead of answering, because that
# preference's own key happens to be the single word "general" -- an
# ordinary English adjective, not a distinctive topic -- and
# _preference_candidates()'s overlap check accepts a single shared token
# with no floor on how common/distinctive that token is. Raising the
# overlap threshold generally would break the existing, validated "What
# coffee do I prefer?" -> preference:coffee test case, which also relies
# on exactly one shared token ("coffee"). The actual problem is narrower:
# a small set of words are too generic to ever be a meaningful match
# signal for a *preference* record specifically (a preference's real
# topic lives in more specific vocabulary), unlike "coffee", which is
# itself a specific, distinctive topic word. Deliberately not merged into
# the shared _STOPWORDS (used by notes/entities too, where this hasn't
# been shown to be a problem) -- narrow, evidence-anchored fix, not a
# broad one.
_GENERIC_PREFERENCE_WORDS = frozenset({"general"})
_METHOD_SCORE = {"exact_id": 1000.0, "exact_alias": 900.0, "normalized_alias": 850.0}
_LIFECYCLE_ADJUSTMENT = {"active": 25.0, "renamed": 0.0, "retired": -500.0}
USAGE_FINGERPRINT_VERSION = "atlas-record-usage-v1"


def _tokens(text: str) -> list[str]:
    return list(dict.fromkeys(
        token.lower() for token in _TOKEN_RE.findall(text)
        if len(token) > 2 and token.lower() not in _STOPWORDS
    ))


def _contains_normalized_phrase(query: str, surface: str) -> bool:
    normalized_query = f" {atlas_entity_registry.normalize_alias(query)} "
    normalized_surface = atlas_entity_registry.normalize_alias(surface)
    return bool(normalized_surface and f" {normalized_surface} " in normalized_query)


def _entity_candidates(query: str, context_scope: str, registry: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return unique scoped entity candidates plus ambiguity/retirement notices."""
    scoped = [entity for entity in registry if entity["context_scope"] == context_scope]
    surfaces = []
    for entity in scoped:
        for method, surface in (
            [("exact_id", entity["entity_id"]), ("exact_alias", entity["canonical_name"])]
            + [("exact_alias", alias) for alias in entity["aliases"]]
        ):
            if _contains_normalized_phrase(query, surface):
                surfaces.append((len(atlas_entity_registry.normalize_alias(surface)), method, surface))

    candidates_by_id: dict[str, dict] = {}
    notices = []
    seen_notices = set()
    for _length, detected_method, surface in sorted(surfaces, reverse=True):
        result = atlas_entity_registry.resolve(surface, context_scope, registry)
        if result["status"] == "ambiguous":
            key = ("ambiguous", tuple(result.get("candidates") or []))
            if key not in seen_notices:
                notices.append({"status": "ambiguous", "candidate_count": len(key[1])})
                seen_notices.add(key)
            continue
        if result["status"] == "retired":
            key = ("retired", result["entity_id"])
            if key not in seen_notices:
                notices.append({"status": "retired", "candidate_count": 1})
                seen_notices.add(key)
            continue
        if result["status"] != "resolved":
            continue
        method = "exact_id" if detected_method == "exact_id" else result["resolution"]
        candidate = {
            "candidate_id": f"entity:{result['entity_id']}",
            "subject_id": result["entity_id"],
            "source_type": "canonical_entity",
            "context_scope": context_scope,
            "title": result["canonical_name"],
            "text": result["canonical_name"],
            "source": "ATLAS canonical entity registry",
            "updated_at": "",
            "lifecycle": result["lifecycle"],
            "match_method": method,
            "score": _METHOD_SCORE[method] + _LIFECYCLE_ADJUSTMENT[result["lifecycle"]],
        }
        previous = candidates_by_id.get(result["entity_id"])
        if previous is None or candidate["score"] > previous["score"]:
            candidates_by_id[result["entity_id"]] = candidate
    return list(candidates_by_id.values()), notices


def resolve_query_entities(query: str, context_scope: str, registry: list[dict]) -> tuple[list[dict], list[dict]]:
    """Public entry point for callers outside R3's own candidate pipeline
    (R2's homelab-knowledge query enhancement) that only need entity
    resolution, not the full note/preference candidate set. Thin wrapper so
    that logic stays in one place rather than being reimplemented."""
    return _entity_candidates(query, context_scope, registry)


def _fts_query(tokens: list[str]) -> str:
    # Tokens come from the alphanumeric-only lexer above, so quoting is
    # defense-in-depth rather than an escaping mechanism for arbitrary input.
    return " OR ".join(f'"{token}"' for token in tokens[:12])


def _note_candidates(db_path: Path, query_tokens: list[str], context_scope: str) -> list[dict]:
    if not query_tokens:
        return []
    rows = atlas_app_store.search_notes(
        db_path, _fts_query(query_tokens), limit=MAX_CANDIDATES, context_scope=context_scope
    )
    candidates = []
    token_set = set(query_tokens)
    for rank, row in enumerate(rows):
        overlap = len(token_set & set(_tokens(f"{row['title']} {row['text']}")))
        if overlap == 0:
            continue
        candidates.append({
            "candidate_id": f"note:{row['id']}",
            "subject_id": f"note:{row['id']}",
            "source_type": "note",
            "context_scope": context_scope,
            "title": row["title"],
            "text": row["text"],
            "source": "ATLAS explicit note",
            "updated_at": row["updated_at"],
            "lifecycle": "active",
            "match_method": "fts5_bm25",
            "score": 600.0 - rank + min(overlap, 5) * 10.0,
        })
    return candidates


def _preference_candidates(db_path: Path, query_tokens: list[str], context_scope: str) -> list[dict]:
    # Preferences predate scoped storage and are defined by the existing
    # memory contract as personal-only. Never even load them for other scopes.
    if context_scope != "personal" or not query_tokens:
        return []
    token_set = set(query_tokens)
    candidates = []
    for row in atlas_app_store.list_preferences(db_path):
        preference_tokens = set(_tokens(f"{row['key']} {row['value']}")) - _GENERIC_PREFERENCE_WORDS
        overlap = len(token_set & preference_tokens)
        if overlap == 0:
            continue
        candidates.append({
            "candidate_id": f"preference:{row['key']}",
            "subject_id": f"preference:{row['key']}",
            "source_type": "preference",
            "context_scope": "personal",
            "title": row["key"],
            "text": row["value"],
            "source": "ATLAS explicit preference",
            "updated_at": row["updated_at"],
            "lifecycle": "active",
            "match_method": "lexical_overlap",
            "score": 650.0 + min(overlap, 5) * 10.0,
        })
    return candidates


def deterministic_rerank(candidates: list[dict], limit: int = 5) -> list[dict]:
    """Rank bounded candidates, preferring active evidence and exact entities."""
    bounded_limit = max(1, min(int(limit), MAX_CANDIDATES))
    best_by_id = {}
    for candidate in candidates[: MAX_CANDIDATES * 3]:
        if candidate.get("lifecycle") == "retired":
            continue
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            continue
        previous = best_by_id.get(candidate_id)
        if previous is None or float(candidate.get("score", 0)) > float(previous.get("score", 0)):
            best_by_id[candidate_id] = candidate
    return sorted(
        best_by_id.values(),
        key=lambda item: (
            item.get("lifecycle") == "active",
            item.get("match_method") == "exact_id",
            float(item.get("score", 0)),
            item.get("updated_at") or "",
            item["candidate_id"],
        ),
        reverse=True,
    )[:bounded_limit]


def retrieve_candidates(
    query: str,
    context_scope: str,
    db_path: Path,
    registry: list[dict],
    *,
    limit: int = 5,
    semantic_matches: list[dict] | None = None,
    current_state_candidates: list[dict] | None = None,
) -> dict:
    """Generate and rank authorized local candidates without model or network use."""
    if context_scope not in SCOPES:
        raise ValueError("invalid_context_scope")
    if not isinstance(query, str) or not query.strip():
        return {"status": "no_match", "candidates": [], "notices": []}
    clean_query = " ".join(query.split())[:MAX_QUERY_CHARS]
    query_tokens = _tokens(clean_query)
    entity_candidates, notices = _entity_candidates(clean_query, context_scope, registry)
    candidates = entity_candidates
    candidates += _note_candidates(db_path, query_tokens, context_scope)
    candidates += _preference_candidates(db_path, query_tokens, context_scope)
    for match in semantic_matches or []:
        if match.get("context_scope") != context_scope:
            raise ValueError("semantic_scope_mismatch")
        similarity = match.get("similarity")
        if isinstance(similarity, bool) or not isinstance(similarity, (int, float)):
            raise ValueError("invalid_semantic_match")
        candidate_id = match.get("candidate_id")
        source_type = match.get("source_type")
        if not isinstance(candidate_id, str) or not candidate_id or source_type not in {"note", "preference"}:
            raise ValueError("invalid_semantic_match")
        candidates.append({
            "candidate_id": candidate_id,
            "subject_id": candidate_id,
            "source_type": source_type,
            "context_scope": context_scope,
            # Semantic shadow candidates intentionally carry no content. A
            # future answer consumer must re-fetch canonical content with a
            # second exact scope check; this slice only measures candidate IDs.
            "title": "",
            "text": "",
            "source": "ATLAS local semantic index",
            "updated_at": match.get("updated_at") or "",
            "lifecycle": "active",
            "match_method": "semantic_cosine",
            "score": 700.0 + max(-1.0, min(1.0, float(similarity))) * 100.0,
        })
    for candidate in current_state_candidates or []:
        if candidate.get("context_scope") != context_scope:
            raise ValueError("current_state_scope_mismatch")
        if (
            candidate.get("source_type") != "structured_current_state"
            or candidate.get("match_method") != "structured_current_state"
            or candidate.get("freshness") != "current"
        ):
            raise ValueError("invalid_current_state_candidate")
        candidates.append(dict(candidate))
    ranked = deterministic_rerank(candidates, limit=limit)
    if ranked:
        status = "matched"
    elif any(notice["status"] == "ambiguous" for notice in notices):
        status = "ambiguous"
    elif any(notice["status"] == "retired" for notice in notices):
        status = "retired"
    else:
        status = "no_match"
    return {"status": status, "candidates": ranked, "notices": notices}


def usage_fingerprint(candidate_id: str, context_scope: str) -> str | None:
    """Stable audit marker for maintenance use; never stores raw IDs/content."""
    if context_scope not in SCOPES or not isinstance(candidate_id, str):
        return None
    if not (candidate_id.startswith("note:") or candidate_id.startswith("preference:")):
        return None
    material = f"{USAGE_FINGERPRINT_VERSION}\0{context_scope}\0{candidate_id}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def privacy_metrics(result: dict) -> dict:
    """Audit-safe R3 structure metrics; deliberately excludes content and IDs."""
    candidates = result.get("candidates") or []
    fingerprints = sorted({
        fingerprint
        for item in candidates
        if (fingerprint := usage_fingerprint(item.get("candidate_id", ""), item.get("context_scope", "")))
    })
    return {
        "status": result.get("status"),
        "candidate_count": len(candidates),
        "source_types": sorted({item["source_type"] for item in candidates}),
        "match_methods": sorted({item["match_method"] for item in candidates}),
        "notice_statuses": sorted({item["status"] for item in result.get("notices") or []}),
        "record_usage_fingerprint_version": USAGE_FINGERPRINT_VERSION,
        "record_usage_fingerprints": fingerprints,
        "retriever_version": "atlas-hybrid-retrieval-r3-local-1",
    }
