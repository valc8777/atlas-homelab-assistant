#!/usr/bin/env python3
"""Run the repeatable, non-sealed development gate for R3's first slice."""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import tempfile
import time

import atlas_app_store
import atlas_entity_registry
import atlas_hybrid_retrieval


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "atlas-entity-registry-2026-09-11.json"


def build_entity_cases(registry: list[dict]) -> list[dict]:
    cases = []
    for entity in registry:
        if entity["status"] == "retired":
            continue
        cases.append({
            "category": "exact_id",
            "query": entity["entity_id"],
            "scope": entity["context_scope"],
            "expected": f"entity:{entity['entity_id']}",
        })

    # An alias is a positive recall case only when it is unique at R2's
    # normalized tier. Real collisions are graded separately as ambiguity.
    by_scope_surface = {}
    for entity in registry:
        if entity["status"] == "retired":
            continue
        for surface in [entity["canonical_name"], *entity["aliases"]]:
            key = (entity["context_scope"], atlas_entity_registry.normalize_alias(surface))
            by_scope_surface.setdefault(key, set()).add(entity["entity_id"])
    for (scope, _normalized), entity_ids in sorted(by_scope_surface.items()):
        if len(entity_ids) != 1:
            continue
        entity_id = next(iter(entity_ids))
        entity = next(item for item in registry if item["entity_id"] == entity_id)
        surfaces = [entity["canonical_name"], *entity["aliases"]]
        surface = next(
            value for value in surfaces
            if atlas_entity_registry.normalize_alias(value) == _normalized
        )
        cases.append({
            "category": "unique_alias",
            "query": f"Tell me about {surface}",
            "scope": scope,
            "expected": f"entity:{entity_id}",
        })
    return cases


def create_fixture_db(db_path: pathlib.Path) -> list[dict]:
    atlas_app_store.init_db(db_path)
    cases = []
    fixtures = [
        ("personal", "Bicycle tire", "The bicycle uses a forty-two millimeter tire.", "bicycle"),
        ("operations", "Jellyfin dataset", "Jellyfin media is stored on the atlas-media dataset.", "atlas-media"),
        ("admin", "Maintenance window", "The maintenance window label is blue-moon.", "blue-moon"),
    ]
    for scope, title, text, term in fixtures:
        note = atlas_app_store.create_note(db_path, title, text, scope)
        cases.append({
            "category": "scoped_note",
            "query": f"Find {term}",
            "scope": scope,
            "expected": f"note:{note['id']}",
        })
    for key, value, term in [
        ("tea", "jasmine green", "jasmine"),
        ("keyboard", "quiet tactile switches", "tactile"),
        ("reading", "science history", "science history"),
    ]:
        atlas_app_store.set_preference(db_path, key, value)
        cases.append({
            "category": "personal_preference",
            "query": f"What is my {term} preference?",
            "scope": "personal",
            "expected": f"preference:{key}",
        })
    return cases


