#!/usr/bin/env python3

import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools" / "ai" / "run-atlas-readonly.py"


CASES = [
    {
        "question": "What is 15% of 240?",
        "route": "math",
        "answer": "36",
        "tool": "local-math",
    },
    {
        "question": "Split a $92 bill between 4 people",
        "route": "math",
        "answer": "23",
        "tool": "local-math",
    },
    {
        "question": "How many cups are in a quart?",
        "route": "conversion",
        "answer": "4 cups.",
        "tool": "local-conversion",
    },
    {
        "question": "How many teaspoons are in a tablespoon?",
        "route": "conversion",
        "answer": "3 teaspoons.",
        "tool": "local-conversion",
    },
    {
        "question": "Convert 100 dollars to euros",
        "route": "conversion",
        "answer": "About 92 euros using a static planning rate of 1 USD = 0.92 EUR.",
        "tool": "local-conversion",
    },
    {
        "question": "What time is it in London?",
        "route": "datetime",
        "tool": "local-clock",
        "timezone": "Europe/London",
    },
    {
        "question": "How many days until Christmas?",
        "route": "countdown",
        "tool": "local-calendar-math",
    },
]


def run_case(case: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--json", case["question"]],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def main() -> None:
    failures = []
    for case in CASES:
        payload = run_case(case)
        for key in ["route", "tool", "answer"]:
            if key in case and payload.get(key) != case[key]:
                failures.append(
                    f"{case['question']}: expected {key}={case[key]!r}, got {payload.get(key)!r}"
                )
        if "timezone" in case and (payload.get("result") or {}).get("timezone") != case["timezone"]:
            failures.append(
                f"{case['question']}: expected timezone={case['timezone']!r}, "
                f"got {(payload.get('result') or {}).get('timezone')!r}"
            )

    if failures:
        print("ATLAS deterministic smoke test failed:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)

    print(f"ATLAS deterministic smoke test passed: {len(CASES)} cases")


if __name__ == "__main__":
    main()
