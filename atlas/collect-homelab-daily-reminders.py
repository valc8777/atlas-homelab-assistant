#!/usr/bin/env python3

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/Users/valdemort/Projects/homelab")
TZ = ZoneInfo("America/Los_Angeles")

BRIEFING_JSON = ROOT / "homelab-daily-briefing-latest.json"
OUT_MD = ROOT / "homelab-daily-reminders-latest.md"
OUT_JSON = ROOT / "homelab-daily-reminders-latest.json"
CALENDAR_EXPORT_SCRIPT = ROOT / "tools/ai/collect-homelab-calendar-export.py"


def main() -> None:
    stamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    briefing = json.loads(BRIEFING_JSON.read_text(encoding="utf-8"))

    attention_items = briefing.get("attention_items", [])
    followups = briefing.get("followups", [])
    next_actions = briefing.get("plan_next_actions", [])

    reminders: list[dict[str, str]] = []

    for item in attention_items:
        lowered = item.lower()
        if "crowdsec" in lowered:
            reminders.append(
                {
                    "title": "Investigate crowdsec restart loop",
                    "window": "today",
                    "reason": item,
                }
            )
        elif "backup failure" in lowered or "no recent successful vzdump" in lowered:
            reminders.append(
                {
                    "title": "Review Proxmox backup health",
                    "window": "today",
                    "reason": item,
                }
            )
        elif "windows" in lowered and "stopped" in lowered:
            reminders.append(
                {
                    "title": "Confirm Windows VM 202 should remain stopped",
                    "window": "today",
                    "reason": item,
                }
            )

    for action in next_actions[:3]:
        reminders.append(
            {
                "title": action.rstrip("."),
                "window": "this week",
                "reason": "Top plan next action",
            }
        )

    # Keep unique titles and stable ordering.
    deduped = []
    seen = set()
    for reminder in reminders:
        title = reminder["title"]
        if title not in seen:
            deduped.append(reminder)
            seen.add(title)

    payload = {
        "generated_at": stamp,
        "source_briefing": str(BRIEFING_JSON),
        "reminders": deduped,
        "followups": followups,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Homelab Daily Reminders",
        "",
        f"Generated: {stamp}",
        "",
        "Purpose: provide one machine-readable and human-readable reminder layer derived from the daily homelab briefing.",
        "",
        "## Reminder Candidates",
        "",
    ]
    if deduped:
        for idx, reminder in enumerate(deduped, start=1):
            lines.append(f"{idx}. `{reminder['window']}` - {reminder['title']}")
            lines.append(f"Reason: {reminder['reason']}")
    else:
        lines.append("1. No reminder candidates were derived from the current briefing.")

    lines.extend(
        [
            "",
            "## Follow-Up Context",
            "",
        ]
    )
    if followups:
        lines.extend(f"- {item}" for item in followups)
    else:
        lines.append("- No follow-up context was derived from the current briefing.")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    subprocess.run(
        [sys.executable, str(CALENDAR_EXPORT_SCRIPT)],
        check=True,
        cwd=ROOT,
    )


if __name__ == "__main__":
    main()
