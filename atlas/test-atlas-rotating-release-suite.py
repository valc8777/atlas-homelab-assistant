#!/usr/bin/env python3

import hashlib
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[2]
CYCLE = "2026-09-a"
SEED = 2026090101
RELEASE = ROOT / "ai-evals" / "rotating-release"
SUITE = RELEASE / f"atlas-rotating-release-{CYCLE}-400-seed-{SEED}.json"
MANIFEST = RELEASE / f"atlas-rotating-release-{CYCLE}-manifest.json"
DEVELOPMENT = ROOT / "ai-evals" / "mixed-acceptance" / "atlas-mixed-acceptance-development-80-seed-20260901.json"
HOLDOUT = ROOT / "ai-evals" / "mixed-acceptance" / "atlas-mixed-acceptance-holdout-20-seed-20260901.json"
CYCLE_C = RELEASE / "atlas-rotating-release-2026-09-c-400-seed-2026090301.json"


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def questions(path: pathlib.Path) -> set[str]:
    return {" ".join(item["question"].split()).casefold() for item in json.loads(path.read_text(encoding="utf-8"))}


def main() -> None:
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert len(suite) == 400
    assert len({item["id"] for item in suite}) == 400
    assert len({" ".join(item["question"].split()).casefold() for item in suite}) == 400
    assert digest(SUITE) == manifest["suite_sha256"]
    assert sum(manifest["category_counts"].values()) == 400
    assert min(manifest["category_counts"].values()) >= 40
    assert manifest["source_counts"] == {
        "ATLAS rotating synthetic release evaluation": 280,
        "BIG-bench JSON tasks": 120,
    }
    assert questions(SUITE).isdisjoint(questions(DEVELOPMENT) | questions(HOLDOUT))
    bigbench = [item for item in suite if item["source"]["dataset"] == "BIG-bench JSON tasks"]
    assert len(bigbench) == 120
    assert all(item["source"].get("canary") for item in bigbench)
    assert all(item.get("eval_only") is True for item in suite)

    candidate_questions = questions(CYCLE_C)
    prior_questions = questions(DEVELOPMENT) | questions(HOLDOUT)
    for prior_path in RELEASE.glob("atlas-rotating-release-*-400-seed-*.json"):
        if prior_path != CYCLE_C:
            prior_questions |= questions(prior_path)
    assert candidate_questions.isdisjoint(prior_questions)
    assert len(candidate_questions) == 400
    print("ATLAS rotating release suite test passed")


if __name__ == "__main__":
    main()
