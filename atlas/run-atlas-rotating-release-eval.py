#!/usr/bin/env python3

"""Execute a sealed ATLAS rotating-release evaluation suite against a live
atlas-shortcuts server and grade the results.

No such runner existed in this repo before -- historical cycles A-D were run
via ephemeral tooling outside this repository (see homelab-codex-handoff-
latest.md). This one is built to grade against the exact same case schema
those cycles already used (found by reading the sealed cycle JSON files and
the generator/importer scripts that produced them), so results are directly
comparable to the historical reports under ai-evals/rotating-release/runs/.

Each case is sent as a fresh, independent conversation (no conversation_id)
through the real /api/v1/messages endpoint -- the same path a real user
takes, including every deterministic route handler, the safety gate, and
the orchestrator canary. This is deliberately NOT a shortcut through
run-atlas-readonly.py directly, since many of the deterministic gates this
suite exercises (pre_route_safety_result, unsupported-device-action, etc.)
live only in atlas_shortcuts_server.py's HTTP layer.

Grading, inferred directly from the case schema (two shapes coexist in the
sealed files):
- Synthetic cases: {"scoring": {"any_of": [...], "forbid": [...]?,
  "minimum_length": N?, "numeric": {"value": V, "tolerance": T}?}}
- BIG-bench-derived cases: {"expect_any": [...], "forbid": [...]}
Both are normalized to the same internal check set. A case passes when:
- route matches "expected_route" exactly, if that key is present at all
  (privacy_refusal cases have no expected_route -- any route is acceptable,
  content is what's graded);
- at least one of any_of/expect_any appears in the answer (substring match
  after normalize_text(), which folds case, typographic Unicode punctuation,
  and whitespace on BOTH sides), if the list is non-empty;
- none of forbid appears in the answer, under the same normalization;
- the answer has at least minimum_length characters, if set (verified
  against historical results: this is a character count, not a word count --
  cross-checking against cycle A's actual results caught this directly,
  since a 36-character/6-word answer passed historically at
  minimum_length=8);
- a number within the numeric tolerance appears in the answer, if set.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request


def load_suite(path: pathlib.Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def task_for(item: dict) -> str:
    case_id = item["id"]
    match = re.search(r":bigbench:([a-z_]+):", case_id)
    if match:
        return match.group(1)
    return item["category"]


def normalize_scoring(item: dict) -> dict:
    scoring = item.get("scoring") or {}
    any_of = scoring.get("any_of") or item.get("expect_any") or []
    forbid = scoring.get("forbid") or item.get("forbid") or []
    return {
        "any_of": any_of,
        "forbid": forbid,
        "minimum_length": scoring.get("minimum_length", 0),
        "numeric": scoring.get("numeric"),
    }


# Typographic characters that model output commonly contains but which eval
# expectations are always written with in ASCII. Without folding these, a
# correct answer is scored as a miss purely on punctuation -- found 2026-09-06
# during the gpt-oss:20b trial, where 'Ursula K. Le Guin' was
# scored as missing "Ursula K. Le Guin" and 'I can’t provide that' was
# scored as missing "can't" (DEF-008 in ATLAS_CAPABILITY_ROADMAP.md is separate;
# this is DEF-007). NFKC already folds the space variants (U+00A0, U+202F,
# U+2009, U+2007) to a plain space; these are the ones it leaves alone.
_PUNCTUATION_EQUIVALENTS = {
    0x2018: "'",  # left single quote
    0x2019: "'",  # right single quote / apostrophe
    0x201C: '"',  # left double quote
    0x201D: '"',  # right double quote
    0x2010: "-",  # hyphen
    0x2011: "-",  # non-breaking hyphen
    0x2012: "-",  # figure dash
    0x2013: "-",  # en dash
    0x2014: "-",  # em dash
    0x2015: "-",  # horizontal bar
    0x2212: "-",  # minus sign
    0x2044: "/",  # fraction slash
}


def normalize_text(text: str | None) -> str:
    """Fold typographic Unicode and case so grading compares like with like.

    Applied to both the answer and every expectation, so the comparison stays
    symmetric. This is a presentation-layer fold only: it never changes which
    words are present, so it cannot turn a wrong answer into a passing one.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text)
    folded = folded.translate(_PUNCTUATION_EQUIVALENTS)
    return " ".join(folded.split()).casefold()


_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def numeric_ok(answer: str, target: float, tolerance: float) -> bool:
    # Normalize first so a U+2212 minus sign is recognized as a negative sign
    # and narrow no-break spaces inside numbers do not split them.
    for raw in _NUMBER_RE.findall(normalize_text(answer)):
        cleaned = raw.replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        allowed = max(abs(target) * tolerance, 1e-9)
        if abs(value - target) <= allowed:
            return True
    return False


def grade(item: dict, route: str | None, answer: str | None, transport_error: str | None) -> dict:
    if transport_error:
        return {
            "passed": False, "route_ok": False, "missing": [], "forbidden_hits": [],
            "length_ok": True, "numeric_ok": True, "transport_error": transport_error,
        }
    scoring = normalize_scoring(item)
    answer_text = answer or ""
    answer_normalized = normalize_text(answer_text)

    route_ok = True
    expected_route = item.get("expected_route")
    if expected_route is not None:
        route_ok = route == expected_route

    missing: list[str] = []
    if scoring["any_of"]:
        if not any(normalize_text(req) in answer_normalized for req in scoring["any_of"]):
            missing = list(scoring["any_of"])

    forbidden_hits = [
        term for term in scoring["forbid"] if normalize_text(term) in answer_normalized
    ]

    length_ok = True
    if scoring["minimum_length"]:
        length_ok = len(answer_text) >= scoring["minimum_length"]

    numeric_pass = True
    if scoring["numeric"]:
        numeric_pass = numeric_ok(answer_text, scoring["numeric"]["value"], scoring["numeric"]["tolerance"])

    passed = route_ok and not missing and not forbidden_hits and length_ok and numeric_pass
    return {
        "passed": passed, "route_ok": route_ok, "missing": missing, "forbidden_hits": forbidden_hits,
        "length_ok": length_ok, "numeric_ok": numeric_pass, "transport_error": None,
    }


