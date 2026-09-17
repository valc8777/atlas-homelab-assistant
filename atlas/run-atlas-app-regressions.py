#!/usr/bin/env python3

import argparse
import json
import pathlib
import random
import re
import sys
import time
from datetime import datetime
import urllib.error
import urllib.request


SCRIPT_PATH = pathlib.Path(__file__).resolve()
ROOT = SCRIPT_PATH.parents[2] if len(SCRIPT_PATH.parents) > 2 else pathlib.Path.cwd()
DEFAULT_SUITE = ROOT / "ai-evals" / "atlas-app-simple-questions-2026-08-18.json"
DEFAULT_OUTPUT_DIR = ROOT / "ai-evals" / "runs"


def normalize(text: str) -> str:
    cleaned = re.sub(r"[`*_#]", "", text or "").lower()
    return re.sub(r"\s+", " ", cleaned).strip()


def semantic_failures(case: dict, answer: str, route: str | None) -> list[str]:
    scoring = case.get("scoring") or {}
    normalized = normalize(answer)
    failures: list[str] = []

    all_of = scoring.get("all_of", case.get("expect", []))
    any_of = scoring.get("any_of", case.get("expect_any", []))
    none_of = scoring.get("none_of", case.get("forbid", []))
    regex_any = scoring.get("regex_any", [])

    missing = [item for item in all_of if normalize(str(item)) not in normalized]
    failures.extend(f"missing: {item}" for item in missing)
    if any_of and not any(normalize(str(item)) in normalized for item in any_of):
        failures.append("missing any of: " + ", ".join(str(item) for item in any_of))
    if regex_any and not any(re.search(pattern, answer, flags=re.IGNORECASE) for pattern in regex_any):
        failures.append("missing regex match")
    for item in none_of:
        if normalize(str(item)) in normalized or normalize(str(item)) == normalize(str(route)):
            failures.append(f"forbidden: {item}")

    minimum_length = int(scoring.get("minimum_length", 0))
    if len(normalized) < minimum_length:
        failures.append(f"answer shorter than {minimum_length} characters")

    numeric = scoring.get("numeric")
    if numeric:
        matches = re.findall(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?", answer)
        values = [float(item.replace(",", "")) for item in matches]
        target = float(numeric["value"])
        tolerance = float(numeric.get("tolerance", 0))
        if not any(abs(value - target) <= tolerance for value in values):
            failures.append(f"missing numeric value {target}±{tolerance}")

    return failures


def request_json(base_url: str, token: str, payload: dict, attempts: int = 2, timeout: int = 180) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/v1/messages",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        data=data,
    )
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError) as exc:
            if attempt >= attempts:
                raise RuntimeError(f"ATLAS request failed after {attempts} attempts: {exc}") from exc
            time.sleep(attempt)
    raise RuntimeError("ATLAS request retry loop ended unexpectedly")


