#!/usr/bin/env python3

import importlib.util
import json
import pathlib
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools" / "ai"


def load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


generator = load("atlas_orchestration_generator", TOOLS / "generate-atlas-orchestration-suite.py")
runner = load("atlas_orchestration_eval_runner_test", TOOLS / "run-atlas-orchestration-eval.py")


def decision_for(case: dict) -> dict:
    expected = case["expected"]
    return {
        "mode": expected["modes"][0],
        "capabilities": expected["capability_sets"][0],
        "context_scope": expected["context_scopes"][0],
        "risk": expected["risks"][0],
        "freshness": expected["freshness"][0],
        "needs_verification": expected["needs_verification"][0],
        "reason_code": "policy_boundary" if expected.get("safety_critical") else "stable_knowledge",
        "confidence": 0.8,
    }


def main() -> None:
    development = generator.generate(80, 2026090103, "stage2-development-a")
    release = generator.generate(400, 2026090104, "stage2-release-a")
    assert development == generator.generate(80, 2026090103, "stage2-development-a")
    assert len(development) == 80
    assert len(release) == 400
    assert len({item["id"] for item in release}) == 400
    assert len({generator.case_signature(item) for item in release}) == 400
    assert generator.normalized(
        "For request label Amber Kite 123, Request 9: Explain why ice floats."
    ) == "explain why ice floats."
    assert {item["category"] for item in release} == set(generator.CATEGORIES)
    assert all(sum(item["category"] == category for item in release) == 50 for category in generator.CATEGORIES)
    assert all(item["eval_only"] is True for item in release)
    assert all("answer" not in item for item in release)
    assert all(item["source"]["reported_license"] == "CC0-1.0" for item in release)

    stable = next(item for item in development if item["category"] == "stable_direct")
    stable_score = runner.score_case(
        stable,
        {"status": "completed", "model": "test", "decision": decision_for(stable)},
        12,
    )
    assert stable_score["plan_ok"]
    assert stable_score["schema_valid"]

    refusal = next(item for item in development if item["category"] == "policy_refusal")
    refusal_score = runner.score_case(
        refusal,
        {"status": "completed", "model": "test", "decision": decision_for(refusal)},
        14,
    )
    assert refusal_score["plan_ok"]
    assert refusal_score["safety_ok"]

    invalid_score = runner.score_case(refusal, {"status": "invalid", "error_type": "invalid_json"}, 10)
    assert not invalid_score["schema_valid"]
    assert not invalid_score["plan_ok"]
    assert not invalid_score["safety_ok"]

    perfect_results = [
        runner.score_case(
            case,
            {"status": "completed", "model": "test", "decision": decision_for(case)},
            10 + index,
        )
        for index, case in enumerate(development)
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        suite_path = pathlib.Path(temp_dir) / "suite.json"
        suite_path.write_text(json.dumps(development) + "\n", encoding="utf-8")
        report = runner.build_report(suite_path, development, perfect_results, "test-run", 1.0)
    assert report["gate"]["passed"]
    assert report["plan_accuracy"] == 1.0
    assert report["safety"]["pass_rate"] == 1.0
    serialized = json.dumps(report).lower()
    assert '"question"' not in serialized
    assert "raw_model_output" in serialized and '"results_include_raw_model_output": false' in serialized

    print("ATLAS orchestration evaluation tests passed")


if __name__ == "__main__":
    main()
