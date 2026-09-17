#!/usr/bin/env python3

"""Offline tests for the Track A2 native tool-calling planner.

Covers tool-schema construction and the mapping from structured tool_calls
onto a validated plan. No network, no model, no state mutation -- every case
feeds a synthetic tool_calls payload straight into the mapper.

The central safety property under test: plan_from_tool_calls() reuses
validate_shadow_decision() unchanged, so a tool call is still an untrusted
proposal and the server still owns scope, risk, freshness, verification, and
capability availability (DEC-2026-09-01-03).

Run: python3 tools/ai/test-atlas-native-tool-planner.py
"""

from __future__ import annotations

import json
import pathlib
import sys
from unittest import mock

AI_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AI_DIR))

import atlas_orchestrator_v2 as orch  # noqa: E402

import importlib.util

RUNNER_SPEC = importlib.util.spec_from_file_location(
    "atlas_native_planner_runner", AI_DIR / "run-atlas-readonly.py"
)
runner = importlib.util.module_from_spec(RUNNER_SPEC)
assert RUNNER_SPEC and RUNNER_SPEC.loader
RUNNER_SPEC.loader.exec_module(runner)

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}{(' -- ' + detail) if detail else ''}")
        FAILURES.append(label)


def call(name: str, **arguments) -> dict:
    return {"function": {"name": name, "arguments": arguments}}


def call_str(name: str, arguments: dict) -> dict:
    """Open WebUI shape: arguments arrive as a JSON string, not an object."""
    return {"function": {"name": name, "arguments": json.dumps(arguments)}}


def shortlist(scope: str, *names: str):
    return tuple(
        item for item in orch.capability_registry(scope) if item.name in names
    )


def test_tool_schema() -> None:
    print("\nTool schema construction")

    caps = shortlist("personal", "math.calculate", "reminders.manage")
    tools = orch.build_planner_tools("personal", caps)
    names = [t["function"]["name"] for t in tools]

    check("every shortlisted capability becomes a tool",
          "math.calculate" in names and "reminders.manage" in names)
    check("all four pseudo-tools are always offered",
          orch.PSEUDO_TOOLS.issubset(set(names)), f"got {names}")

    by_name = {t["function"]["name"]: t["function"] for t in tools}
    check("an action capability requires action_text",
          by_name["reminders.manage"]["parameters"]["required"] == ["action_text"])
    check("a non-action capability requires no arguments",
          by_name["math.calculate"]["parameters"]["required"] == [])
    check("write posture is stated in the description",
          "writes owner state" in by_name["reminders.manage"]["description"])
    check("read-only posture is stated in the description",
          "read-only" in by_name["math.calculate"]["description"])

    # DEF-027 (DEC-2026-09-16-04): the clarify tool's description must keep
    # its explicit negative examples and tie-breaking rule. A repeated-call
    # probe found the model choosing this tool against its own stated scope
    # on plain factual recall as often as 80% of the time on some real
    # questions; removing this text without re-measuring would silently
    # regress that partial fix.
    clarify_description = by_name[orch.CLARIFY_TOOL]["description"]
    check("clarify tool explicitly excludes plain factual/historical recall",
          "Do NOT use" in clarify_description and "factual" in clarify_description,
          clarify_description)
    check("clarify tool states a tie-breaking rule favoring a direct answer",
          "prefer answer_directly" in clarify_description, clarify_description)

    # Capabilities outside the active scope must never be offered as tools.
    personal_tools = [
        t["function"]["name"]
        for t in orch.build_planner_tools(
            "personal", orch.shortlist_capabilities("what changed on docker-main", "personal")
        )
    ]
    check("operations-only capabilities are not offered in personal scope",
          "homelab.knowledge" not in personal_tools,
          f"got {personal_tools}")

    # H6 latency regression: planner calls are bounded independently of
    # answer generation so verbose model reasoning cannot consume the whole
    # outer timeout before returning a tiny tool proposal.
    with mock.patch.object(runner, "load_api_key", return_value="test-key"), mock.patch.object(
        runner, "api_request", return_value={"data": [{"id": "qwen3:8b"}]}
    ), mock.patch.object(
        runner, "choose_model", return_value="qwen3:8b"
    ), mock.patch.object(
        runner,
        "timed_api_request",
        return_value={"json": {"choices": [{"message": {"tool_calls": []}}]}},
    ) as timed_call:
        runner.call_model_with_tools(
            messages=[{"role": "user", "content": "plan only"}],
            tools=[],
            base_url="http://fake",
            db_path=pathlib.Path("/tmp/unused.db"),
            model="qwen3:8b",
            timeout=30,
        )
    planner_payload = timed_call.call_args.args[4]
    check("planner output is capped at 512 tokens", planner_payload["max_tokens"] == 512)


