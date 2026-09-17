#!/usr/bin/env python3

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/Users/valdemort/Projects/homelab")
TZ = ZoneInfo("America/Los_Angeles")

PLAN_PATH = ROOT / "homelab-plan-2026-06-08.md"

OUT_MD = ROOT / "homelab-daily-briefing-latest.md"
OUT_JSON = ROOT / "homelab-daily-briefing-latest.json"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def latest_dated_markdown(prefix: str) -> Path:
    matches = sorted(ROOT.glob(f"{prefix}-*.md"))
    if not matches:
        raise FileNotFoundError(f"No markdown files found for prefix: {prefix}")
    return matches[-1]


def section_body(text: str, heading: str) -> str:
    pattern = rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def bullet_lines(block: str) -> list[str]:
    return [line[2:].strip() for line in block.splitlines() if line.startswith("- ")]


def numbered_lines(block: str) -> list[str]:
    items = []
    for line in block.splitlines():
        match = re.match(r"^\d+\.\s+(.*)$", line.strip())
        if match:
            items.append(match.group(1).strip())
    return items


def parse_immediate_actions(plan_text: str) -> list[str]:
    return numbered_lines(section_body(plan_text, "Immediate Next Actions"))


def parse_notable_findings(runtime_text: str) -> list[str]:
    return bullet_lines(section_body(runtime_text, "Notable Findings"))


def parse_backup_summary(backup_text: str) -> dict[str, str]:
    summary: dict[str, str] = {}
    for line in backup_text.splitlines():
        if line.startswith("- Last successful `vzdump`:"):
            summary["last_successful_vzdump"] = line.split(":", 1)[1].strip()
        elif line.startswith("- Last failed `vzdump`:"):
            summary["last_failed_vzdump"] = line.split(":", 1)[1].strip()
    return summary


def parse_host_section_metrics(host_text: str, heading: str) -> dict[str, str]:
    block = section_body(host_text, heading)
    metrics: dict[str, str] = {}

    load_match = re.search(r"load average:\s*([0-9., ]+)", block)
    if load_match:
        metrics["load_average"] = load_match.group(1).strip()

    mem_match = re.search(r"^Mem:\s+(.+)$", block, flags=re.MULTILINE)
    if mem_match:
        metrics["memory_line"] = mem_match.group(1).strip()

    fs_match = re.search(r"^(/dev/\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+/)$", block, flags=re.MULTILINE)
    if fs_match:
        metrics["root_fs_line"] = fs_match.group(1).strip()

    gpu_match = re.search(r"^(NVIDIA .+)$", block, flags=re.MULTILINE)
    if gpu_match:
        metrics["gpu_line"] = gpu_match.group(1).strip()

    return metrics


def build_attention_items(findings: list[str], backup_summary: dict[str, str]) -> list[str]:
    items: list[str] = []

    for finding in findings:
        lowered = finding.lower()
        if "restarting" in lowered or "stopped" in lowered:
            items.append(finding)

    failed = backup_summary.get("last_failed_vzdump")
    successful = backup_summary.get("last_successful_vzdump")
    if failed and failed != "none seen in recent task window":
        items.append(f"Recent backup failure seen: {failed}")
    if successful == "none seen in recent task window":
        items.append("No recent successful vzdump seen in the current backup status window")

    return items


def build_followups(findings: list[str], backup_summary: dict[str, str], plan_actions: list[str]) -> list[str]:
    followups: list[str] = []

    if any("crowdsec" in finding.lower() and "restarting" in finding.lower() for finding in findings):
        followups.append("Inspect the crowdsec restart loop on docker-main before it becomes background noise.")

    failed = backup_summary.get("last_failed_vzdump")
    if failed and failed != "none seen in recent task window":
        followups.append("Review the most recent failed Proxmox backup task and confirm the next successful vzdump.")

    for action in plan_actions[:3]:
        followups.append(f"Plan says next: {action}")

    # Keep the briefing compact and stable.
    deduped: list[str] = []
    seen = set()
    for item in followups:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped[:5]


def main() -> None:
    stamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    runtime_path = latest_dated_markdown("homelab-runtime-status")
    host_health_path = latest_dated_markdown("homelab-host-health")
    backup_path = latest_dated_markdown("homelab-backup-status")

    plan_text = read_text(PLAN_PATH)
    runtime_text = read_text(runtime_path)
    host_text = read_text(host_health_path)
    backup_text = read_text(backup_path)

    plan_actions = parse_immediate_actions(plan_text)
    findings = parse_notable_findings(runtime_text)
    backup_summary = parse_backup_summary(backup_text)

    host_snapshots = {
        "docker-main": parse_host_section_metrics(host_text, "Docker-Main"),
        "ai-stack": parse_host_section_metrics(host_text, "AI-Stack"),
        "pve": parse_host_section_metrics(host_text, "Proxmox Host"),
    }

    attention_items = build_attention_items(findings, backup_summary)
    followups = build_followups(findings, backup_summary, plan_actions)

    payload = {
        "generated_at": stamp,
        "sources": {
            "plan": str(PLAN_PATH),
            "runtime_status": str(runtime_path),
            "host_health": str(host_health_path),
            "backup_status": str(backup_path),
        },
        "attention_items": attention_items,
        "plan_next_actions": plan_actions[:5],
        "host_snapshots": host_snapshots,
        "backup_summary": backup_summary,
        "followups": followups,
    }

    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Homelab Daily Briefing",
        "",
        f"Generated: {stamp}",
        "",
        "Purpose: provide one compact daily operations briefing for homelab review, AI grounding, and later schedule/task automation.",
        "",
        "## Today At A Glance",
        "",
    ]

    if attention_items:
        lines.extend(f"- {item}" for item in attention_items)
    else:
        lines.append("- No urgent attention items were derived from the current briefing inputs.")

    lines.extend(
        [
            "",
            "## Plan Next Actions",
            "",
        ]
    )
    if plan_actions:
        lines.extend(f"{idx}. {item}" for idx, item in enumerate(plan_actions[:5], start=1))
    else:
        lines.append("1. No immediate next actions were parsed from the current plan file.")

    lines.extend(
        [
            "",
            "## Host Snapshot",
            "",
            f"- `docker-main`: load `{host_snapshots['docker-main'].get('load_average', 'unknown')}`",
            f"- `ai-stack`: load `{host_snapshots['ai-stack'].get('load_average', 'unknown')}`",
            f"- `pve`: load `{host_snapshots['pve'].get('load_average', 'unknown')}`",
            "",
            "## Backup Summary",
            "",
            f"- Last successful `vzdump`: {backup_summary.get('last_successful_vzdump', 'unknown')}",
            f"- Last failed `vzdump`: {backup_summary.get('last_failed_vzdump', 'unknown')}",
            "",
            "## Suggested Follow-Ups",
            "",
        ]
    )
    if followups:
        lines.extend(f"- {item}" for item in followups)
    else:
        lines.append("- No follow-ups were derived from the current briefing inputs.")

    lines.extend(
        [
            "",
            "## Source Files",
            "",
            f"- `{PLAN_PATH.name}`",
            f"- `{runtime_path.name}`",
            f"- `{host_health_path.name}`",
            f"- `{backup_path.name}`",
        ]
    )

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
