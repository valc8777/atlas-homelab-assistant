#!/usr/bin/env python3
"""Fresh, repeatable development gate for the default-off R3 answer canary."""

from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atlas_app_store
import atlas_entity_registry
import atlas_hybrid_retrieval
import atlas_r3_answer_canary


ROOT = pathlib.Path(__file__).resolve().parents[2]


def evaluate(registry_path: pathlib.Path) -> dict:
    registry = atlas_entity_registry.load_registry_file(registry_path)
    category = {name: {"passed": 0, "total": 0} for name in (
        "grounded_answers", "citation_contract", "scope_isolation",
        "honest_no_match", "current_state_freshness", "rollback",
    )}

    def check(name: str, condition: bool) -> None:
        category[name]["total"] += 1
        category[name]["passed"] += int(bool(condition))

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = pathlib.Path(temp_dir) / "atlas.db"
        atlas_app_store.init_db(db_path)
        notes = {
            "personal": atlas_app_store.create_note(db_path, "Tea timer", "Steep green tea for three minutes.", "personal"),
            "operations": atlas_app_store.create_note(db_path, "Media mount", "Jellyfin media uses the atlas-media dataset.", "operations"),
            "admin": atlas_app_store.create_note(db_path, "Maintenance window", "The private maintenance window starts at 02:00.", "admin"),
        }
        atlas_app_store.set_preference(db_path, "coffee roast", "dark roast")
        atlas_app_store.set_preference(db_path, "desk theme", "purple")

        grounded = [
            ("What saved note says how long to steep green tea?", "personal", "three minutes"),
            ("What note is saved about Jellyfin media?", "operations", "atlas-media"),
            ("What saved note describes the maintenance window?", "admin", "02:00"),
            ("What coffee roast preference is saved?", "personal", "dark roast"),
            ("What desk theme do I prefer?", "personal", "purple"),
            ("Which saved note says where Jellyfin media lives?", "operations", "atlas-media"),
            ("Which saved note mentions the media dataset?", "operations", "atlas-media"),
            ("What did I save about green tea?", "personal", "three minutes"),
        ]
        for query, scope, needle in grounded:
            retrieval = atlas_hybrid_retrieval.retrieve_candidates(query, scope, db_path, registry)
            answer = atlas_r3_answer_canary.answer_from_candidates(
                query, scope, db_path, registry, retrieval
            )
            matched = bool(answer and answer["result"]["status"] == "matched" and needle in answer["answer"])
            check("grounded_answers", matched)
            check(
                "citation_contract",
                bool(matched and answer["result"]["citation_count"] == answer["result"]["evidence_count"]
                     and "\nSources:\n[1]" in answer["answer"]),
            )

        # ID-only semantic candidates must be re-fetched under exact scope.
        for scope in ("personal", "operations", "admin"):
            note = notes[scope]
            retrieval = atlas_hybrid_retrieval.retrieve_candidates(
                "What saved detail is relevant?", scope, db_path, registry,
                semantic_matches=[{
                    "candidate_id": f"note:{note['id']}", "source_type": "note",
                    "context_scope": scope, "similarity": 0.93,
                }],
            )
            answer = atlas_r3_answer_canary.answer_from_candidates(
                "What saved detail is relevant?", scope, db_path, registry, retrieval
            )
            check("grounded_answers", bool(answer and answer["result"]["status"] == "matched"))
            check("citation_contract", bool(answer and "ATLAS explicit note" in answer["answer"]))

        # Directly injected IDs simulate a compromised/stale semantic locator.
        scopes = ("personal", "operations", "admin")
        for requested_scope in scopes:
            for source_scope in scopes:
                if requested_scope == source_scope:
                    continue
                candidate = {
                    "status": "matched", "notices": [],
                    "candidates": [{
                        "candidate_id": f"note:{notes[source_scope]['id']}",
                        "source_type": "note",
                    }],
                }
                answer = atlas_r3_answer_canary.answer_from_candidates(
                    "What saved note is on file?", requested_scope, db_path, registry, candidate
                )
                check("scope_isolation", bool(answer and answer["result"]["status"] == "no_match"))

        for query in (
            "What note did I save about a lunar greenhouse?",
            "Do I have a saved preference for xylophone strings?",
            "What is on file about the quasar orchard?",
            "Did I mention a cobalt submarine?",
            "What notes mention the nebula pantry?",
            "What preference is saved for a zeppelin engine?",
        ):
            retrieval = atlas_hybrid_retrieval.retrieve_candidates(query, "personal", db_path, registry)
            answer = atlas_r3_answer_canary.answer_from_candidates(
                query, "personal", db_path, registry, retrieval
            )
            check("honest_no_match", bool(answer and answer["result"]["status"] == "no_match" and "won't guess" in answer["answer"]))

        old_memory = atlas_hybrid_retrieval.retrieve_candidates(
            "Are Jellyfin services unreachable right now?", "operations", db_path, registry
        )
        for unavailable_status in ("stale", "future", "error", "invalid_value", "unavailable"):
            answer = atlas_r3_answer_canary.answer_from_candidates(
                "Are Jellyfin services unreachable right now?", "operations", db_path, registry,
                old_memory,
                current_state_result={
                    "status": "unavailable", "candidates": [],
                    "notices": [{"status": unavailable_status}],
                },
            )
            check(
                "current_state_freshness",
                bool(answer and answer["result"]["status"] == "current_state_unavailable"
                     and "atlas-media" not in answer["answer"] and "won't substitute" in answer["answer"]),
            )

        current_candidate = {
            "candidate_id": "current_state:service_reachability", "subject_id": "current_state:service_reachability",
            "source_type": "structured_current_state", "context_scope": "operations",
            "title": "Current monitored-service reachability", "text": "1 of 12 monitored services is unreachable.",
            "source": "ATLAS Phase 2b signal observation", "updated_at": "2026-09-12T14:00:00-07:00",
            "lifecycle": "active", "freshness": "current", "match_method": "structured_current_state", "score": 950.0,
        }
        retrieval = atlas_hybrid_retrieval.retrieve_candidates(
            "Are services unreachable right now?", "operations", db_path, registry,
            current_state_candidates=[current_candidate],
        )
        answer = atlas_r3_answer_canary.answer_from_candidates(
            "Are services unreachable right now?", "operations", db_path, registry, retrieval,
            current_state_result={"status": "matched", "candidates": [current_candidate], "notices": []},
        )
        check(
            "current_state_freshness",
            bool(answer and "1 of 12" in answer["answer"] and "Phase 2b" in answer["answer"]
                 and "atlas-media" not in answer["answer"]
                 and answer["result"]["source_types"] == ["structured_current_state"]),
        )

        general_fallthrough = atlas_r3_answer_canary.answer_from_candidates(
                "Explain photosynthesis", "personal", db_path, registry,
                {"status": "no_match", "candidates": [], "notices": []},
            ) is None
        entity_result = atlas_hybrid_retrieval.retrieve_candidates(
            "Tell me about proxmox.vm.202", "operations", db_path, registry
        )
        entity_fallthrough = atlas_r3_answer_canary.answer_from_candidates(
            "Tell me about proxmox.vm.202", "operations", db_path, registry, entity_result
        ) is None
        check(
            "rollback",
            general_fallthrough and entity_fallthrough,
        )

    passed = sum(item["passed"] for item in category.values())
    total = sum(item["total"] for item in category.values())
    return {"passed": passed, "total": total, "categories": category}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--registry", type=pathlib.Path,
        default=ROOT / "atlas-entity-registry-2026-09-11.json",
    )
    parser.add_argument("--report", type=pathlib.Path)
    args = parser.parse_args()
    result = evaluate(args.registry)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# ATLAS R3 answer-canary development gate", "",
            "> Fresh repeatable development evidence; not deployment or live-path proof.", "",
            f"- Overall: `{result['passed']}/{result['total']}`", "",
            "| Category | Passed | Total |", "|---|---:|---:|",
        ]
        for name, values in result["categories"].items():
            lines.append(f"| `{name}` | {values['passed']} | {values['total']} |")
        lines.extend([
            "", "## Boundaries", "",
            "- The development run did not enable the answer consumer; deployment status is tracked in the canonical ATLAS documents.",
            "- Semantic cases used ID-only candidate fixtures and canonical scope re-fetch.",
            "- Current-state failure cases prohibited fallback to historical notes.",
            "- This is an open development set, not a sealed holdout.", "",
        ])
        args.report.write_text("\n".join(lines), encoding="utf-8")
    print(f"PASS: R3 answer-canary development gate {result['passed']}/{result['total']}" if result["passed"] == result["total"] else f"FAIL: R3 answer-canary development gate {result['passed']}/{result['total']}")
    if result["passed"] != result["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
