#!/usr/bin/env python3
"""Security and lifecycle tests for the dormant R3 semantic index."""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atlas_app_store
import atlas_semantic_index as semantic


VOCAB = ("coffee", "jellyfin", "bicycle", "violet", "unrelated")


def fake_embed(texts: list[str]) -> list[list[float]]:
    return [
        [float(text.lower().count(word)) for word in VOCAB]
        for text in texts
    ]


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        db_path = root / "atlas.db"
        index_path = root / "semantic.db"
        atlas_app_store.init_db(db_path)
        personal = atlas_app_store.create_note(
            db_path, "Coffee", "Coffee beans are stored in the freezer.", "personal"
        )
        operations = atlas_app_store.create_note(
            db_path, "Jellyfin", "Jellyfin runs in the media stack.", "operations"
        )
        admin = atlas_app_store.create_note(
            db_path, "Admin", "The private maintenance label is violet.", "admin"
        )
        atlas_app_store.set_preference(db_path, "bicycle", "upright bicycle fit")

        records = semantic.authorized_records(db_path)
        assert len(records) == 4
        assert {record["context_scope"] for record in records} == {"personal", "operations", "admin"}
        assert next(record for record in records if record["source_type"] == "preference")["context_scope"] == "personal"

        built = semantic.rebuild_index(index_path, records, fake_embed, model="fake-v1", batch_size=2)
        assert built == {"indexed": 4, "dimensions": len(VOCAB), "model": "fake-v1"}
        assert semantic.index_is_current(index_path, records, model="fake-v1") is True
        changed_records = [dict(record) for record in records]
        changed_records[0]["text"] += " changed"
        assert semantic.index_is_current(index_path, changed_records, model="fake-v1") is False
        assert semantic.index_is_current(index_path, records, model="other-model") is False

        coffee = semantic.semantic_matches(
            "coffee", "personal", index_path, fake_embed, model="fake-v1", min_similarity=0.5
        )
        assert coffee[0]["candidate_id"] == f"note:{personal['id']}"
        assert all(item["context_scope"] == "personal" for item in coffee)

        jellyfin = semantic.semantic_matches(
            "jellyfin", "operations", index_path, fake_embed, model="fake-v1", min_similarity=0.5
        )
        assert [item["candidate_id"] for item in jellyfin] == [f"note:{operations['id']}"]

        # Exact scope filtering prevents a highly similar disallowed vector
        # from entering comparison/reranking at all.
        assert semantic.semantic_matches(
            "violet", "personal", index_path, fake_embed, model="fake-v1", min_similarity=0.5
        ) == []
        assert semantic.semantic_matches(
            "violet", "admin", index_path, fake_embed, model="fake-v1", min_similarity=0.5
        )[0]["candidate_id"] == f"note:{admin['id']}"

        assert semantic.semantic_matches(
            "unrelated", "personal", index_path, fake_embed, model="fake-v1", min_similarity=0.5
        ) == []
        assert semantic.semantic_matches("", "personal", index_path, fake_embed, model="fake-v1") == []

        # DEF-049: the production caller never overrides min_similarity, so
        # the function's own default is the real live threshold. Found live
        # via Track A3 Gate 1 -- 0.45 let a confirmed real false positive
        # through (an unrelated question scored 0.4686 against a stored
        # preference); measured genuine matches scored 0.51+ with the real
        # embedding model. Raised the default to 0.5, matching what this
        # test file's own explicit calls above already assumed was correct.
        import inspect
        default_min_similarity = inspect.signature(semantic.semantic_matches).parameters["min_similarity"].default
        assert default_min_similarity == 0.5, default_min_similarity

        try:
            semantic.semantic_matches("coffee", "invalid", index_path, fake_embed, model="fake-v1")
        except ValueError as exc:
            assert str(exc) == "invalid_context_scope"
        else:
            raise AssertionError("invalid scope accepted")

        # Index schema and file contain no source-text field/value. Only an
        # irreversible content hash and numeric vector are durable.
        with sqlite3.connect(index_path) as connection:
            columns = [row[1] for row in connection.execute("pragma table_info(semantic_vectors)")]
        assert "text" not in columns and "title" not in columns and "content" not in columns
        index_bytes = index_path.read_bytes().lower()
        assert b"beans are stored" not in index_bytes
        assert b"private maintenance" not in index_bytes

        # Rebuild is replacement, not append: removed canonical records leave
        # no queryable vector row.
        remaining = [record for record in records if record["candidate_id"] != f"note:{operations['id']}"]
        rebuilt = semantic.rebuild_index(index_path, remaining, fake_embed, model="fake-v1")
        assert rebuilt["indexed"] == 3
        assert semantic.index_is_current(index_path, remaining, model="fake-v1") is True
        assert semantic.index_is_current(index_path, records, model="fake-v1") is False
        assert semantic.semantic_matches(
            "jellyfin", "operations", index_path, fake_embed, model="fake-v1", min_similarity=0.5
        ) == []

        for bad_embedder, expected in [
            (lambda texts: [], "invalid_embedding_count"),
            (lambda texts: [[1.0], [2.0]], "invalid_embedding_count"),
            (lambda texts: [[float("nan")] for _ in texts], "invalid_embedding_value"),
        ]:
            try:
                semantic.rebuild_index(index_path, records[:1], bad_embedder, model="bad")
            except ValueError as exc:
                assert str(exc) == expected
            else:
                raise AssertionError(f"bad embedding accepted: {expected}")

    print("PASS: R3 semantic index scope isolation, text-free storage, replacement lifecycle, and fail-closed vectors")


if __name__ == "__main__":
    main()
