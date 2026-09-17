#!/usr/bin/env python3
"""Tests for Track B2's signal registry and condition mini-grammar
(tools/ai/atlas_signals.py, 2026-09-08).

This module is deliberately config-free and side-effect-free (see its own
docstring), so every check here runs offline with mocked no-arg callables --
no database, no network, no model. Covers the two things the roadmap
explicitly requires of Track B1's condition engine:

1. "Fail loudly at creation, not silently at evaluation" --
   validate_condition_for_creation() must reject an unknown signal name or
   an ambiguous bare state-type reference, while continuing to accept free
   text and the legacy 'always'/'now' stub unchanged.
2. Correct three-way evaluation branching (structured / not_structured /
   unknown_signal / unregistered_route) that tools/ai/atlas_app_store.py's
   due_rules() depends on to decide whether a rule is due, defensively skip,
   or fall back to the legacy stub path.

Run: python3 tools/ai/test-atlas-signals.py
"""

from __future__ import annotations

import pathlib
import sys

AI_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AI_DIR))

# Plain import, not importlib.util.spec_from_file_location(): unlike
# run-atlas-readonly.py (hyphenated, not a valid Python identifier),
# atlas_signals.py has a normal module name and can just be imported
# directly. This also sidesteps a real Python 3.14 dataclass quirk: a
# frozen dataclass decorated in a module loaded via spec_from_file_location
# with a synthetic module name crashes resolving type annotations, because
# sys.modules.get(cls.__module__) returns None for a module never actually
# registered in sys.modules under that name.
import atlas_signals as sig

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}{(' -- ' + detail) if detail else ''}")
        FAILURES.append(label)


def test_registry_shape() -> None:
    print("\nRegistry sanity")
    check("Phase 2a plus first Phase 2b signal present", {s.name for s in sig.SIGNAL_REGISTRY} == {
        "reminders_overdue_count", "confirmations_pending_count", "notifications_failed_count",
        "bedside_lamp_state", "calendar_event_within_24h", "services_unreachable_count",
        "hosts_unhealthy_count", "backup_latest_ok", "backup_last_success_age_hours",
    })
    check("registry phases are explicit", {s.phase for s in sig.SIGNAL_REGISTRY} == {"2a", "2b"})
    check("legacy stub set is exactly always/now", sig.LEGACY_STUB_CONDITIONS == frozenset({"always", "now"}))


def test_creation_validation() -> None:
    print("\nvalidate_condition_for_creation() -- fail loudly at creation")
    check("legacy 'always' accepted unchanged", sig.validate_condition_for_creation("always") is None)
    check("legacy 'NOW' accepted case-insensitively", sig.validate_condition_for_creation("NOW") is None)
    check("free text still accepted (matches today's behavior for non-stub text)",
          sig.validate_condition_for_creation("backup fails") is None)
    check("valid structured count condition", sig.validate_condition_for_creation("reminders_overdue_count > 0") is None)
    check("valid bare count (truthy)", sig.validate_condition_for_creation("confirmations_pending_count") is None)
    check("valid bare bool", sig.validate_condition_for_creation("calendar_event_within_24h") is None)
    check("valid state WITH comparison", sig.validate_condition_for_creation("bedside_lamp_state == on") is None)
    unknown_error = sig.validate_condition_for_creation("nonexistent_signal_xyz > 0")
    check("unknown signal rejected", unknown_error is not None and "nonexistent_signal_xyz" in unknown_error)
    bare_state_error = sig.validate_condition_for_creation("bedside_lamp_state")
    check("bare state-type signal rejected (ambiguous truthiness)", bare_state_error is not None)


