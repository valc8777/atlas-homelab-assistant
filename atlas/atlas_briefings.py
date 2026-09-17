#!/usr/bin/env python3
"""Deterministic, config-free composition for ATLAS Track B3 briefings."""

from __future__ import annotations

from datetime import datetime, timedelta


def parse_schedule_time(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.strip().split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("briefing time must use 24-hour HH:MM format") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("briefing time must use 24-hour HH:MM format")
    return hour, minute


def is_due(now: datetime, schedule_time: str) -> bool:
    hour, minute = parse_schedule_time(schedule_time)
    return (now.hour, now.minute) >= (hour, minute)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


def _local_time(value: str | None, now: datetime) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "Time unavailable"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    local = parsed.astimezone(now.tzinfo)
    day = "Today" if local.date() == now.date() else "Tomorrow" if local.date() == (now + timedelta(days=1)).date() else local.strftime("%a %b %-d")
    return f"{day} {local.strftime('%-I:%M %p')}"


def compose_daily(
    now: datetime,
    *,
    events: list[dict],
    reminders: list[dict],
    confirmations: list[dict],
    failed_notifications: list[dict],
    unavailable: list[str] | None = None,
) -> str:
    """Compose factual owner-facing text without a model or inferred data."""
    sections: list[str] = [f"ATLAS midday briefing — {now.strftime('%A, %B %-d')}"]
    horizon = now + timedelta(hours=24)

    event_lines = []
    for event in sorted(events, key=lambda item: item.get("dtstart") or "")[:10]:
        summary = (event.get("summary") or "Untitled event").strip()
        location = (event.get("location") or "").strip()
        suffix = f" — {location}" if location else ""
        event_lines.append(f"• {_local_time(event.get('dtstart'), now)} — {summary}{suffix}")
    if event_lines:
        sections.append("Calendar (next 24 hours)\n" + "\n".join(event_lines))

    reminder_lines = []
    for reminder in reminders:
        due = _parse_datetime(reminder.get("due_at"))
        if due is None:
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=now.tzinfo)
        due = due.astimezone(now.tzinfo)
        if due <= horizon:
            label = "Overdue" if due < now else _local_time(reminder.get("due_at"), now)
            reminder_lines.append(f"• {label} — {reminder.get('text') or 'Untitled reminder'}")
    if reminder_lines:
        sections.append("Reminders\n" + "\n".join(reminder_lines[:10]))

    if confirmations:
        lines = [f"• {item.get('summary') or item.get('action_type') or 'Pending action'}" for item in confirmations[:10]]
        sections.append("Pending confirmations\n" + "\n".join(lines))

    if failed_notifications:
        lines = [f"• {item.get('title') or 'Notification'}" for item in failed_notifications[:10]]
        sections.append("Failed notifications\n" + "\n".join(lines))

    if len(sections) == 1:
        sections.append("Nothing needs your attention in the next 24 hours.")
    if unavailable:
        sections.append("Unavailable\n" + "\n".join(f"• {name}" for name in sorted(set(unavailable))))
    return "\n\n".join(sections)
