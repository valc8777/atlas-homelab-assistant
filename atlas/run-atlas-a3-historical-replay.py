#!/usr/bin/env python3
"""Replay real historical questions through the current (fixed) A3 system.

This exists because waiting for brand-new real traffic asks the owner to
either sit idle or manually write prompts -- which just recreates the
"synthetic test written to pass" problem this whole DEF-026 investigation
was about. Instead: pull questions real users actually asked in the past
(from tools/ai/atlas_app_store.py's `messages` table, via the same
collect_real_exchanges() join used by run-atlas-a3-real-traffic-review.py),
and send that exact real text through the live system today.

This is real language, not synthetic -- it just isn't NEW. It cannot
substitute for genuinely fresh real-traffic review forever (see
DEC-2026-09-15-12's 3-non-overlapping-samples rule), but it is strictly
better signal than another round of hand-written or generated test cases,
and requires zero new effort from the owner.

Caveat, stated plainly in every report this produces: some historical
questions were relative to the date/time they were originally asked
("is my backup happening tonight," "what's the weather right now"). Replayed
today, they get evaluated against today's real date/time, not the original
one -- a different-but-not-wrong answer for those, not a fair regression
check. Treat date/time-relative mismatches as expected, not a failure.

Same house rule as every other real-data review in this repo: this script
does NOT grade or auto-diff for correctness. It flags objective signals
(route changed, answer went from present to empty or vice versa, new
transport error) to help a human prioritize what to read first, but a
human still has to read the actual answers.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import os
import pathlib
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone


DEFAULT_DB = pathlib.Path("/opt/stacks/ai-stack/atlas-app/atlas-app.db")
DEFAULT_CLIENTS = ("apple-app", "atlas-mobile-pwa", "shortcuts")

_REVIEW_MODULE_PATH = pathlib.Path(__file__).with_name("run-atlas-a3-real-traffic-review.py")


def _load_collect_real_exchanges():
    spec = importlib.util.spec_from_file_location("real_traffic_review", _REVIEW_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.collect_real_exchanges, module.redact


collect_real_exchanges, redact = _load_collect_real_exchanges()


def token_from_args(args: argparse.Namespace) -> str:
    if args.token:
        return args.token
    if args.token_env_file:
        for line in pathlib.Path(args.token_env_file).read_text(encoding="utf-8").splitlines():
            if line.startswith("ATLAS_SHORTCUTS_TOKEN="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("ATLAS_SHORTCUTS_TOKEN", "")


def replay_message(base_url: str, token: str, client: str, question: str, timeout: int) -> dict:
    payload = json.dumps({
        "text": question,
        "input_mode": "typed",
        "client": client,
        "context_scope": "personal",
    }).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/v1/messages",
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=payload,
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        assistant = body.get("assistant_message") or {}
        answer = assistant.get("text") or ""
        route = assistant.get("route")
        tool = assistant.get("tool")
        transport_error = None
    except TimeoutError:
        answer, route, tool, transport_error = "", None, None, "timeout"
    except urllib.error.URLError as exc:
        answer, route, tool, transport_error = "", None, None, f"URLError: {exc}"
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        answer, route, tool, transport_error = "", None, None, f"{type(exc).__name__}: {exc}"
    latency_ms = round((time.monotonic() - started) * 1000)
    return {"answer": redact(answer), "route": route, "tool": tool, "latency_ms": latency_ms, "transport_error": transport_error}


def flag_signals(original: dict, replay: dict) -> list[str]:
    signals = []
    if replay["transport_error"] and not original.get("error"):
        signals.append("new_transport_error")
    orig_route = original.get("route")
    if orig_route and replay["route"] and orig_route != replay["route"]:
        signals.append(f"route_changed:{orig_route}->{replay['route']}")
    orig_empty = not (original.get("answer") or "").strip()
    new_empty = not (replay["answer"] or "").strip()
    if new_empty and not orig_empty:
        signals.append("answer_now_empty")
    if orig_empty and not new_empty:
        signals.append("answer_now_present")
    return signals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    parser.add_argument("--since", default="1970-01-01T00:00:00+00:00")
    parser.add_argument("--until", default=None)
    parser.add_argument("--clients", default=",".join(DEFAULT_CLIENTS))
    parser.add_argument("--context-scope", default="personal")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of historical questions replayed")
    parser.add_argument("--base-url", default=os.environ.get("ATLAS_SCHEDULER_BASE_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--token", default="")
    parser.add_argument("--token-env-file")
    parser.add_argument("--replay-client", default="claude-a3-historical-replay")
    parser.add_argument("--timeout", type=int, default=40)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--output-json", type=pathlib.Path, required=True)
    parser.add_argument("--output-md", type=pathlib.Path, required=True)
    args = parser.parse_args()

    token = token_from_args(args)
    if not token:
        raise SystemExit("ATLAS_SHORTCUTS_TOKEN, --token, or --token-env-file is required")

    until = args.until or datetime.now(timezone.utc).isoformat(timespec="seconds")
    clients = tuple(c.strip() for c in args.clients.split(",") if c.strip())
    historical = collect_real_exchanges(args.db, clients, args.context_scope, args.since, until)
    # Only replay questions that actually got an answer the first time --
    # nothing to compare a no-answer historical row against.
    historical = [h for h in historical if h["answer_found"]]
    if args.limit:
        historical = historical[-args.limit:]  # most recent N

    print(f"Replaying {len(historical)} real historical questions (since {args.since})")
    rows: list[dict] = [None] * len(historical)  # type: ignore[list-item]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        future_to_index = {
            pool.submit(replay_message, args.base_url, token, args.replay_client, original["question"], args.timeout): i
            for i, original in enumerate(historical)
        }
        completed = 0
        for future in concurrent.futures.as_completed(future_to_index):
            i = future_to_index[future]
            original = historical[i]
            replay = future.result()
            signals = flag_signals(original, replay)
            rows[i] = {
                "question_created_at": original["question_created_at"],
                "question": original["question"],
                "original_answer": original["answer"],
                "original_route": original["route"],
                "replay_answer": replay["answer"],
                "replay_route": replay["route"],
                "replay_tool": replay["tool"],
                "replay_latency_ms": replay["latency_ms"],
                "replay_transport_error": replay["transport_error"],
                "signals": signals,
            }
            completed += 1
            if completed % 20 == 0 or completed == len(historical):
                print(f"[{completed}/{len(historical)}]", flush=True)

    flagged = [r for r in rows if r["signals"]]
    summary = {
        "count": len(rows),
        "flagged_count": len(flagged),
        "signal_counts": dict(Counter(s for r in rows for s in r["signals"])),
        "transport_errors": sum(1 for r in rows if r["replay_transport_error"]),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": {"since": args.since, "until": until, "clients": list(clients), "context_scope": args.context_scope},
        "note": (
            "Real historical questions replayed through the current system. NOT auto-graded for "
            "correctness -- 'signals' only flag objective differences (route change, answer "
            "appearing/disappearing, new transport error) to help prioritize what to read. "
            "Date/time-relative questions will legitimately differ from their original answer "
            "since they're evaluated against today's real date, not the original one. Contains "
            "real personal text (secrets redacted only). Never commit this file."
        ),
        "summary": summary,
        "rows": rows,
    }
    args.output_json.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# ATLAS A3 Historical Replay (real questions, ungraded)",
        "",
        "**Contains real personal conversation text (secrets redacted only). Do not commit.**",
        "",
        f"- Replayed: {summary['count']} real historical questions",
        f"- Flagged (objective signal differs): {summary['flagged_count']}",
        f"- Signal counts: `{summary['signal_counts']}`",
        f"- New transport errors: {summary['transport_errors']}",
        "",
        "Read the flagged rows first, but a clean signal doesn't mean the new answer is good --",
        "it only means the route/presence didn't change. Read a sample of the unflagged rows too.",
        "",
        "## Flagged rows",
        "",
    ]
    for row in flagged:
        lines.extend([
            f"### {row['question_created_at']}",
            "",
            f"- Signals: {', '.join(row['signals'])}",
            f"- Original route: `{row['original_route']}` | Replay route: `{row['replay_route']}` / `{row['replay_tool']}`",
            f"- Replay latency: `{row['replay_latency_ms']}ms`",
            "",
            f"Question: {row['question']}",
            "",
            f"Original answer: {(row['original_answer'] or '_none_').strip()}",
            "",
            f"Replay answer: {(row['replay_answer'] or '_none_').strip()}",
            "",
        ])
    if not flagged:
        lines.append("None.")
    output_md_lines = lines + ["", "## All rows", ""]
    for row in rows:
        output_md_lines.extend([
            f"### {row['question_created_at']}{' [flagged]' if row['signals'] else ''}",
            "",
            f"Question: {row['question']}",
            "",
            f"Original ({row['original_route']}): {(row['original_answer'] or '_none_').strip()}",
            "",
            f"Replay ({row['replay_route']}): {(row['replay_answer'] or '_none_').strip()}",
            "",
        ])
    args.output_md.write_text("\n".join(output_md_lines), encoding="utf-8")
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_md}")
    print("REMINDER: contains real personal text. Do not commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
