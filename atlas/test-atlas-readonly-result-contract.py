#!/usr/bin/env python3
"""Focused H1 tests for semantic read-only result status."""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "tools" / "ai"
sys.path.insert(0, str(AI_DIR))
SPEC = importlib.util.spec_from_file_location("atlas_runner_result_contract", AI_DIR / "run-atlas-readonly.py")
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(runner)


def main() -> None:
    math_ok = runner.run_math("What is 17 times 23?")
    assert (math_ok["result_status"], math_ok["error_type"]) == ("ok", None)
    math_bad = runner.run_math("Please calculate this mysterious expression")
    assert (math_bad["result_status"], math_bad["error_type"]) == ("invalid", "parse_failed")

    conversion_ok = runner.ensure_readonly_result_contract(
        runner.run_conversion("Convert 81 kilometers to miles."),
        "units.convert",
        "Convert 81 kilometers to miles.",
    )
    assert (conversion_ok["result_status"], conversion_ok["error_type"]) == ("ok", None)
    conversion_bad = runner.ensure_readonly_result_contract(
        runner.run_conversion("Convert this impossible unit pairing."),
        "units.convert",
        "Convert this impossible unit pairing.",
    )
    assert (conversion_bad["result_status"], conversion_bad["error_type"]) == ("invalid", "parse_failed")

    date_bad = runner.run_datetime("What date is 19 days after Nonday 44, 2026?")
    assert (date_bad["result_status"], date_bad["error_type"]) == ("invalid", "parse_failed")

    malformed_contract = runner.ensure_readonly_result_contract(
        {"answer": "looks valid", "result": {}, "result_status": "success", "error_type": None},
        "public.reference",
        "q",
    )
    assert (malformed_contract["result_status"], malformed_contract["error_type"]) == (
        "invalid",
        "contract_invalid",
    )

    with mock.patch.object(
        runner,
        "run_json_tool",
        return_value={
            "resolved_name": "Berkeley",
            "country": "United States",
            "weather_text": "Clear sky",
            "temperature_c": 20,
            "apparent_temperature_c": 20,
            "source": "Open-Meteo",
            "result_status": "ok",
            "error_type": None,
        },
    ):
        weather_ok = runner.run_weather("What is the weather in Berkeley?")
    assert (weather_ok["result_status"], weather_ok["error_type"]) == ("ok", None)

    with mock.patch.object(
        runner,
        "run_json_tool",
        side_effect=[
            subprocess.CalledProcessError(1, "weather", stderr="private provider detail"),
            subprocess.CalledProcessError(1, "search", stderr="private search detail"),
        ],
    ):
        weather_down = runner.run_weather("What is the weather in Berkeley?")
    assert (weather_down["result_status"], weather_down["error_type"]) == (
        "unavailable",
        "adapter_unavailable",
    )

    no_public_evidence = runner.ensure_readonly_result_contract(
        {"answer": "A model guessed.", "result": {"search": {}}, "route": "general_reference"},
        "public.search",
        "latest version?",
    )
    assert (no_public_evidence["result_status"], no_public_evidence["error_type"]) == (
        "unavailable",
        "ungrounded_result",
    )

    no_private_evidence = runner.ensure_readonly_result_contract(
        {
            "answer": "A model guessed.",
            "tool": "openwebui-chat",
            "route": "homelab",
            "result": {"retrieval_documents": []},
        },
        "homelab.knowledge",
        "where does it run?",
    )
    assert (no_private_evidence["result_status"], no_private_evidence["error_type"]) == (
        "no_data",
        "no_data",
    )

    print("PASS: semantic result contract rejects procedural-only and failure-shaped outputs")


if __name__ == "__main__":
    main()
