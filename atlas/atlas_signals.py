#!/usr/bin/env python3
"""Track B2 (2026-09-08): the signal registry and condition mini-grammar for
ATLAS's rules engine (Track B1).

Deliberately Phase 2a only -- the five signals below are all live queries
(SQLite reads via atlas_app_store, or a live HTTP call) that are fresh by
construction on every call, so no staleness field is needed yet. Phase 2b
(backup status, service reachability, host health) needs new periodic-
refresh infrastructure before it can be added safely -- see
ATLAS_CAPABILITY_ROADMAP.md's B2 section for why those three are deferred.

This module is deliberately config-free and side-effect-free: it holds only
metadata (SIGNAL_REGISTRY) and the pure grammar parser/evaluator. The actual
callables that read live data (SIGNAL_ROUTES, built with real db paths, HA
tokens, Nextcloud credentials, etc.) are constructed by the caller
(atlas_shortcuts_server.py, which already owns all of that config) and
passed into evaluate_condition() as a plain dict -- the same metadata/
dispatch split this project already uses for capabilities
(atlas_orchestrator_v2.py's Capability/CAPABILITIES/capability_registry(),
and run-atlas-readonly.py's separate CAPABILITY_ROUTES). Keeping this module
free of real config makes it fully unit-testable with mocked no-arg
callables -- no database, no network, no model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Signal:
    name: str
    description: str
    # "count": a non-negative integer, bare usage means "nonzero" (Python
    #   truthiness convention, reasonable for a technical single-owner tool).
    # "bool": already boolean-shaped, bare usage means the value itself.
    # "state": a string (e.g. "on"/"off"). Bare usage is deliberately
    #   rejected at creation time (see validate_condition_for_creation) --
    #   a bare state name has no non-arbitrary truthy interpretation, unlike
    #   count/bool, so an explicit comparison is required.
    value_type: str
    phase: str  # "2a" today; "2b" reserved for the deferred stale-prone signals


SIGNAL_REGISTRY: tuple[Signal, ...] = (
    Signal("reminders_overdue_count", "Number of overdue (past-due, not completed) reminders", "count", "2a"),
    Signal("confirmations_pending_count", "Number of action confirmations awaiting owner approval", "count", "2a"),
    Signal("notifications_failed_count", "Number of notifications that failed delivery (dead-lettered)", "count", "2a"),
    Signal("bedside_lamp_state", "Bedside lamp on/off state via Home Assistant", "state", "2a"),
    Signal("calendar_event_within_24h", "Whether an upcoming Nextcloud calendar event starts within 24 hours", "bool", "2a"),
    Signal("services_unreachable_count", "Number of monitored homelab services unreachable in the latest fresh probe", "count", "2b"),
    Signal("hosts_unhealthy_count", "Number of monitored hosts breaching load, memory, or disk thresholds", "count", "2b"),
    Signal("backup_latest_ok", "Whether the latest Proxmox vzdump task completed successfully", "bool", "2b"),
    Signal("backup_last_success_age_hours", "Hours since the most recent successful Proxmox vzdump task", "count", "2b"),
)

_REGISTRY_BY_NAME: dict[str, Signal] = {signal.name: signal for signal in SIGNAL_REGISTRY}

# Bare identifier, or identifier <op> value. Value is a signed number,
# true/false, or a bare word (for state comparisons like "== on").
_GRAMMAR_PATTERN = re.compile(
    r"^(?P<name>[a-z][a-z0-9_]*)"
    r"(?:\s*(?P<op>>=|<=|==|!=|>|<)\s*(?P<value>-?\d+(?:\.\d+)?|true|false|[a-z][a-z0-9_]*))?$",
    re.IGNORECASE,
)

# The pre-existing stub conditions from before Track B1 -- exempted from
# grammar validation entirely (they're bare-word-shaped and would otherwise
# be misidentified as an unknown signal reference and rejected). Handled by
# a completely separate, unchanged code path in atlas_app_store.due_rules().
LEGACY_STUB_CONDITIONS = frozenset({"always", "now"})


def signal_names() -> list[str]:
    return [signal.name for signal in SIGNAL_REGISTRY]


def validate_condition_for_creation(condition_text: str) -> str | None:
    """Track B1's "fail loudly at creation, not silently at evaluation"
    requirement. Returns an error message if condition_text is grammar-
    shaped but invalid (unknown signal, or a bare state-type signal with no
    comparison). Returns None if the condition is either a valid structured
    condition, a legacy stub ('always'/'now'), or doesn't match the grammar
    at all (free text -- accepted exactly as today, and will simply never
    become due, matching current behavior for any non-stub condition).
    """
    stripped = condition_text.strip()
    if stripped.lower() in LEGACY_STUB_CONDITIONS:
        return None
    match = _GRAMMAR_PATTERN.match(stripped)
    if not match:
        return None
    name = match.group("name").lower()
    op = match.group("op")
    signal = _REGISTRY_BY_NAME.get(name)
    if signal is None:
        return f"Unknown signal '{name}'. Valid signals: {', '.join(signal_names())}"
    if op is None and signal.value_type == "state":
        return f"Signal '{name}' returns a state value and needs a comparison, e.g. '{name} == on'"
    return None


def _parse_value(raw: str) -> bool | float | int | str:
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        return lowered


def _compare(actual, op: str, expected) -> bool:
    if isinstance(actual, str):
        actual = actual.lower()
    if isinstance(expected, str):
        expected = expected.lower()
    try:
        if op == ">":
            return actual > expected
        if op == ">=":
            return actual >= expected
        if op == "<":
            return actual < expected
        if op == "<=":
            return actual <= expected
        if op == "==":
            return actual == expected
        if op == "!=":
            return actual != expected
    except TypeError:
        # Incomparable types (e.g. a count vs. a bare word) -- fail closed
        # at evaluation time. Creation-time validation is the primary
        # defense; this is a defensive fallback, not the expected path.
        return False
    return False


def evaluate_condition(condition_text: str, signal_routes: dict[str, Callable[[], object]]) -> tuple[bool | None, str]:
    """Evaluate a structured (mini-grammar) condition against live signal
    callables. Returns (result, status):
      - (bool, "structured"): the condition matched a registered signal and
        was evaluated successfully.
      - (None, "not_structured"): condition_text doesn't match the grammar
        at all -- includes legacy 'always'/'now' stubs and free text. The
        caller (due_rules()) handles these via its own separate,
        byte-for-byte-unchanged literal-stub path.
      - (None, "unknown_signal") / (None, "unregistered_route"): defensive
        cases that should have been rejected at creation time by
        validate_condition_for_creation() -- included so due_rules() can
        skip safely rather than crash if one somehow reaches evaluation
        (e.g. a signal added to the registry then later removed).
    """
    stripped = condition_text.strip()
    if stripped.lower() in LEGACY_STUB_CONDITIONS:
        return None, "not_structured"
    match = _GRAMMAR_PATTERN.match(stripped)
    if not match:
        return None, "not_structured"
    name = match.group("name").lower()
    op = match.group("op")
    signal = _REGISTRY_BY_NAME.get(name)
    if signal is None:
        return None, "unknown_signal"
    if op is None and signal.value_type == "state":
        return None, "unknown_signal"
    if name not in signal_routes:
        return None, "unregistered_route"
    actual = signal_routes[name]()
    if op is None:
        return bool(actual), "structured"
    expected = _parse_value(match.group("value"))
    return _compare(actual, op, expected), "structured"
