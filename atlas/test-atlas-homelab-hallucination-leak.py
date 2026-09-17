#!/usr/bin/env python3
"""Regression test for DEF-002's hallucination-leak half (2026-09-07).

Confirmed on the 2026-09-06 hardening calibration run: homelab-shaped
questions that matched no keyword in classify_question()'s `homelab` branch
fell through to `general_reference` (open web search + model synthesis).
One case confidently hallucinated a wrong, plausible-sounding answer --
"What indexer manager runs alongside the servarr stack?" got "Jackett"
(this homelab's real indexer manager is Prowlarr, confirmed directly
against homelab-service-location-index-2026-08-06.md). Two more cases
("What local language model does ATLAS use?", "What web UI does ATLAS's
chat pipeline sit behind?") didn't hallucinate but reached an honest "I
don't know" via the wrong route (open web search, which genuinely can't
know) instead of the right one (the homelab corpus, which does).

Fix: widened the existing, complete `homelab` route -- added real,
confirmed servarr-stack service names, and a narrow "atlas + nearby
infra word" pattern for self-referential questions about ATLAS's own
stack. Requires "atlas" plus a nearby infra-shaped word (not bare "atlas"
alone), so Atlas Shrugged / the Titan / unrelated "Atlas" products don't
misroute -- worst case of a false positive here is a corpus-grounded
"I don't know," never a wrong action.

This is a pure-function offline test -- no network, no model, no state.

Run: python3 tools/ai/test-atlas-homelab-hallucination-leak.py
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

AI_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AI_DIR))

_SPEC = importlib.util.spec_from_file_location("atlas_runner_homelab_leak", AI_DIR / "run-atlas-readonly.py")
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)

FAILURES: list[str] = []


def check(label: str, question: str, expected: str, context_scope: str = "personal") -> None:
    got = runner.classify_question(question, context_scope=context_scope)
    if got == expected:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} -- got {got!r}, expected {expected!r}")
        FAILURES.append(label)


def test_real_confirmed_failures_now_route_homelab() -> None:
    print("\nThe 3 real confirmed calibration failures that missed every keyword")
    check("servarr indexer (hallucinated 'Jackett' pre-fix)",
          "What indexer manager runs alongside the servarr stack?", "homelab")
    check("ATLAS's own web UI",
          "What web UI does ATLAS's chat pipeline sit behind for local model access?", "homelab")
    check("ATLAS's own model",
          "What local language model does ATLAS use?", "homelab")


def test_other_calibration_cases_unaffected() -> None:
    print("\nThe 5 already-fixed (DEF-002 scope-wall half) calibration cases still route homelab")
    check("request-management frontend", "What request-management frontend pairs with Jellyfin?", "homelab")
    check("Proxmox host for Headscale", "What Proxmox host runs the Headscale LXC container?", "homelab")
    check("ai-stack LAN IP", "What is the current LAN IP address reserved for ai-stack?", "homelab")
    check("docker-main groupware", "What groupware suite runs on docker-main for calendars and files?", "homelab")
    check("Piper TTS host", "Which host runs Piper TTS in this homelab?", "homelab")


def test_new_servarr_keywords_individually() -> None:
    print("\nEach new servarr-stack keyword routes to homelab on its own")
    check("sonarr", "Is sonarr currently reachable?", "homelab")
    check("radarr", "What version of radarr is deployed?", "homelab")
    check("lidarr", "Did lidarr finish its last import?", "homelab")
    check("prowlarr", "Is prowlarr configured with any indexers?", "homelab")
    check("jackett", "Do we still run jackett anywhere?", "homelab")
    check("qbittorrent", "What port does qbittorrent listen on?", "homelab")
    check("mylar", "Is mylar still running?", "homelab")


def test_negative_controls_do_not_misroute() -> None:
    print("\nNegative controls: unrelated 'atlas' and generic-tech questions must NOT misroute")
    check("Atlas Shrugged (book)", "Should I read Atlas Shrugged, or is it overrated?", "general_reference")
    check("Atlas the Titan (mythology)", "Tell me about Atlas the Titan from Greek mythology.", "general_reference")
    check("unrelated generic question", "What is a good tutorial for learning SQL?", "general_reference")
    check("generic model question, no atlas", "What is the best language model for creative writing?", "general_reference")
    check("generic indexer question, no homelab term", "What is a database indexer used for?", "general_reference")
    check("bare 'atlas' with no infra word", "Did you see the world atlas on the shelf?", "general_reference")


def main() -> int:
    test_real_confirmed_failures_now_route_homelab()
    test_other_calibration_cases_unaffected()
    test_new_servarr_keywords_individually()
    test_negative_controls_do_not_misroute()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
