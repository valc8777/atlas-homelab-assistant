#!/usr/bin/env python3

import json
import re
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = "/Users/valdemort/Projects/homelab"
OUT_MD = f"{ROOT}/homelab-backup-status-2026-08-06.md"
OUT_JSON = f"{ROOT}/homelab-backup-status-2026-08-06.json"
TZ = ZoneInfo("America/Los_Angeles")
ERROR_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\berror\b",
        r"\bfailed\b",
        r"\bno space left\b",
        r"\btimeout\b",
        r"\bpermission denied\b",
        r"\bdenied\b",
        r"\bunable to\b",
    ]
]


def run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True)


def ts(epoch: int | None) -> str:
    if not epoch:
        return "unknown"
    return datetime.fromtimestamp(epoch, TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


def run_ssh(remote_cmd: str) -> str:
    return run(["ssh", "root@100.70.171.62", remote_cmd])


def extract_failure_line(log_entries: list[dict]) -> str | None:
    texts = [str(entry.get("t", "")).strip() for entry in log_entries if entry.get("t")]
    for text in texts:
        if any(pattern.search(text) for pattern in ERROR_PATTERNS):
            return text
    return texts[-1] if texts else None


def fetch_task_log_summary(task: dict) -> dict | None:
    if task.get("status") == "OK":
        return None
    node = task.get("node")
    upid = task.get("upid")
    if not node or not upid:
        return {"error": "missing node or upid"}

    try:
        # The Proxmox API defaults to a short first page. Concrete failures can
        # appear after guest setup and transfer output, so request a bounded
        # page large enough to include the task tail.
        raw = run_ssh(
            f"pvesh get /nodes/{node}/tasks/{upid}/log --start 0 --limit 500 --output-format json"
        )
        log_entries = json.loads(raw)
    except subprocess.CalledProcessError as exc:
        return {"error": f"ssh command failed with exit code {exc.returncode}"}
    except json.JSONDecodeError:
        return {"error": "task log was not valid json"}

    first_failure = extract_failure_line(log_entries)
    tail = [str(entry.get("t", "")).strip() for entry in log_entries[-3:] if entry.get("t")]
    return {
        "first_failure_line": first_failure,
        "tail": tail,
        "line_count": len(log_entries),
    }


def main():
    stamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    raw = run_ssh(
        "pvesh get /nodes/pve/tasks --typefilter vzdump --limit 500 --output-format json"
    )
    tasks = json.loads(raw)
    vzdump = sorted(
        [t for t in tasks if t.get("type") == "vzdump"],
        key=lambda t: t.get("starttime", 0),
        reverse=True,
    )
    recent = vzdump[:10]
    for task in recent:
        task["log_summary"] = fetch_task_log_summary(task)

    # Summaries search the full task history returned by Proxmox rather than
    # only the compact ten-row display window.
    last_ok = next((t for t in vzdump if t.get("status") == "OK"), None)
    last_fail = next((t for t in vzdump if t.get("status") != "OK"), None)

    payload = {
        "generated_at": stamp,
        "recent_vzdump_tasks": recent,
        "last_ok": last_ok,
        "last_failure": last_fail,
    }

    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")

    lines = [
        "# Homelab Backup Status",
        "",
        f"Generated: {stamp}",
        "",
        "Purpose: provide a live backup-task summary from Proxmox recent task history.",
        "",
        f"- Last successful `vzdump`: {ts(last_ok.get('starttime')) if last_ok else 'none seen in recent task window'}",
        f"- Last failed `vzdump`: {ts(last_fail.get('starttime')) if last_fail else 'none seen in recent task window'}",
        "",
        "## Recent Vzdump Tasks",
        "",
        "| Start | End | Status | Node | User | First Failure | UPID |",
        "|---|---|---|---|---|---|---|",
    ]
    for task in recent:
        first_failure = ""
        if isinstance(task.get("log_summary"), dict):
            first_failure = task["log_summary"].get("first_failure_line") or task["log_summary"].get("error") or ""
        first_failure = first_failure.replace("|", "/")
        lines.append(
            f"| {ts(task.get('starttime'))} | {ts(task.get('endtime'))} | {task.get('status')} | "
            f"{task.get('node')} | {task.get('user')} | {first_failure} | `{task.get('upid')}` |"
        )

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
