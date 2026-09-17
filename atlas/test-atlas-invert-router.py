#!/usr/bin/env python3
"""Tests for Track A3 ("invert the router"), ATLAS_INVERT_ROUTER (2026-09-08).

Stage 1 of the approved plan: build the dispatch-order change behind a flag,
default off. This covers the three properties the plan requires before any
deploy:

1. With the flag off (default), run_atlas_with_readonly_canary() behaves
   byte-for-byte identically to today -- no new telemetry field, no new code
   path exercised at all.
2. With the flag on, deterministic-first carve-outs are never sent to the
   planner, regardless of what the planner would have decided. That includes
   home_light/home_read for the original physical-world safety carve-out and
   the narrow read-only
   countdown/quantitative_reasoning/closed_world_reasoning/
   counterfactual_boundary_reasoning/causal_tradeoff_reasoning/
   evidence_quality_reasoning routes for DEF-025a through DEF-025f.
3. With the flag on, every other question still goes planner-first exactly
   as A2's canary already does, with a new deciding_layer field recording
   which layer actually decided ("planner" vs "classifier_fallback").

classifier_route_is_deterministic_first() is also tested directly: it must
never raise (a classification failure should silently fall through to
planner-first, not break dispatch), and it must only special-case the routes
named above -- every other route (including "general_reference", which nothing
should ever short-circuit) returns None.
"""

from __future__ import annotations

import subprocess
from unittest import mock

import atlas_shortcuts_server as server


def plan(mode: str, capabilities: list[str], scope: str = "personal") -> dict:
    return {
        "status": "completed",
        "decision_source": "model",
        "decision": {
            "mode": mode,
            "context_scope": scope,
            "capabilities": capabilities,
        },
    }


FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}{(' -- ' + detail) if detail else ''}")
        FAILURES.append(label)


def test_classifier_pre_check_direct() -> None:
    print("\nclassifier_route_is_deterministic_first() -- direct, real classify_question(), no mocks")
    check("lamp control -> home_light -> deterministic-first",
          server.classifier_route_is_deterministic_first("Turn on the bedside lamp", "personal") == "home_light")
    check("lamp state read -> home_read -> deterministic-first",
          server.classifier_route_is_deterministic_first("Is the bedside lamp on?", "personal") == "home_read")
    check("quantitative prompt -> quantitative_reasoning -> deterministic-first",
          server.classifier_route_is_deterministic_first(
              "A backup is 556 GB and the real upload speed averages 50 Mbps after overhead; roughly how many hours should the upload take? If the backup window is 8 hours, is that enough?",
              "personal",
          ) == "quantitative_reasoning")
    check("countdown prompt -> countdown -> deterministic-first",
          server.classifier_route_is_deterministic_first("How far from Halloween?", "personal") == "countdown")
    check("closed-world prompt -> closed_world_reasoning -> deterministic-first",
          server.classifier_route_is_deterministic_first(
              "First the purple pen is placed inside the green charger, then the green charger is moved under the silver key, and finally the purple pen is taken out and put on the desk; where is the purple pen now? Where is the green charger now?",
              "personal",
          ) == "closed_world_reasoning")
    check("evidence-quality prompt -> evidence_quality_reasoning -> deterministic-first",
          server.classifier_route_is_deterministic_first(
              "An answer about private notifications can cite a local audit event and a README, but they disagree on the current state and the user cares most about privacy exposure; which source should get more weight and why? What should the assistant say if neither source proves the current answer?",
              "personal",
          ) == "evidence_quality_reasoning")
    check("counterfactual boundary prompt -> counterfactual_boundary_reasoning -> deterministic-first",
          server.classifier_route_is_deterministic_first(
              "If the network is down during a read-only check for the memory maintenance report while investigating router failover, assume no user data may be changed; what should the assistant do first? What should it explicitly avoid claiming until verification succeeds?",
              "personal",
          ) == "counterfactual_boundary_reasoning")
    check("causal tradeoff prompt -> causal_tradeoff_reasoning -> deterministic-first",
          server.classifier_route_is_deterministic_first(
              "For the ATLAS mobile PWA, imagine we optimize encrypted backups aggressively and accept a little extra latency; what is the most likely benefit and the most likely downside? If the owner complains that it feels slower, what measurement would tell us whether the tradeoff is actually worth keeping?",
              "personal",
          ) == "causal_tradeoff_reasoning")
    check("evidence-quality prompt is not swallowed by self-contained reasoning bypass",
          server.is_self_contained_reasoning_request(
              "An answer about private notifications can cite a local audit event and a README, but they disagree on the current state and the user cares most about privacy exposure; which source should get more weight and why? What should the assistant say if neither source proves the current answer?"
          ) is False)
    check("generic question -> general_reference -> NOT deterministic-first",
          server.classifier_route_is_deterministic_first("What is the capital of France?", "personal") is None)
    check("math question -> math -> NOT deterministic-first (not a carve-out route)",
          server.classifier_route_is_deterministic_first("What is 12 plus 5?", "personal") is None)

    with mock.patch.object(server, "_load_readonly_runner_module", side_effect=RuntimeError("boom")):
        check("a classification failure never raises, falls through as None",
              server.classifier_route_is_deterministic_first("Turn on the bedside lamp", "personal") is None)


