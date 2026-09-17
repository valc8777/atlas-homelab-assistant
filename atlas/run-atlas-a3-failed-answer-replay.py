#!/usr/bin/env python3
"""Find real historical questions ATLAS answered badly, then replay just
those through the current (fixed) system.

Built on owner request: rather than wait for brand-new real traffic, or
replay everything indiscriminately, this filters real historical exchanges
(tools/ai/atlas_app_store.py's `messages` table, via the same
collect_real_exchanges() join used by run-atlas-a3-real-traffic-review.py
and run-atlas-a3-historical-replay.py) down to ones where the ORIGINAL
answer shows an objective sign of failure -- an error, an empty answer, a
generic non-answer phrase, or a clarify/refuse route -- and re-asks exactly
those questions today.

This is real language the owner actually wrote, not a synthetic test case,
so it counts toward the real-traffic review requirement in DEC-2026-09-15-12
even though it isn't brand-new traffic.

Caveat, same as the historical replay script: some questions were relative
to when they were originally asked ("is my backup happening tonight").
Replayed today, they get evaluated against today's date/time -- a
different-but-not-wrong answer for those, not a fair regression check.

Same house rule as every other real-data review in this repo: this script
does NOT grade or auto-diff for correctness. It flags objective signals to
help prioritize reading, but a human still has to read the actual answers.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import time
from datetime import datetime, timezone


DEFAULT_DB = pathlib.Path("/opt/stacks/ai-stack/atlas-app/atlas-app.db")
DEFAULT_CLIENTS = ("apple-app", "atlas-mobile-pwa", "shortcuts")

_REVIEW_MODULE_PATH = pathlib.Path(__file__).with_name("run-atlas-a3-real-traffic-review.py")
_REPLAY_MODULE_PATH = pathlib.Path(__file__).with_name("run-atlas-a3-historical-replay.py")


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


_review = _load(_REVIEW_MODULE_PATH, "real_traffic_review")
_replay = _load(_REPLAY_MODULE_PATH, "historical_replay")
collect_real_exchanges = _review.collect_real_exchanges
redact = _review.redact
replay_message = _replay.replay_message
token_from_args = _replay.token_from_args

# Same vocabulary as tools/ai/run-atlas-overnight-stress-probe.py's
# _GENERIC_NONANSWER_PHRASES, kept in sync deliberately rather than
# reimplemented differently.
_GENERIC_NONANSWER_PHRASES = (
    "i need one clarification",
    "what criterion, scope, or target",
    "could not verify",
    "missing results do not",
    "i couldn't parse",
    "i couldn't reach",
)


def original_failure_flags(exchange: dict) -> list[str]:
    """Objective signals the ORIGINAL answer looked bad -- not a grade."""
    flags = []
    if exchange.get("error"):
        flags.append("error_present")
    if not exchange.get("answer_found"):
        flags.append("no_answer_found")
    answer = (exchange.get("answer") or "")
    if exchange.get("answer_found") and not answer.strip():
        flags.append("empty_answer")
    route = exchange.get("route") or ""
    if route in {"clarification", "hard_clarification"}:
        flags.append("clarify_route")
    if route in {"refuse", "policy_boundary", "authority_required"}:
        flags.append("refuse_route")
    lowered = answer.lower()
    if any(phrase in lowered for phrase in _GENERIC_NONANSWER_PHRASES):
        flags.append("generic_nonanswer_phrase")
    return flags


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    parser.add_argument("--since", default="1970-01-01T00:00:00+00:00")
    parser.add_argument("--until", default=None)
    parser.add_argument("--clients", default=",".join(DEFAULT_CLIENTS))
    parser.add_argument("--context-scope", default="personal")
    parser.add_argument("--base-url", default=os.environ.get("ATLAS_SCHEDULER_BASE_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--token", default="")
    parser.add_argument("--token-env-file")
    parser.add_argument("--replay-client", default="claude-a3-failed-answer-replay")
    parser.add_argument("--timeout", type=int, default=40)
    parser.add_argument("--output-json", type=pathlib.Path, required=True)
    parser.add_argument("--output-md", type=pathlib.Path, required=True)
    args = parser.parse_args()

    until = args.until or datetime.now(timezone.utc).isoformat()
    clients = tuple(c.strip() for c in args.clients.split(",") if c.strip())
    token = token_from_args(args)

    all_exchanges = collect_real_exchanges(args.db, clients, args.context_scope, args.since, until)
    flagged = []
    for exchange in all_exchanges:
        flags = original_failure_flags(exchange)
        if flags:
            flagged.append({**exchange, "original_flags": flags})

    print(f"{len(all_exchanges)} real exchanges in range; {len(flagged)} flagged for a bad original answer", flush=True)

    results = []
    for i, exchange in enumerate(flagged, 1):
        replay = replay_message(args.base_url, token, args.replay_client, exchange["question"], args.timeout)
        # redact() was already applied to the stored question/answer by
        # collect_real_exchanges(); redact() is idempotent so re-applying
        # here (replay_message already does it for the new answer) is safe.
        results.append({
            "conversation_id": exchange["conversation_id"],
            "question_created_at": exchange["question_created_at"],
            "question": exchange["question"],
            "original_answer": exchange["answer"],
            "original_route": exchange["route"],
            "original_flags": exchange["original_flags"],
            "new_answer": replay["answer"],
            "new_route": replay["route"],
            "new_transport_error": replay["transport_error"],
            "new_latency_ms": replay["latency_ms"],
        })
        print(f"[{i}/{len(flagged)}] replayed", flush=True)

    output = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Real historical questions whose ORIGINAL answer showed an objective "
            "failure signal, replayed through the current system. NOT auto-graded "
            "for correctness -- read every answer by hand."
        ),
        "params": {"since": args.since, "until": until, "clients": list(clients), "context_scope": args.context_scope},
        "total_exchanges_in_range": len(all_exchanges),
        "flagged_count": len(flagged),
        "results": results,
    }

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(output, indent=2), encoding="utf-8")

    lines = [
        "# ATLAS A3 failed-answer replay (real historical questions, re-asked today)",
        "",
        "**Contains real personal conversation text. Never commit this file.**",
        "",
        f"- Total real exchanges in range: {len(all_exchanges)}",
        f"- Flagged for a bad original answer: {len(flagged)}",
        "",
        "Read every answer below by hand -- flags are objective signals only, not a grade.",
        "",
    ]
    for row in results:
        lines.append(f"## {row['question_created_at']} -- flags: {', '.join(row['original_flags'])}")
        lines.append(f"**Question:** {row['question']}")
        lines.append(f"**Original answer** (route: {row['original_route']}): {row['original_answer']}")
        lines.append(
            f"**New answer** (route: {row['new_route']}, {row['new_latency_ms']}ms"
            + (f", transport_error: {row['new_transport_error']}" if row["new_transport_error"] else "")
            + f"): {row['new_answer']}"
        )
        lines.append("")
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote {args.output_json} and {args.output_md}")
    print("PRIVACY: contains real personal conversation text -- never commit these files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
