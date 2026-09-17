#!/usr/bin/env python3
"""Disposable, scope-first semantic index primitives for ATLAS R3.

The index stores vectors and source identifiers, never source text. Canonical
content remains in the existing ATLAS database and rebuilding the index is the
only update mechanism. No function in this module writes owner memory or calls
an ATLAS capability.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import sqlite3
import urllib.request
from collections.abc import Callable

import atlas_app_store


SCOPES = ("personal", "operations", "admin")
DEFAULT_MODEL = "nomic-embed-text:latest"
MAX_RECORDS = 2000
MAX_VECTOR_DIMENSIONS = 8192


def authorized_records(db_path: pathlib.Path) -> list[dict]:
    """Load only explicit, already-authorized memory records."""
    records = []
    for scope in SCOPES:
        for note in atlas_app_store.list_notes(db_path, limit=500, context_scope=scope):
            records.append({
                "candidate_id": f"note:{note['id']}",
                "source_type": "note",
                "source_key": note["id"],
                "context_scope": scope,
                "updated_at": note["updated_at"],
                "text": f"{note['title']}\n{note['text']}",
            })
    # The legacy preferences table has no scope column. Existing policy defines
    # it as personal memory, so never project it into operations/admin.
    for preference in atlas_app_store.list_preferences(db_path):
        records.append({
            "candidate_id": f"preference:{preference['key']}",
            "source_type": "preference",
            "source_key": preference["key"],
            "context_scope": "personal",
            "updated_at": preference["updated_at"],
            "text": f"{preference['key']}\n{preference['value']}",
        })
    return records[:MAX_RECORDS]


def ollama_embed(
    texts: list[str],
    *,
    base_url: str = "http://ollama:11434",
    model: str = DEFAULT_MODEL,
    timeout: int = 60,
) -> list[list[float]]:
    """Embed a bounded batch through the local Ollama API."""
    if not texts or len(texts) > 128 or any(not isinstance(text, str) or not text for text in texts):
        raise ValueError("invalid_embedding_batch")
    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/embed",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    embeddings = result.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
        raise ValueError("invalid_embedding_response")
    return embeddings


def _validate_vectors(vectors: list[list[float]], expected_count: int) -> list[list[float]]:
    if len(vectors) != expected_count or not vectors:
        raise ValueError("invalid_embedding_count")
    dimension = len(vectors[0]) if isinstance(vectors[0], list) else 0
    if dimension < 1 or dimension > MAX_VECTOR_DIMENSIONS:
        raise ValueError("invalid_embedding_dimension")
    normalized = []
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimension:
            raise ValueError("inconsistent_embedding_dimension")
        clean = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("invalid_embedding_value")
            clean.append(float(value))
        normalized.append(clean)
    return normalized


def _connect(index_path: pathlib.Path) -> sqlite3.Connection:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(index_path))
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        create table if not exists semantic_vectors (
          candidate_id text primary key,
          source_type text not null,
          source_key text not null,
          context_scope text not null,
          updated_at text not null,
          content_sha256 text not null,
          model text not null,
          dimensions integer not null,
          vector_json text not null
        )
        """
    )
    connection.execute(
        "create index if not exists idx_semantic_scope_model on semantic_vectors(context_scope, model)"
    )
    connection.execute(
        "create table if not exists semantic_metadata (key text primary key, value text not null)"
    )
    return connection


def records_fingerprint(records: list[dict], model: str) -> str:
    """Fingerprint canonical identity/content without storing the content."""
    rows = sorted(
        (
            record["candidate_id"], record["context_scope"], record["updated_at"],
            hashlib.sha256(record["text"].encode("utf-8")).hexdigest(), model,
        )
        for record in records
    )
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode("utf-8")).hexdigest()


def index_is_current(index_path: pathlib.Path, records: list[dict], model: str = DEFAULT_MODEL) -> bool:
    """Return false for a missing, corrupt, differently sourced, or stale index."""
    if not index_path.exists():
        return False
    try:
        with _connect(index_path) as connection:
            row = connection.execute(
                "select value from semantic_metadata where key = 'records_fingerprint'"
            ).fetchone()
            count = connection.execute("select count(*) from semantic_vectors").fetchone()[0]
        return bool(
            row and row["value"] == records_fingerprint(records, model) and count == len(records)
        )
    except (OSError, sqlite3.DatabaseError, KeyError, TypeError, ValueError):
        return False


