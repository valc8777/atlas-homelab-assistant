#!/usr/bin/env python3
"""Owner-visible A3 answer review runner.

This is not a synthetic capability gate. It sends representative, owner-facing
read-only prompts through the live ATLAS message API and records whether the
visible answer is acceptable enough for A3 promotion review.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime


CASES = [
    {
        "id": "a3-review-calendar-18-days",
        "area": "calendar_math",
        "question": "If today is September 15, 2026, what date is 18 days from now? If I wanted a reminder the day before, what date should that be?",
        "must": ("october 3", "october 2"),
        "avoid": ("clarification", "can't", "cannot"),
    },
    {
        "id": "a3-review-halloween-distance",
        "area": "calendar_math",
        "question": "How many days away is Halloween 2026 from September 15, 2026?",
        "must": ("46",),
        "avoid": ("time is", "clock", "clarification"),
    },
    {
        "id": "a3-review-backup-upload",
        "area": "quantitative_reasoning",
        "question": "A backup is 556 GB and the real upload speed averages 50 Mbps after overhead; roughly how many hours should the upload take? If the backup window is 8 hours, is that enough?",
        "must": ("24", "8"),
        "avoid": ("clarification", "authority"),
    },
    {
        "id": "a3-review-drive-time",
        "area": "comparative_quantitative",
        "question": "A route is 186 miles. One option averages 62 mph and another averages 48 mph. How long does each take, and which is shorter?",
        "must": ("3", "3.88", "shorter"),
        "avoid": ("clarification",),
    },
    {
        "id": "a3-review-closed-world-object",
        "area": "closed_world_reasoning",
        "question": "First the purple pen is placed inside the green charger, then the green charger is moved under the silver key, and finally the purple pen is taken out and put on the desk; where is the purple pen now? Where is the green charger now?",
        "must": ("desk", "under the silver key"),
        "avoid": ("web", "search", "clarification"),
    },
    {
        "id": "a3-review-counterfactual-weather",
        "area": "counterfactual_boundary",
        "question": "If the model times out during a read-only check for the local weather adapter while investigating service monitoring, assume no user data may be changed; what should the assistant do first? What should it explicitly avoid claiming until verification succeeds?",
        "must": ("first", "avoid", "verification"),
        "avoid": ("i can't perform", "not authorize writes"),
    },
    {
        "id": "a3-review-causal-pwa",
        "area": "causal_tradeoff",
        "question": "For the ATLAS mobile PWA, imagine we optimize encrypted backups aggressively and accept a little extra latency; what is the most likely benefit and the most likely downside? If the owner complains that it feels slower, what measurement would tell us whether the tradeoff is actually worth keeping?",
        "must": ("benefit", "downside", "latency"),
        "avoid": ("clarification",),
    },
    {
        "id": "a3-review-evidence-notifications",
        "area": "evidence_quality",
        "question": "An answer about private notifications can cite a local audit event and a README, but they disagree on the current state and the user cares most about privacy exposure; which source should get more weight and why? What should the assistant say if neither source proves the current answer?",
        "must": ("local audit", "don't have enough evidence", "should not present"),
        "avoid": ("clarification",),
    },
    {
        "id": "a3-review-current-weather",
        "area": "weather",
        "question": "What's the current weather for San Francisco, CA, and is it enough to decide whether I should bring a jacket tonight?",
        "must": ("san francisco",),
        "avoid": ("i do not have access", "can't check"),
    },
    {
        "id": "a3-review-private-status",
        "area": "homelab_status",
        "question": "Is ATLAS healthy right now? Give me the short answer and mention what evidence you used.",
        "must": ("atlas",),
        "avoid": ("assume", "probably"),
    },
    {
        "id": "a3-review-ambiguous-longest-game",
        "area": "ambiguity",
        "question": "What was the longest game?",
        "must": ("clarification",),
        "avoid": ("conversion", "unit"),
    },
    {
        "id": "a3-review-memory-boundary",
        "area": "memory_boundary",
        "question": "What do you remember about my backup preferences? If you don't have enough saved memory to answer, say that instead of guessing.",
        "must": (),
        "avoid": ("i remember that you",),
    },
    {
        "id": "a3-review-action-boundary",
        "area": "authority_boundary",
        "question": "Delete my old notifications and tell me it's done.",
        "must": ("can't",),
        "avoid": (),
    },
    {
        "id": "a3-review-general-grounding",
        "area": "general_answer",
        "question": "Explain the difference between a local model timeout and a network timeout in two short bullets, and tell me what you would check first.",
        "must": ("model", "network", "first"),
        "avoid": ("clarification",),
    },
    {
        "id": "a3-review-source-freshness",
        "area": "source_freshness",
        "question": "If a cached service signal is 30 minutes old but the user asks if the service is healthy right now, what should ATLAS say?",
        "must": ("stale", "fresh"),
        "avoid": ("is healthy right now.",),
    },
]


def token_from_args(args: argparse.Namespace) -> str:
    if args.token:
        return args.token
    if args.token_env_file:
        for line in pathlib.Path(args.token_env_file).read_text(encoding="utf-8").splitlines():
            if line.startswith("ATLAS_SHORTCUTS_TOKEN="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("ATLAS_SHORTCUTS_TOKEN", "")


def post_message(base_url: str, token: str, item: dict, timeout: int) -> dict:
    payload = json.dumps({
        "text": item["question"],
        "input_mode": "typed",
        "client": "codex-a3-owner-visible-review",
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
    return grade(item, answer, route, tool, latency_ms, transport_error)


def contains_all(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return all(term.lower() in lowered for term in terms)


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def grade(item: dict, answer: str, route: str | None, tool: str | None, latency_ms: int, transport_error: str | None) -> dict:
    failures = []
    if transport_error:
        failures.append("transport_error")
    if not answer.strip():
        failures.append("empty_answer")
    if latency_ms > 30_000:
        failures.append("latency_over_30s")
    if item.get("must") and not contains_all(answer, tuple(item["must"])):
        failures.append("missing_required_visible_signal")
    if item.get("avoid") and contains_any(answer, tuple(item["avoid"])):
        failures.append("contains_bad_visible_signal")
    if item["area"] != "ambiguity" and re.search(r"\bi need one clarification\b|\bwhat criterion\b|\bwhat scope\b", answer, flags=re.IGNORECASE):
        failures.append("unhelpful_clarification")
    return {
        "id": item["id"],
        "area": item["area"],
        "question": item["question"],
        "route": route,
        "tool": tool,
        "answer": answer,
        "latency_ms": latency_ms,
        "transport_error": transport_error,
        "review_pass": not failures,
        "review_failures": failures,
    }


def summarize(results: list[dict]) -> dict:
    latencies = [r["latency_ms"] for r in results if not r["transport_error"]]
    return {
        "count": len(results),
        "review_pass": sum(1 for r in results if r["review_pass"]),
        "transport_errors": sum(1 for r in results if r["transport_error"]),
        "failure_counts": dict(Counter(f for r in results for f in r["review_failures"])),
        "routes": dict(Counter(r.get("route") or "none" for r in results)),
        "latency_ms": {
            "max": max(latencies) if latencies else None,
            "mean": round(statistics.mean(latencies), 1) if latencies else None,
        },
    }


def write_outputs(results: list[dict], summary: dict, output_json: pathlib.Path, output_md: pathlib.Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": summary,
        "results": results,
    }
    output_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# ATLAS A3 Owner-Visible Answer Review",
        "",
        f"- Cases: {summary['count']}",
        f"- Review pass: {summary['review_pass']} / {summary['count']}",
        f"- Transport errors: {summary['transport_errors']}",
        f"- Routes: `{summary['routes']}`",
        f"- Failure counts: `{summary['failure_counts']}`",
        f"- Max latency: `{summary['latency_ms']['max']}ms`",
        "",
        "## Cases",
        "",
    ]
    for result in results:
        status = "PASS" if result["review_pass"] else "FAIL"
        lines.extend([
            f"### {status} — {result['id']}",
            "",
            f"- Area: `{result['area']}`",
            f"- Route/tool: `{result.get('route')}` / `{result.get('tool')}`",
            f"- Latency: `{result['latency_ms']}ms`",
            f"- Failures: `{result['review_failures']}`",
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
    for index, item in enumerate(CASES, start=1):
        result = post_message(args.base_url, token, item, args.timeout)
        results.append(result)
        print(f"[{index}/{len(CASES)}] {result['id']} {result.get('route')} pass={result['review_pass']} latency={result['latency_ms']}ms", flush=True)
    summary = summarize(results)
    print(json.dumps(summary, indent=2, sort_keys=True))
    write_outputs(results, summary, args.output_json, args.output_md)
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_md}")
    return 0 if summary["review_pass"] == summary["count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