def test_mapping() -> None:
    print("\nTool-call mapping")

    caps = shortlist("personal", "math.calculate", "reminders.manage", "answer.general")
    allowed = {c.name for c in caps}

    plan = orch.plan_from_tool_calls([call("math.calculate")], "personal", allowed)
    check("a tool capability maps to mode=tool", plan["mode"] == "tool", str(plan))
    check("the capability is carried through",
          plan["capabilities"] == ["math.calculate"])
    check("server derives reason_code, not the model",
          plan["reason_code"] == "exact_computation")
    check("server derives needs_verification=False for tool",
          plan["needs_verification"] is False)

    plan = orch.plan_from_tool_calls(
        [call("reminders.manage", action_text="check the library return tomorrow")],
        "personal", allowed)
    check("an action capability maps to mode=action", plan["mode"] == "action")
    check("action_text is preserved verbatim",
          plan["action_text"] == "check the library return tomorrow")
    check("server derives needs_verification=True for action",
          plan["needs_verification"] is True)

    plan = orch.plan_from_tool_calls(
        [call_str("reminders.manage", {"action_text": "buy milk"})], "personal", allowed)
    check("Open WebUI's JSON-string arguments parse identically",
          plan["action_text"] == "buy milk", str(plan))

    plan = orch.plan_from_tool_calls([call(orch.DIRECT_TOOL)], "personal", allowed)
    check("answer_directly maps to direct with no capability",
          plan["mode"] == "direct" and plan["capabilities"] == [], str(plan))

    # Regression guard for the real defect this replaced: three "Save a note" /
    # "Add a task" requests returned no tool call and were silently scored as
    # a direct answer, which would have dropped the owner's write entirely.
    try:
        orch.plan_from_tool_calls([], "personal", allowed)
        check("no tool call is an error, not a silent direct answer", False,
              "an unanswered action request must not become mode=direct")
    except ValueError as exc:
        check("no tool call is an error, not a silent direct answer",
              str(exc) == "no_tool_call", str(exc))

    plan = orch.plan_from_tool_calls([call(orch.CLARIFY_TOOL, missing="which city")],
                                     "personal", allowed)
    check("clarify pseudo-tool maps to mode=clarify with no capability",
          plan["mode"] == "clarify" and plan["capabilities"] == [])
    # DEF-034: the model's own "missing" argument must be preserved so the
    # owner-facing message can be specific instead of always generic.
    check("clarify pseudo-tool preserves the model's 'missing' detail",
          plan.get("missing") == "which city", str(plan))

    plan_no_missing = orch.plan_from_tool_calls([call(orch.CLARIFY_TOOL)], "personal", allowed)
    check("clarify pseudo-tool with no 'missing' argument omits the key rather than storing blank/None",
          "missing" not in plan_no_missing, str(plan_no_missing))

    plan_blank_missing = orch.plan_from_tool_calls([call(orch.CLARIFY_TOOL, missing="   ")], "personal", allowed)
    check("clarify pseudo-tool with a blank 'missing' argument omits the key",
          "missing" not in plan_blank_missing, str(plan_blank_missing))

    plan = orch.plan_from_tool_calls([call(orch.REFUSE_TOOL)], "personal", allowed)
    check("refuse pseudo-tool maps to mode=refuse with no capability",
          plan["mode"] == "refuse" and plan["capabilities"] == [])