def evaluate(registry_path: pathlib.Path) -> dict:
    registry = atlas_entity_registry.load_registry_file(registry_path)
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = pathlib.Path(temp_dir) / "atlas-r3-eval.db"
        cases = build_entity_cases(registry) + create_fixture_db(db_path)
        reciprocal_ranks = []
        recalled = 0
        exact_id_rank1 = 0
        exact_id_total = 0
        failures = []
        for case in cases:
            result = atlas_hybrid_retrieval.retrieve_candidates(
                case["query"], case["scope"], db_path, registry, limit=5
            )
            ids = [item["candidate_id"] for item in result["candidates"]]
            if case["expected"] in ids:
                rank = ids.index(case["expected"]) + 1
                recalled += 1
                reciprocal_ranks.append(1.0 / rank)
            else:
                rank = None
                reciprocal_ranks.append(0.0)
                failures.append({"category": case["category"], "expected": case["expected"]})
            if case["category"] == "exact_id":
                exact_id_total += 1
                exact_id_rank1 += int(rank == 1)

        leak_checks = 0
        leaks = 0
        for entity in registry:
            if entity["status"] == "retired":
                continue
            for other_scope in sorted(atlas_hybrid_retrieval.SCOPES - {entity["context_scope"]}):
                leak_checks += 1
                result = atlas_hybrid_retrieval.retrieve_candidates(
                    entity["entity_id"], other_scope, db_path, registry
                )
                leaks += int(any(
                    item["candidate_id"] == f"entity:{entity['entity_id']}"
                    for item in result["candidates"]
                ))

        no_match_queries = [
            "quasar xylophone nebula", "cerulean marmalade theorem", "orbital pancake index",
            "silent cactus protocol", "velvet comet recipe", "amber violin checksum",
            "floating acorn dialect", "granite hummingbird forecast", "paper lantern compiler",
            "magenta teapot topology", "winter mango semaphore", "plaid dolphin algebra",
            "copper moonlight grammar", "crystal turnip bandwidth", "saffron umbrella matrix",
            "porcelain fox quotient", "indigo waterfall syntax", "marble kiwi archive",
            "silver thimble calculus", "wooden aurora paradox",
        ]
        honest_no_matches = sum(
            atlas_hybrid_retrieval.retrieve_candidates(query, "personal", db_path, registry)["status"] == "no_match"
            for query in no_match_queries
        )
        ambiguous = atlas_hybrid_retrieval.retrieve_candidates(
            "Tell me about Proxmox", "operations", db_path, registry
        )

        warm_queries = [case["query"] for case in cases[: min(len(cases), 40)]]
        for query in warm_queries:
            atlas_hybrid_retrieval.retrieve_candidates(query, "operations", db_path, registry)
        timings_ms = []
        for index in range(200):
            query = warm_queries[index % len(warm_queries)]
            started = time.perf_counter()
            atlas_hybrid_retrieval.retrieve_candidates(query, "operations", db_path, registry)
            timings_ms.append((time.perf_counter() - started) * 1000)

    recall_at_5 = recalled / len(cases)
    mrr = statistics.fmean(reciprocal_ranks)
    p95_ms = statistics.quantiles(timings_ms, n=100, method="inclusive")[94]
    result = {
        "suite": "r3-development-foundation-1",
        "case_count": len(cases),
        "recall_at_5": round(recall_at_5, 6),
        "mean_reciprocal_rank": round(mrr, 6),
        "exact_id_rank1": exact_id_rank1,
        "exact_id_total": exact_id_total,
        "scope_leaks": leaks,
        "scope_checks": leak_checks,
        "no_match_precision": round(honest_no_matches / len(no_match_queries), 6),
        "ambiguity_status": ambiguous["status"],
        "ambiguity_candidate_count": len(ambiguous["candidates"]),
        "warm_latency_p95_ms": round(p95_ms, 3),
        "failures": failures,
    }
    result["passed"] = all([
        result["recall_at_5"] >= 0.95,
        result["mean_reciprocal_rank"] >= 0.90,
        exact_id_rank1 == exact_id_total,
        leaks == 0,
        result["no_match_precision"] >= 0.95,
        result["ambiguity_status"] == "ambiguous",
        result["ambiguity_candidate_count"] == 0,
        p95_ms < 500,
    ])
    return result


def write_report(path: pathlib.Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# ATLAS R3 development foundation\n\n"
        "> Repeatable development evidence; not a sealed holdout and not live-answer proof.\n\n"
        f"- Cases: `{result['case_count']}`\n"
        f"- recall@5: `{result['recall_at_5']:.1%}`\n"
        f"- MRR: `{result['mean_reciprocal_rank']:.3f}`\n"
        f"- Exact IDs rank first: `{result['exact_id_rank1']}/{result['exact_id_total']}`\n"
        f"- Cross-scope leaks: `{result['scope_leaks']}/{result['scope_checks']}`\n"
        f"- No-match precision: `{result['no_match_precision']:.1%}`\n"
        f"- Real collision: `{result['ambiguity_status']}`, candidates `{result['ambiguity_candidate_count']}`\n"
        f"- Warm local p95: `{result['warm_latency_p95_ms']:.3f} ms`\n"
        f"- Gate: `{'PASS' if result['passed'] else 'FAIL'}`\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=pathlib.Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--report", type=pathlib.Path)
    args = parser.parse_args()
    result = evaluate(args.registry)
    if args.report:
        write_report(args.report, result)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