def run_case(base_url: str, token: str, client: str, item: dict, timeout: int) -> dict:
    payload = json.dumps({
        "text": item["question"],
        "input_mode": "typed",
        "client": client,
        "context_scope": item.get("context_scope", "personal"),
    }).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/v1/messages",
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=payload,
    )
    route = None
    answer = None
    transport_error = None
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        assistant_message = body.get("assistant_message") or {}
        route = assistant_message.get("route")
        answer = assistant_message.get("text")
    except urllib.error.URLError as exc:
        transport_error = f"URLError: {exc}"
    except TimeoutError:
        transport_error = "timeout"
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        transport_error = f"{type(exc).__name__}: {exc}"
    latency_ms = round((time.monotonic() - started) * 1000)

    outcome = grade(item, route, answer, transport_error)
    return {
        "id": item["id"],
        "category": item["category"],
        "task": task_for(item),
        "question": item["question"],
        "route": route,
        "expected_route": item.get("expected_route"),
        "answer": answer,
        "latency_ms": latency_ms,
        **outcome,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--base-url", default=os.environ.get("ATLAS_SCHEDULER_BASE_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--token", default=os.environ.get("ATLAS_SHORTCUTS_TOKEN", ""))
    parser.add_argument("--client", default="atlas-rotating-release-eval")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("ATLAS_SHORTCUTS_TOKEN or --token is required")

    suite_path = pathlib.Path(args.suite)
    cases = load_suite(suite_path)
    total = len(cases)
    print(f"Loaded {total} cases from {suite_path.name}", file=sys.stderr, flush=True)

    results: list[dict] = [None] * total  # type: ignore[list-item]
    completed = 0
    started_at = time.monotonic()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        future_to_index = {
            pool.submit(run_case, args.base_url, args.token, args.client, item, args.timeout): index
            for index, item in enumerate(cases)
        }
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            results[index] = future.result()
            completed += 1
            if completed % args.progress_every == 0 or completed == total:
                elapsed = time.monotonic() - started_at
                rate = completed / elapsed if elapsed > 0 else 0
                remaining = (total - completed) / rate if rate > 0 else float("inf")
                print(
                    f"[{completed}/{total}] elapsed={elapsed:.0f}s "
                    f"est_remaining={remaining:.0f}s",
                    file=sys.stderr, flush=True,
                )

    passed = sum(1 for r in results if r["passed"])
    by_task: dict[str, list[int]] = {}
    by_category: dict[str, list[int]] = {}
    for r in results:
        by_task.setdefault(r["task"], [0, 0])
        by_task[r["task"]][1] += 1
        by_task[r["task"]][0] += int(r["passed"])
        by_category.setdefault(r["category"], [0, 0])
        by_category[r["category"]][1] += 1
        by_category[r["category"]][0] += int(r["passed"])

    transport_errors = [r for r in results if r["transport_error"]]

    output = {
        "suite_file": suite_path.name,
        "total": total,
        "passed": passed,
        "score": round(passed / total, 4) if total else 0.0,
        "transport_errors": len(transport_errors),
        "by_task": {k: {"passed": v[0], "total": v[1]} for k, v in sorted(by_task.items())},
        "by_category": {k: {"passed": v[0], "total": v[1]} for k, v in sorted(by_category.items())},
        "results": results,
    }

    output_json_path = pathlib.Path(args.output_json)
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    md_lines = [
        f"# ATLAS regression run — {suite_path.name}",
        "",
        f"- Passed: {passed} / {total}",
        f"- Transport errors: {len(transport_errors)}",
        f"- JSON: `{output_json_path}`",
        "",
        "## Results by task",
        "",
    ]
    for task, counts in sorted(output["by_task"].items()):
        md_lines.append(f"- `{task}`: {counts['passed']} / {counts['total']}")
    md_lines += ["", "## Results by category", ""]
    for category, counts in sorted(output["by_category"].items()):
        md_lines.append(f"- `{category}`: {counts['passed']} / {counts['total']}")
    md_lines += ["", "## Failures", ""]
    for r in results:
        if r["passed"]:
            continue
        md_lines.append(f"### {r['id']}")
        md_lines.append("")
        md_lines.append(f"- Question: {r['question']}")
        md_lines.append(f"- Route: `{r['route']}`")
        md_lines.append(f"- Expected route: `{r['expected_route']}`")
        md_lines.append(f"- Category: {r['category']}")
        md_lines.append(f"- Missing: {'missing any of: ' + ', '.join(r['missing']) if r['missing'] else 'none'}")
        md_lines.append(f"- Forbidden hits: {', '.join(r['forbidden_hits']) if r['forbidden_hits'] else 'none'}")
        if r["transport_error"]:
            md_lines.append(f"- Transport error: {r['transport_error']}")
        md_lines.append("")
        md_lines.append("Answer:")
        md_lines.append("")
        md_lines.append(str(r["answer"]))
        md_lines.append("")

    output_md_path = pathlib.Path(args.output_md)
    output_md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(json.dumps({"passed": passed, "total": total, "score": output["score"], "transport_errors": len(transport_errors)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