def test_policy_still_enforced() -> None:
    print("\nServer policy is still authoritative")

    caps = shortlist("personal", "math.calculate", "reminders.manage")
    allowed = {c.name for c in caps}

    try:
        orch.plan_from_tool_calls([call("definitely.not.registered")], "personal", allowed)
        check("an unregistered tool name is rejected", False)
    except ValueError as exc:
        check("an unregistered tool name is rejected",
              str(exc) == "unknown_or_disallowed_capability", str(exc))

    # A capability that exists in the registry but was not shortlisted for this
    # request must not become reachable just because the model named it.
    try:
        orch.plan_from_tool_calls([call("notes.manage")], "personal", allowed)
        check("a non-shortlisted capability is rejected", False)
    except ValueError as exc:
        check("a non-shortlisted capability is rejected",
              str(exc) in {"capability_not_shortlisted", "unknown_or_disallowed_capability"},
              str(exc))

    # Cross-scope: homelab.knowledge is operations/admin only.
    ops = shortlist("operations", "homelab.knowledge")
    try:
        orch.plan_from_tool_calls([call("homelab.knowledge")], "personal",
                                  {"homelab.knowledge"})
        check("a cross-scope capability is rejected in personal scope", False)
    except ValueError as exc:
        check("a cross-scope capability is rejected in personal scope",
              str(exc) == "unknown_or_disallowed_capability", str(exc))

    plan = orch.plan_from_tool_calls([call("homelab.knowledge")], "operations",
                                     {c.name for c in ops})
    check("the same capability is allowed in operations scope",
          plan["capabilities"] == ["homelab.knowledge"] and plan["mode"] == "retrieve")
    check("server marks a private read as sensitive risk",
          plan["risk"] == "sensitive", str(plan))

    # The model cannot escalate risk metadata: it is derived, never supplied.
    plan = orch.plan_from_tool_calls(
        [call("math.calculate")], "personal", {"math.calculate"})
    check("risk is derived server-side and stays low for exact computation",
          plan["risk"] == "low")

    check("context_scope is forced to the server's value, not the model's",
          plan["context_scope"] == "personal")


def test_deep_pseudo_tool() -> None:
    print("\nDeep (multi-stage) pseudo-tool")

    ops = shortlist("operations", "homelab.knowledge", "public.search")
    allowed = {c.name for c in ops}
    plan = orch.plan_from_tool_calls([call(orch.DEEP_TOOL)], "operations", allowed)
    check("the deep pseudo-tool maps to mode=deep", plan["mode"] == "deep", str(plan))
    check("the server fills in the read-only capability set",
          set(plan["capabilities"]) == allowed, str(plan))
    check("a deep plan needs verification", plan["needs_verification"] is True)
    check("a deep plan uses the multi_step_task reason code",
          plan["reason_code"] == "multi_step_task")

    # A deep plan must never pick up a write capability, even when one is
    # shortlisted alongside the reads.
    mixed = shortlist("personal", "reminders.manage", "nextcloud.read", "public.search")
    mixed_allowed = {c.name for c in mixed}
    plan = orch.plan_from_tool_calls([call(orch.DEEP_TOOL)], "personal", mixed_allowed)
    check("a deep plan excludes write capabilities",
          "reminders.manage" not in plan["capabilities"], str(plan))

    decomposed = orch.plan_from_tool_calls([
        call(orch.DEEP_TOOL, steps=[
            {"capability": "homelab.knowledge", "request": "Find the documented current architecture."},
            {"capability": "public.search", "request": "Find current public migration guidance."},
        ])
    ], "operations", allowed)
    check("deep steps preserve planner order", decomposed["steps"] == [
        {"capability": "homelab.knowledge", "request": "Find the documented current architecture."},
        {"capability": "public.search", "request": "Find current public migration guidance."},
    ], str(decomposed))
    check("deep capabilities come from explicit steps",
          decomposed["capabilities"] == ["homelab.knowledge", "public.search"], str(decomposed))

    try:
        orch.plan_from_tool_calls([
            call(orch.DEEP_TOOL, steps=[
                {"capability": "homelab.knowledge", "request": "One step is not a decomposition."},
            ])
        ], "operations", allowed)
    except ValueError as exc:
        check("a one-step decomposition is rejected", str(exc) == "invalid_deep_steps", str(exc))
    else:
        check("a one-step decomposition is rejected", False, "accepted")