def test_flag_off_is_byte_for_byte_unchanged() -> None:
    print("\nFlag off (default): behavior and telemetry shape byte-for-byte unchanged")
    legacy = {"answer": "legacy answer", "route": "general_reference", "tool": "openwebui-chat"}
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "ATLAS_INVERT_ROUTER_ENABLED", False
    ), mock.patch.object(
        server, "classifier_route_is_deterministic_first"
    ) as pre_check_mock, mock.patch.object(
        server, "run_atlas", return_value=dict(legacy)
    ), mock.patch.object(
        server, "run_orchestrator_plan", side_effect=subprocess.TimeoutExpired("planner", 1)
    ):
        result = server.run_atlas_with_readonly_canary("Turn on the bedside lamp", "personal")
    check("the A3 pre-check is never even called when the flag is off", pre_check_mock.call_count == 0)
    check("no deciding_layer field appears when the flag is off",
          "deciding_layer" not in result["orchestrator_canary"])
    check("existing telemetry fields are exactly as before",
          result["orchestrator_canary"]["status"] == "fallback"
          and result["orchestrator_canary"]["reason"] == "planner_timeout")


def test_pre_planner_def025_routes_beat_keyword_handlers() -> None:
    print("\nHTTP pre-planner: DEF-025 read-only reasoning beats older keyword handlers")
    prompt = (
        "If the network is down during a read-only check for the memory maintenance report while investigating router failover, "
        "assume no user data may be changed; what should the assistant do first? "
        "What should it explicitly avoid claiming until verification succeeds?"
    )
    legacy = {
        "answer": "boundary answer",
        "route": "counterfactual_boundary_reasoning",
        "tool": "local-counterfactual-boundary-reasoning",
    }
    with mock.patch.object(server, "run_atlas", return_value=dict(legacy)) as run_mock:
        result = server.run_deterministic_first_readonly_if_matched(prompt, "personal")
    check("counterfactual prompt is handled before memory_maintenance keyword routing",
          result and result["route"] == "counterfactual_boundary_reasoning")
    check("deterministic runner was executed once", run_mock.call_count == 1)

    with mock.patch.object(server, "run_atlas") as run_mock:
        result = server.run_deterministic_first_readonly_if_matched("Please run the memory maintenance report.", "personal")
    check("real memory-maintenance command is not intercepted by DEF-025 pre-handler", result is None)
    check("real memory-maintenance command did not execute the runner here", run_mock.call_count == 0)


def test_flag_on_deterministic_first_carveout_never_reaches_planner() -> None:
    print("\nFlag on: home_light/home_read never reach the planner, regardless of what it would have decided")
    legacy_answer = {"answer": "lamp is now on", "route": "home_light", "tool": "atlas-home-assistant"}
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "ATLAS_INVERT_ROUTER_ENABLED", True
    ), mock.patch.object(
        server, "run_atlas", return_value=dict(legacy_answer)
    ) as run_atlas_mock, mock.patch.object(
        server, "run_orchestrator_plan"
    ) as planner_mock, mock.patch.object(
        server, "run_atlas_selected_capability"
    ) as selected_mock:
        result = server.run_atlas_with_readonly_canary("Turn on the bedside lamp", "personal")
    check("the planner is never consulted for a deterministic-first route", planner_mock.call_count == 0)
    check("run_atlas_selected_capability is never called either", selected_mock.call_count == 0)
    check("run_atlas (the deterministic classifier's own executor) is called once", run_atlas_mock.call_count == 1)
    check("the answer is the deterministic executor's real answer", result["answer"] == "lamp is now on")
    check("deciding_layer is classifier_first", result["orchestrator_canary"]["deciding_layer"] == "classifier_first")
    check("decision_source records the carve-out, not a real planner decision",
          result["orchestrator_canary"]["decision_source"] == "deterministic_first_carveout")


def test_flag_on_deterministic_first_carveout_beats_a_confident_planner() -> None:
    print("\nFlag on: the carve-out wins even if the planner would have confidently selected a capability")
    legacy_answer = {"answer": "lamp is now off", "route": "home_light", "tool": "atlas-home-assistant"}
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "ATLAS_INVERT_ROUTER_ENABLED", True
    ), mock.patch.object(
        server, "run_atlas", return_value=dict(legacy_answer)
    ), mock.patch.object(
        # Even a planner that WOULD confidently answer must never be reached --
        # the carve-out check happens first and short-circuits before this call.
        server, "run_orchestrator_plan", return_value=plan("tool", ["home.light.manage"])
    ) as planner_mock:
        result = server.run_atlas_with_readonly_canary("Turn off the bedside lamp", "personal")
    check("planner still never called despite being ready to answer confidently", planner_mock.call_count == 0)
    check("deciding_layer is classifier_first", result["orchestrator_canary"]["deciding_layer"] == "classifier_first")


