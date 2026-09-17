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
    counterfactual = (
        "If the network is down during a read-only check for the memory maintenance report while investigating router failover, "
        "assume no user data may be changed; what should the assistant do first? "
        "What should it explicitly avoid claiming until verification succeeds?"
    )
    assert atlas_readonly.is_counterfactual_boundary_reasoning_question(counterfactual)
    assert atlas_readonly.classify_question(counterfactual) == "counterfactual_boundary_reasoning"
    boundary = atlas_readonly.run_counterfactual_boundary_reasoning(counterfactual)
    assert boundary["result_status"] == "ok", boundary
    assert boundary["route"] == "counterfactual_boundary_reasoning", boundary
    assert boundary["tool"] == "local-counterfactual-boundary-reasoning", boundary
    assert "First" in boundary["answer"], boundary
    assert "avoid claiming" in boundary["answer"], boundary
    assert "verification succeeds" in boundary["answer"] or "verification is incomplete" in boundary["answer"], boundary
    assert "should not change user data" in boundary["answer"], boundary

    tradeoff = (
        "For the ATLAS mobile PWA, imagine we optimize encrypted backups aggressively and accept a little extra latency; "
        "what is the most likely benefit and the most likely downside? "
        "If the owner complains that it feels slower, what measurement would tell us whether the tradeoff is actually worth keeping?"
    )
    assert atlas_readonly.is_causal_tradeoff_reasoning_question(tradeoff)
    assert atlas_readonly.classify_question(tradeoff) == "causal_tradeoff_reasoning"
    causal = atlas_readonly.run_causal_tradeoff_reasoning(tradeoff)
    assert causal["result_status"] == "ok", causal
    assert causal["route"] == "causal_tradeoff_reasoning", causal
    assert causal["tool"] == "local-causal-tradeoff-reasoning", causal
    assert "benefit" in causal["answer"].lower(), causal
    assert "downside" in causal["answer"].lower(), causal
    assert "measure" in causal["answer"].lower() or "latency" in causal["answer"].lower(), causal

    generic = "What is the capital of Japan?"
    assert not atlas_readonly.is_counterfactual_boundary_reasoning_question(generic)
    assert not atlas_readonly.is_causal_tradeoff_reasoning_question(generic)

    halloween = "How many days away is Halloween 2026 from September 15, 2026?"
    assert atlas_readonly.classify_question(halloween) == "countdown"
    halloween_result = atlas_readonly.run_countdown(halloween)
    assert halloween_result["result_status"] == "ok", halloween_result
    assert halloween_result["result"]["days"] == 46, halloween_result
    assert "46 days" in halloween_result["answer"], halloween_result

    drive_time = (
        "A route is 186 miles. One option averages 62 mph and another averages 48 mph. "
        "How long does each take, and which is shorter?"
    )
    assert atlas_readonly.classify_question(drive_time) == "quantitative_reasoning"
    drive_result = atlas_readonly.run_quantitative_reasoning(drive_time)
    assert drive_result["result_status"] == "ok", drive_result
    assert "3 hours" in drive_result["answer"], drive_result
    assert "3.88 hours" in drive_result["answer"], drive_result
    assert "62 mph option is shorter" in drive_result["answer"], drive_result

    date_offset = (
        "If today is September 15, 2026, what date is 18 days from now? "
        "If I wanted a reminder the day before, what date should that be?"
    )
    assert atlas_readonly.classify_question(date_offset) == "countdown"
    offset_result = atlas_readonly.run_countdown(date_offset)
    assert offset_result["result_status"] == "ok", offset_result
    assert offset_result["result"]["event_date"] == "2026-10-03", offset_result
    assert offset_result["result"]["reminder_date"] == "2026-10-02", offset_result

    stale_signal = "If a cached service signal is 30 minutes old but the user asks if the service is healthy right now, what should ATLAS say?"
    assert atlas_readonly.classify_question(stale_signal) == "source_freshness_reasoning"
    stale_result = atlas_readonly.run_source_freshness_reasoning(stale_signal)
    assert stale_result["result_status"] == "ok", stale_result
    assert "stale" in stale_result["answer"].lower(), stale_result
    assert "fresh read-only check" in stale_result["answer"].lower(), stale_result

    timeout_prompt = "Explain the difference between a local model timeout and a network timeout in two short bullets, and tell me what you would check first."
    assert atlas_readonly.classify_question(timeout_prompt) == "timeout_explanation_reasoning"
    timeout_result = atlas_readonly.run_timeout_explanation_reasoning(timeout_prompt)
    assert timeout_result["result_status"] == "ok", timeout_result
    assert "Local model timeout" in timeout_result["answer"], timeout_result
    assert "Network timeout" in timeout_result["answer"], timeout_result
    assert "First check" in timeout_result["answer"], timeout_result
    print("PASS: DEF-025 counterfactual boundary and causal tradeoff routes")


if __name__ == "__main__":
    main()
