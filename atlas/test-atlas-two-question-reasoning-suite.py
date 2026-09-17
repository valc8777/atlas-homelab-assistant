#!/usr/bin/env python3
"""Validate the generated ATLAS two-question reasoning suite."""

from __future__ import annotations

import importlib.util
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[2]
GENERATOR_PATH = ROOT / "tools" / "ai" / "generate-atlas-two-question-reasoning-suite.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("atlas_twoq_reasoning_generator", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> None:
    gen = load_generator()
    cases = gen.generate(1000, 20260913, "test-two-question-reasoning", skip_disjointness=True)
    assert len(cases) == 1000
    ids = [item["id"] for item in cases]
    questions = [item["question"] for item in cases]
    assert len(set(ids)) == len(ids)
    assert len(set(gen.normalized(q) for q in questions)) == len(questions)
    assert all(q.count("?") == 2 for q in questions)
    assert all(item["context_scope"] == "personal" for item in cases)
    assert all(item["eval_only"] is True for item in cases)
    assert all(item["scoring"]["manual_review_required"] is True for item in cases)
    assert all(item["scoring"]["must_answer_both_questions"] is True for item in cases)
    assert all("home.light.manage" not in str(item) for item in cases)
    counts = {category: 0 for category in gen.CATEGORIES}
    for item in cases:
        counts[item["category"]] += 1
    assert set(counts.values()) == {100}, counts
    print("PASS: generated 1,000 unique two-question reasoning prompts across 10 categories")


if __name__ == "__main__":
    main()
