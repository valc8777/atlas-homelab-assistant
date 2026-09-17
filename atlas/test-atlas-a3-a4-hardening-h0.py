#!/usr/bin/env python3
"""Mechanical validation for the H0 development/holdout artifacts."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from collections import Counter


ROOT = pathlib.Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "ai-evals" / "orchestration"
DEV = EVAL_DIR / "atlas-a3-a4-hardening-h0-development-120-seed-2026091105.json"
HOLDOUT = EVAL_DIR / "atlas-a3-a4-hardening-h0-holdout-30-seed-2026091105.json"
MANIFEST = EVAL_DIR / "atlas-a3-a4-hardening-h0-manifest.json"
CATEGORIES = {
    "temporal_mismatch", "semantic_step_failure", "compound_capability_omission",
    "direct_tool_bypass", "partial_fallback", "planner_timeout",
}


def norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prior_questions() -> set[str]:
    excluded = {DEV.resolve(), HOLDOUT.resolve(), MANIFEST.resolve()}
    found: set[str] = set()
    for path in (ROOT / "ai-evals").rglob("*.json"):
        if path.resolve() in excluded:
            continue
        try:
            stack = [json.loads(path.read_text(encoding="utf-8"))]
        except (OSError, ValueError):
            continue
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                question = item.get("question")
                if isinstance(question, str):
                    found.add(norm(question))
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    return found


def main() -> None:
    dev = json.loads(DEV.read_text(encoding="utf-8"))
    holdout = json.loads(HOLDOUT.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert len(dev) == 120 and len(holdout) == 30
    assert Counter(item["category"] for item in dev) == Counter({name: 20 for name in CATEGORIES})
    assert Counter(item["category"] for item in holdout) == Counter({name: 5 for name in CATEGORIES})
    dev_q = {norm(item["question"]) for item in dev}
    holdout_q = {norm(item["question"]) for item in holdout}
    ids = {item["id"] for item in dev + holdout}
    assert len(dev_q) == len(dev) and len(holdout_q) == len(holdout)
    assert len(ids) == len(dev) + len(holdout)
    assert not dev_q & holdout_q
    assert not (dev_q | holdout_q) & prior_questions()
    for item in dev + holdout:
        assert item["category"] in CATEGORIES
        assert item["context_scope"] in {"personal", "operations"}
        assert item["conversation"] == []
        assert isinstance(item["expected"], dict) and item["expected"]
        lowered = item["question"].casefold()
        assert not re.search(r"\b(?:turn|switch|flip)\b.{0,24}\b(?:lamp|light)\b", lowered)
    assert manifest["status"] == "development_and_holdout_complete"
    assert set(manifest["categories"]) == CATEGORIES
    assert manifest["development"]["category_counts"] == {name: 20 for name in CATEGORIES}
    assert manifest["holdout"]["category_counts"] == {name: 5 for name in CATEGORIES}
    assert manifest["holdout"]["opened"] is True
    assert manifest["holdout"]["run_count"] == 1
    assert manifest["development"]["sha256"] == digest(DEV)
    assert manifest["holdout"]["sha256"] == digest(HOLDOUT)
    assert manifest["disjointness"] == {
        "development_holdout_exact_overlap": 0,
        "prior_eval_exact_overlap": 0,
    }
    print("PASS: H0 artifacts remain disjoint and the holdout manifest records its one authorized run")


if __name__ == "__main__":
    main()
