#!/usr/bin/env python3

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/Users/valdemort/Projects/homelab")
TZ = ZoneInfo("America/Los_Angeles")

REMINDERS_JSON = ROOT / "homelab-daily-reminders-latest.json"
OUT_ICS = ROOT / "homelab-daily-reminders-latest.ics"
OUT_TASKS_JSON = ROOT / "homelab-daily-reminders-tasks-latest.json"
OUT_TASKS_MD = ROOT / "homelab-daily-reminders-tasks-latest.md"


def fmt_ics_local(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def sanitize_uid(title: str, window: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in title).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)
    return f"{window}-{slug or 'reminder'}@homelab.local"


def reminder_start(now: datetime, window: str, idx: int) -> datetime:
    base = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if window == "today":
        return base + timedelta(minutes=30 * idx)
    if window == "this week":
        return base + timedelta(days=min(idx, 4), hours=1)
    return base + timedelta(days=1, minutes=30 * idx)


def reminder_due_label(window: str) -> str:
    if window == "today":
        return "today"
    if window == "this week":
        return "this week"
    return "later"


def main() -> None:
    now = datetime.now(TZ)
    stamp = now.strftime("%Y-%m-%d %H:%M:%S %Z")
    payload = json.loads(REMINDERS_JSON.read_text(encoding="utf-8"))
    reminders = payload.get("reminders", [])

    events = []
    tasks = []
    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Homelab//Daily Reminders//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:Homelab Daily Reminders",
        f"X-WR-TIMEZONE:{TZ.key}",
    ]

    for idx, reminder in enumerate(reminders):
        title = reminder["title"]
        window = reminder["window"]
        reason = reminder["reason"]
        start = reminder_start(now, window, idx)
        end = start + timedelta(minutes=30)
        uid = sanitize_uid(title, window)

        ics_lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{fmt_ics_local(now)}",
                f"DTSTART;TZID={TZ.key}:{fmt_ics_local(start)}",
                f"DTEND;TZID={TZ.key}:{fmt_ics_local(end)}",
                f"SUMMARY:{title}",
                f"DESCRIPTION:Reason: {reason}\\nWindow: {window}",
                "STATUS:CONFIRMED",
                "END:VEVENT",
            ]
        )

        events.append(
            {
                "title": title,
                "window": window,
                "start_local": start.isoformat(),
                "end_local": end.isoformat(),
                "reason": reason,
                "uid": uid,
            }
        )

        tasks.append(
            {
                "title": title,
                "due_window": reminder_due_label(window),
                "reason": reason,
                "source": str(REMINDERS_JSON),
            }
        )

    ics_lines.append("END:VCALENDAR")
    OUT_ICS.write_text("\n".join(ics_lines) + "\n", encoding="utf-8")

    OUT_TASKS_JSON.write_text(
        json.dumps(
            {
                "generated_at": stamp,
                "source_reminders": str(REMINDERS_JSON),
                "calendar_events": events,
                "tasks": tasks,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    md_lines = [
        "# Homelab Daily Reminder Tasks",
        "",
        f"Generated: {stamp}",
        "",
        "Purpose: provide one export-friendly reminder/task layer for calendar or task-manager ingestion.",
        "",
        "## Tasks",
        "",
    ]
    if tasks:
        for idx, task in enumerate(tasks, start=1):
            md_lines.append(f"{idx}. `{task['due_window']}` - {task['title']}")
            md_lines.append(f"Reason: {task['reason']}")
    else:
        md_lines.append("1. No task candidates were derived from the current reminders.")

    md_lines.extend(
        [
            "",
            "## Export Files",
            "",
            f"- `{OUT_ICS.name}`",
            f"- `{OUT_TASKS_JSON.name}`",
        ]
    )
    OUT_TASKS_MD.write_text("\n".join(md_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