def rebuild_index(
    index_path: pathlib.Path,
    records: list[dict],
    embedder: Callable[[list[str]], list[list[float]]],
    *,
    model: str = DEFAULT_MODEL,
    batch_size: int = 64,
) -> dict:
    """Atomically replace the disposable index from canonical records."""
    if not 1 <= batch_size <= 128 or len(records) > MAX_RECORDS:
        raise ValueError("invalid_rebuild_bounds")
    prepared = []
    dimensions = None
    for offset in range(0, len(records), batch_size):
        batch = records[offset : offset + batch_size]
        vectors = _validate_vectors(embedder([record["text"] for record in batch]), len(batch))
        for record, vector in zip(batch, vectors):
            if record.get("context_scope") not in SCOPES:
                raise ValueError("invalid_record_scope")
            if dimensions is None:
                dimensions = len(vector)
            elif len(vector) != dimensions:
                raise ValueError("inconsistent_embedding_dimension")
            prepared.append((
                record["candidate_id"], record["source_type"], record["source_key"],
                record["context_scope"], record["updated_at"],
                hashlib.sha256(record["text"].encode("utf-8")).hexdigest(),
                model, len(vector), json.dumps(vector, separators=(",", ":")),
            ))
    with _connect(index_path) as connection:
        connection.execute("begin immediate")
        connection.execute("delete from semantic_vectors")
        connection.executemany(
            """
            insert into semantic_vectors(
              candidate_id, source_type, source_key, context_scope, updated_at,
              content_sha256, model, dimensions, vector_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            prepared,
        )
        connection.execute(
            """
            insert into semantic_metadata(key, value) values ('records_fingerprint', ?)
            on conflict(key) do update set value = excluded.value
            """,
            (records_fingerprint(records, model),),
        )
        connection.commit()
    return {"indexed": len(prepared), "dimensions": dimensions or 0, "model": model}


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return dot / (left_norm * right_norm)


def semantic_matches(
    query: str,
    context_scope: str,
    index_path: pathlib.Path,
    embedder: Callable[[list[str]], list[list[float]]],
    *,
    model: str = DEFAULT_MODEL,
    limit: int = 5,
    # DEF-049: found live via Track A3 Gate 1 (DEC-2026-09-17-02). The
    # production caller (atlas_shortcuts_server.py) never overrides this
    # default, so 0.45 was the real live threshold -- but this file's own
    # test suite has only ever validated the function at 0.5, never at
    # 0.45. Measured directly against the real nomic-embed-text model:
    # "What is the safest sunscreen ingredient?" (genuinely unrelated to a
    # stored coffee-order preference) scored 0.4686 -- above 0.45, a
    # confirmed false positive that leaked real personal preference text
    # in response to an unrelated question. Two genuinely relevant
    # coffee-preference-recall queries scored 0.51 and 0.59 in the same
    # measurement, and two more unrelated queries scored 0.29 and 0.37 --
    # 0.5 sits in the gap between confirmed false positives and confirmed
    # true positives, matching what the test suite already assumed.
    min_similarity: float = 0.5,
) -> list[dict]:
    """Return ID-only matches after selecting index rows by exact scope."""
    if context_scope not in SCOPES:
        raise ValueError("invalid_context_scope")
    if not isinstance(query, str) or not query.strip():
        return []
    bounded_limit = max(1, min(int(limit), 20))
    query_vector = _validate_vectors(embedder([" ".join(query.split())[:500]]), 1)[0]
    with _connect(index_path) as connection:
        # Scope filtering happens in SQLite before vectors enter Python or the
        # reranker; a disallowed vector is never considered for similarity.
        rows = connection.execute(
            """
            select candidate_id, source_type, source_key, context_scope,
                   updated_at, dimensions, vector_json
            from semantic_vectors
            where context_scope = ? and model = ?
            """,
            (context_scope, model),
        ).fetchall()
    matches = []
    for row in rows:
        vector = json.loads(row["vector_json"])
        score = _cosine_similarity(query_vector, vector)
        if score < min_similarity:
            continue
        matches.append({
            "candidate_id": row["candidate_id"],
            "source_type": row["source_type"],
            "source_key": row["source_key"],
            "context_scope": row["context_scope"],
            "updated_at": row["updated_at"],
            "match_method": "semantic_cosine",
            "similarity": round(score, 6),
        })
    return sorted(
        matches,
        key=lambda item: (item["similarity"], item["updated_at"], item["candidate_id"]),
        reverse=True,
    )[:bounded_limit]