def test_flag_on_non_carveout_question_still_goes_planner_first() -> None:
    print("\nFlag on: an ordinary (non-carve-out) question still goes planner-first, exactly as A2's canary does")
    selected_result = {"answer": "retrieved answer", "route": "general_reference", "tool": "public-search"}
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "ATLAS_INVERT_ROUTER_ENABLED", True
    ), mock.patch.object(
        server, "run_atlas"
    ) as legacy_mock, mock.patch.object(
        server, "run_atlas_selected_capability", return_value=dict(selected_result)
    ) as capability_mock, mock.patch.object(
        server, "run_orchestrator_plan", return_value=plan("retrieve", ["public.search"])
    ) as planner_mock:
        result = server.run_atlas_with_readonly_canary("Find current public evidence.", "personal")
    check("planner IS consulted for a non-carve-out question", planner_mock.call_count == 1)
    check("the planner's selected capability executes", capability_mock.call_count == 1)
    check("the deterministic executor is never called on the planner-success path", legacy_mock.call_count == 0)
    check("deciding_layer is planner", result["orchestrator_canary"]["deciding_layer"] == "planner")


def test_flag_on_planner_unavailable_falls_back_with_correct_label() -> None:
    print("\nFlag on: planner timeout/unavailable falls back to the classifier, labeled classifier_fallback")
    legacy = {"answer": "legacy answer", "route": "general_reference", "tool": "openwebui-chat"}
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "ATLAS_INVERT_ROUTER_ENABLED", True
    ), mock.patch.object(
        server, "run_atlas", return_value=dict(legacy)
    ) as legacy_mock, mock.patch.object(
        server, "run_orchestrator_plan", side_effect=subprocess.TimeoutExpired("planner", 1)
    ):
        result = server.run_atlas_with_readonly_canary("An ordinary question with no clear capability.", "personal")
    check("falls back to the deterministic executor on planner timeout", legacy_mock.call_count == 1)
    check("deciding_layer is classifier_fallback", result["orchestrator_canary"]["deciding_layer"] == "classifier_fallback")
    check("status is still the existing 'fallback' value", result["orchestrator_canary"]["status"] == "fallback")


def test_canary_disabled_short_circuits_before_a3_logic_entirely() -> None:
    print("\nORCHESTRATOR_READONLY_CANARY_ENABLED off short-circuits before any A3 logic, even with the flag on")
    legacy = {"answer": "legacy answer", "route": "general_reference", "tool": "openwebui-chat"}
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", False), mock.patch.object(
        server, "ATLAS_INVERT_ROUTER_ENABLED", True
    ), mock.patch.object(
        server, "run_atlas", return_value=dict(legacy)
    ) as legacy_mock, mock.patch.object(
        server, "classifier_route_is_deterministic_first"
    ) as pre_check_mock, mock.patch.object(
        server, "run_orchestrator_plan"
    ) as planner_mock:
        result = server.run_atlas_with_readonly_canary("Turn on the bedside lamp", "personal")
    check("A3 pre-check never runs when the canary itself is off", pre_check_mock.call_count == 0)
    check("planner never runs either", planner_mock.call_count == 0)
    check("plain legacy dispatch, no orchestrator_canary key at all", "orchestrator_canary" not in result)
    check("legacy executor called once", legacy_mock.call_count == 1)


def test_a3_deciding_layer_is_persisted_in_audit() -> None:
    print("\nA3 audit: deciding_layer is persisted with the rest of the canary telemetry")
    exchange = {"conversation_id": "conversation-1"}
    result = {
        "orchestrator_canary": {
            "status": "selected",
            "decision_source": "native_tool_call",
            "deciding_layer": "planner",
            "planner_latency_ms": 123,
        }
    }
    with mock.patch.object(server.atlas_app_store, "add_audit_event") as audit_mock:
        server.record_readonly_canary_audit(exchange, result, "personal")
    details = audit_mock.call_args.kwargs["details"]
    check("audit payload records deciding_layer", details.get("deciding_layer") == "planner")


def main() -> int:
    test_classifier_pre_check_direct()
    test_flag_off_is_byte_for_byte_unchanged()
    test_pre_planner_def025_routes_beat_keyword_handlers()
    test_flag_on_deterministic_first_carveout_never_reaches_planner()
    test_flag_on_deterministic_first_carveout_beats_a_confident_planner()
    test_flag_on_non_carveout_question_still_goes_planner_first()
    test_flag_on_planner_unavailable_falls_back_with_correct_label()
    test_canary_disabled_short_circuits_before_a3_logic_entirely()
    test_a3_deciding_layer_is_persisted_in_audit()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
