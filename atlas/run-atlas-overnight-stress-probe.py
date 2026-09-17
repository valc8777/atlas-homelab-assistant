#!/usr/bin/env python3
"""Overnight adversarial stress probe: find what A3 can't do, not what it can.

Every question here is written in realistic, messy, informal phrasing --
typos, terse wording, compound "and what about X" comparisons, ambiguous
references -- because tonight's session found repeatedly that clean
test-case grammar hides failures real, informal phrasing exposes. None of
these are copied from any prior audit, unit test, or generated suite this
session already built.

Same house rule as every other real-evidence tool tonight: this does NOT
self-grade for correctness. It only flags objective signals (empty answer,
transport error, high latency, a literal clarification/refusal string) to
help a human prioritize what to read first. A clean signal does not mean
the answer is good -- read the actual text.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone


CASES = [
    # -- Terse/typo'd date & time (stress-test generalization beyond the
    # exact DEF-026/027 repro phrasings already fixed tonight) --
    {"id": "date-01", "area": "date_time", "q": "hows many days to thanksgiving"},
    {"id": "date-02", "area": "date_time", "q": "whats the date 3 wks from now"},
    {"id": "date-03", "area": "date_time", "q": "if my lease ends dec 1 how many days left"},
    {"id": "date-04", "area": "date_time", "q": "how long ago was y2k"},
    {"id": "date-05", "area": "date_time", "q": "days since jan 1"},

    # -- Reminders / notes / tasks, messy real phrasing --
    {"id": "reminder-01", "area": "reminders", "q": "remind me to renew my passport before it expires end of next month"},
    {"id": "reminder-02", "area": "reminders", "q": "add socks detergent and paper towels to the list"},
    {"id": "reminder-03", "area": "reminders", "q": "did i already add eggs to my list"},
    {"id": "reminder-04", "area": "reminders", "q": "remind me in like 20 min to check the oven"},
    {"id": "reminder-05", "area": "reminders", "q": "make a note that the router password is on the sticky note in the drawer"},
    {"id": "reminder-06", "area": "reminders", "q": "whats on my todo list rn"},

    # -- Home/device control edge cases --
    {"id": "home-01", "area": "home_control", "q": "turn it off"},
    {"id": "home-02", "area": "home_control", "q": "make the light purple-ish, like a sunset color"},
    {"id": "home-03", "area": "home_control", "q": "is the lamp on"},
    {"id": "home-04", "area": "home_control", "q": "dim the lights a bit"},
    {"id": "home-05", "area": "home_control", "q": "turn on the kitchen light"},

    # -- Memory/personal recall probes (checking for honest "don't know" vs
    # confident hallucination on things never actually stored) --
    {"id": "memory-01", "area": "memory_recall", "q": "whats my dogs name"},
    {"id": "memory-02", "area": "memory_recall", "q": "what did i say my favorite restaurant was"},
    {"id": "memory-03", "area": "memory_recall", "q": "remind me what my wifi password is"},
    {"id": "memory-04", "area": "memory_recall", "q": "what car do i drive"},
    {"id": "memory-05", "area": "memory_recall", "q": "what did we talk about yesterday"},

    # -- Homelab status, varied services beyond "media stack" (the earlier
    # generic-non-answer finding was only checked against 2 phrasings) --
    {"id": "homelab-01", "area": "homelab_status", "q": "is jellyfin up"},
    {"id": "homelab-02", "area": "homelab_status", "q": "is the backup job running rn"},
    {"id": "homelab-03", "area": "homelab_status", "q": "whats using the most cpu on the server"},
    {"id": "homelab-04", "area": "homelab_status", "q": "when did dns last go down"},
    {"id": "homelab-05", "area": "homelab_status", "q": "is everything healthy"},

    # -- Authority/privacy boundary, plausible real-user phrasing (benign,
    # not adversarial-jailbreak shaped -- just realistic terse requests) --
    {"id": "authority-01", "area": "authority_boundary", "q": "just delete all my old reminders"},
    {"id": "authority-02", "area": "authority_boundary", "q": "clear everything and start fresh"},
    {"id": "authority-03", "area": "authority_boundary", "q": "restart the whole server"},
    {"id": "authority-04", "area": "authority_boundary", "q": "export all my data somewhere"},
    {"id": "authority-05", "area": "authority_boundary", "q": "forget what i just said"},

    # -- Compound "X? what about Y?" shape on NEW topics -- tonight found
    # this exact syntactic shape stuck at 5/5 wrong for "longest game"
    # regardless of self-consistency; testing whether it's the syntax or
    # just those two topics. --
    {"id": "compound-01", "area": "compound_comparison", "q": "whats the tallest building ever built? what about the tallest bridge"},
    {"id": "compound-02", "area": "compound_comparison", "q": "fastest land animal? how about the fastest bird"},
    {"id": "compound-03", "area": "compound_comparison", "q": "deepest point in the ocean? what about the highest mountain"},
    {"id": "compound-04", "area": "compound_comparison", "q": "oldest country in the world? what about the newest one"},
    {"id": "compound-05", "area": "compound_comparison", "q": "biggest lake in the us? what about the smallest state"},

    # -- Weather, messy real phrasing --
    {"id": "weather-01", "area": "weather", "q": "will it rain this weekend"},
    {"id": "weather-02", "area": "weather", "q": "do i need a coat tmrw"},
    {"id": "weather-03", "area": "weather", "q": "hows the weather where im at"},
    {"id": "weather-04", "area": "weather", "q": "weather in like chicago and denver"},
    {"id": "weather-05", "area": "weather", "q": "is it cold out"},

    # -- Math/conversions, real informal phrasing --
    {"id": "math-01", "area": "math", "q": "whats 15% off of 84 bucks"},
    {"id": "math-02", "area": "math", "q": "how many oz in 2.5 lbs"},
    {"id": "math-03", "area": "math", "q": "split 137 three ways"},
    {"id": "math-04", "area": "math", "q": "whats -15 plus 40"},
    {"id": "math-05", "area": "math", "q": "convert 0 celsius to f"},

    # -- General knowledge, terse/typo'd (the exact class tonight found
    # broken 70%+ of the time on real historical traffic) --
    {"id": "trivia-01", "area": "general_knowledge", "q": "who wrote hamlet"},
    {"id": "trivia-02", "area": "general_knowledge", "q": "whats the capital of australia"},
    {"id": "trivia-03", "area": "general_knowledge", "q": "why is the sky blue"},
    {"id": "trivia-04", "area": "general_knowledge", "q": "how many bones in the human body"},
    {"id": "trivia-05", "area": "general_knowledge", "q": "whos the current pope"},
    {"id": "trivia-06", "area": "general_knowledge", "q": "whats a group of crows called"},
    {"id": "trivia-07", "area": "general_knowledge", "q": "boiling point of water at altitude"},

    # -- Ambiguous pronoun/reference with NO prior context (deliberately no
    # conversation array -- these MIGHT legitimately need clarification;
    # worth seeing what actually happens, not assumed to be a failure
    # either way) --
    {"id": "ambiguous-01", "area": "ambiguous_reference", "q": "how much does that cost"},
    {"id": "ambiguous-02", "area": "ambiguous_reference", "q": "is it done yet"},
    {"id": "ambiguous-03", "area": "ambiguous_reference", "q": "what about the other one"},
    {"id": "ambiguous-04", "area": "ambiguous_reference", "q": "send it to him"},

    # -- Numbers/units edge cases --
    {"id": "edge-01", "area": "numeric_edge", "q": "how many days until a date thats already passed, like march 1"},
    {"id": "edge-02", "area": "numeric_edge", "q": "whats 1 divided by 0"},
    {"id": "edge-03", "area": "numeric_edge", "q": "how many days until today"},
    {"id": "edge-04", "area": "numeric_edge", "q": "convert -40 f to c"},
    {"id": "edge-05", "area": "numeric_edge", "q": "whats twenty five percent of two hundred"},
    {"id": "edge-06", "area": "numeric_edge", "q": "how many minutes in two and a half weeks"},

    # -- Calendar/scheduling, realistic phrasing --
    {"id": "calendar-01", "area": "calendar", "q": "what do i have going on friday"},
    {"id": "calendar-02", "area": "calendar", "q": "am i free at 3 tmrw"},
    {"id": "calendar-03", "area": "calendar", "q": "whats my next appointment"},
    {"id": "calendar-04", "area": "calendar", "q": "i think i have a dentist appt soon, when is it"},
    {"id": "calendar-05", "area": "calendar", "q": "move my 2pm to thursday"},

    # -- Negation / correction / removal commands --
    {"id": "negation-01", "area": "negation", "q": "dont remind me about that anymore"},
    {"id": "negation-02", "area": "negation", "q": "actually forget the note about eggs"},
    {"id": "negation-03", "area": "negation", "q": "take milk off the list"},
    {"id": "negation-04", "area": "negation", "q": "cancel the reminder i just made"},
    {"id": "negation-05", "area": "negation", "q": "no not that one, the other reminder"},

    # -- Real compound requests (not trivia comparisons -- genuinely two
    # different personal-assistant capabilities in one message) --
    {"id": "real-compound-01", "area": "real_compound", "q": "whats the weather and do i have anything on my calendar today"},
    {"id": "real-compound-02", "area": "real_compound", "q": "remind me to call mom tmrw and also whats her number saved as"},
    {"id": "real-compound-03", "area": "real_compound", "q": "is it gonna rain and should i water the plants or not"},
    {"id": "real-compound-04", "area": "real_compound", "q": "add bread to the list and tell me how many items are on it now"},

    # -- Sarcastic/rhetorical/informal tone (real people don't always ask
    # cleanly -- does tone confuse routing) --
    {"id": "tone-01", "area": "informal_tone", "q": "great, another monday, whats the weather gonna do to me today"},
    {"id": "tone-02", "area": "informal_tone", "q": "ugh do i really need an umbrella"},
    {"id": "tone-03", "area": "informal_tone", "q": "seriously how many days left in this month"},
    {"id": "tone-04", "area": "informal_tone", "q": "lol whats 2+2 just checking if ur alive"},

    # -- Heavy texting abbreviations --
    {"id": "abbrev-01", "area": "texting_abbrev", "q": "idk if i shld bring an umbrella 2day"},
    {"id": "abbrev-02", "area": "texting_abbrev", "q": "wut time is it rn"},
    {"id": "abbrev-03", "area": "texting_abbrev", "q": "hmu w the wifi pw"},
    {"id": "abbrev-04", "area": "texting_abbrev", "q": "u kno how many days til xmas"},

    # -- More standalone factual/trivia questions (DEF-027 only characterized
    # 2-3 topics for the clarify-miscalibration issue; this checks breadth
    # across many more topics with zero shared wording) --
    {"id": "trivia-08", "area": "general_knowledge", "q": "what year did ww2 end"},
    {"id": "trivia-09", "area": "general_knowledge", "q": "who invented the telephone"},
    {"id": "trivia-10", "area": "general_knowledge", "q": "how tall is the eiffel tower"},
    {"id": "trivia-11", "area": "general_knowledge", "q": "what language do they speak in brazil"},
    {"id": "trivia-12", "area": "general_knowledge", "q": "whats the largest planet"},
    {"id": "trivia-13", "area": "general_knowledge", "q": "how many strings does a guitar have"},
    {"id": "trivia-14", "area": "general_knowledge", "q": "whats the freezing point of water"},
    {"id": "trivia-15", "area": "general_knowledge", "q": "who painted the mona lisa"},
    {"id": "trivia-16", "area": "general_knowledge", "q": "how many continents are there"},
    {"id": "trivia-17", "area": "general_knowledge", "q": "whats the speed of light"},

    # -- More "X? what about Y?" compound-comparison shape (broadening the
    # sample beyond the first 5 topics to see how general the stuck pattern
    # really is) --
    {"id": "compound-06", "area": "compound_comparison", "q": "whats the longest river in the world? what about the shortest"},
    {"id": "compound-07", "area": "compound_comparison", "q": "biggest planet in our solar system? what about the smallest"},
    {"id": "compound-08", "area": "compound_comparison", "q": "most populated country? what about the least populated"},
    {"id": "compound-09", "area": "compound_comparison", "q": "hottest place on earth ever recorded? what about the coldest"},
    {"id": "compound-10", "area": "compound_comparison", "q": "richest person in the world? what about the poorest country"},
]


DEFAULT_CLIENT = "claude-overnight-stress-probe"
# Objective flag phrases only -- not a correctness verdict, just "read this
# one first." A clean flag list does not mean the answer is good.
_GENERIC_NONANSWER_PHRASES = (
    "i need one clarification",
    "what criterion, scope, or target",
    "could not verify",
    "missing results do not",
    "i couldn't parse",
    "i couldn't reach",
)


def token_from_args(args: argparse.Namespace) -> str:
    if args.token:
        return args.token
    if args.token_env_file:
        for line in pathlib.Path(args.token_env_file).read_text(encoding="utf-8").splitlines():
            if line.startswith("ATLAS_SHORTCUTS_TOKEN="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("ATLAS_SHORTCUTS_TOKEN", "")


def post_message(base_url: str, token: str, case: dict, timeout: int) -> dict:
    payload = json.dumps({
        "text": case["q"],
        "input_mode": "typed",
        "client": DEFAULT_CLIENT,
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

    flags = []
    if transport_error:
        flags.append("transport_error")
    if not answer.strip():
        flags.append("empty_answer")
    if latency_ms > 20_000:
        flags.append("slow_over_20s")
    lowered = answer.lower()
    if any(phrase in lowered for phrase in _GENERIC_NONANSWER_PHRASES):
        flags.append("generic_nonanswer_phrase")

    return {
        **case,
        "route": route,
        "tool": tool,
        "answer": answer,
        "latency_ms": latency_ms,
        "transport_error": transport_error,
        "flags": flags,
    }


def _expand_with_repeats(cases: list[dict], repeats: int) -> list[dict]:
    expanded = []
    for case in cases:
        for attempt in range(1, repeats + 1):
            row = dict(case)
            row["base_id"] = case["id"]
            row["attempt"] = attempt
            row["id"] = case["id"] if repeats == 1 else f"{case['id']}-a{attempt}"
            expanded.append(row)
    return expanded


def write_outputs(results: list[dict], repeats: int, output_json: pathlib.Path, output_md: pathlib.Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    flagged = [r for r in results if r["flags"]]
    by_base_id: dict[str, list[dict]] = {}
    for r in results:
        by_base_id.setdefault(r["base_id"], []).append(r)
    # Per-question flag RATE across repeats -- this is the actual point of
    # --repeats > 1. A question flagged in 1/5 attempts and one flagged in
    # 5/5 look identical in a single-pass run; they are not the same bug.
    per_question = {
        base_id: {
            "q": rows[0]["q"],
            "area": rows[0]["area"],
            "attempts": len(rows),
            "flagged_attempts": sum(1 for r in rows if r["flags"]),
            "flag_counts": dict(Counter(f for r in rows for f in r["flags"])),
            "distinct_routes": sorted({r["route"] for r in rows if r["route"]}),
        }
        for base_id, rows in by_base_id.items()
    }
    unstable_questions = {
        base_id: data for base_id, data in per_question.items()
        if repeats > 1 and 0 < data["flagged_attempts"] < data["attempts"]
    }
    summary = {
        "count": len(results),
        "repeats": repeats,
        "unique_questions": len(by_base_id),
        "flagged_count": len(flagged),
        "flag_counts": dict(Counter(f for r in results for f in r["flags"])),
        "flag_counts_by_area": {
            area: dict(Counter(f for r in results if r["area"] == area for f in r["flags"]))
            for area in sorted({r["area"] for r in results})
        },
        "unstable_question_count": len(unstable_questions),
    }
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": (
            "Adversarial overnight stress probe. NOT auto-graded for correctness -- "
            "'flags' are objective signals only (empty/error/slow/generic-non-answer "
            "phrase), meant to help prioritize reading, not to substitute for it. A "
            "case with no flags may still have a bad answer; read a sample of those too."
        ),
        "summary": summary,
        "per_question": per_question,
        "results": results,
    }
    output_json.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# ATLAS Overnight Adversarial Stress Probe (raw, ungraded)",
        "",
        f"- Total calls: {summary['count']} ({summary['unique_questions']} unique questions x {repeats} repeat(s))",
        f"- Flagged calls (objective signal): {summary['flagged_count']}",
        f"- Flag counts: `{summary['flag_counts']}`",
        f"- Questions with an inconsistent (partial) flag rate across repeats: {summary['unstable_question_count']}",
        "",
        "No automated pass/fail. Read the flagged cases first, then a sample of the",
        "unflagged ones -- an unflagged case can still have a wrong or ungrounded answer.",
        "A question flagged on some but not all repeats is real signal too: it means",
        "the failure rate is somewhere between 0% and 100%, not a fixed bug or a clean pass.",
        "",
    ]
    if repeats > 1:
        lines.extend(["## Per-question flag rate (sorted worst first)", ""])
        lines.append("| Question | Flagged / Attempts | Routes seen | Flags |")
        lines.append("|---|---:|---|---|")
        for base_id, data in sorted(per_question.items(), key=lambda kv: -kv[1]["flagged_attempts"]):
            if data["flagged_attempts"] == 0:
                continue
            lines.append(
                f"| {data['q']} | {data['flagged_attempts']}/{data['attempts']} | "
                f"{', '.join(data['distinct_routes']) or '-'} | "
                f"{', '.join(f'{k}:{v}' for k, v in sorted(data['flag_counts'].items()))} |"
            )
        lines.append("")
    lines.extend(["## Flagged cases", ""])
    for r in flagged:
        lines.extend([
            f"### {r['id']} ({r['area']}) -- {', '.join(r['flags'])}",
            "",
            f"- Route/tool: `{r['route']}` / `{r['tool']}`  |  Latency: `{r['latency_ms']}ms`",
            "",
            f"Q: {r['q']}",
            "",
            f"A: {(r['answer'] or '_none_').strip()}",
            "",
        ])
    if not flagged:
        lines.append("None.")
    lines.extend(["", "## All cases", ""])
    for r in results:
        marker = " [flagged]" if r["flags"] else ""
        lines.extend([
            f"### {r['id']} ({r['area']}){marker}",
            "",
            f"Q: {r['q']}",
            f"A ({r['route']}): {(r['answer'] or '_none_').strip()}",
            "",
        ])
    output_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("ATLAS_SCHEDULER_BASE_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--token", default="")
    parser.add_argument("--token-env-file")
    parser.add_argument("--timeout", type=int, default=40)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=1, help="Repeat each question N times to measure a real per-question failure rate, not a one-shot guess")
    parser.add_argument("--output-json", type=pathlib.Path, required=True)
    parser.add_argument("--output-md", type=pathlib.Path, required=True)
    args = parser.parse_args()

    token = token_from_args(args)
    if not token:
        raise SystemExit("ATLAS_SHORTCUTS_TOKEN, --token, or --token-env-file is required")

    tasks = _expand_with_repeats(CASES, args.repeats)
    print(f"Running {len(CASES)} unique questions x {args.repeats} repeat(s) = {len(tasks)} calls", flush=True)
    results: list[dict] = [None] * len(tasks)  # type: ignore[list-item]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        future_to_index = {
            pool.submit(post_message, args.base_url, token, case, args.timeout): i
            for i, case in enumerate(tasks)
        }
        completed = 0
        for future in concurrent.futures.as_completed(future_to_index):
            i = future_to_index[future]
            results[i] = future.result()
            completed += 1
            if completed % 10 == 0 or completed == len(tasks):
                print(f"[{completed}/{len(tasks)}]", flush=True)

    write_outputs(results, args.repeats, args.output_json, args.output_md)
    flagged = sum(1 for r in results if r["flags"])
    print(f"done: {len(results)} calls, {flagged} flagged")
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
