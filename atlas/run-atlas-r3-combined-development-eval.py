#!/usr/bin/env python3
"""Fresh fusion gate for all implemented ATLAS R3 candidate sources."""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import tempfile
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import atlas_app_store
import atlas_current_state_retrieval
import atlas_entity_registry
import atlas_hybrid_retrieval


ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=pathlib.Path)
    parser.add_argument(
        "--registry",
        type=pathlib.Path,
        default=ROOT / "atlas-entity-registry-2026-09-11.json",
    )
    args = parser.parse_args()
    registry = atlas_entity_registry.load_registry_file(args.registry)
    now = datetime(2026, 9, 12, 12, 30, tzinfo=LOCAL_TZ)
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = pathlib.Path(temp_dir) / "atlas.db"
        atlas_app_store.init_db(db_path)
        stale_service_note = atlas_app_store.create_note(
            db_path, "Old Jellyfin status", "Jellyfin was offline during old maintenance.", "operations"
        )
        stale_host_note = atlas_app_store.create_note(
            db_path, "Old host status", "The monitored hosts were unhealthy during an old test.", "operations"
        )
        stale_backup_note = atlas_app_store.create_note(
            db_path, "Old backup status", "The latest backup had failed during an old test.", "operations"
        )
        atlas_app_store.put_signal_observation(
            db_path, "service_reachability", {"total": 10, "unreachable_count": 0},
            observed_at=(now - timedelta(minutes=1)).isoformat(),
        )
        atlas_app_store.put_signal_observation(
            db_path, "host_health", {"total": 3, "unhealthy_count": 0},
            observed_at=(now - timedelta(minutes=1)).isoformat(),
        )
        atlas_app_store.put_signal_observation(
            db_path, "backup_status", {"latest_ok": True, "last_success_age_hours": 2.0},
            observed_at=(now - timedelta(minutes=1)).isoformat(),
        )

        cases = []
        for entity in registry:
            if entity["status"] != "retired":
                cases.append((entity["entity_id"], entity["context_scope"], f"entity:{entity['entity_id']}"))

        failures = []
        ranks = []
        exact_first = 0
        latencies = []
        for query, scope, expected in cases:
            started = time.perf_counter()
            result = atlas_hybrid_retrieval.retrieve_candidates(query, scope, db_path, registry)
            latencies.append((time.perf_counter() - started) * 1000)
            ids = [item["candidate_id"] for item in result["candidates"]]
            if expected in ids:
                rank = ids.index(expected) + 1
                ranks.append(1 / rank)
                exact_first += int(rank == 1)
            else:
                ranks.append(0.0)
                failures.append({"category": "exact_id", "expected": expected})

        conflicts = [
            ("Is the Jellyfin service currently online?", "service_reachability", stale_service_note),
            ("Are the monitored hosts healthy right now?", "host_health", stale_host_note),
            ("Did the latest backup succeed?", "backup_status", stale_backup_note),
        ]
        conflict_first = 0
        for query, signal_name, note in conflicts:
            state = atlas_current_state_retrieval.retrieve_current_state(
                query, "operations", db_path, now_value=now
            )
            semantic = [{
                "candidate_id": f"note:{note['id']}", "source_type": "note",
                "source_key": note["id"], "context_scope": "operations",
                "updated_at": note["updated_at"], "similarity": 0.99,
            }]
            result = atlas_hybrid_retrieval.retrieve_candidates(
                query, "operations", db_path, registry,
                semantic_matches=semantic,
                current_state_candidates=state["candidates"],
            )
            expected = f"current_state:{signal_name}"
            ids = [item["candidate_id"] for item in result["candidates"]]
            conflict_first += int(ids and ids[0] == expected)
            if not ids or ids[0] != expected:
                failures.append({"category": "fresh_state_precedence", "expected": expected, "returned": ids})

        scope_checks = 0
        scope_leaks = 0
        for entity in registry:
            for other_scope in set(atlas_hybrid_retrieval.SCOPES) - {entity["context_scope"]}:
                scope_checks += 1
                result = atlas_hybrid_retrieval.retrieve_candidates(
                    entity["entity_id"], other_scope, db_path, registry
                )
                scope_leaks += int(any(
                    item["candidate_id"] == f"entity:{entity['entity_id']}"
                    for item in result["candidates"]
                ))

        no_match_queries = [f"unrelated grounded control {index} quasar xylophone" for index in range(20)]
        honest_no_matches = sum(
            atlas_hybrid_retrieval.retrieve_candidates(query, "personal", db_path, registry)["status"] == "no_match"
            for query in no_match_queries
        )
        p95_ms = statistics.quantiles(latencies * 7, n=100, method="inclusive")[94]

    recall = sum(value > 0 for value in ranks) / len(ranks)
    result = {
        "suite": "r3-combined-development-fusion-1",
        "exact_id_cases": len(cases),
        "recall_at_5": round(recall, 6),
        "mean_reciprocal_rank": round(statistics.fmean(ranks), 6),
        "exact_id_rank1": exact_first,
        "fresh_state_precedence": conflict_first,
        "fresh_state_conflicts": len(conflicts),
        "scope_leaks": scope_leaks,
        "scope_checks": scope_checks,
        "no_match_precision": round(honest_no_matches / len(no_match_queries), 6),
        "fusion_p95_ms": round(p95_ms, 3),
        "failures": failures,
    }
    result["passed"] = all([
        result["recall_at_5"] >= 0.95,
        result["mean_reciprocal_rank"] >= 0.90,
        exact_first == len(cases),
        conflict_first == len(conflicts),
        scope_leaks == 0,
        result["no_match_precision"] >= 0.95,
        p95_ms < 500,
    ])
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            "# ATLAS R3 combined development fusion gate\n\n"
            "> Fresh development evidence over implemented R3 source fusion; not a sealed holdout or live-answer proof.\n\n"
            f"- Exact-ID recall@5: `{result['recall_at_5']:.1%}`\n"
            f"- MRR: `{result['mean_reciprocal_rank']:.3f}`\n"
            f"- Exact IDs rank first: `{result['exact_id_rank1']}/{result['exact_id_cases']}`\n"
            f"- Fresh authoritative state outranks conflicting lexical+semantic notes: `{result['fresh_state_precedence']}/{result['fresh_state_conflicts']}`\n"
            f"- Scope leaks: `{result['scope_leaks']}/{result['scope_checks']}`\n"
            f"- No-match precision: `{result['no_match_precision']:.1%}`\n"
            f"- Fusion p95: `{result['fusion_p95_ms']:.3f} ms`\n"
            f"- Gate: `{'PASS' if result['passed'] else 'FAIL'}`\n",
            encoding="utf-8",
        )
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
