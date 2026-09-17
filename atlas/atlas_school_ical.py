#!/usr/bin/env python3

"""Strict, metadata-only Canvas iCalendar parser for the ATLAS school bridge."""

from __future__ import annotations


MAX_ICAL_BYTES = 1_000_000
MAX_UNFOLDED_LINE = 8192
MAX_EVENTS = 500
REJECTED_PROPERTIES = {"ATTACH", "X-ALT-DESC"}
PROPERTY_MAP = {
    "UID": "event_id",
    "SUMMARY": "title",
    "DTSTART": "starts_at",
    "DTEND": "ends_at",
    "DUE": "due_at",
    "URL": "url",
    "STATUS": "status",
    "LAST-MODIFIED": "source_updated_at",
}


def _unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _unfold(text: str) -> list[str]:
    physical = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: list[str] = []
    for line in physical:
        if line.startswith((" ", "\t")):
            if not lines:
                raise ValueError("invalid folded iCalendar line")
            lines[-1] += line[1:]
        else:
            lines.append(line)
        if lines and len(lines[-1]) > MAX_UNFOLDED_LINE:
            raise ValueError("iCalendar line exceeds size limit")
    return lines


def parse_canvas_ical(data: bytes | str) -> list[dict]:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_ICAL_BYTES:
        raise ValueError("iCalendar input exceeds size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("iCalendar input must be UTF-8") from exc
    if "\x00" in text:
        raise ValueError("iCalendar input contains a null byte")

    lines = _unfold(text)
    if "BEGIN:VCALENDAR" not in lines or "END:VCALENDAR" not in lines:
        raise ValueError("missing VCALENDAR envelope")

    events: list[dict] = []
    current: dict | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise ValueError("nested VEVENT is not allowed")
            current = {}
            continue
        if line == "END:VEVENT":
            if current is None:
                raise ValueError("unexpected VEVENT end")
            if not current.get("event_id") or not current.get("title"):
                raise ValueError("VEVENT requires UID and SUMMARY")
            events.append(current)
            if len(events) > MAX_EVENTS:
                raise ValueError("iCalendar event count exceeds limit")
            current = None
            continue
        if current is None or ":" not in line:
            continue
        raw_name, raw_value = line.split(":", 1)
        name = raw_name.split(";", 1)[0].upper()
        if name in REJECTED_PROPERTIES:
            raise ValueError(f"iCalendar property {name} is not allowed")
        field = PROPERTY_MAP.get(name)
        if field:
            current[field] = _unescape(raw_value).strip()

    if current is not None:
        raise ValueError("unterminated VEVENT")
    if not events:
        raise ValueError("iCalendar input contains no events")
    return events
