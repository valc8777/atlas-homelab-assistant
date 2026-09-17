#!/usr/bin/env python3

"""Focused tests for ATLAS orchestration v2 deep-plan (multi-step read)
authority: the second half of the orchestration-v2 queue item, following
action-write authority.

Covers:
- run_deep_plan_readonly (atlas_shortcuts_server.py): each capability step
  runs independently; one failing step (timeout / process error / invalid
  JSON) never aborts the rest -- read-only, so partial evidence is still
  useful; when zero steps succeed, synthesis is skipped entirely (nothing
  but latency/hallucination risk to gain); when synthesis itself fails
  despite successful steps, falls back to a raw concatenation rather than
  losing the gathered evidence.
- build_deep_plan_synthesis_prompt / run_deep_plan_synthesis_answer
  (run-atlas-readonly.py): a failed step is framed as "FAILED", not as
  "nothing exists"; a successful step's answer appears verbatim as evidence;
  an empty step list doesn't crash.
"""

import importlib.util
import pathlib
import subprocess
import sys
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "tools" / "ai"
sys.path.insert(0, str(AI_DIR))

RUNNER_SPEC = importlib.util.spec_from_file_location("atlas_runner_deep_plan", AI_DIR / "run-atlas-readonly.py")
runner = importlib.util.module_from_spec(RUNNER_SPEC)
assert RUNNER_SPEC.loader
RUNNER_SPEC.loader.exec_module(runner)

SERVER_SPEC = importlib.util.spec_from_file_location("atlas_shortcuts_server_deep_plan", AI_DIR / "atlas_shortcuts_server.py")
server = importlib.util.module_from_spec(SERVER_SPEC)
assert SERVER_SPEC.loader
SERVER_SPEC.loader.exec_module(server)