def load_suite(path: pathlib.Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def render_template(value, variables: dict[str, str]):
    if isinstance(value, str):
        return value.format(**variables)
    if isinstance(value, list):
        return [render_template(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: render_template(item, variables) for key, item in value.items()}
    return value


def render_case(case: dict, rng: random.Random, shared_variables: dict[str, dict[str, str]] | None = None) -> dict:
    variables = {}
    group = case.get("variable_group")
    reused_group = bool(group and shared_variables and group in shared_variables)
    if reused_group:
        variables.update(shared_variables[group])
    for name, values in (case.get("randomize") or {}).items():
        if reused_group or name in variables:
            continue
        if not values:
            raise ValueError(f"{case.get('id', 'unknown')} randomize.{name} is empty")
        choice = rng.choice(values)
        if isinstance(choice, dict):
            for key, value in choice.items():
                variables[key] = str(value)
        else:
            variables[name] = str(choice)

    if group and shared_variables is not None and group not in shared_variables:
        shared_variables[group] = dict(variables)

    rendered = render_template(
        {key: value for key, value in case.items() if key not in {"randomize", "variable_group"}},
        variables,
    )
    if variables:
        rendered["randomized_variables"] = variables
    return rendered


def write_report(output_dir: pathlib.Path, suite_path: pathlib.Path, results: list[dict], failures: list[dict], seed: int) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    stem = f"{suite_path.stem}-{timestamp}"
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    failed_ids = {failure.get("id") for failure in failures}
    by_task: dict[str, dict[str, int]] = {}
    by_category: dict[str, dict[str, int]] = {}
    for result in results:
        source = result.get("source") or {}
        task = str(source.get("task") or source.get("dataset") or "suite")
        counts = by_task.setdefault(task, {"passed": 0, "total": 0})
        counts["total"] += 1
        if result.get("id") not in failed_ids:
            counts["passed"] += 1
        category = str(result.get("category") or "uncategorized")
        category_counts = by_category.setdefault(category, {"passed": 0, "total": 0})
        category_counts["total"] += 1
        if result.get("id") not in failed_ids:
            category_counts["passed"] += 1

    summary = {
        "suite": str(suite_path),
        "generated_at": timestamp,
        "seed": seed,
        "passed": len(results) - len(failures),
        "total": len(results),
        "by_task": by_task,
        "by_category": by_category,
        "failures": failures,
        "results": results,
    }
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        f"# ATLAS regression run — {suite_path.name}",
        "",
        f"- Passed: {summary['passed']} / {summary['total']}",
        f"- Random seed: `{seed}`",
        f"- JSON: `{json_path}`",
        "",
    ]
    if by_task:
        lines.extend(["## Results by task", ""])
        for task, counts in sorted(by_task.items()):
            lines.append(f"- `{task}`: {counts['passed']} / {counts['total']}")
        lines.append("")
    if by_category:
        lines.extend(["## Results by category", ""])
        for category, counts in sorted(by_category.items()):
            lines.append(f"- `{category}`: {counts['passed']} / {counts['total']}")
        lines.append("")
    if failures:
        lines.extend(["## Failures", ""])
        for failure in failures:
            lines.extend(
                [
                    f"### {failure.get('id', 'unknown')}",
                    "",
                    f"- Question: {failure.get('question', '')}",
                    f"- Randomized variables: `{json.dumps(failure.get('randomized_variables') or {}, sort_keys=True)}`",
                    f"- Route: `{failure.get('route')}`",
                    f"- Expected route: `{failure.get('expected_route')}`",
                    f"- Category: `{failure.get('category') or 'uncategorized'}`",
                    f"- Missing: {', '.join(failure.get('missing') or []) or 'none'}",
                    f"- Forbidden hits: {', '.join(failure.get('forbidden_hits') or []) or 'none'}",
                    "",
                    "Answer:",
                    "",
                    failure.get("answer", ""),
                    "",
                ]
            )
    else:
        lines.extend(["## Failures", "", "None.", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="ATLAS base URL, for example https://ai-stack.example-tailnet.ts.net/atlas")
    token_group = parser.add_mutually_exclusive_group(required=True)
    token_group.add_argument("--token", help="ATLAS bearer token")
    token_group.add_argument("--token-env-file", help="Environment file containing ATLAS_SHORTCUTS_TOKEN")
    parser.add_argument("--suite", default=str(DEFAULT_SUITE))
    parser.add_argument("--client", default="atlas-regression-runner")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--seed", type=int, help="Optional random seed for reproducible randomized cases")
    parser.add_argument("--request-timeout", type=int, default=180, help="Seconds to wait for each ATLAS model response")
    parser.add_argument("--dry-run", action="store_true", help="Render randomized cases without calling ATLAS")
    args = parser.parse_args()

    token = args.token
    if args.token_env_file:
        for line in pathlib.Path(args.token_env_file).read_text(encoding="utf-8").splitlines():
            if line.startswith("ATLAS_SHORTCUTS_TOKEN="):
                token = line.split("=", 1)[1].strip()
                break
    if not token:
        raise SystemExit("ATLAS bearer token was not found")

    suite_path = pathlib.Path(args.suite)
    cases = load_suite(suite_path)
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(0, 2**63)
    rng = random.Random(seed)
    shared_variables: dict[str, dict[str, str]] = {}
    conversations: dict[str, str] = {}
    results: list[dict] = []
    failures: list[dict] = []

    if args.dry_run:
        print(json.dumps({"seed": seed, "suite": str(suite_path)}, ensure_ascii=False))

    for raw_case in cases:
        case = render_case(raw_case, rng, shared_variables)
        if args.dry_run:
            print(json.dumps({
                "id": case.get("id"),
                "question": case.get("question"),
                "expect": case.get("expect"),
                "expect_any": case.get("expect_any"),
                "forbid": case.get("forbid"),
                "expected_route": case.get("expected_route"),
                "category": case.get("category"),
                "scoring": case.get("scoring") or {},
                "randomized_variables": case.get("randomized_variables") or {},
            }, ensure_ascii=False))
            continue
        payload = {
            "text": case["question"],
            "input_mode": "typed",
            "client": args.client,
        }

        continue_from = case.get("continue_from")
        if continue_from:
            conversation_id = conversations.get(continue_from)
            if not conversation_id:
                failures.append(
                    {
                        "id": case["id"],
                        "error": f"missing conversation key: {continue_from}",
                    }
                )
                continue
            payload["conversation_id"] = conversation_id

        try:
            response = request_json(args.base_url, token, payload, timeout=args.request_timeout)
        except RuntimeError as exc:
            result = {
                "id": case["id"],
                "question": case["question"],
                "route": None,
                "expected_route": case.get("expected_route"),
                "missing": [],
                "forbidden_hits": [],
                "answer": "",
                "error": str(exc),
                "source": case.get("source") or {},
                "category": case.get("category"),
                "scoring": case.get("scoring") or {},
                "randomized_variables": case.get("randomized_variables") or {},
            }
            results.append(result)
            failures.append(result)
            print(f"FAIL {case['id']}")
            print(f"  transport: {exc}")
            continue
        assistant = response["assistant_message"]
        answer = assistant.get("text") or ""
        route = assistant.get("route")
        scoring_failures = semantic_failures(case, answer, route)
        missing = [item for item in scoring_failures if not item.startswith("forbidden: ")]
        forbidden_hits = [
            item.removeprefix("forbidden: ") for item in scoring_failures if item.startswith("forbidden: ")
        ]

        if case.get("conversation_key"):
            conversations[case["conversation_key"]] = response["conversation_id"]

        route_ok = True
        expected_route = case.get("expected_route")
        if expected_route:
            route_ok = route == expected_route

        result = {
            "id": case["id"],
            "question": case["question"],
            "route": route,
            "expected_route": expected_route,
            "missing": missing,
            "forbidden_hits": forbidden_hits,
            "answer": answer,
            "conversation_id": response.get("conversation_id"),
            "randomized_variables": case.get("randomized_variables") or {},
            "source": case.get("source") or {},
            "category": case.get("category"),
            "scoring": case.get("scoring") or {},
        }
        results.append(result)

        if missing or forbidden_hits or not route_ok:
            failures.append(result)
            print(f"FAIL {case['id']}")
            print(f"  route: {route}")
            if expected_route:
                print(f"  expected route: {expected_route}")
            if missing:
                print(f"  missing: {', '.join(missing)}")
            if forbidden_hits:
                print(f"  forbidden hits: {', '.join(forbidden_hits)}")
            print(f"  answer: {answer}")
        else:
            print(f"PASS {case['id']} route={route}")

    if args.dry_run:
        return 0

    print()
    print(f"Suite: {suite_path}")
    print(f"Seed: {seed}")
    print(f"Passed: {len(cases) - len(failures)} / {len(cases)}")
    report_paths = write_report(pathlib.Path(args.output_dir), suite_path, results, failures, seed)
    print(f"Markdown report: {report_paths['markdown']}")
    print(f"JSON report: {report_paths['json']}")

    if failures:
        print(json.dumps({"failures": failures}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
