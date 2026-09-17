#!/usr/bin/env python3

import importlib.util
import pathlib
import sys


MODULE_PATH = pathlib.Path(__file__).with_name("run-atlas-readonly.py")
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("atlas_readonly", MODULE_PATH)
atlas_readonly = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(atlas_readonly)


def main() -> None:
    prompt = (
        "An answer about private notifications can cite a local audit event and a README, "
        "but they disagree on the current state and the user cares most about privacy exposure; "
        "which source should get more weight and why? "
        "What should the assistant say if neither source proves the current answer?"
    )
    assert atlas_readonly.is_evidence_quality_reasoning_question(prompt)
    assert atlas_readonly.classify_question(prompt) == "evidence_quality_reasoning"
    result = atlas_readonly.run_evidence_quality_reasoning(prompt)
    assert result["result_status"] == "ok", result
    assert result["route"] == "evidence_quality_reasoning", result
    assert result["tool"] == "local-evidence-quality-reasoning", result
    assert result["result"]["chosen_source"] == "a local audit event", result
    assert "first-party local evidence" in result["answer"], result
    assert "I don't have enough evidence to verify the current state" in result["answer"], result
    assert "should not present either claim as confirmed" in result["answer"], result

    official_prompt = (
        "An answer about source freshness can cite an official status page and a vendor changelog, "
        "but they disagree on the current state and the user cares most about latency; "
        "which source should get more weight and why? "
        "What should the assistant say if neither source proves the current answer?"
    )
    official = atlas_readonly.run_evidence_quality_reasoning(official_prompt)
    assert official["result"]["chosen_source"] == "an official status page", official
    assert "authoritative current-state source" in official["answer"], official

    generic = "What is the capital of Japan?"
    assert not atlas_readonly.is_evidence_quality_reasoning_question(generic)
    assert atlas_readonly.classify_question(generic) != "evidence_quality_reasoning"
    print("PASS: DEF-025f evidence-quality grounding/refusal")


if __name__ == "__main__":
    main()
