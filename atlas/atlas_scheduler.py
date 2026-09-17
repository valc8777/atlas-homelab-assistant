#!/usr/bin/env python3

import argparse
import json
import os
import pathlib
import time
import urllib.request


DEFAULT_BASE_URL = os.environ.get("ATLAS_SCHEDULER_BASE_URL", "http://127.0.0.1:8787")
DEFAULT_TOKEN = os.environ.get("ATLAS_SHORTCUTS_TOKEN", "")
DEFAULT_HEARTBEAT_FILE = os.environ.get("ATLAS_SCHEDULER_HEARTBEAT_FILE", "/app/atlas-app/scheduler-heartbeat.json")


def post_json(base_url: str, token: str, path: str, timeout: int = 30) -> dict:
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        data=b"{}",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def run_once(base_url: str, token: str) -> dict:
    evaluation = post_json(base_url, token, "/api/v1/evaluate")
    briefing = post_json(base_url, token, "/api/v1/briefings/evaluate")
    delivery = post_json(base_url, token, "/api/v1/notifications/deliver")
    # Longer timeout: unlike the two calls above, this one can upload files and
    # trigger embedding generation in Open WebUI on the (infrequent) syncs where
    # memory content actually changed -- the common case (nothing changed) still
    # returns almost instantly via the fingerprint short-circuit.
    memory_sync = post_json(base_url, token, "/api/v1/memory/sync", timeout=120)
    # Executes any owner-approved fuzzy action confirmations (reminders/tasks/
    # shopping/notes) since the last tick. Not called from the ntfy Approve
    # button itself -- see _handle_execute_approved_confirmations's docstring
    # for why synchronous-on-approve was rejected.
    confirmation_execution = post_json(base_url, token, "/api/v1/confirmations/execute-approved")
    return {
        "ok": (
            bool(evaluation.get("ok"))
            and bool(briefing.get("ok"))
            and bool(delivery.get("ok"))
            and bool(memory_sync.get("ok"))
            and bool(confirmation_execution.get("ok"))
        ),
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "evaluation": evaluation,
        "briefing": briefing,
        "delivery": delivery,
        "memory_sync": memory_sync,
        "confirmation_execution": confirmation_execution,
    }


def write_heartbeat(path: str, payload: dict) -> None:
    heartbeat_path = pathlib.Path(path)
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--heartbeat-file", default=DEFAULT_HEARTBEAT_FILE)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("ATLAS_SHORTCUTS_TOKEN or --token is required")

    if args.once:
        result = run_once(args.base_url, args.token)
        write_heartbeat(args.heartbeat_file, result)
        print(json.dumps(result, indent=2))
        return

    while True:
        try:
            result = run_once(args.base_url, args.token)
            write_heartbeat(args.heartbeat_file, result)
            print(json.dumps(result), flush=True)
        except Exception as exc:
            result = {
                "ok": False,
                "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "error": str(exc),
            }
            write_heartbeat(args.heartbeat_file, result)
            print(json.dumps(result), flush=True)
        time.sleep(max(5, args.interval_seconds))


if __name__ == "__main__":
    main()
