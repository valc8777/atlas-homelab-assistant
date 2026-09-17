#!/usr/bin/env python3

from __future__ import annotations

import pathlib
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

import atlas_app_store
import atlas_briefings


def main() -> None:
    tz = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 8, 12, 0, tzinfo=tz)
    assert not atlas_briefings.is_due(now.replace(hour=11, minute=59), "12:00")
    assert atlas_briefings.is_due(now, "12:00")
    try:
        atlas_briefings.parse_schedule_time("25:00")
        raise AssertionError("invalid schedule accepted")
    except ValueError:
        pass

    body = atlas_briefings.compose_daily(
        now,
        events=[{"summary": "Dentist", "dtstart": "2026-09-08T14:00:00-07:00", "location": "Downtown", "description": "must not leak"}],
        reminders=[
            {"text": "Submit form", "due_at": "2026-09-08T10:00:00-07:00"},
            {"text": "Future item", "due_at": "2026-09-10T12:00:00-07:00"},
        ],
        confirmations=[{"summary": "Turn off server"}],
        failed_notifications=[{"title": "Reminder"}],
        unavailable=["Calendar sync metadata"],
    )
    assert "Dentist" in body and "Downtown" in body
    assert "must not leak" not in body
    assert "Submit form" in body and "Overdue" in body
    assert "Future item" not in body
    assert "Turn off server" in body and "Failed notifications" in body
    assert "Calendar sync metadata" in body

    empty = atlas_briefings.compose_daily(now, events=[], reminders=[], confirmations=[], failed_notifications=[])
    assert "Nothing needs your attention" in empty

    with tempfile.TemporaryDirectory() as temp_dir:
        db = pathlib.Path(temp_dir) / "atlas-app.db"
        atlas_app_store.init_db(db)
        first = atlas_app_store.create_briefing_notification_once(db, "daily", "2026-09-08", "Briefing", body)
        second = atlas_app_store.create_briefing_notification_once(db, "daily", "2026-09-08", "Briefing", "duplicate")
        assert first is not None and second is None
        run = atlas_app_store.get_briefing_run(db, "daily", "2026-09-08")
        notifications = atlas_app_store.list_notifications(db)
        assert run and run["notification_id"] == first["notification_id"]
        assert len(notifications) == 1
        assert notifications[0]["source_type"] == "briefing"
        assert notifications[0]["body"] == body

    print("ATLAS briefing tests passed")


if __name__ == "__main__":
    main()