def test_evaluation_branches() -> None:
    print("\nevaluate_condition() -- three-way branching due_rules() depends on")
    routes = {
        "reminders_overdue_count": lambda: 3,
        "confirmations_pending_count": lambda: 0,
        "notifications_failed_count": lambda: 2,
        "bedside_lamp_state": lambda: "on",
        "calendar_event_within_24h": lambda: True,
        "services_unreachable_count": lambda: 2,
        "hosts_unhealthy_count": lambda: 1,
        "backup_latest_ok": lambda: True,
        "backup_last_success_age_hours": lambda: 4.5,
    }
    check("count > threshold, true", sig.evaluate_condition("reminders_overdue_count > 0", routes) == (True, "structured"))
    check("count > threshold, false", sig.evaluate_condition("confirmations_pending_count > 0", routes) == (False, "structured"))
    check("count == exact match", sig.evaluate_condition("notifications_failed_count == 2", routes) == (True, "structured"))
    check("count <= comparison", sig.evaluate_condition("reminders_overdue_count <= 3", routes) == (True, "structured"))
    check("count != comparison", sig.evaluate_condition("reminders_overdue_count != 3", routes) == (False, "structured"))
    check("bare count truthy (nonzero)", sig.evaluate_condition("reminders_overdue_count", routes) == (True, "structured"))
    check("bare count falsy (zero)", sig.evaluate_condition("confirmations_pending_count", routes) == (False, "structured"))
    check("state equality, case-insensitive value", sig.evaluate_condition("bedside_lamp_state == ON", routes) == (True, "structured"))
    check("state inequality", sig.evaluate_condition("bedside_lamp_state != off", routes) == (True, "structured"))
    check("bare bool true", sig.evaluate_condition("calendar_event_within_24h", routes) == (True, "structured"))
    check("phase 2b count condition", sig.evaluate_condition("services_unreachable_count > 0", routes) == (True, "structured"))
    check("host health count condition", sig.evaluate_condition("hosts_unhealthy_count > 0", routes) == (True, "structured"))
    check("backup latest bool condition", sig.evaluate_condition("backup_latest_ok == true", routes) == (True, "structured"))
    check("backup age condition", sig.evaluate_condition("backup_last_success_age_hours > 24", routes) == (False, "structured"))

    check("legacy stub -> not_structured (handled separately by due_rules())",
          sig.evaluate_condition("always", routes) == (None, "not_structured"))
    check("free text -> not_structured", sig.evaluate_condition("call mom tomorrow", routes) == (None, "not_structured"))
    check("unknown signal -> unknown_signal", sig.evaluate_condition("made_up_signal > 0", routes) == (None, "unknown_signal"))
    check("bare state signal (no comparison) -> unknown_signal (matches creation-time rejection)",
          sig.evaluate_condition("bedside_lamp_state", routes) == (None, "unknown_signal"))
    check("registered signal but missing from routes dict -> unregistered_route",
          sig.evaluate_condition("reminders_overdue_count > 0", {}) == (None, "unregistered_route"))


def test_type_coercion_and_incomparable_values() -> None:
    print("\nValue parsing and defensive incomparable-type handling")
    routes = {"reminders_overdue_count": lambda: 5}
    check("float value parses", sig.evaluate_condition("reminders_overdue_count > 4.5", routes) == (True, "structured"))
    check("negative value parses", sig.evaluate_condition("reminders_overdue_count > -1", routes) == (True, "structured"))
    bool_routes = {"calendar_event_within_24h": lambda: True}
    check("explicit true comparison", sig.evaluate_condition("calendar_event_within_24h == true", bool_routes) == (True, "structured"))
    check("explicit false comparison", sig.evaluate_condition("calendar_event_within_24h == false", bool_routes) == (False, "structured"))
    # A count compared against a bare word is a real author mistake (e.g. a
    # typo) -- must fail closed (False), not raise, so due_rules() never
    # crashes on a bad-but-grammar-shaped condition that somehow reached
    # evaluation.
    mismatched_routes = {"reminders_overdue_count": lambda: 5}
    check("incomparable types fail closed rather than raising",
          sig.evaluate_condition("reminders_overdue_count == on", mismatched_routes) == (False, "structured"))


def main() -> int:
    test_registry_shape()
    test_creation_validation()
    test_evaluation_branches()
    test_type_coercion_and_incomparable_values()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
