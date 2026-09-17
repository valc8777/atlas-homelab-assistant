#!/usr/bin/env python3

import importlib.util
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = ROOT / "tools" / "ai" / "run-atlas-app-regressions.py"
SPEC = importlib.util.spec_from_file_location("atlas_eval_runner", PATH)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(runner)


def main() -> None:
    assert not runner.semantic_failures(
        {"scoring": {"any_of": ["volcanic rock", "igneous rock"], "minimum_length": 10}},
        "Basalt is a fine-grained igneous rock.",
        "general_reference",
    )
    assert not runner.semantic_failures(
        {"scoring": {"numeric": {"value": 36, "tolerance": 0.01}}},
        "The result is 36.0 square units.",
        "math",
    )
    failures = runner.semantic_failures(
        {"scoring": {"all_of": ["permission"], "none_of": ["completed"]}},
        "The action completed.",
        "admin",
    )
    assert "missing: permission" in failures
    assert "forbidden: completed" in failures
    print("ATLAS eval semantic scoring test passed")


if __name__ == "__main__":
    main()
