#!/usr/bin/env python3
"""Regression test for the capability-gap flagging feature (2026-09-07).

Owner-approved scope (explicit AskUserQuestion selection: "Flag gaps for
owner review"): ATLAS does NOT autonomously add or call new APIs. When the
read-only runner (run-atlas-readonly.py) hits a recognizable capability
boundary -- currently just unsupported currency pairs, since that's the only
place with an honest, already-disclosed partial capability to compare
against -- it adds a `capability_gap` field to its JSON result. The runner
itself has no database access (subprocess-per-request, read-only by design).
atlas_shortcuts_server.py is the layer that turns that field into a durable,
owner-visible `atlas_app_store` audit event via record_capability_gap_audit().

This test covers both halves without touching any live system:
1. run-atlas-readonly.py's detect_unsupported_currency_pair() /
   run_conversion() -- pure functions, no network, no state.
2. atlas_shortcuts_server.py's record_capability_gap_audit() -- mocks
   atlas_app_store.add_audit_event so no real database is touched.

Run: python3 tools/ai/test-atlas-capability-gap-audit.py
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from unittest import mock

AI_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AI_DIR))

_RUNNER_SPEC = importlib.util.spec_from_file_location("atlas_runner_gap", AI_DIR / "run-atlas-readonly.py")
runner = importlib.util.module_from_spec(_RUNNER_SPEC)
_RUNNER_SPEC.loader.exec_module(runner)

_SERVER_SPEC = importlib.util.spec_from_file_location("atlas_shortcuts_server_gap", AI_DIR / "atlas_shortcuts_server.py")
server = importlib.util.module_from_spec(_SERVER_SPEC)
_SERVER_SPEC.loader.exec_module(server)

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}{(' -- ' + detail) if detail else ''}")
        FAILURES.append(label)


def gap_of(question: str) -> dict | None:
    return runner.run_conversion(question).get("result", {}).get("capability_gap")


def test_supported_pairs_never_flag() -> None:
    print("\nAlready-supported static-rate pairs must not flag a gap (answered honestly today)")
    check("USD to EUR", gap_of("Convert 100 dollars to euros.") is None)
    check("EUR to USD", gap_of("Convert 50 euros to dollars.") is None)
    check("USD to EUR abbreviations", gap_of("Convert 100 USD to EUR.") is None)


def test_unsupported_pairs_flag() -> None:
    print("\nGenuinely unsupported currency pairs must flag a gap")
    check("GBP to JPY", gap_of("Convert 50 British pounds to yen.") is not None)
    check("adjective-qualified dollars (CAD vs AUD)",
          gap_of("How many Australian dollars is 200 Canadian dollars?") is not None)
    check("exchange rate phrasing", gap_of("What is the exchange rate for Swiss francs?") is not None)
    check("USD to GBP", gap_of("Convert 100 dollars to pounds.") is not None)
    check("INR to USD", gap_of("Convert 5000 rupees to dollars.") is not None)


def test_unrelated_failures_do_not_flag_currency_gap() -> None:
    print("\nUnrelated conversion misses must not falsely flag a currency gap")
    check("nonsense units", gap_of("Convert 5 gallons to smiles.") is None)
    check("unsupported physical unit", gap_of("Convert 10 fathoms to leagues.") is None)
    check("single currency word, no conversion intent", gap_of("Tell me a joke about dollars.") is None)
    check("unrelated is-sentence, no currency", gap_of("What is the capital of France?") is None)
    check("single currency mention only", gap_of("What is 100 dollars worth?") is None)


def test_answer_text_unchanged_by_gap_flag() -> None:
    print("\nUser-facing answer/route/tool must be byte-identical whether or not a gap is flagged")
    with_gap = runner.run_conversion("Convert 50 British pounds to yen.")
    without_gap = runner.run_conversion("Convert 5 gallons to smiles.")
    check("gapped case still uses the same fallback answer text",
          with_gap["answer"] == "I couldn't parse that conversion directly.")
    check("gapped case still uses the same fallback route/tool",
          (with_gap["route"], with_gap["tool"]) == (without_gap["route"], without_gap["tool"]))


def test_audit_hook_fires_only_when_gap_present() -> None:
    print("\nrecord_capability_gap_audit() logs exactly when a gap is present")
    exchange = {"conversation_id": "conv-test-1"}

    gapped_result = runner.run_conversion("Convert 50 British pounds to yen.")
    with mock.patch.object(server.atlas_app_store, "add_audit_event") as mocked:
        server.record_capability_gap_audit(exchange, gapped_result, "personal")
    check("fires exactly once for a flagged gap", mocked.call_count == 1, f"got {mocked.call_count}")
    if mocked.call_count == 1:
        _db, kwargs = mocked.call_args.args[0], mocked.call_args.kwargs
        check("event_type is currency_pair_unsupported",
              kwargs.get("event_type") == "capability_gap.currency_pair_unsupported")
        check("details carry conversation_id", kwargs.get("details", {}).get("conversation_id") == "conv-test-1")
        check("details carry mentioned_currency_terms",
              "mentioned_currency_terms" in kwargs.get("details", {}))

    clean_result = runner.run_conversion("Convert 5 gallons to smiles.")
    with mock.patch.object(server.atlas_app_store, "add_audit_event") as mocked_clean:
        server.record_capability_gap_audit(exchange, clean_result, "personal")
    check("does not fire when no gap is present", mocked_clean.call_count == 0)

    with mock.patch.object(server.atlas_app_store, "add_audit_event") as mocked_no_exchange:
        server.record_capability_gap_audit(None, gapped_result, "personal")
    check("does not fire when there is no saved exchange (e.g. save_exchange failed)",
          mocked_no_exchange.call_count == 0)

    supported_result = runner.run_conversion("Convert 100 dollars to euros.")
    with mock.patch.object(server.atlas_app_store, "add_audit_event") as mocked_supported:
        server.record_capability_gap_audit(exchange, supported_result, "personal")
    check("does not fire for an already-supported currency pair", mocked_supported.call_count == 0)


def main() -> int:
    test_supported_pairs_never_flag()
    test_unsupported_pairs_flag()
    test_unrelated_failures_do_not_flag_currency_gap()
    test_answer_text_unchanged_by_gap_flag()
    test_audit_hook_fires_only_when_gap_present()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