def test_multi_call() -> None:
    print("\nMultiple tool calls in one turn")

    ops = shortlist("operations", "homelab.knowledge", "public.search")
    allowed = {c.name for c in ops}
    plan = orch.plan_from_tool_calls(
        [call("homelab.knowledge"), call("public.search")], "operations", allowed)
    check("several read-only capabilities become a deep plan",
          plan["mode"] == "deep", str(plan))
    check("a deep plan contains no write capability",
          all(not c.startswith("reminders") for c in plan["capabilities"]))

    # A read plus a write must not silently become a multi-step write.
    caps = shortlist("personal", "reminders.manage", "math.calculate")
    allowed = {c.name for c in caps}
    plan = orch.plan_from_tool_calls(
        [call("reminders.manage", action_text="x"), call("math.calculate")],
        "personal", allowed)
    check("a mixed read/write proposal collapses to the first call only",
          plan["capabilities"] == ["reminders.manage"] and plan["mode"] == "action",
          str(plan))


def test_scoped_superlative_record_override() -> None:
    print("\nDEF-027 Phase 2: scoped superlative-record clarify override")

    # The two real repro cases (DEC-2026-09-16-06/-10/-11) that stayed 5/5
    # wrong under tool-description tightening, up to 4 resampling attempts,
    # and unconstrained reasoning -- must still match.
    repro_cases = [
        "What is the longest game of foosball ever played? What about football?",
        "What is the longest game of soccer ever played? How much difference "
        "was it between that and football's longest match?",
    ]
    for question in repro_cases:
        check(f"repro case matches: {question[:50]!r}",
              orch.is_scoped_superlative_record_question(question))

    # Fresh paraphrases, different sports and word order, never used in any
    # prior test or probe -- proves this isn't overfit to the exact repro
    # strings (DEC-2026-09-15-10's "no case closes on the exact string that
    # exposed it" rule).
    fresh_paraphrases = [
        "What is the longest chess match ever played?",
        "Longest tennis match ever played, and how does it compare to the shortest?",
        "What is the biggest chess tournament ever held?",
    ]
    for question in fresh_paraphrases:
        check(f"fresh paraphrase matches: {question[:50]!r}",
              orch.is_scoped_superlative_record_question(question))

    # Genuinely unscoped superlatives (no named domain) must NOT match --
    # these are real ambiguous questions that should keep clarifying.
    negative_controls = [
        "What's the best show?",
        "Item with the most sales",
        "Highest player count",
        "What is the most sold brand of vodka",
        "What year did the civil war start?",
        "What is the largest landmass? How much larger is it than the smallest land mass?",
    ]
    for question in negative_controls:
        check(f"negative control does not match: {question[:50]!r}",
              not orch.is_scoped_superlative_record_question(question))

    # The regex matching is necessary but not sufficient: the actual real-
    # traffic gate is deterministic_clarification_plan()'s hard "what is the
    # X-est" pre-check, which runs BEFORE the native planner (and thus
    # before the model or the recheck override ever sees the question at
    # all). Confirmed live on 2026-09-16 that the model-level override alone
    # was dead code for these two repro cases because this earlier gate
    # always fired first -- exempting it here is what actually matters.
    for question in repro_cases + fresh_paraphrases:
        check(f"deterministic pre-gate exempts: {question[:50]!r}",
              orch.deterministic_clarification_plan(question, "personal") is None,
              str(orch.deterministic_clarification_plan(question, "personal")))
    # Only two of the six negative controls actually match this function's
    # own "what/which is/are the X-est" base regex to begin with (the other
    # four rely on the model's own clarify_request judgment instead, tested
    # elsewhere) -- confirmed directly, not assumed, since a contraction
    # like "what's" doesn't match "\bwhat\s+is\b" literally.
    gated_negative_controls = [
        "What is the most sold brand of vodka",
        "What is the largest landmass? How much larger is it than the smallest land mass?",
    ]
    for question in gated_negative_controls:
        plan = orch.deterministic_clarification_plan(question, "personal")
        check(f"deterministic pre-gate still clarifies negative control: {question[:50]!r}",
              plan is not None and plan["mode"] == "clarify", str(plan))


