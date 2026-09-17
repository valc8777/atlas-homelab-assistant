#!/usr/bin/env python3

import json
import pathlib
import sqlite3
import tempfile
from unittest import mock

import atlas_app_store
import atlas_orchestrator_v2
import atlas_shortcuts_server


def valid_payload(**overrides) -> dict:
    payload = {
        "mode": "direct",
        "freshness": "stable",
        "risk": "low",
        "context_scope": "personal",
        "capabilities": [],
        "needs_verification": False,
        "reason_code": "stable_knowledge",
        "confidence": 0.72,
    }
    payload.update(overrides)
    return payload


def expect_invalid(payload: dict, scope: str, error: str) -> None:
    try:
        atlas_orchestrator_v2.validate_shadow_decision(payload, scope)
    except ValueError as exc:
        assert str(exc) == error
    else:
        raise AssertionError(f"expected {error}")


def main() -> None:
    personal_names = {item.name for item in atlas_orchestrator_v2.capability_registry("personal")}
    admin_names = {item.name for item in atlas_orchestrator_v2.capability_registry("admin")}
    assert "admin.request_confirmation" not in personal_names
    assert "admin.request_confirmation" in admin_names
    assert "homelab.knowledge" not in personal_names

    direct = atlas_orchestrator_v2.validate_shadow_decision(valid_payload(), "personal")
    assert direct["mode"] == "direct"
    assert direct["confidence"] == 0.72
    server_scoped = atlas_orchestrator_v2.validate_shadow_decision(
        valid_payload(context_scope="admin"), "personal"
    )
    assert server_scoped["context_scope"] == "personal"

    retrieve_payload = valid_payload(
        mode="retrieve",
        freshness="live",
        capabilities=["weather.forecast"],
        needs_verification=True,
        reason_code="live_information",
    )
    retrieve = atlas_orchestrator_v2.parse_shadow_decision(json.dumps(retrieve_payload), "personal")
    assert retrieve["capabilities"] == ["weather.forecast"]
    normalized_retrieve = atlas_orchestrator_v2.validate_shadow_decision(
        {**retrieve_payload, "mode": "tool"},
        "personal",
        {"weather.forecast"},
    )
    assert normalized_retrieve["mode"] == "retrieve"
    completed_retrieve = atlas_orchestrator_v2.validate_shadow_decision(
        {**retrieve_payload, "capabilities": []},
        "personal",
        {"weather.forecast"},
    )
    assert completed_retrieve["capabilities"] == ["weather.forecast"]
    replaced_unknown = atlas_orchestrator_v2.validate_shadow_decision(
        {**retrieve_payload, "capabilities": ["invented.weather"]},
        "personal",
        {"weather.forecast"},
    )
    assert replaced_unknown["capabilities"] == ["weather.forecast"]
    expect_invalid(
        {**retrieve_payload, "capabilities": []},
        "personal",
        "missing_capability",
    )
    unsupported_action = atlas_orchestrator_v2.validate_shadow_decision(
        valid_payload(mode="action", capabilities=[]), "personal", set()
    )
    assert unsupported_action["mode"] == "refuse"

    expect_invalid(
        valid_payload(mode="retrieve", capabilities=["homelab.knowledge"], reason_code="private_data"),
        "personal",
        "unknown_or_disallowed_capability",
    )
    normalized_tool = atlas_orchestrator_v2.validate_shadow_decision(
        valid_payload(mode="direct", capabilities=["math.calculate"]),
        "personal",
    )
    assert normalized_tool["mode"] == "tool"
    direct_with_capability = atlas_orchestrator_v2.validate_shadow_decision(
        valid_payload(capabilities=["answer.general"]),
        "personal",
    )
    assert direct_with_capability["capabilities"] == ["answer.general"]
    normalized_action = atlas_orchestrator_v2.validate_shadow_decision(
        valid_payload(
            mode="action",
            freshness="invented",
            risk="invented",
            capabilities=["reminders.manage"],
            needs_verification=False,
            reason_code="invented",
        ),
        "personal",
    )
    assert normalized_action["risk"] == "low"
    assert normalized_action["freshness"] == "current"
    assert normalized_action["needs_verification"] is True
    assert normalized_action["reason_code"] == "supported_action"
    admin_action = atlas_orchestrator_v2.validate_shadow_decision(
        valid_payload(
            mode="action",
            freshness="live",
            risk="high_impact",
            context_scope="admin",
            capabilities=["admin.request_confirmation"],
            needs_verification=True,
            reason_code="supported_action",
        ),
        "admin",
    )
    assert admin_action["risk"] == "high_impact"

    prompt = atlas_orchestrator_v2.build_shadow_prompt(
        "Check the weather forecast for a fictional trip.",
        "personal",
        [],
    )
    assert "Do not answer the user" in prompt
    assert "weather.forecast" in prompt
    assert "admin.request_confirmation" not in prompt
    deep_shortlist = {
        item.name
        for item in atlas_orchestrator_v2.shortlist_capabilities(
            "Review the private homelab architecture and current public best practices for a phased migration.",
            "operations",
        )
    }
    assert {"homelab.knowledge", "public.search"}.issubset(deep_shortlist)
    assert "memory.recall" not in deep_shortlist
    assert "datetime.resolve" not in deep_shortlist
    public_deep_shortlist = {
        item.name
        for item in atlas_orchestrator_v2.shortlist_capabilities(
            "Research current laptop options, compare explicit criteria, and produce a phased recommendation.",
            "personal",
        )
    }
    assert "public.search" in public_deep_shortlist
    assert "weather.forecast" not in public_deep_shortlist
    assert not public_deep_shortlist & {
        "notes.manage", "tasks.manage", "reminders.manage", "shopping.manage"
    }
    weather_math_shortlist = {
        item.name
        for item in atlas_orchestrator_v2.shortlist_capabilities(
            "What is the weather forecast for San Francisco, and what is 17 times 23?",
            "personal",
        )
    }
    assert {"weather.forecast", "math.calculate"}.issubset(weather_math_shortlist)
    indirect_weather_shortlist = {
        item.name
        for item in atlas_orchestrator_v2.shortlist_capabilities(
            "Do I need an umbrella in Daly City tomorrow, and what is 23 times 19?",
            "personal",
        )
    }
    assert {"weather.forecast", "math.calculate"}.issubset(indirect_weather_shortlist)
    weather_condition_shortlist = {
        item.name
        for item in atlas_orchestrator_v2.shortlist_capabilities(
            "Is it likely to be overcast near Pacifica tomorrow morning?", "personal"
        )
    }
    assert "weather.forecast" in weather_condition_shortlist
    documented_component_shortlist = {
        item.name
        for item in atlas_orchestrator_v2.shortlist_capabilities_for_compound(
            "Find current public guidance on job queues, then explain the documented role of the scheduler service.",
            "operations",
        )
    }
    assert {"public.search", "homelab.knowledge"}.issubset(documented_component_shortlist)
    assert atlas_orchestrator_v2.discover_required_capabilities(
        "Research current queue practices, then tell me how the internal worker is configured.",
        "operations",
    ) >= {"public.search", "homelab.knowledge"}
    assert "homelab.knowledge" not in {
        item.name
        for item in atlas_orchestrator_v2.shortlist_capabilities(
            "What services does the public library offer?", "operations"
        )
    }
    assert "weather.forecast" not in {
        item.name
        for item in atlas_orchestrator_v2.shortlist_capabilities(
            "Think outside the box and explain the metaphor.", "personal"
        )
    }
    live_public_shortlist = {
        item.name
        for item in atlas_orchestrator_v2.shortlist_capabilities(
            "Retrieve live public information about a changing officeholder.", "personal"
        )
    }
    assert "public.search" in live_public_shortlist
    normalized_deep = atlas_orchestrator_v2.validate_shadow_decision(
        valid_payload(
            mode="deep",
            context_scope="operations",
            capabilities=["homelab.knowledge"],
        ),
        "operations",
        {"homelab.knowledge", "public.search"},
    )
    assert set(normalized_deep["capabilities"]) == {"homelab.knowledge", "public.search"}
    assert atlas_orchestrator_v2.deterministic_clarification_plan(
        "What is the strongest material?", "personal"
    )["mode"] == "clarify"
    assert atlas_orchestrator_v2.deterministic_clarification_plan(
        "What is the strongest material by tensile strength?", "personal"
    ) is None
    daytona_plan = atlas_orchestrator_v2.deterministic_clarification_plan(
        "Who won the first Daytona?", "personal"
    )
    assert daytona_plan["mode"] == "clarify"
    assert daytona_plan["reason_code"] == "ambiguous_request"
    assert atlas_orchestrator_v2.deterministic_clarification_plan(
        "Who won the first Daytona 500 race?", "personal"
    ) is None
    safety_plan = atlas_orchestrator_v2.deterministic_safety_plan(
        {"route": "privacy_boundary"}, "personal"
    )
    assert safety_plan["mode"] == "refuse"
    assert safety_plan["capabilities"] == []
    recall_plan = atlas_orchestrator_v2.deterministic_context_plan(
        "What name did I choose for the prototype?",
        "personal",
        [{"role": "user", "text": "I chose a synthetic name."}],
    )
    assert recall_plan["mode"] == "retrieve"
    assert recall_plan["capabilities"] == ["memory.recall"]
    contextual_clarification = atlas_orchestrator_v2.deterministic_context_plan(
        "Use the better one.",
        "personal",
        [{"role": "assistant", "text": "Which criterion should I use?"}],
    )
    assert contextual_clarification["mode"] == "clarify"
    assert atlas_orchestrator_v2.deterministic_context_plan(
        "Use the documented backup procedure.",
        "operations",
        [{"role": "assistant", "text": "The procedure is documented."}],
    ) is None

    # A5 (DEC-2026-09-16-23): found via real conversation telemetry, not
    # intuition. A rhetorical closing question ("Would you like help with
    # something else?") is not a genuine disambiguation request -- a short
    # vague-pronoun follow-up after one must not be treated as continuing
    # a clarification exchange that never actually happened. Redacted
    # shape of the real conversation that exposed this.
    rhetorical_closer_history = [
        {"role": "user", "text": "Can you explode?"},
        {"role": "assistant", "text": "I cannot explode. My capabilities are limited to knowledge retrieval, note management, and deterministic processes. Would you like help with something else?"},
        {"role": "user", "text": "What does the end of that sentence mean?"},
        {"role": "assistant", "text": "The end of the sentence means the assistant is offering help with other tasks instead of the previous request."},
    ]
    assert atlas_orchestrator_v2.deterministic_context_plan(
        "What if I find that phrasing weird?", "personal", rhetorical_closer_history
    ) is None
    # Regression: a genuine disambiguation request still triggers the
    # follow-up clarify path correctly.
    genuine_clarification_history = [
        {"role": "user", "text": "Where is the best burger place in the region?"},
        {"role": "assistant", "text": "What criterion, measurement, or scope should I use for \"best\"?"},
    ]
    genuine_followup = atlas_orchestrator_v2.deterministic_context_plan(
        "What if that is still unclear?", "personal", genuine_clarification_history
    )
    assert genuine_followup is not None and genuine_followup["mode"] == "clarify"

    fingerprint = atlas_orchestrator_v2.question_fingerprint("  A Private Request ", "owner-secret")
    assert fingerprint == atlas_orchestrator_v2.question_fingerprint("a private request", "owner-secret")
    assert fingerprint != atlas_orchestrator_v2.question_fingerprint("a private request", "different-secret")
    assert "private" not in fingerprint

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = pathlib.Path(temp_dir) / "atlas-app.db"
        atlas_app_store.init_db(db_path)
        exchange = atlas_app_store.save_exchange(
            db_path,
            question="A raw request that must not enter the trace",
            result={"answer": "test", "route": "general_reference", "tool": "openwebui-chat"},
        )
        stored = atlas_app_store.add_orchestration_shadow_trace(
            db_path,
            {
                "conversation_id": exchange["conversation_id"],
                "user_message_id": exchange["user_message"]["id"],
                "planner_version": atlas_orchestrator_v2.PLANNER_VERSION,
                "model": "test-model",
                "decision_source": "model",
                "context_scope": "personal",
                "question_sha256": fingerprint,
                "question_chars": 44,
                "decision_mode": "direct",
                "decision_freshness": "stable",
                "decision_risk": "low",
                "decision_scope": "personal",
                "capabilities": [],
                "needs_verification": False,
                "reason_code": "stable_knowledge",
                "confidence": 0.72,
                "current_route": "general_reference",
                "current_tool": "openwebui-chat",
                "planner_latency_ms": 12,
                "status": "completed",
            },
        )
        assert stored["question_sha256"] == fingerprint
        listed = atlas_app_store.list_orchestration_shadow_traces(db_path)
        assert listed[0]["decision_mode"] == "direct"
        assert listed[0]["decision_source"] == "model"
        assert atlas_app_store.orchestration_shadow_stats(db_path)["statuses"] == {"completed": 1}
        with sqlite3.connect(db_path) as conn:
            columns = {row[1] for row in conn.execute("pragma table_info(orchestration_shadow_traces)")}
            raw_row = repr(conn.execute("select * from orchestration_shadow_traces").fetchone()).lower()
        assert "question" not in columns
        assert "raw request" not in raw_row

        shadow_exchange = atlas_app_store.save_exchange(
            db_path,
            question="Will the invented city of Nacre Bay need umbrellas tomorrow?",
            result={"answer": "existing answer", "route": "weather", "tool": "open-meteo"},
        )
        planner_result = {
            "ok": True,
            "status": "completed",
            "planner_version": atlas_orchestrator_v2.PLANNER_VERSION,
            "model": "test-model",
            "decision": retrieve,
        }
        planner_completed = mock.Mock(stdout=json.dumps(planner_result), returncode=0)
        with mock.patch.object(atlas_shortcuts_server, "APP_DB", db_path), mock.patch.object(
            atlas_shortcuts_server, "SHORTCUTS_TOKEN", "test-trace-secret"
        ), mock.patch.object(atlas_shortcuts_server.subprocess, "run", return_value=planner_completed) as planner_mock:
            atlas_shortcuts_server._run_shadow_job(
                {
                    "question": "Will the invented city of Nacre Bay need umbrellas tomorrow?",
                    "conversation_id": shadow_exchange["conversation_id"],
                    "user_message_id": shadow_exchange["user_message"]["id"],
                    "context_scope": "personal",
                    "current_route": "weather",
                    "current_tool": "open-meteo",
                    "conversation": [],
                }
            )
        planner_command = planner_mock.call_args.args[0]
        assert "Nacre Bay" not in " ".join(planner_command)
        assert planner_mock.call_args.kwargs["input"].startswith("Will the invented city")
        latest = atlas_app_store.list_orchestration_shadow_traces(db_path, limit=1)[0]
        assert latest["status"] == "completed"
        assert latest["decision_mode"] == "retrieve"
        assert latest["capabilities"] == ["weather.forecast"]

    fake_completed = mock.Mock(stdout='{"answer":"36"}', returncode=0)
    with mock.patch.object(atlas_shortcuts_server.subprocess, "run", return_value=fake_completed) as run_mock:
        assert atlas_shortcuts_server.run_atlas("What is 15% of 240?")["answer"] == "36"
    called_command = run_mock.call_args.args[0]
    assert "What is 15% of 240?" not in called_command
    assert run_mock.call_args.kwargs["input"] == "What is 15% of 240?"
    assert "--question-stdin" in called_command

    print("ATLAS Orchestration v2 shadow tests passed")


if __name__ == "__main__":
    main()