def main():
    # -- semantic contract matrix: every injected adapter failure is non-ok
    failure_contracts = [
        ({"result_status": "no_data", "error_type": "no_data", "answer": "Nothing found."}, "no_data"),
        ({"result_status": "unavailable", "error_type": "adapter_unavailable", "answer": "Provider down."}, "unavailable"),
        ({"result_status": "invalid", "error_type": "parse_failed", "answer": "Could not parse."}, "invalid"),
        ({"result_status": "stale", "error_type": "stale_data", "answer": "Old reading."}, "stale"),
        ({"result_status": "ok", "error_type": "adapter_unavailable", "answer": "Contradiction."}, "invalid"),
        ({"result_status": "unavailable", "error_type": None, "answer": "Missing error type."}, "invalid"),
        ({"result_status": "made_up", "error_type": None, "answer": "Unknown status."}, "invalid"),
        ({"result_status": "ok", "error_type": None, "answer": ""}, "invalid"),
        ({"answer": "No contract at all."}, "invalid"),
    ]
    for injected, expected_status in failure_contracts:
        status, _ = server.semantic_step_result(injected)
        assert status == expected_status and status != "ok"

    # -- run_deep_plan_readonly: all steps succeed -------------------------
    with mock.patch.object(
        server, "run_atlas_selected_capability",
        side_effect=[
            {
                "answer": "18.3 C and foggy in SF.",
                "source": "open-meteo",
                "result_status": "ok",
                "error_type": None,
                "result": {
                    "result_status": "ok",
                    "requested_period": "current",
                    "resolved_name": "San Francisco",
                    "country": "United States",
                    "weather_text": "Fog",
                    "temperature_c": 18.3,
                    "apparent_temperature_c": 17.9,
                    "time": "2026-09-12T21:00",
                    "timezone": "America/Los_Angeles",
                },
            },
            {
                "answer": "SF is in the Pacific time zone.",
                "source": "wikidata",
                "result_status": "ok",
                "error_type": None,
                "result": {"sources_count": 1, "citation_count": 1},
            },
        ],
    ) as cap_mock, mock.patch.object(
        server, "run_deep_plan_synthesis", return_value={"answer": "It's 65F and foggy; SF is Pacific time."}
    ) as synth_mock:
        result = server.run_deep_plan_readonly(
            "Research weather and reference details for SF", "personal", [], ["weather.forecast", "public.reference"]
        )
    assert result["route"] == "deep_plan_synthesis"
    assert "65F and foggy" in result["answer"] or "Pacific" in result["answer"]
    assert "Combined answer:" in result["answer"]
    assert "Per-clause status:" in result["answer"]
    assert "1. Read current weather and forecasts: OK" in result["answer"]
    assert "2. Look up public structured reference data: OK" in result["answer"]
    assert "64.9 F (18.3 C)" in result["answer"]
    assert "Source: open-meteo; observed at 2026-09-12T21:00; timezone America/Los_Angeles." in result["answer"]
    assert "Source: wikidata; provenance: 1 source(s), 1 citation(s)." in result["answer"]
    assert result["answer"].rstrip().endswith("Nothing was changed.")
    assert cap_mock.call_count == 2
    synth_step_results = synth_mock.call_args[0][1]
    assert [s["status"] for s in synth_step_results] == ["ok", "ok"]
    assert all(s["latency_ms"] >= 0 for s in synth_step_results)

    # -- one step times out mid-sequence; the remaining step still runs ---
    with mock.patch.object(
        server, "run_atlas_selected_capability",
        side_effect=[
            subprocess.TimeoutExpired("cmd", 45),
            {"answer": "Found it.", "source": "wikidata", "result_status": "ok", "error_type": None},
        ],
    ) as cap_mock_timeout, mock.patch.object(
        server, "run_deep_plan_synthesis", return_value={"answer": "Partial synthesis."}
    ) as synth_mock_timeout:
        result_timeout = server.run_deep_plan_readonly(
            "q", "personal", [], ["weather.forecast", "public.reference"]
        )
    assert cap_mock_timeout.call_count == 2, "the second step must still run after the first times out"
    steps = result_timeout["result"]["steps"]
    assert steps[0]["status"] == "unavailable" and steps[0]["error_type"] == "subprocess_timeout"
    assert steps[1]["status"] == "ok" and steps[1]["answer"] == "Found it."
    assert result_timeout["route"] == "deep_plan_partial"
    assert result_timeout["result"]["synthesis_status"] == "ok_partial"
    # Owner-facing text names the capability in plain language and the
    # concrete reason, not the internal step_id/status/error_type codes.
    assert "weather and forecasts" in result_timeout["answer"]
    assert "too long to respond" in result_timeout["answer"]
    assert "1. Read current weather and forecasts: Not completed" in result_timeout["answer"]
    assert "2. Look up public structured reference data: OK" in result_timeout["answer"]
    assert "step-1" not in result_timeout["answer"]
    assert "subprocess_timeout" not in result_timeout["answer"]
    synth_mock_timeout.assert_called_once()
    assert synth_mock_timeout.call_args[0][1] == [steps[1]], "failed evidence must not reach synthesis"

    # -- CalledProcessError on one step -------------------------------------
    with mock.patch.object(
        server, "run_atlas_selected_capability",
        side_effect=[subprocess.CalledProcessError(1, "cmd"), {"answer": "ok answer", "source": "s", "result_status": "ok", "error_type": None}],
    ), mock.patch.object(server, "run_deep_plan_synthesis", return_value={"answer": "synth"}):
        result_error = server.run_deep_plan_readonly("q", "personal", [], ["math.calculate", "public.search"])
    assert result_error["result"]["steps"][0]["status"] == "unavailable"
    assert result_error["result"]["steps"][0]["error_type"] == "subprocess_error"
    assert result_error["result"]["steps"][1]["status"] == "ok"

    # -- invalid/malformed JSON from a step ---------------------------------
    with mock.patch.object(
        server, "run_atlas_selected_capability",
        side_effect=[{"answer": "ok answer", "source": "s", "result_status": "ok", "error_type": None}, ValueError("bad json")],
    ), mock.patch.object(server, "run_deep_plan_synthesis", return_value={"answer": "synth"}):
        result_invalid = server.run_deep_plan_readonly("q", "personal", [], ["math.calculate", "public.search"])
    assert result_invalid["result"]["steps"][1]["status"] == "invalid"
    assert result_invalid["result"]["steps"][1]["error_type"] == "invalid_payload"

    # -- a semantic adapter failure is non-ok even though the subprocess
    # returned valid JSON and a human-readable fallback answer -------------
    with mock.patch.object(
        server, "run_atlas_selected_capability",
        side_effect=[
            {
                "answer": "I couldn't reach the weather adapter.",
                "source": None,
                "result_status": "unavailable",
                "error_type": "adapter_unavailable",
            },
            {"answer": "42", "source": "math", "result_status": "ok", "error_type": None},
        ],
    ), mock.patch.object(server, "run_deep_plan_synthesis", return_value={"answer": "42."}) as semantic_synth:
        semantic_failure = server.run_deep_plan_readonly(
            "weather and math", "personal", [], ["weather.forecast", "math.calculate"]
        )
    semantic_steps = semantic_failure["result"]["steps"]
    assert [step["status"] for step in semantic_steps] == ["unavailable", "ok"]
    assert semantic_failure["route"] == "deep_plan_partial"
    assert semantic_synth.call_args[0][1] == [semantic_steps[1]]

    # A response missing the contract fails closed instead of inheriting
    # procedural subprocess success.
    with mock.patch.object(
        server, "run_atlas_selected_capability", return_value={"answer": "looks useful", "source": "s"}
    ), mock.patch.object(server, "run_deep_plan_synthesis") as missing_contract_synth:
        missing_contract = server.run_deep_plan_readonly("q", "personal", [], ["weather.forecast"])
    assert missing_contract["result"]["steps"][0]["status"] == "invalid"
    assert missing_contract["result"]["steps"][0]["error_type"] == "contract_invalid"
    missing_contract_synth.assert_not_called()

    # Repeated capabilities retain separate ordered identities.
    repeated_plan = [
        {"capability": "weather.forecast", "request": "weather in A"},
        {"capability": "weather.forecast", "request": "weather in B"},
    ]
    with mock.patch.object(
        server,
        "run_atlas_selected_capability",
        side_effect=[
            {"answer": "A", "source": "s", "result_status": "ok", "error_type": None},
            {"answer": "B", "source": "s", "result_status": "ok", "error_type": None},
        ],
    ), mock.patch.object(server, "run_deep_plan_synthesis", return_value={"answer": "A and B"}):
        repeated = server.run_deep_plan_readonly(
            "two cities", "personal", [], ["weather.forecast", "weather.forecast"], repeated_plan
        )
    repeated_steps = repeated["result"]["steps"]
    assert [step["ordinal"] for step in repeated_steps] == [1, 2]
    assert [step["step_id"] for step in repeated_steps] == ["step-1", "step-2"]

    daily_answer = server.compose_deep_plan_owner_answer(
        "Bring a jacket.",
        [
            {
                "ordinal": 1,
                "capability": "weather.forecast",
                "status": "ok",
                "answer": "high 20 C, low 11 C.",
                "source": "Open-Meteo",
                "result": {
                    "result_status": "ok",
                    "requested_period": "tomorrow",
                    "resolved_date": "2026-09-13",
                    "resolved_name": "Martinez",
                    "country": "United States",
                    "weather_text": "Partly cloudy",
                    "temperature_max_c": 20,
                    "temperature_min_c": 11,
                    "timezone": "America/Los_Angeles",
                },
            }
        ],
        "personal",
    )
    assert "high 68 F (20 C), low 51.8 F (11 C)" in daily_answer
    assert "Source: Open-Meteo; forecast date 2026-09-13; timezone America/Los_Angeles." in daily_answer

    unreported_confidence = server.compose_deep_plan_owner_answer(
        "The public reference answer was available.",
        [
            {
                "ordinal": 1,
                "capability": "public.reference",
                "status": "ok",
                "answer": "Reference fact.",
                "source": "wikidata",
            }
        ],
        "personal",
    )
    assert "Source: wikidata; source confidence not reported by this adapter." in unreported_confidence

    # -- all steps fail: synthesis must never be called ---------------------
    with mock.patch.object(
        server, "run_atlas_selected_capability", side_effect=subprocess.TimeoutExpired("cmd", 45)
    ), mock.patch.object(server, "run_deep_plan_synthesis") as synth_mock_allfail:
        result_allfail = server.run_deep_plan_readonly("q", "personal", [], ["weather.forecast", "public.reference"])
    assert result_allfail["route"] == "deep_plan_partial"
    assert result_allfail["result"]["synthesis_status"] == "skipped_no_evidence"
    assert "Combined answer:" in result_allfail["answer"]
    assert "Per-clause status:" in result_allfail["answer"]
    assert "Not completed" in result_allfail["answer"]
    assert "Nothing was changed." in result_allfail["answer"]
    synth_mock_allfail.assert_not_called()

    # -- synthesis itself fails despite successful steps: fall back to a
    # raw concatenation rather than losing the gathered evidence -----------
    with mock.patch.object(
        server, "run_atlas_selected_capability",
        return_value={"answer": "A real finding.", "source": "s", "result_status": "ok", "error_type": None},
    ), mock.patch.object(
        server, "run_deep_plan_synthesis", side_effect=subprocess.CalledProcessError(1, "cmd")
    ):
        result_synth_fail = server.run_deep_plan_readonly("q", "personal", [], ["weather.forecast"])
    assert result_synth_fail["route"] == "deep_plan_partial"
    assert result_synth_fail["result"]["synthesis_status"] == "failed"
    assert "A real finding." in result_synth_fail["answer"]
    assert "Per-clause status:" in result_synth_fail["answer"]
    assert "Source: s." in result_synth_fail["answer"]

    # -- build_deep_plan_synthesis_prompt ------------------------------------
    prompt = runner.build_deep_plan_synthesis_prompt(
        "What's the weather and reference info for SF?",
        [
            {"capability": "weather.forecast", "status": "ok", "answer": "65F and foggy."},
            {"capability": "public.reference", "status": "timeout", "answer": None},
        ],
    )
    assert "65F and foggy." in prompt
    assert "FAILED" in prompt
    assert "public.reference" in prompt
    assert "Do not claim to know its answer" in prompt

    empty_prompt = runner.build_deep_plan_synthesis_prompt("q", [])
    assert "No research steps were available" in empty_prompt

    # -- run_deep_plan_synthesis_answer ---------------------------------------
    with mock.patch.object(runner, "answer_with_model", return_value=("Synthesized final answer.", "mock-model")):
        synthesis_result = runner.run_deep_plan_synthesis_answer(
            question="q",
            step_results=[
                {"capability": "weather.forecast", "status": "ok", "answer": "65F."},
                {"capability": "public.reference", "status": "error", "answer": None},
            ],
            base_url="http://unused",
            db_path=pathlib.Path("/tmp/unused.db"),
            model=None,
            timeout=1,
            system_prompt_file=pathlib.Path("/tmp/unused-system.md"),
        )
    assert synthesis_result["route"] == "deep_plan_synthesis"
    assert synthesis_result["answer"] == "Synthesized final answer."
    assert synthesis_result["result"]["step_count"] == 2
    assert synthesis_result["result"]["failed_steps"] == ["public.reference"]

    print("ATLAS deep-plan (multi-step read) authority test passed")


if __name__ == "__main__":
    main()
