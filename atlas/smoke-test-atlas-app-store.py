#!/usr/bin/env python3

import pathlib
import tempfile

import atlas_app_store


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = pathlib.Path(temp_dir) / "atlas-app.db"
        atlas_app_store.init_db(db_path)

        first = atlas_app_store.save_exchange(
            db_path,
            question="What is 15% of 240?",
            result={
                "answer": "36",
                "route": "math",
                "tool": "local-math",
                "source": "Built-in math",
            },
            input_mode="typed",
            client="smoke-test",
            latency_ms=12,
        )
        conversation_id = first["conversation_id"]

        second = atlas_app_store.save_exchange(
            db_path,
            question="How many cups are in a quart?",
            result={
                "answer": "4 cups.",
                "route": "conversion",
                "tool": "local-conversion",
                "source": "Built-in conversion",
            },
            conversation_id=conversation_id,
            input_mode="typed",
            client="smoke-test",
            latency_ms=10,
        )

        conversations = atlas_app_store.list_conversations(db_path)
        conversation = atlas_app_store.get_conversation(db_path, conversation_id)
        search_results = atlas_app_store.search_messages(db_path, "quart")
        recent_messages = atlas_app_store.get_recent_messages(db_path, conversation_id, limit=3)
        reminder = atlas_app_store.create_reminder(db_path, "check the backup status")
        reminders = atlas_app_store.list_reminders(db_path)
        operations_reminder = atlas_app_store.create_reminder(db_path, "check docker status", context_scope="operations")
        operations_reminders = atlas_app_store.list_reminders(db_path, context_scope="operations")
        personal_reminders = atlas_app_store.list_reminders(db_path, context_scope="personal")
        completed = atlas_app_store.complete_reminder(db_path, reminder["id"])
        note = atlas_app_store.create_note(db_path, "Router password location", "Router password is in the network binder.")
        notes = atlas_app_store.search_notes(db_path, "router")
        operations_note = atlas_app_store.create_note(db_path, "Open WebUI host", "Open WebUI runs on ai-stack.", context_scope="operations")
        operations_notes = atlas_app_store.search_notes(db_path, "Open WebUI", context_scope="operations")
        personal_notes = atlas_app_store.search_notes(db_path, "Open WebUI", context_scope="personal")
        task = atlas_app_store.create_task(db_path, "review atlas logs")
        tasks = atlas_app_store.list_tasks(db_path)
        operations_task = atlas_app_store.create_task(db_path, "review docker containers", context_scope="operations")
        operations_tasks = atlas_app_store.list_tasks(db_path, context_scope="operations")
        personal_tasks = atlas_app_store.list_tasks(db_path, context_scope="personal")
        completed_task = atlas_app_store.complete_task(db_path, task["id"])
        rule = atlas_app_store.create_rule(db_path, "backup fails", "notify me")
        due_rule = atlas_app_store.create_rule(db_path, "always", "rule notification test")
        rules = atlas_app_store.list_rules(db_path)
        due_rules = atlas_app_store.due_rules(db_path, "2999-01-01T00:00:00-08:00")
        # DEF-001: firing a stub 'always'/'now' rule must stop it from
        # reappearing in due_rules() on the next scheduler tick (previously
        # it re-fired forever), and the PWA's Rules panel must visibly show
        # it as disabled rather than silently doing nothing.
        atlas_app_store.mark_rule_fired(db_path, due_rule["id"], "2999-01-01T00:00:00-08:00")
        due_rules_after_fire = atlas_app_store.due_rules(db_path, "2999-01-01T00:05:00-08:00")
        fired_rule = next(item for item in atlas_app_store.list_rules(db_path) if item["id"] == due_rule["id"])
        # Re-enabling must clear last_fired_at so the rule becomes fireable
        # again -- otherwise a stale last_fired_at would silently keep
        # excluding it even though the owner explicitly turned it back on.
        re_enabled_rule = atlas_app_store.update_rule(db_path, due_rule["id"], enabled=True)
        due_rules_after_reenable = atlas_app_store.due_rules(db_path, "2999-01-01T00:10:00-08:00")
        audit = atlas_app_store.add_audit_event(db_path, "smoke", "store smoke event", {"ok": True})
        events = atlas_app_store.list_audit_events(db_path)
        due_reminder = atlas_app_store.create_reminder(db_path, "due test", due_at="2000-01-01T00:00:00-08:00")
        due = atlas_app_store.due_reminders(db_path, "2999-01-01T00:00:00-08:00")
        notification = atlas_app_store.create_notification(db_path, "Reminder", "check backups", source_type="reminder", source_id=reminder["id"])
        failed_once = atlas_app_store.mark_notification_failed(db_path, notification["id"], "temporary failure")
        failed_twice = atlas_app_store.mark_notification_failed(db_path, notification["id"], "second failure")
        notifications = atlas_app_store.list_notifications(db_path)
        confirmation = atlas_app_store.request_confirmation(db_path, "unlock", "unlock front door", "high")
        confirmations = atlas_app_store.list_confirmations(db_path)
        decided = atlas_app_store.decide_confirmation(db_path, confirmation["id"], "denied")
        preference = atlas_app_store.set_preference(db_path, "privacy", "prefer local/private options")
        preferences = atlas_app_store.list_preferences(db_path)
        snoozed = atlas_app_store.snooze_reminder(db_path, due_reminder["id"], "2999-01-01T00:15:00-08:00")

        assert second["conversation_id"] == conversation_id
        assert len(conversations) == 1
        assert conversations[0]["message_count"] == 4
        assert conversation is not None
        assert len(conversation["messages"]) == 4
        assert search_results
        assert search_results[0]["conversation_id"] == conversation_id
        assert len(recent_messages) == 3
        assert reminder["text"] == "check the backup status"
        assert reminder["context_scope"] == "personal"
        assert reminders and reminders[0]["id"] == reminder["id"]
        assert operations_reminders and operations_reminders[0]["id"] == operations_reminder["id"]
        assert all(item["context_scope"] == "operations" for item in operations_reminders)
        assert all(item["context_scope"] == "personal" for item in personal_reminders)
        assert completed and completed["completed_at"]
        assert note["title"] == "Router password location"
        assert note["context_scope"] == "personal"
        assert notes and notes[0]["id"] == note["id"]
        assert operations_notes and operations_notes[0]["id"] == operations_note["id"]
        assert not personal_notes
        assert tasks and tasks[0]["id"] == task["id"]
        assert task["context_scope"] == "personal"
        assert operations_tasks and operations_tasks[0]["id"] == operations_task["id"]
        assert all(item["context_scope"] == "operations" for item in operations_tasks)
        assert all(item["context_scope"] == "personal" for item in personal_tasks)
        assert completed_task and completed_task["completed_at"]
        assert rules and rules[0]["id"] == rule["id"]
        assert due_rules and due_rules[0]["id"] == due_rule["id"]
        assert not due_rules_after_fire, "a fired rule must not reappear in due_rules() on the next tick"
        assert fired_rule["enabled"] == 0, "firing a stub rule must auto-disable it"
        assert fired_rule["last_fired_at"] == "2999-01-01T00:00:00-08:00"
        assert re_enabled_rule["enabled"] == 1
        assert re_enabled_rule["last_fired_at"] is None, "re-enabling must clear last_fired_at so it can fire again"
        assert due_rules_after_reenable and due_rules_after_reenable[0]["id"] == due_rule["id"], (
            "an explicitly re-enabled rule must become fireable again"
        )

        # Track B1 (2026-09-08): structured (mini-grammar) rules are
        # edge-triggered, not one-shot-then-disable like the legacy stub
        # above -- a real condition can legitimately resolve and become true
        # again, so auto-disabling after the first firing would silently
        # stop a rule the owner still wants active.
        structured_rule = atlas_app_store.create_rule(db_path, "reminders_overdue_count > 0", "notify me of overdue reminders")
        false_routes = {"reminders_overdue_count": lambda: 0}
        true_routes = {"reminders_overdue_count": lambda: 1}
        not_yet_due = atlas_app_store.due_rules(db_path, "2999-02-01T00:00:00-08:00", signal_routes=false_routes)
        assert structured_rule["id"] not in [r["id"] for r in not_yet_due], "condition false -> not due"
        first_true = atlas_app_store.due_rules(db_path, "2999-02-01T00:01:00-08:00", signal_routes=true_routes)
        assert structured_rule["id"] in [r["id"] for r in first_true], "false->true transition must fire"
        atlas_app_store.mark_condition_rule_fired(db_path, structured_rule["id"], "2999-02-01T00:01:00-08:00")
        still_true_repeat = atlas_app_store.due_rules(db_path, "2999-02-01T00:02:00-08:00", signal_routes=true_routes)
        assert structured_rule["id"] not in [r["id"] for r in still_true_repeat], (
            "condition still true (no new transition) must NOT re-fire on the next tick"
        )
        after_still_true_row = next(r for r in atlas_app_store.list_rules(db_path) if r["id"] == structured_rule["id"])
        assert after_still_true_row["enabled"] == 1, (
            "mark_condition_rule_fired() must NOT auto-disable, unlike mark_rule_fired() for the legacy stub"
        )
        assert after_still_true_row["last_fired_at"] == "2999-02-01T00:01:00-08:00"
        gone_false = atlas_app_store.due_rules(db_path, "2999-02-01T00:03:00-08:00", signal_routes=false_routes)
        assert structured_rule["id"] not in [r["id"] for r in gone_false]
        second_true = atlas_app_store.due_rules(db_path, "2999-02-01T00:04:00-08:00", signal_routes=true_routes)
        assert structured_rule["id"] in [r["id"] for r in second_true], (
            "a second false->true transition must fire again -- this is the entire point of edge-triggering"
        )

        assert audit["event_type"] == "smoke"
        assert events and events[0]["id"] == audit["id"]
        assert due
        assert due[0]["id"] == due_reminder["id"]
        assert failed_once and failed_once["status"] == "queued"
        assert failed_twice and failed_twice["status"] == "dead_letter"
        assert notifications and notifications[0]["id"] == notification["id"]
        assert confirmations and confirmations[0]["id"] == confirmation["id"]
        assert decided and decided["status"] == "denied"
        assert preference["key"] == "privacy"
        assert preferences and preferences[0]["key"] == "privacy"
        assert snoozed and snoozed["due_at"] == "2999-01-01T00:15:00-08:00"

    print("ATLAS app store smoke test passed")


if __name__ == "__main__":
    main()
