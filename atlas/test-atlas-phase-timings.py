#!/usr/bin/env python3
"""H5 tests: privacy-safe phase timings (queue wait, first model call,
repair call, per-step execution, synthesis) must be captured end-to-end
without ever carrying question text, model output, or answer content.
"""

from __future__ import annotations

import pathlib
import sys
import time
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "tools" / "ai"
sys.path.insert(0, str(AI_DIR))

import importlib.util

SPEC = importlib.util.spec_from_file_location("atlas_runner_phase_timings", AI_DIR / "run-atlas-readonly.py")
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)

import atlas_shortcuts_server as server


def _direct_tool_call(name: str) -> dict:
    return {"tool_calls": [{"function": {"name": name, "arguments": "{}"}}]}


def main() -> None:
    # DEF-024: deterministic clarification must short-circuit the shadow-plan
    # runner before native/model planning, otherwise a model validation miss can
    # fall through to legacy weather/reference and silently answer one clause.
    with mock.patch.object(runner, "run_shadow_plan_native") as native_mock:
        clarification = runner.run_shadow_plan(
            question="Who won the first Daytona, and what is the weather in Martinez tomorrow?",
            context_scope="personal",
            conversation=[],
            base_url="http://fake",
            db_path=pathlib.Path("/tmp/unused.db"),
            model=None,
            timeout=30,
            system_prompt_file=pathlib.Path("/tmp/unused.txt"),
            native_tools=True,
        )
    native_mock.assert_not_called()
    assert clarification["status"] == "completed"
    assert clarification["decision_source"] == "hard_clarification"
    assert clarification["decision"]["mode"] == "clarify"

    # -- run_shadow_plan_native(): a clean first-try decision reports
    # queue_wait_ms and first_call_ms, and an explicit null repair_call_ms --
    def slow_first_call(**kwargs):
        time.sleep(0.02)
        return _direct_tool_call("answer_directly"), "qwen3:8b"

    with mock.patch.object(runner, "call_model_with_tools", side_effect=slow_first_call) as call_mock:
        result = runner.run_shadow_plan_native(
            question="Explain a stable concept.",
            context_scope="personal",
            conversation=None,
            base_url="http://fake",
            db_path=pathlib.Path("/tmp/unused.db"),
            model=None,
            timeout=30,
        )
    assert call_mock.call_count == 1
    timings = result["phase_timings"]
    assert set(timings) == {"queue_wait_ms", "first_call_ms", "repair_call_ms", "clarify_recheck"}
    assert isinstance(timings["queue_wait_ms"], int) and timings["queue_wait_ms"] >= 0
    assert timings["first_call_ms"] >= 20  # the injected 20ms sleep
    assert timings["repair_call_ms"] is None
    # DEF-027: a non-clarify first decision must not trigger any recheck call.
    assert timings["clarify_recheck"] == {"attempts": 0, "recheck_ms": 0, "overturned": False}

    # -- a rejected-then-repaired decision reports both call durations --
    call_count = {"n": 0}

    def first_then_repair(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"tool_calls": []}, "qwen3:8b"  # no_tool_call -> triggers repair
        time.sleep(0.03)
        return _direct_tool_call("answer_directly"), "qwen3:8b"

    with mock.patch.object(runner, "call_model_with_tools", side_effect=first_then_repair) as call_mock:
        result = runner.run_shadow_plan_native(
            question="Explain a stable concept.",
            context_scope="personal",
            conversation=None,
            base_url="http://fake",
            db_path=pathlib.Path("/tmp/unused.db"),
            model=None,
            timeout=30,
        )
    assert call_mock.call_count == 2
    assert result["decision_source"] == "native_tool_call_repair"
    timings = result["phase_timings"]
    assert timings["repair_call_ms"] is not None and timings["repair_call_ms"] >= 30

    # -- privacy: phase_timings is durations/ints only, never question text,
    # model content, or the decision itself. clarify_recheck is the one
    # nested field; its own values must be the same duration/count/bool
    # shape, not richer than that. --
    import json
    serialized = json.dumps(timings).lower()
    assert "explain a stable concept" not in serialized
    for key, value in timings.items():
        if key == "clarify_recheck":
            assert isinstance(value["attempts"], int) and isinstance(value["overturned"], bool)
            assert isinstance(value["recheck_ms"], int)
        else:
            assert isinstance(value, (int, type(None)))

    # -- DEF-027: a clarify decision that gets overturned on the very next
    # call is used instead of the original refusal, and the recheck is
    # reported as durations/counts only. --
    def clarify_then_direct(**kwargs):
        clarify_then_direct.calls += 1
        if clarify_then_direct.calls == 1:
            return _direct_tool_call("clarify_request"), "qwen3:8b"
        return _direct_tool_call("answer_directly"), "qwen3:8b"

    clarify_then_direct.calls = 0
    with mock.patch.object(runner, "call_model_with_tools", side_effect=clarify_then_direct) as call_mock:
        result = runner.run_shadow_plan_native(
            question="What year did the civil war start?",
            context_scope="personal",
            conversation=None,
            base_url="http://fake",
            db_path=pathlib.Path("/tmp/unused.db"),
            model=None,
            timeout=30,
        )
    assert call_mock.call_count == 2  # first call + one recheck, not the full max
    assert result["decision"]["mode"] == "direct"
    assert result["decision_source"] == "native_tool_call_clarify_recheck"
    recheck = result["phase_timings"]["clarify_recheck"]
    assert recheck == {"attempts": 1, "overturned": True, "recheck_ms": recheck["recheck_ms"]}
    assert isinstance(recheck["recheck_ms"], int) and recheck["recheck_ms"] >= 0

    # -- DEF-027: if every recheck attempt also says clarify, the original
    # refusal is kept (not silently dropped), and the attempt count reflects
    # the full bounded retry budget. Uses a genuinely unscoped superlative
    # (no named domain), not the "longest game of X" shape -- that one now
    # takes the deterministic override path tested separately below. --
    with mock.patch.object(runner, "call_model_with_tools", return_value=(_direct_tool_call("clarify_request"), "qwen3:8b")) as call_mock:
        result = runner.run_shadow_plan_native(
            question="What's the best show?",
            context_scope="personal",
            conversation=None,
            base_url="http://fake",
            db_path=pathlib.Path("/tmp/unused.db"),
            model=None,
            timeout=30,
        )
    assert call_mock.call_count == 1 + runner._CLARIFY_RECHECK_MAX_ATTEMPTS
    assert result["decision"]["mode"] == "clarify"
    assert result["decision_source"] == "native_tool_call"
    assert result["phase_timings"]["clarify_recheck"]["attempts"] == runner._CLARIFY_RECHECK_MAX_ATTEMPTS
    assert result["phase_timings"]["clarify_recheck"]["overturned"] is False

    # -- DEF-027 Phase 2 (DEC-2026-09-16-12): a named-domain superlative
    # record question ("longest game of X ever played") overrides clarify
    # to direct deterministically, with ZERO extra model calls -- no amount
    # of resampling ever overturns this one (DEC-2026-09-16-11), so paying
    # for more calls would be pure waste. --
    with mock.patch.object(runner, "call_model_with_tools", return_value=(_direct_tool_call("clarify_request"), "qwen3:8b")) as call_mock:
        result = runner.run_shadow_plan_native(
            question="What is the longest game of foosball ever played? What about football?",
            context_scope="personal",
            conversation=None,
            base_url="http://fake",
            db_path=pathlib.Path("/tmp/unused.db"),
            model=None,
            timeout=30,
        )
    assert call_mock.call_count == 1  # no recheck calls spent on a case resampling never fixes
    assert result["decision"]["mode"] == "direct"
    assert result["decision_source"] == "native_tool_call_clarify_recheck"
    recheck = result["phase_timings"]["clarify_recheck"]
    assert recheck == {"attempts": 0, "recheck_ms": 0, "overturned": True, "override": "scoped_superlative_record"}

    # -- DEF-027 Phase 2 (DEC-2026-09-16-12): un-gating the deterministic
    # pre-check let the model's own choice through, and it turned out to
    # pick refuse almost as often as clarify for this exact question shape
    # -- refuse is just as wrong here (no policy/authority boundary applies),
    # so the same override must also catch mode=refuse, not just clarify. --
    with mock.patch.object(runner, "call_model_with_tools", return_value=(_direct_tool_call("refuse_request"), "qwen3:8b")) as call_mock:
        result = runner.run_shadow_plan_native(
            question="What is the longest game of foosball ever played? What about football?",
            context_scope="personal",
            conversation=None,
            base_url="http://fake",
            db_path=pathlib.Path("/tmp/unused.db"),
            model=None,
            timeout=30,
        )
    assert call_mock.call_count == 1
    assert result["decision"]["mode"] == "direct"
    recheck = result["phase_timings"]["clarify_recheck"]
    assert recheck == {"attempts": 0, "recheck_ms": 0, "overturned": True, "override": "scoped_superlative_record"}

    # -- a genuine refuse (real policy boundary, doesn't match the narrow
    # pattern) must NEVER be touched by this override. --
    with mock.patch.object(runner, "call_model_with_tools", return_value=(_direct_tool_call("refuse_request"), "qwen3:8b")) as call_mock:
        result = runner.run_shadow_plan_native(
            question="Read another person's private calendar without permission.",
            context_scope="personal",
            conversation=None,
            base_url="http://fake",
            db_path=pathlib.Path("/tmp/unused.db"),
            model=None,
            timeout=30,
        )
    assert call_mock.call_count == 1
    assert result["decision"]["mode"] == "refuse"
    assert result["phase_timings"]["clarify_recheck"] == {"attempts": 0, "recheck_ms": 0, "overturned": False}

    # -- end-to-end: run_atlas_with_readonly_canary() propagates
    # phase_timings from the planner payload and synthesis_ms from a
    # deep-plan result into telemetry --
    plan_payload = {
        "status": "completed",
        "decision_source": "native_tool_call",
        "decision": {"mode": "deep", "context_scope": "operations", "capabilities": ["weather.forecast", "public.reference"]},
        "phase_timings": {"queue_wait_ms": 2, "first_call_ms": 4200, "repair_call_ms": None},
    }
    deep_result = {
        "route": "deep_plan_synthesis",
        "tool": "atlas-deep-plan-synthesis",
        "answer": "Synthesized answer.",
        "source": "ATLAS deep-plan synthesis",
        "result": {
            "steps": [
                {"ordinal": 1, "step_id": "step-1", "capability": "weather.forecast", "status": "ok",
                 "error_type": None, "answer": "65F.", "source": "open-meteo", "latency_ms": 150},
            ],
            "synthesis_status": "ok",
            "synthesis_ms": 900,
        },
    }
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas"
    ), mock.patch.object(
        server, "run_orchestrator_plan", return_value=plan_payload
    ), mock.patch.object(
        server, "run_deep_plan_readonly", return_value=dict(deep_result)
    ):
        outcome = server.run_atlas_with_readonly_canary("Research the weather.", "operations")
    canary = outcome["orchestrator_canary"]
    assert canary["phase_timings"] == {"queue_wait_ms": 2, "first_call_ms": 4200, "repair_call_ms": None}
    assert canary["synthesis_ms"] == 900
    assert canary["step_status_list"][0]["latency_ms"] == 150

    print("PASS: H5 privacy-safe phase timings captured end-to-end")


if __name__ == "__main__":
    main()