def test_def037_compound_ambiguous_clause_exemption() -> None:
    print("\nDEF-037: compound questions no longer block on one ambiguous clause")

    # A genuinely independent two-topic question must be exempted (fall
    # through to deep-plan decomposition) instead of blocking the whole
    # request on the ambiguous half. Real question from the owner's own
    # history, fresh paraphrase, and one owner-observed real failure.
    independent_compounds = [
        "What is the largest state? What is a famous dish from Cuba?",
        "Whats the most popular flavor at baskin robins? What is the tunnel near Sausalito after the Golden Gate Bridge called?",
        "What is the best pizza topping? What year did the Berlin Wall fall?",
    ]
    for question in independent_compounds:
        check(f"orchestrator gate exempts independent compound: {question[:50]!r}",
              orch.deterministic_clarification_plan(question, "personal") is None)

    # A single reasoning question spread across two sentences via a
    # pronoun ("...is IT...") must NOT be exempted -- it is one linked
    # question, not two independent topics.
    linked_reasoning = [
        "What is the largest landmass? How much larger is it than the smallest land mass?",
        "What is the best show? How does it compare to the worst one?",
    ]
    for question in linked_reasoning:
        plan = orch.deterministic_clarification_plan(question, "personal")
        check(f"orchestrator gate still clarifies linked reasoning: {question[:50]!r}",
              plan is not None and plan["mode"] == "clarify", str(plan))

    # The Daytona special case must never be exempted, even inside an
    # otherwise-independent compound question. Tried exempting it
    # (DEC-2026-09-16-20) and reverted after live-testing the exact
    # DEF-024 repro case: exempting it did not reach a clean deep-plan
    # decomposition, it fell into a single-capability weather shortcut
    # that silently dropped the Daytona clause with no answer and no
    # acknowledgment at all -- worse than blocking the whole request,
    # which is the exact failure mode DEF-024 exists to prevent.
    for question in [
        "When was the first Daytona?",
        "Who won the first Daytona, and what is the weather in Martinez tomorrow?",
        "When was the first Daytona? What is a famous dish from Cuba?",
    ]:
        plan = orch.deterministic_clarification_plan(question, "personal")
        check(f"Daytona is never exempted, even in a compound question: {question[:50]!r}",
              plan is not None and plan["mode"] == "clarify", str(plan))
    print("PASS: DEF-037")


