#!/usr/bin/env python3
"""Safety and rollback tests for the dormant read-only orchestration canary."""

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


def main() -> None:
    assert server.readonly_canary_eligible(plan("direct", ["answer.general"])["decision"], "personal")
    assert server.readonly_canary_eligible(plan("tool", ["math.calculate"])["decision"], "personal")
    assert server.readonly_canary_eligible(plan("retrieve", ["public.search"])["decision"], "personal")
    assert not server.readonly_canary_eligible(
        plan("retrieve", ["public.search", "public.reference"])["decision"], "personal"
    )
    assert not server.readonly_canary_eligible(plan("action", ["reminders.manage"])["decision"], "personal")
    assert not server.readonly_canary_eligible(plan("deep", ["public.search"])["decision"], "personal")
    assert not server.readonly_canary_eligible(plan("retrieve", ["public.search"], "admin")["decision"], "admin")
    assert not server.readonly_canary_eligible(plan("retrieve", ["invented.capability"])["decision"], "personal")

    legacy = {"answer": "legacy answer", "route": "general_reference", "tool": "openwebui-chat"}
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", False), mock.patch.object(
        server, "run_atlas", return_value=dict(legacy)
    ) as legacy_mock, mock.patch.object(server, "run_orchestrator_plan") as planner_mock:
        disabled = server.run_atlas_with_readonly_canary("Explain a stable concept.", "personal")
    assert disabled == legacy
    legacy_mock.assert_called_once()
    planner_mock.assert_not_called()

    selected_result = {"answer": "retrieved answer", "route": "general_reference", "tool": "public-search"}
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas"
    ) as legacy_selected, mock.patch.object(
        server, "run_atlas_selected_capability", return_value=dict(selected_result)
    ) as capability_mock, mock.patch.object(
        server, "run_orchestrator_plan", return_value=plan("retrieve", ["public.search"])
    ):
        selected = server.run_atlas_with_readonly_canary("Find current public evidence.", "personal")
    assert selected["answer"] == "retrieved answer"
    assert selected["orchestrator_canary"]["status"] == "selected"
    assert selected["orchestrator_canary"]["capabilities"] == ["public.search"]
    assert selected["orchestrator_canary"]["executed_capability"] == "public.search"
    capability_mock.assert_called_once_with(
        "Find current public evidence.", "public.search", None, "personal",
        timeout_override=server.DEEP_PLAN_STEP_TIMEOUT,
    )
    legacy_selected.assert_not_called()

    structured_question = (
        "Brass costs less than copper. Steel costs less than brass. Which is most expensive?\n"
        "Choices: steel; brass; copper\nAnswer with the choice and a short reason."
    )
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas"
    ) as legacy_structured, mock.patch.object(
        server, "run_atlas_selected_capability", return_value=dict(selected_result)
    ) as structured_capability, mock.patch.object(server, "run_orchestrator_plan") as structured_planner:
        structured = server.run_atlas_with_readonly_canary(structured_question, "personal")
    assert structured["orchestrator_canary"]["decision_source"] == "deterministic_structured_reasoning"
    assert structured["orchestrator_canary"]["executed_capability"] == "answer.general"
    structured_capability.assert_called_once_with(
        structured_question, "answer.general", None, "personal",
        timeout_override=server.DEEP_PLAN_STEP_TIMEOUT,
    )
    structured_planner.assert_not_called()
    legacy_structured.assert_not_called()

    self_contained_reasoning = (
        "For the local weather adapter, imagine we optimize source freshness aggressively and accept a little extra latency; "
        "what is the most likely benefit and the most likely downside? If the owner complains that it feels slower, "
        "what measurement would tell us whether the tradeoff is actually worth keeping?"
    )
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas"
    ) as legacy_reasoning, mock.patch.object(
        server, "run_atlas_selected_capability", return_value=dict(selected_result)
    ) as reasoning_capability, mock.patch.object(server, "run_orchestrator_plan") as reasoning_planner:
        reasoning = server.run_atlas_with_readonly_canary(self_contained_reasoning, "personal")
    assert reasoning["orchestrator_canary"]["decision_source"] == "deterministic_self_contained_reasoning"
    assert reasoning["orchestrator_canary"]["executed_capability"] == "answer.general"
    reasoning_capability.assert_called_once_with(
        self_contained_reasoning, "answer.general", None, "personal",
        timeout_override=server.DEEP_PLAN_STEP_TIMEOUT,
    )
    reasoning_planner.assert_not_called()
    legacy_reasoning.assert_not_called()

    hypothetical_advice = (
        "If the network is down during a read-only check for the daily briefing while investigating latency budgets, "
        "assume no user data may be changed; what should the assistant do first? What should it avoid?"
    )
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas"
    ) as legacy_advice, mock.patch.object(
        server, "run_atlas_selected_capability", return_value=dict(selected_result)
    ) as advice_capability, mock.patch.object(server, "run_orchestrator_plan") as advice_planner:
        advice = server.run_atlas_with_readonly_canary(hypothetical_advice, "personal")
    assert advice["orchestrator_canary"]["decision_source"] == "deterministic_hypothetical_boundary_advice"
    assert advice["orchestrator_canary"]["executed_capability"] == "answer.general"
    advice_capability.assert_called_once_with(
        hypothetical_advice, "answer.general", None, "personal",
        timeout_override=server.DEEP_PLAN_STEP_TIMEOUT,
    )
    advice_planner.assert_not_called()
    legacy_advice.assert_not_called()

    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas", return_value=dict(legacy)
    ), mock.patch.object(
        server, "run_orchestrator_plan", side_effect=subprocess.TimeoutExpired("planner", 1)
    ):
        timed_out = server.run_atlas_with_readonly_canary("A request", "personal")
    assert timed_out["answer"] == "legacy answer"
    assert timed_out["orchestrator_canary"]["status"] == "fallback"
    assert timed_out["orchestrator_canary"]["reason"] == "planner_timeout"

    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas"
    ) as no_clarify_fallback, mock.patch.object(
        server,
        "run_orchestrator_plan",
        return_value={
            "status": "completed",
            "decision_source": "hard_clarification",
            "decision": {
                "mode": "clarify",
                "capabilities": [],
                "reason_code": "ambiguous_request",
            },
        },
    ):
        clarified = server.run_atlas_with_readonly_canary(
            "Who won the first Daytona, and what is the weather in Martinez tomorrow?",
            "personal",
        )
    no_clarify_fallback.assert_not_called()
    assert clarified["route"] == "clarification"
    assert clarified["result"]["needs_clarification"] is True
    assert "Which Daytona do you mean" in clarified["answer"]
    assert clarified["orchestrator_canary"]["status"] == "clarification"
    assert clarified["orchestrator_canary"]["executed_capability"] is None

    # DEF-022: once the planner selects a capability, a subprocess failure is
    # contained here instead of escaping run_atlas_with_readonly_canary().
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas"
    ) as no_second_model_call, mock.patch.object(
        server, "run_orchestrator_plan", return_value=plan("tool", ["units.convert"])
    ), mock.patch.object(
        server,
        "run_atlas_selected_capability",
        side_effect=subprocess.CalledProcessError(1, "runner", stderr="private runner detail"),
    ):
        selected_failed = server.run_atlas_with_readonly_canary(
            "Convert 93 miles to kilometers.", "personal"
        )
    no_second_model_call.assert_not_called()
    assert selected_failed["route"] == "readonly_capability_unavailable"
    assert selected_failed["result_status"] == "unavailable"
    assert selected_failed["error_type"] == "subprocess_error"
    assert selected_failed["orchestrator_canary"]["execution_error_type"] == "subprocess_error"
    assert "private runner detail" not in selected_failed["answer"]
    assert "Nothing was changed" in selected_failed["answer"]

    # DEF-022: the ordinary legacy fallback gets its own short timeout, and a
    # timeout becomes a deterministic response rather than a 504/exception.
    def legacy_timeout(question, conversation, scope, timeout_override=None):
        assert timeout_override == server.LEGACY_FALLBACK_TIMEOUT
        raise subprocess.TimeoutExpired("runner", timeout_override)

    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_orchestrator_plan", side_effect=subprocess.TimeoutExpired("planner", 1)
    ), mock.patch.object(server, "run_atlas", side_effect=legacy_timeout):
        fallback_failed = server.run_atlas_with_readonly_canary(
            "Explain a stable philosophical concept.", "personal"
        )
    assert fallback_failed["route"] == "legacy_fallback_unavailable"
    assert fallback_failed["error_type"] == "subprocess_timeout"
    assert fallback_failed["orchestrator_canary"]["fallback_execution_error_type"] == "subprocess_timeout"
    assert "Nothing was changed" in fallback_failed["answer"]

    # Unsupported-plus-supported requests must execute only the supported
    # capability and always disclose the omitted modality. A generic planner
    # tool must not impersonate an adapter ATLAS does not have.
    unsupported_question = (
        "Please transcribe a microphone audio recording, and also convert 88 miles to kilometers."
    )
    supported_conversion = {
        "answer": "88 miles is about 141.62 kilometers.",
        "source": "local-conversion",
        "result_status": "ok",
        "error_type": None,
    }
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_orchestrator_plan", return_value=plan("tool", ["units.convert"])
    ), mock.patch.object(
        server, "run_atlas_selected_capability", return_value=supported_conversion
    ) as supported_only:
        unsupported_partial = server.run_atlas_with_readonly_canary(
            unsupported_question, "personal"
        )
    supported_only.assert_called_once_with(
        unsupported_question, "units.convert", None, "personal",
        timeout_override=server.DEEP_PLAN_STEP_TIMEOUT,
    )
    assert unsupported_partial["route"] == "partial_with_unsupported_capability"
    assert "141.62 kilometers" in unsupported_partial["answer"]
    assert "audio transcription" in unsupported_partial["answer"]
    assert "does not have an approved capability" in unsupported_partial["answer"]
    assert unsupported_partial["result"]["changed"] is False
    assert unsupported_partial["orchestrator_canary"]["status"] == "partial_unsupported"
    assert "answer.general" not in unsupported_partial["orchestrator_canary"].get("executed_capabilities", [])

    assert server.detect_unsupported_readonly_requests(
        "Explain airline history and calculate 3 times 4."
    ) == []
    assert server.detect_unsupported_readonly_requests(
        "Describe the camera obscura technique."
    ) == []
    assert server.detect_unsupported_readonly_requests(
        "Inspect current airport gate-camera imagery."
    ) == ["camera-image analysis"]

    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas", return_value=dict(legacy)
    ), mock.patch.object(
        server, "run_orchestrator_plan", return_value=plan("action", ["reminders.manage"])
    ):
        action = server.run_atlas_with_readonly_canary("Create a reminder", "personal")
    assert action["orchestrator_canary"]["status"] == "fallback"
    assert action["orchestrator_canary"]["reason"] == "ineligible_plan"
    assert action["result"] == {"executed": False, "changed": False}
    assert action["route"] == "orchestrator_action_not_executed"
    assert "did not create or change anything" in action["answer"]

    # -- Chat-quality review cluster 1: a factual dispute about ATLAS's own
    # prior general-knowledge claim must not be misrouted to the action
    # boundary, even when the native planner (wrongly) classifies it as
    # mode="action" -- "that's not true"/"that's wrong" phrasing overlaps
    # memory.correct's own discovery terms. Found live 2026-09-17: an
    # explicit correction of a fabricated character name ("That is not
    # true, in the book the character was also named Louis de Pointe du
    # Lac") got "I did not create or change anything," a nonsensical reply
    # to a factual statement. Reproduced live before this fix.
    general_reference_history = [
        {"role": "user", "text": "Who is the main character in Interview with the Vampire?"},
        {"role": "assistant", "text": "Louis de Pointe du Bois.", "route": "general_reference"},
    ]
    assert server.is_disputing_prior_general_knowledge_claim(
        "That is not true, in the book the character was also named Louis de Pointe du Lac",
        general_reference_history,
    )
    assert server.is_disputing_prior_general_knowledge_claim("That's wrong.", general_reference_history)
    assert server.is_disputing_prior_general_knowledge_claim("No, that's not true.", general_reference_history)
    assert server.is_disputing_prior_general_knowledge_claim("I don't think that's true.", general_reference_history)
    # A genuine personal-memory correction (the phrase's real intended use)
    # must be untouched: it only ever follows a personal_memory turn, not a
    # general_reference one.
    memory_history = [
        {"role": "user", "text": "What coffee do I like?"},
        {"role": "assistant", "text": "Black coffee.", "route": "personal_memory"},
    ]
    assert not server.is_disputing_prior_general_knowledge_claim(
        "That's wrong, it's actually a cortado.", memory_history
    )
    # No history, or a dispute-shaped question with no preceding assistant
    # turn at all, must not match either.
    assert not server.is_disputing_prior_general_knowledge_claim("That's wrong.", None)
    assert not server.is_disputing_prior_general_knowledge_claim("That's wrong.", [])
    # A question that merely contains dispute-ish words mid-sentence, not as
    # its own opening move, must not match -- this only recognizes the
    # correction as the question's own leading clause.
    assert not server.is_disputing_prior_general_knowledge_claim(
        "Is it true that the movie changed the ending?", general_reference_history
    )

    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas", return_value=dict(legacy)
    ) as dispute_legacy, mock.patch.object(
        server, "run_orchestrator_plan", return_value=plan("action", ["memory.correct"])
    ):
        dispute = server.run_atlas_with_readonly_canary(
            "That is not true, in the book the character was also named Louis de Pointe du Lac",
            "personal",
            general_reference_history,
        )
    dispute_legacy.assert_called_once()
    assert dispute["route"] == "general_reference"
    assert dispute["answer"] == "legacy answer"

    # -- H4: a genuinely compound request that reaches the classifier
    # fallback (planner timeout) must not silently execute only one clause
    # through the single-route legacy classifier as though it answered the
    # whole thing --
    compound_question = "What is 17 times 23, and what is the weather in Chicago?"

    def selected_capability_side_effect(question, capability, conversation, scope, timeout_override=None):
        assert timeout_override == server.DEEP_PLAN_STEP_TIMEOUT
        if capability == "math.calculate":
            return {"answer": "17 times 23 is 391.", "source": "local-math", "result_status": "ok", "error_type": None}
        if capability == "weather.forecast":
            return {"answer": "Chicago: Clear sky, 22 C.", "source": "Open-Meteo", "result_status": "ok", "error_type": None}
        raise AssertionError(f"unexpected capability: {capability}")

    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas"
    ) as legacy_compound, mock.patch.object(
        server, "run_orchestrator_plan", side_effect=subprocess.TimeoutExpired("planner", 1)
    ), mock.patch.object(
        server, "run_atlas_selected_capability", side_effect=selected_capability_side_effect
    ) as compound_capability_mock:
        compound = server.run_atlas_with_readonly_canary(compound_question, "operations")
    legacy_compound.assert_not_called()
    assert compound_capability_mock.call_count == 2
    assert "17 times 23 is 391." in compound["answer"]
    assert "Chicago: Clear sky, 22 C." in compound["answer"]
    assert compound["route"] == "legacy_compound_complete"
    assert compound["orchestrator_canary"]["decomposition"] == "legacy_compound_fallback"
    assert sorted(compound["orchestrator_canary"]["executed_capabilities"]) == ["math.calculate", "weather.forecast"]
    # DEF-023: found by tracing every h6 "required_mode: deep" failure in the
    # 2026-09-12 token-cap-rerun dataset back to this exact branch -- a
    # planner timeout/rejection with a confidently-discovered 2+-capability
    # compound question. This path performs the same multi-step
    # decomposition "deep" mode would have; failing to tag telemetry with
    # `mode: "deep"` made every one of its invocations fail that check
    # deterministically, previously misdiagnosed as planner sampling
    # variance (a repeat of a deterministic miss will, of course, fail
    # again).
    assert compound["orchestrator_canary"]["mode"] == "deep"

    # A single-intent request in the same fallback situation is completely
    # unaffected -- fewer than two mandatory capabilities means the original
    # single-route legacy path runs exactly as before H4.
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas", return_value=dict(legacy)
    ) as legacy_single, mock.patch.object(
        server, "run_orchestrator_plan", side_effect=subprocess.TimeoutExpired("planner", 1)
    ), mock.patch.object(server, "run_atlas_selected_capability") as single_capability_mock:
        single = server.run_atlas_with_readonly_canary("What is 17 times 23?", "personal")
    legacy_single.assert_called_once_with(
        "What is 17 times 23?", None, "personal",
        timeout_override=server.LEGACY_FALLBACK_TIMEOUT,
    )
    single_capability_mock.assert_not_called()
    assert single["answer"] == "legacy answer"

    # A partial compound fallback names what wasn't completed, in plain
    # language, not raw internal step_id/status/error_type codes.
    def partial_side_effect(question, capability, conversation, scope, timeout_override=None):
        assert timeout_override == server.DEEP_PLAN_STEP_TIMEOUT
        if capability == "math.calculate":
            return {"answer": "17 times 23 is 391.", "source": "local-math", "result_status": "ok", "error_type": None}
        raise subprocess.TimeoutExpired("weather", 45)

    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas"
    ), mock.patch.object(
        server, "run_orchestrator_plan", side_effect=subprocess.TimeoutExpired("planner", 1)
    ), mock.patch.object(server, "run_atlas_selected_capability", side_effect=partial_side_effect):
        partial = server.run_atlas_with_readonly_canary(compound_question, "operations")
    assert partial["route"] == "legacy_compound_partial"
    assert "17 times 23 is 391." in partial["answer"]
    assert "too long to respond" in partial["answer"]
    assert "subprocess_timeout" not in partial["answer"]
    assert "step-2" not in partial["answer"]

    # DEF-040: unlike the H4 cases above, the planner can SUCCEED with a
    # valid but too-narrow single-capability decision for a genuinely
    # compound question -- readonly_canary_eligible() only checks that the
    # decision is well-formed, not that it addresses the whole request.
    # Confirmed live: "What is the weather in Denver? What is the
    # population of Tokyo?" got mode=retrieve/capabilities=[weather.
    # forecast], silently dropping Tokyo, with no timeout/error to trigger
    # the H4 path above. This must also reach the compound fallback.
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas"
    ) as legacy_def040, mock.patch.object(
        server, "run_orchestrator_plan", return_value=plan("retrieve", ["weather.forecast"])
    ), mock.patch.object(
        server, "run_atlas_selected_capability", side_effect=selected_capability_side_effect
    ) as def040_capability_mock:
        def040_result = server.run_atlas_with_readonly_canary(compound_question, "operations")
    legacy_def040.assert_not_called()
    assert def040_capability_mock.call_count == 2
    assert "17 times 23 is 391." in def040_result["answer"]
    assert "Chicago: Clear sky, 22 C." in def040_result["answer"]
    assert def040_result["route"] == "legacy_compound_complete"

    # Regression: a single-intent question the model correctly answers with
    # one capability must be completely unaffected -- discover_required_
    # capabilities() finding fewer than two capabilities means the model's
    # own single-capability decision is trusted exactly as before DEF-040.
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas"
    ) as legacy_untouched, mock.patch.object(
        server, "run_orchestrator_plan", return_value=plan("tool", ["math.calculate"])
    ), mock.patch.object(
        server, "run_atlas_selected_capability",
        return_value={"answer": "391", "source": "local-math", "result_status": "ok", "error_type": None},
    ) as single_untouched:
        untouched = server.run_atlas_with_readonly_canary("What is 17 times 23?", "personal")
    legacy_untouched.assert_not_called()
    single_untouched.assert_called_once()
    assert untouched["orchestrator_canary"]["status"] == "selected"

    # The canary itself (readonly_canary_eligible) must still unconditionally
    # refuse every action-mode plan, regardless of action_text -- confirmed
    # execution is a caller-side branch bolted onto the rejection, never a
    # relaxation of the canary's own eligibility rule.
    plan_with_text = plan("action", ["reminders.manage"])
    plan_with_text["decision"]["action_text"] = "call the dentist tomorrow at 3pm"
    assert not server.readonly_canary_eligible(plan_with_text["decision"], "personal")

    # A fuzzy action-mode plan WITH action_text, for an allowlisted
    # capability, in the matching scope, must produce a confirmation instead
    # of the hard refusal -- and must never touch atlas_app_store directly
    # inside this function (request_fuzzy_action_confirmation owns that).
    confirmation_stub = {
        "route": "confirmation_required",
        "tool": "atlas-fuzzy-action-confirmation",
        "answer": "I did not create or change anything yet. I created a pending confirmation instead.",
        "source": "ATLAS confirmation policy",
        "result": {"confirmation": {"id": "stub-id"}, "executed": False, "changed": False},
    }
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas", return_value=dict(legacy)
    ), mock.patch.object(
        server, "run_orchestrator_plan", return_value=plan_with_text
    ), mock.patch.object(
        server, "request_fuzzy_action_confirmation", return_value=dict(confirmation_stub)
    ) as confirmation_mock:
        fuzzy_action = server.run_atlas_with_readonly_canary("Jot down that I need to reschedule the dentist", "personal")
    assert fuzzy_action["route"] == "confirmation_required"
    assert fuzzy_action["result"]["executed"] is False
    confirmation_mock.assert_called_once_with(
        "Jot down that I need to reschedule the dentist", plan_with_text["decision"], "personal", None
    )

    # Wrong context_scope on the decision (model proposed a scope that
    # doesn't match the active request) must still hard-refuse, not create a
    # confirmation for the wrong scope.
    plan_wrong_scope = plan("action", ["reminders.manage"], scope="operations")
    plan_wrong_scope["decision"]["action_text"] = "call the dentist tomorrow at 3pm"
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas", return_value=dict(legacy)
    ), mock.patch.object(
        server, "run_orchestrator_plan", return_value=plan_wrong_scope
    ), mock.patch.object(server, "request_fuzzy_action_confirmation") as scope_mismatch_mock:
        wrong_scope = server.run_atlas_with_readonly_canary("Jot down that I need to reschedule the dentist", "personal")
    assert wrong_scope["route"] == "orchestrator_action_not_executed"
    scope_mismatch_mock.assert_not_called()

    # A capability outside the allowlist (e.g. a hypothetical future
    # admin/device action) must still hard-refuse even with action_text.
    plan_disallowed = plan("action", ["admin.request_confirmation"], scope="admin")
    plan_disallowed["decision"]["action_text"] = "disable the alarm"
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas", return_value=dict(legacy)
    ), mock.patch.object(
        server, "run_orchestrator_plan", return_value=plan_disallowed
    ), mock.patch.object(server, "request_fuzzy_action_confirmation") as disallowed_mock:
        disallowed = server.run_atlas_with_readonly_canary("Disable the alarm", "admin")
    assert disallowed["route"] == "orchestrator_action_not_executed"
    disallowed_mock.assert_not_called()

    # -- deep_plan_eligible unit cases -------------------------------------
    assert server.deep_plan_eligible(plan("deep", ["weather.forecast", "public.reference"])["decision"], "personal")
    assert not server.deep_plan_eligible(plan("deep", ["weather.forecast"], "admin")["decision"], "admin")
    assert not server.deep_plan_eligible(plan("deep", ["weather.forecast"])["decision"], "operations")
    # Not in _DEEP_PLAN_ROUTABLE_CAPABILITIES -- the specific regression
    # proving the allowlist prevents selecting a capability that has no
    # executor wired up in run-atlas-readonly.py's CAPABILITY_ROUTES, even
    # though home.read is currently excluded from capability_registry() for
    # an unrelated reason (its feature flag is off).
    assert not server.deep_plan_eligible(plan("deep", ["home.read"])["decision"], "personal")
    assert not server.deep_plan_eligible(plan("deep", [])["decision"], "personal")
    assert not server.deep_plan_eligible(plan("deep", ["math.calculate"] * 5)["decision"], "personal")
    assert not server.deep_plan_eligible(plan("retrieve", ["public.search"])["decision"], "personal")

    # Sibling-branch regression: the canary itself must still unconditionally
    # refuse every deep-mode plan, multi-capability included -- proves the
    # new path is an additional sibling branch, not a relaxation of the
    # canary's own eligibility rule.
    assert not server.readonly_canary_eligible(
        plan("deep", ["weather.forecast", "public.reference"])["decision"], "personal"
    )

    # -- run_atlas_with_readonly_canary: deep-plan integration -------------
    deep_result_stub = {
        "route": "deep_plan_synthesis",
        "tool": "atlas-deep-plan-synthesis",
        "answer": "Synthesized answer.",
        "source": "ATLAS deep-plan synthesis",
        "result": {
            "steps": [
                {"ordinal": 1, "step_id": "step-1", "capability": "weather.forecast", "status": "ok", "error_type": None, "answer": "65F and foggy.", "source": "open-meteo", "latency_ms": 100},
                {"ordinal": 2, "step_id": "step-2", "capability": "public.reference", "status": "ok", "error_type": None, "answer": "SF is in California.", "source": "wikidata", "latency_ms": 120},
            ],
            "synthesis_status": "ok",
        },
    }
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas", return_value=dict(legacy)
    ) as legacy_deep, mock.patch.object(
        server, "run_atlas_selected_capability"
    ) as capability_deep_mock, mock.patch.object(
        server, "run_orchestrator_plan", return_value=plan("deep", ["weather.forecast", "public.reference"])
    ), mock.patch.object(
        server, "run_deep_plan_readonly", return_value=dict(deep_result_stub)
    ) as deep_mock:
        deep = server.run_atlas_with_readonly_canary("Research the weather and reference details for SF.", "personal")
    assert deep["route"] == "deep_plan_synthesis"
    assert deep["answer"] == "Synthesized answer."
    assert deep["orchestrator_canary"]["status"] == "deep_selected"
    assert deep["orchestrator_canary"]["mode"] == "deep"
    assert deep["orchestrator_canary"]["executed_capabilities"] == ["weather.forecast", "public.reference"]
    assert deep["orchestrator_canary"]["step_statuses"] == {"weather.forecast": "ok", "public.reference": "ok"}
    assert deep["orchestrator_canary"]["step_status_list"] == [
        {"ordinal": 1, "step_id": "step-1", "capability": "weather.forecast", "status": "ok", "error_type": None, "latency_ms": 100},
        {"ordinal": 2, "step_id": "step-2", "capability": "public.reference", "status": "ok", "error_type": None, "latency_ms": 120},
    ]
    assert deep["orchestrator_canary"]["synthesis_status"] == "ok"
    deep_mock.assert_called_once_with(
        "Research the weather and reference details for SF.", "personal", None, ["weather.forecast", "public.reference"], None
    )
    legacy_deep.assert_not_called()
    capability_deep_mock.assert_not_called()

    decomposed_plan = plan("deep", ["weather.forecast", "public.reference"])
    decomposed_plan["decision"]["steps"] = [
        {"capability": "weather.forecast", "request": "Check tomorrow's weather in San Francisco."},
        {"capability": "public.reference", "request": "Find San Francisco's state."},
    ]
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "MULTI_INTENT_DECOMPOSITION_ENABLED", True
    ), mock.patch.object(server, "run_orchestrator_plan", return_value=decomposed_plan), mock.patch.object(
        server, "run_deep_plan_readonly", return_value=dict(deep_result_stub)
    ) as decomposed_mock:
        decomposed = server.run_atlas_with_readonly_canary(
            "Check tomorrow's SF weather and tell me which state it is in.", "personal"
        )
    decomposed_mock.assert_called_once_with(
        "Check tomorrow's SF weather and tell me which state it is in.",
        "personal", None, ["weather.forecast", "public.reference"], decomposed_plan["decision"]["steps"]
    )
    assert decomposed["orchestrator_canary"]["decomposition"] == "step_specific"

    # The executor must send only each bounded subrequest to its capability;
    # no result from the first step is passed into the second.
    step_calls = []
    def selected_step(request, capability, conversation, scope, timeout_override=None):
        step_calls.append((request, capability, conversation, scope, timeout_override))
        return {
            "answer": f"answer for {capability}",
            "source": capability,
            "result_status": "ok",
            "error_type": None,
        }
    with mock.patch.object(
        server, "run_atlas_selected_capability", side_effect=selected_step
    ), mock.patch.object(
        server, "run_deep_plan_synthesis", return_value={"answer": "Combined."}
    ):
        executed = server.run_deep_plan_readonly(
            "Original compound request.", "personal", None,
            ["weather.forecast", "public.reference"], decomposed_plan["decision"]["steps"]
        )
    assert [call[0] for call in step_calls] == [
        "Check tomorrow's weather in San Francisco.", "Find San Francisco's state."
    ]
    assert "Combined." in executed["answer"]
    assert "Per-clause status:" in executed["answer"]
    assert "1. Read current weather and forecasts: OK" in executed["answer"]
    assert "2. Look up public structured reference data: OK" in executed["answer"]

    # A deep decision naming a disallowed capability must fall through to the
    # existing ineligible-plan fallback, not raise or attempt execution.
    with mock.patch.object(server, "ORCHESTRATOR_READONLY_CANARY_ENABLED", True), mock.patch.object(
        server, "run_atlas", return_value=dict(legacy)
    ), mock.patch.object(
        server, "run_orchestrator_plan", return_value=plan("deep", ["home.read"])
    ), mock.patch.object(server, "run_deep_plan_readonly") as deep_disallowed_mock:
        deep_disallowed = server.run_atlas_with_readonly_canary("Research something using home sensors.", "personal")
    assert deep_disallowed["orchestrator_canary"]["status"] == "fallback"
    assert deep_disallowed["orchestrator_canary"]["reason"] == "ineligible_plan"
    deep_disallowed_mock.assert_not_called()

    print("PASS: read-only canary allowlist, disabled rollback, timeout fallback, gated action confirmation, and deep-plan authority")


if __name__ == "__main__":
    main()
