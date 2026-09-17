#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
import time


def case_attempt_ok(case_result: dict | None) -> bool:
    return bool(case_result and case_result.get("ok"))


def log_progress(message: str):
    print(message, file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", required=True)
    parser.add_argument("--smoke-script", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--webui-db", default="/opt/stacks/ai-stack/webui_data/webui.db")
    parser.add_argument("--knowledge-name", default="Homelab Source of Truth")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--mode", choices=["chat", "retrieval", "both"], default="both")
    parser.add_argument("--function-calling")
    parser.add_argument("--chat-attempts", type=int, default=1)
    parser.add_argument("--retry-delay", type=float, default=1.5)
    parser.add_argument(
        "--case-repeats",
        type=int,
        default=1,
        help="Run each case this many times to measure stability.",
    )
    args = parser.parse_args()

    with open(args.suite, "r", encoding="utf-8") as handle:
        cases = json.load(handle)

    started = time.monotonic()
    results = []

    for case_index, case in enumerate(cases, start=1):
        log_progress(f"[{case_index}/{len(cases)}] {case['id']}")
        attempts = []
        for repeat_index in range(1, args.case_repeats + 1):
            cmd = [
                "python3",
                args.smoke_script,
                "--base-url",
                args.base_url,
                "--webui-db",
                args.webui_db,
                "--knowledge-name",
                args.knowledge_name,
                "--model",
                args.model,
                "--timeout",
                str(args.timeout),
                "--mode",
                args.mode,
                "--question",
                case["question"],
                "--chat-attempts",
                str(args.chat_attempts),
                "--retry-delay",
                str(args.retry_delay),
            ]
            for needle in case.get("expect", []):
                cmd.extend(["--expect", needle])
            if args.function_calling:
                cmd.extend(["--function-calling", args.function_calling])

            log_progress(f"  repeat {repeat_index}/{args.case_repeats}")
            proc = subprocess.run(cmd, capture_output=True, text=True)
            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()
            parsed = None
            try:
                parsed = json.loads(stdout) if stdout else None
            except json.JSONDecodeError:
                parsed = None

            attempts.append(
                {
                    "ok": proc.returncode == 0,
                    "returncode": proc.returncode,
                    "result": parsed,
                    "stderr": stderr,
                    "stdout": stdout if parsed is None else None,
                }
            )

        ok_attempts = sum(1 for attempt in attempts if case_attempt_ok(attempt))
        log_progress(
            f"  result: {ok_attempts}/{args.case_repeats} passes"
        )
        results.append(
            {
                "id": case["id"],
                "question": case["question"],
                "expect": case.get("expect", []),
                "ok": ok_attempts == args.case_repeats,
                "ok_attempts": ok_attempts,
                "case_repeats": args.case_repeats,
                "stability": round(ok_attempts / args.case_repeats, 3),
                "attempts": attempts,
                "result": attempts[-1]["result"] if attempts else None,
                "stderr": attempts[-1]["stderr"] if attempts else "",
                "stdout": attempts[-1]["stdout"] if attempts else None,
                "returncode": attempts[-1]["returncode"] if attempts else None,
            }
        )

    passed = sum(1 for item in results if item["ok"])
    failed = len(results) - passed
    elapsed = round(time.monotonic() - started, 3)

    summary = {
        "suite": args.suite,
        "model": args.model,
        "mode": args.mode,
        "function_calling": args.function_calling or "default",
        "chat_attempts": args.chat_attempts,
        "retry_delay": args.retry_delay,
        "case_repeats": args.case_repeats,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "elapsed_seconds": elapsed,
        "results": results,
    }
    print(json.dumps(summary, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
