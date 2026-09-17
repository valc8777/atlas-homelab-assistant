#!/usr/bin/env python3
"""Synthetic development evaluation for ATLAS R3's local semantic backend."""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import tempfile
import time

import atlas_semantic_index


RECORDS = [
    ("personal-coffee", "personal", "Coffee brewing uses a ceramic pour over dripper and a medium-fine grind."),
    ("personal-bicycle", "personal", "The commuter bicycle needs forty two millimeter puncture resistant tires."),
    ("personal-garden", "personal", "The balcony herbs should be watered early in the morning."),
    ("operations-jellyfin", "operations", "Jellyfin provides the home media streaming library for movies and television."),
    ("operations-backup", "operations", "Proxmox Backup Server stores incremental virtual machine backups."),
    ("operations-dns", "operations", "The internal DNS resolver maps private service hostnames on the home network."),
    ("admin-maintenance", "admin", "The privileged maintenance window is reserved for operating system patching."),
    ("admin-audit", "admin", "Administrative audit review checks privileged configuration changes."),
]

CASES = [
    ("How should I grind beans for my pour over?", "personal", "personal-coffee"),
    ("What tires fit the bike I commute on?", "personal", "personal-bicycle"),
    ("When should the potted herbs get water?", "personal", "personal-garden"),
    ("Which service streams our film collection?", "operations", "operations-jellyfin"),
    ("Where do VM incremental backups go?", "operations", "operations-backup"),
    ("What resolves private host names?", "operations", "operations-dns"),
    ("When is privileged OS patching allowed?", "admin", "admin-maintenance"),
    ("What review covers privileged config changes?", "admin", "admin-audit"),
]

NO_MATCH = [
    "Explain photosynthesis in kelp forests.",
    "Who composed the opera Carmen?",
    "What is the geological age of the moon?",
    "Describe the rules of tournament chess.",
    "How do migratory birds navigate?",
    "What causes a solar eclipse?",
    "Summarize the history of paper making.",
    "Why do soap bubbles form spheres?",
    "What languages were spoken in ancient Rome?",
    "How does a violin produce sound?",
    "What is the tallest waterfall in Africa?",
    "Explain the proof of the Pythagorean theorem.",
    "How are glass lenses manufactured?",
    "What is the life cycle of a butterfly?",
    "Why is ocean water salty?",
    "Describe the formation of volcanic islands.",
    "What is the origin of the word algebra?",
    "How do electric motors create rotation?",
    "Explain the phases of the moon.",
    "What makes bread dough rise?",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://ollama:11434")
    parser.add_argument("--model", default=atlas_semantic_index.DEFAULT_MODEL)
    parser.add_argument("--min-similarity", type=float, default=0.65)
    args = parser.parse_args()

    def embed(texts: list[str]) -> list[list[float]]:
        return atlas_semantic_index.ollama_embed(
            texts, base_url=args.base_url, model=args.model, timeout=90
        )

    records = [
        {
            "candidate_id": candidate_id,
            "source_type": "synthetic_fixture",
            "source_key": candidate_id,
            "context_scope": scope,
            "updated_at": "2026-09-12T00:00:00-07:00",
            "text": text,
        }
        for candidate_id, scope, text in RECORDS
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        index_path = pathlib.Path(temp_dir) / "semantic-eval.db"
        started = time.perf_counter()
        built = atlas_semantic_index.rebuild_index(index_path, records, embed, model=args.model)
        build_ms = (time.perf_counter() - started) * 1000
        reciprocal_ranks = []
        failures = []
        query_latencies = []
        for query, scope, expected in CASES:
            started = time.perf_counter()
            matches = atlas_semantic_index.semantic_matches(
                query, scope, index_path, embed, model=args.model,
                min_similarity=args.min_similarity,
            )
            query_latencies.append((time.perf_counter() - started) * 1000)
            ids = [match["candidate_id"] for match in matches]
            if expected in ids:
                reciprocal_ranks.append(1.0 / (ids.index(expected) + 1))
            else:
                reciprocal_ranks.append(0.0)
                failures.append({"scope": scope, "expected": expected, "returned": ids})

        scope_leaks = 0
        scope_checks = 0
        for candidate_id, owner_scope, text in RECORDS:
            for other_scope in set(atlas_semantic_index.SCOPES) - {owner_scope}:
                scope_checks += 1
                matches = atlas_semantic_index.semantic_matches(
                    text, other_scope, index_path, embed, model=args.model,
                    min_similarity=args.min_similarity,
                )
                scope_leaks += int(any(match["candidate_id"] == candidate_id for match in matches))

        honest_no_matches = 0
        for query in NO_MATCH:
            matches = atlas_semantic_index.semantic_matches(
                query, "personal", index_path, embed, model=args.model,
                min_similarity=args.min_similarity,
            )
            honest_no_matches += int(not matches)

    recall_at_5 = sum(value > 0 for value in reciprocal_ranks) / len(CASES)
    mrr = statistics.fmean(reciprocal_ranks)
    p95_ms = statistics.quantiles(query_latencies, n=100, method="inclusive")[94]
    result = {
        "suite": "r3-semantic-development-synthetic-1",
        "model": args.model,
        "dimensions": built["dimensions"],
        "case_count": len(CASES),
        "recall_at_5": round(recall_at_5, 6),
        "mean_reciprocal_rank": round(mrr, 6),
        "scope_leaks": scope_leaks,
        "scope_checks": scope_checks,
        "no_match_precision": round(honest_no_matches / len(NO_MATCH), 6),
        "minimum_similarity": args.min_similarity,
        "build_ms": round(build_ms, 3),
        "query_p95_ms": round(p95_ms, 3),
        "failures": failures,
    }
    result["passed"] = all([
        result["recall_at_5"] >= 0.95,
        result["mean_reciprocal_rank"] >= 0.90,
        scope_leaks == 0,
        result["no_match_precision"] >= 0.95,
        p95_ms < 500,
    ])
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
