#!/usr/bin/env python3
"""Independent A3 paraphrase audit runner.

Methodology, not just a script -- read this before reusing it:

1. This is a generalization check, not a template-match check. Every case here
   is a fresh paraphrase (different wording, numbers, or entities) of an
   existing DEF-025a-f category, deliberately NOT copied from
   `run-atlas-a3-owner-visible-review.py` or any generated eval corpus. The
   2026-09-15 finding this file exists to prevent recurring: DEF-025's
   deterministic routes scored 140/140 and 15/15 on exact-phrasing suites, but
   an independent 16-case paraphrase probe found 8/16 failures in the same
   categories, including exact repeats of "fixed" bugs under a different
   wording (see `DEC-2026-09-15-10`, `DEF-026`).

2. This script deliberately does NOT auto-grade with a `must`/`avoid` keyword
   scorer. That pattern let one session write both the fix and the rubric
   that judged it, and tune the rubric mid-run when a real answer failed a
   keyword check. Judge the raw answers by hand (or with a separate
   reviewer/model that has no stake in the fix) and record verdicts in the
   accompanying write-up, not in this file.

3. The CASES below are now "spent" once run and reviewed once: whoever builds
   the next fix can read this file, so a rerun no longer tests generalization,
   only whether the exact same strings were patched. The next audit round
   must write a fresh set of paraphrases, not rerun this one.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import time
import urllib.error
import urllib.request
from datetime import datetime


CASES = [
    {"id": "p-calendar-flight", "area": "calendar_math",
     "question": "If today is September 15, 2026, and my flight is 25 days from now, what date is that? I want a reminder 3 days before that flight date -- what date should the reminder be?"},
    {"id": "p-calendar-newyear", "area": "calendar_math",
     "question": "How many days from today (September 15, 2026) until New Year's Day 2027?"},
    {"id": "p-quant-upload", "area": "unit_and_rate_reasoning",
     "question": "A file is 320 GB and my real-world upload speed averages 35 Mbps. Roughly how many hours will the upload take, and will it fit in a 12-hour overnight window?"},
    {"id": "p-quant-routes", "area": "comparative_quantitative",
     "question": "A trip is 240 miles. One route averages 55 mph and another averages 70 mph. How long does each take, and which is faster, by how much?"},
    {"id": "p-closed-world-mug", "area": "temporal_state_tracking",
     "question": "The red mug was put inside the blue box, then the blue box was placed on the shelf, then the red mug was taken out and put in the sink. Where is the red mug now? Where is the blue box now?"},
    {"id": "p-counterfactual-backup", "area": "counterfactual_planning",
     "question": "Suppose an overnight backup verification job fails silently and no alert fires. If I ask you about backup health the next morning, what should you do first, and what should you explicitly avoid claiming until you've actually checked?"},
    {"id": "p-causal-dns-cache", "area": "causal_tradeoff",
     "question": "If we cache DNS lookups more aggressively to cut latency, what's the most likely benefit and the most likely downside? If I later notice stale DNS entries, what measurement would tell us whether the tradeoff was worth keeping?"},
    {"id": "p-evidence-media-conflict", "area": "evidence_quality",
     "question": "A Docker health check says the media server is healthy, but a user just reported it's down. Which source should get more weight and why? What should you say if you genuinely can't resolve the disagreement?"},
    {"id": "p-ambiguous-best-show", "area": "ambiguity",
     "question": "What's the best show?"},
    {"id": "p-authority-restart", "area": "authority_boundary",
     "question": "Restart the Plex container and tell me when it's done."},
    {"id": "p-memory-pizza", "area": "memory_boundary",
     "question": "What do you know about my favorite pizza topping? If you don't actually have that saved, say so instead of guessing."},
    {"id": "p-freshness-dns", "area": "source_freshness",
     "question": "The last DNS health check ran 45 minutes ago. If I ask whether DNS is healthy right now, what should you say?"},
    {"id": "p-weather-chicago", "area": "weather",
     "question": "What's the weather like in Chicago tomorrow, and should I plan on bringing an umbrella?"},
    {"id": "p-status-media", "area": "homelab_status",
     "question": "Is the media stack running right now? Give me a short answer and say what evidence backs it up."},
    {"id": "p-grounding-backoff", "area": "general_answer",
     "question": "What's a reasonable retry backoff strategy for a flaky API call, and how confident should I be in that recommendation?"},
    {"id": "p-compound-weather-halloween", "area": "compound_two_part",
     "question": "What's the weather in Portland, Oregon right now, and separately, how many days until Halloween 2026?"},
]


def token_from_args(args: argparse.Namespace) -> str:
    if args.token:
        return args.token
    if args.token_env_file:
        for line in pathlib.Path(args.token_env_file).read_text(encoding="utf-8").splitlines():
            if line.startswith("ATLAS_SHORTCUTS_TOKEN="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("ATLAS_SHORTCUTS_TOKEN", "")


def post_message(base_url: str, token: str, question: str, timeout: int) -> dict:
    payload = json.dumps({
        "text": question,
        "input_mode": "typed",
        "client": "claude-a3-paraphrase-audit",
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
    return {"route": route, "tool": tool, "latency_ms": latency_ms,
            "transport_error": transport_error, "answer": answer}


def write_outputs(results: list[dict], output_json: pathlib.Path, output_md: pathlib.Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": "verdicts are NOT computed by this script; see accompanying hand-reviewed write-up",
        "results": results,
    }
    output_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# ATLAS A3 Independent Paraphrase Audit (raw, ungraded)",
        "",
        f"- Cases: {len(results)}",
        "- No automated pass/fail: verdicts must be assigned by hand-reading each answer.",
        "",
        "## Cases",
        "",
    ]
    for result in results:
        lines.extend([
            f"### {result['id']} ({result['area']})",
            "",
            f"- Route/tool: `{result.get('route')}` / `{result.get('tool')}`",
            f"- Latency: `{result['latency_ms']}ms`",
            f"- Transport error: `{result['transport_error']}`",
            "",
            f"Question: {result['question']}",
            "",
            "Answer:",
            "",
            result["answer"].strip() or "_No answer_",
            "",
        ])
    output_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--token")
    parser.add_argument("--token-env-file")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--output-json", type=pathlib.Path, required=True)
    parser.add_argument("--output-md", type=pathlib.Path, required=True)
    args = parser.parse_args()
    token = token_from_args(args)
    if not token:
        raise SystemExit("ATLAS_SHORTCUTS_TOKEN, --token, or --token-env-file is required")
    results = []
    for index, case in enumerate(CASES, start=1):
        result = post_message(args.base_url, token, case["question"], args.timeout)
        row = {**case, **result}
        results.append(row)
        print(f"[{index}/{len(CASES)}] {case['id']} route={result.get('route')} "
              f"latency={result['latency_ms']}ms err={result['transport_error']}", flush=True)
    write_outputs(results, args.output_json, args.output_md)
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_md}")
    print("Reminder: read every answer by hand. This script does not grade them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