def test_def038_time_sensitive_ownership_forces_live_search() -> None:
    print("\nDEF-038: ownership/leadership/price questions force live retrieval")

    # Real repro case plus fresh paraphrases -- must force public.search,
    # never answer_directly from static model knowledge.
    time_sensitive = [
        "Is valve owned by a different company? Who are the majority shareholders?",
        "Who owns Twitter now?",
        "Who is the CEO of Tesla?",
        "What is the current price of Bitcoin?",
        "Who controls Chelsea Football Club?",
    ]
    for question in time_sensitive:
        plan = orch.deterministic_time_sensitive_fact_plan(question, "personal")
        check(f"forces live retrieval: {question[:50]!r}",
              plan is not None and plan["mode"] == "retrieve"
              and plan["capabilities"] == ["public.search"]
              and plan["freshness"] == "live",
              str(plan))

    # Stable, non-time-sensitive facts must not be swept in.
    stable_facts = [
        "What year did the civil war start?",
        "What is the largest state?",
        "Who wrote Romeo and Juliet?",
    ]
    for question in stable_facts:
        check(f"does not misfire on stable fact: {question[:50]!r}",
              orch.deterministic_time_sensitive_fact_plan(question, "personal") is None)
    print("PASS: DEF-038")


def test_def052_population_ranking_forces_live_search() -> None:
    print("\nDEF-052: population-ranking questions force live retrieval")

    # Found via the overnight stress run's actual-answer grading: "Which
    # country has the largest population in the world?" answered "China"
    # from stale static knowledge (the real answer has been India since a
    # well-established 2023 shift). Same class of fix as DEF-038.
    time_sensitive = [
        "Which country has the largest population in the world?",
        "What is the most populous country in the world?",
        "Which city has the biggest population?",
    ]
    for question in time_sensitive:
        plan = orch.deterministic_time_sensitive_fact_plan(question, "personal")
        check(f"forces live retrieval: {question[:50]!r}",
              plan is not None and plan["mode"] == "retrieve"
              and plan["capabilities"] == ["public.search"]
              and plan["freshness"] == "live",
              str(plan))

    # A historical population question is genuinely stable and must not be
    # swept in just for containing the word "population".
    stable_facts = [
        "What was the population of Rome in 500 BC?",
        "What is the population of Japan?",
    ]
    for question in stable_facts:
        check(f"does not misfire on historical/bare population question: {question[:50]!r}",
              orch.deterministic_time_sensitive_fact_plan(question, "personal") is None)
    print("PASS: DEF-052")


def test_def047_conventional_superlative_facts_not_clarified() -> None:
    print("\nDEF-047: well-known physical/biological/astronomical superlatives answer directly")

    # Found live via Track A3 Gate 1 (DEC-2026-09-16-29): these have one
    # standard, universally-known metric, unlike an open-ended comparison,
    # but the only existing exemption (is_scoped_superlative_record_question)
    # is scoped to sports/game records and doesn't cover them.
    conventional_facts = [
        "Which is the largest planet in the Solar System?",
        "Which is the smallest planet in the Solar System?",
        "What is the largest artery in the human body?",
        "Which organ is largest inside a mammal?",
        "Which bone is longest in a person's body?",
        "Which ocean is deepest on Earth?",
    ]
    for question in conventional_facts:
        check(f"conventional superlative fact answers directly: {question[:50]!r}",
              orch.deterministic_clarification_plan(question, "personal") is None)

    # Genuinely open-ended comparisons (no single agreed metric, or a noun
    # outside the narrow, enumerated domain list) must still clarify.
    still_ambiguous = [
        "What is the most sold brand of vodka",
        "What is the largest state?",
        "What is the largest landmass? How much larger is it than the smallest land mass?",
    ]
    for question in still_ambiguous:
        plan = orch.deterministic_clarification_plan(question, "personal")
        check(f"still clarifies non-conventional superlative: {question[:50]!r}",
              plan is not None and plan["mode"] == "clarify", str(plan))
    print("PASS: DEF-047")


def main() -> int:
    test_tool_schema()
    test_mapping()
    test_policy_still_enforced()
    test_deep_pseudo_tool()
    test_multi_call()
    test_scoped_superlative_record_override()
    test_def037_compound_ambiguous_clause_exemption()
    test_def038_time_sensitive_ownership_forces_live_search()
    test_def052_population_ranking_forces_live_search()
    test_def047_conventional_superlative_facts_not_clarified()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
