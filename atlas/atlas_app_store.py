#!/usr/bin/env python3

import os
import pathlib
import sqlite3
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import atlas_comic_bot
import atlas_orchestrator_v2
import atlas_signals

COMIC_BOT_TRIGGER_URL = os.environ.get("ATLAS_COMIC_BOT_TRIGGER_URL", "")
COMIC_BOT_TRIGGER_TOKEN = os.environ.get("ATLAS_COMIC_BOT_TRIGGER_TOKEN", "")


LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def now_iso() -> str:
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


def connect(db_path: pathlib.Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


def init_db(db_path: pathlib.Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            create table if not exists conversations (
              id text primary key,
              title text not null,
              context_scope text not null default 'personal',
              created_at text not null,
              updated_at text not null,
              archived_at text
            );

            create table if not exists messages (
              id text primary key,
              conversation_id text not null references conversations(id),
              role text not null check (role in ('user', 'assistant', 'system')),
              text text not null,
              speak_text text,
              route text,
              tool text,
              source text,
              input_mode text,
              client text,
              context_scope text not null default 'personal',
              latency_ms integer,
              error text,
              created_at text not null
            );

            create index if not exists idx_messages_conversation_created
              on messages(conversation_id, created_at);

            create index if not exists idx_conversations_updated
              on conversations(updated_at desc);

            create table if not exists reminders (
              id text primary key,
              text text not null,
              due_at text,
              recurrence text,
              context_scope text not null default 'personal',
              source_message_id text references messages(id),
              completed_at text,
              notified_at text,
              created_at text not null
            );

            create index if not exists idx_reminders_due
              on reminders(completed_at, due_at, created_at);

            create table if not exists notes (
              id text primary key,
              title text not null,
              text text not null,
              context_scope text not null default 'personal',
              source_message_id text references messages(id),
              source_type text,
              import_batch_id text,
              import_confidence real,
              review_status text,
              expires_at text,
              fact_lifecycle text,
              created_at text not null,
              updated_at text not null,
              archived_at text
            );

            create index if not exists idx_notes_updated
              on notes(updated_at desc);

            create table if not exists tasks (
              id text primary key,
              text text not null,
              priority text,
              context_scope text not null default 'personal',
              source_message_id text references messages(id),
              completed_at text,
              created_at text not null
            );

            create index if not exists idx_tasks_open
              on tasks(completed_at, created_at);

            create table if not exists shopping_items (
              id text primary key,
              text text not null,
              quantity text,
              store text,
              context_scope text not null default 'personal',
              source_message_id text references messages(id),
              completed_at text,
              created_at text not null
            );

            create index if not exists idx_shopping_items_open
              on shopping_items(completed_at, created_at);

            create table if not exists rules (
              id text primary key,
              condition_text text not null,
              action_text text not null,
              enabled integer not null default 1,
              created_at text not null,
              last_fired_at text,
              last_condition_state text
            );

            create table if not exists audit_events (
              id text primary key,
              event_type text not null,
              summary text not null,
              details_json text,
              created_at text not null
            );

            create table if not exists orchestration_shadow_traces (
              id text primary key,
              conversation_id text references conversations(id) on delete set null,
              user_message_id text references messages(id) on delete set null,
              planner_version text not null,
              model text,
              decision_source text,
              context_scope text not null,
              question_sha256 text not null,
              question_chars integer not null,
              decision_mode text,
              decision_freshness text,
              decision_risk text,
              decision_scope text,
              capabilities_json text not null default '[]',
              needs_verification integer,
              reason_code text,
              confidence real,
              current_route text,
              current_tool text,
              planner_latency_ms integer,
              status text not null,
              error_type text,
              created_at text not null
            );

            create index if not exists idx_orchestration_shadow_created
              on orchestration_shadow_traces(created_at desc);

            create index if not exists idx_orchestration_shadow_status
              on orchestration_shadow_traces(status, created_at desc);

            create table if not exists notifications (
              id text primary key,
              channel text not null,
              title text not null,
              body text not null,
              status text not null,
              source_type text,
              source_id text,
              created_at text not null,
              delivered_at text,
              attempt_count integer not null default 0,
              error text
            );

            create index if not exists idx_notifications_status
              on notifications(status, created_at);

            create table if not exists briefing_runs (
              id text primary key,
              briefing_kind text not null,
              scheduled_for text not null,
              status text not null,
              notification_id text references notifications(id),
              body text not null,
              created_at text not null,
              unique(briefing_kind, scheduled_for)
            );

            create table if not exists signal_observations (
              signal_name text primary key,
              value_json text,
              status text not null,
              observed_at text not null,
              error text
            );

            create table if not exists confirmations (
              id text primary key,
              action_type text not null,
              summary text not null,
              risk_level text not null,
              status text not null,
              requested_at text not null,
              decided_at text,
              details_json text
            );

            create table if not exists preferences (
              key text primary key,
              value text not null,
              updated_at text not null
            );

            create table if not exists preference_history (
              id text primary key,
              preference_key text not null,
              old_value text not null,
              new_value text not null,
              status text not null check (status in ('superseded', 'rolled_back')),
              source text not null,
              context_scope text not null default 'personal',
              confirmation_id text references confirmations(id),
              created_at text not null,
              rolled_back_at text
            );

            create index if not exists idx_preference_history_key_created
              on preference_history(preference_key, created_at desc);
            """
        )
        for statement in [
            "alter table conversations add column context_scope text not null default 'personal'",
            "alter table messages add column context_scope text not null default 'personal'",
            "alter table reminders add column recurrence text",
            "alter table reminders add column notified_at text",
            "alter table reminders add column context_scope text not null default 'personal'",
            "alter table notes add column context_scope text not null default 'personal'",
            "alter table notes add column source_type text",
            "alter table notes add column import_batch_id text",
            "alter table notes add column import_confidence real",
            "alter table notes add column review_status text",
            "alter table notes add column expires_at text",
            "alter table notes add column fact_lifecycle text",
            "alter table tasks add column context_scope text not null default 'personal'",
            "alter table shopping_items add column quantity text",
            "alter table shopping_items add column store text",
            "alter table shopping_items add column context_scope text not null default 'personal'",
            "alter table notifications add column attempt_count integer not null default 0",
            "alter table orchestration_shadow_traces add column decision_source text",
            "alter table confirmations add column execution_status text not null default 'not_applicable'",
            "alter table confirmations add column executed_at text",
            "alter table confirmations add column execution_error text",
            "alter table confirmations add column execution_result_json text",
            # DEF-001: fired-state tracking so a matching rule stops re-firing
            # on every scheduler tick (previously unconditional re-fire every
            # 60s -- see due_rules()/mark_rule_fired() below).
            "alter table rules add column last_fired_at text",
            # Track B1 (2026-09-08): stores the last-evaluated boolean for a
            # structured (mini-grammar) rule condition, so due_rules() can
            # detect a false->true transition rather than firing on every
            # tick while the condition holds. Not used by legacy literal
            # 'always'/'now' rules -- see evaluate_condition() in
            # atlas_signals.py and due_rules() below.
            "alter table rules add column last_condition_state text",
        ]:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass
        try:
            conn.executescript(
                """
                create virtual table if not exists messages_fts using fts5(
                  text,
                  content='messages',
                  content_rowid='rowid'
                );

                create trigger if not exists messages_ai after insert on messages begin
                  insert into messages_fts(rowid, text) values (new.rowid, new.text);
                end;

                create trigger if not exists messages_ad after delete on messages begin
                  insert into messages_fts(messages_fts, rowid, text)
                  values('delete', old.rowid, old.text);
                end;

                create trigger if not exists messages_au after update on messages begin
                  insert into messages_fts(messages_fts, rowid, text)
                  values('delete', old.rowid, old.text);
                  insert into messages_fts(rowid, text) values (new.rowid, new.text);
                end;

                create virtual table if not exists notes_fts using fts5(
                  title,
                  text,
                  content='notes',
                  content_rowid='rowid'
                );

                create trigger if not exists notes_ai after insert on notes begin
                  insert into notes_fts(rowid, title, text) values (new.rowid, new.title, new.text);
                end;

                create trigger if not exists notes_ad after delete on notes begin
                  insert into notes_fts(notes_fts, rowid, title, text)
                  values('delete', old.rowid, old.title, old.text);
                end;

                create trigger if not exists notes_au after update on notes begin
                  insert into notes_fts(notes_fts, rowid, title, text)
                  values('delete', old.rowid, old.title, old.text);
                  insert into notes_fts(rowid, title, text) values (new.rowid, new.title, new.text);
                end;
                """
            )
            conn.execute("insert into messages_fts(messages_fts) values('rebuild')")
            conn.execute("insert into notes_fts(notes_fts) values('rebuild')")
        except sqlite3.OperationalError:
            pass


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def title_from_text(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= 60:
        return collapsed or "New Conversation"
    return f"{collapsed[:57]}..."


def ensure_conversation(
    conn: sqlite3.Connection,
    conversation_id: str | None,
    first_text: str,
    context_scope: str = "personal",
) -> str:
    if conversation_id:
        row = conn.execute(
            "select id, context_scope from conversations where id = ? and archived_at is null",
            (conversation_id,),
        ).fetchone()
        if not row:
            raise ValueError("conversation not found")
        if row["context_scope"] != context_scope:
            raise ValueError(f"conversation scope mismatch: expected {row['context_scope']}, got {context_scope}")
        return conversation_id

    created_at = now_iso()
    new_conversation_id = new_id()
    conn.execute(
        """
        insert into conversations(id, title, context_scope, created_at, updated_at)
        values (?, ?, ?, ?, ?)
        """,
        (new_conversation_id, title_from_text(first_text), context_scope, created_at, created_at),
    )
    return new_conversation_id


def insert_message(
    conn: sqlite3.Connection,
    conversation_id: str,
    role: str,
    text: str,
    speak_text: str | None = None,
    route: str | None = None,
    tool: str | None = None,
    source: str | None = None,
    input_mode: str | None = None,
    client: str | None = None,
    context_scope: str = "personal",
    latency_ms: int | None = None,
    error: str | None = None,
) -> dict:
    created_at = now_iso()
    message_id = new_id()
    conn.execute(
        """
        insert into messages(
          id,
          conversation_id,
          role,
          text,
          speak_text,
          route,
          tool,
          source,
          input_mode,
          client,
          context_scope,
          latency_ms,
          error,
          created_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            conversation_id,
            role,
            text,
            speak_text,
            route,
            tool,
            source,
            input_mode,
            client,
            context_scope,
            latency_ms,
            error,
            created_at,
        ),
    )
    conn.execute(
        "update conversations set updated_at = ? where id = ?",
        (created_at, conversation_id),
    )
    return {
        "id": message_id,
        "conversation_id": conversation_id,
        "role": role,
        "text": text,
        "speak_text": speak_text,
        "route": route,
        "tool": tool,
        "source": source,
        "input_mode": input_mode,
        "client": client,
        "context_scope": context_scope,
        "latency_ms": latency_ms,
        "error": error,
        "created_at": created_at,
    }


def save_exchange(
    db_path: pathlib.Path,
    question: str,
    result: dict,
    conversation_id: str | None = None,
    input_mode: str | None = None,
    client: str | None = None,
    context_scope: str = "personal",
    latency_ms: int | None = None,
) -> dict:
    with connect(db_path) as conn:
        conversation_id = ensure_conversation(conn, conversation_id, question, context_scope)
        user_message = insert_message(
            conn,
            conversation_id=conversation_id,
            role="user",
            text=question,
            input_mode=input_mode,
            client=client,
            context_scope=context_scope,
        )
        assistant_message = insert_message(
            conn,
            conversation_id=conversation_id,
            role="assistant",
            text=result.get("answer", ""),
            speak_text=result.get("answer", ""),
            route=result.get("route"),
            tool=result.get("tool"),
            source=result.get("source"),
            client="atlas",
            context_scope=context_scope,
            latency_ms=latency_ms,
        )
        conn.commit()
    return {
        "conversation_id": conversation_id,
        "user_message": user_message,
        "assistant_message": assistant_message,
    }


def list_conversations(
    db_path: pathlib.Path,
    limit: int = 50,
    offset: int = 0,
    context_scope: str | None = None,
) -> list[dict]:
    scope_filter = "and c.context_scope = ?" if context_scope else ""
    params = [limit, offset]
    if context_scope:
        params = [context_scope, limit, offset]
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            select
              c.id,
              c.title,
              c.context_scope,
              c.created_at,
              c.updated_at,
              (
                select text
                from messages
                where conversation_id = c.id
                order by created_at desc
                limit 1
              ) as last_message,
              (
                select route
                from messages
                where conversation_id = c.id and route is not null
                order by created_at desc
                limit 1
              ) as route,
              (
                select count(*)
                from messages
                where conversation_id = c.id
              ) as message_count
            from conversations c
            where c.archived_at is null
              {scope_filter}
            order by c.updated_at desc
            limit ? offset ?
            """,
            params,
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def conversation_stats(db_path: pathlib.Path) -> dict:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            select
              (select count(*) from conversations where archived_at is null) as conversation_count,
              (select count(*) from messages) as message_count,
              (select count(*) from messages where role = 'user') as user_message_count,
              (select count(*) from messages where role = 'assistant') as assistant_message_count
            """
        ).fetchone()
    return row_to_dict(row)


def assistant_failure_summary(db_path: pathlib.Path, limit: int = 12) -> dict:
    failure_patterns = [
        "%couldn't reach%",
        "%atlas runner failed%",
        "%cannot be answered%",
        "%unable to provide%",
        "%search evidence does not%",
        "%provided evidence%",
        "%do not contain%",
        "%too broad%",
    ]
    where = " or ".join("lower(text) like lower(?)" for _ in failure_patterns)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            select id, conversation_id, text, route, tool, source, created_at
            from messages
            where role = 'assistant'
              and ({where})
            order by created_at desc, rowid desc
            limit ?
            """,
            (*failure_patterns, limit),
        ).fetchall()
        route_rows = conn.execute(
            f"""
            select coalesce(route, 'unknown') as route, count(*) as count
            from messages
            where role = 'assistant'
              and ({where})
            group by coalesce(route, 'unknown')
            order by count desc, route asc
            """,
            failure_patterns,
        ).fetchall()
    return {
        "failure_count": sum(row["count"] for row in route_rows),
        "routes": [row_to_dict(row) for row in route_rows],
        "recent": [row_to_dict(row) for row in rows],
    }


def get_conversation(db_path: pathlib.Path, conversation_id: str) -> dict | None:
    with connect(db_path) as conn:
        conversation = conn.execute(
            """
            select id, title, context_scope, created_at, updated_at, archived_at
            from conversations
            where id = ? and archived_at is null
            """,
            (conversation_id,),
        ).fetchone()
        if not conversation:
            return None
        messages = conn.execute(
            """
            select *
            from messages
            where conversation_id = ?
            order by created_at asc
            """,
            (conversation_id,),
        ).fetchall()
    payload = row_to_dict(conversation)
    payload["messages"] = [row_to_dict(row) for row in messages]
    return payload


def archive_conversation(db_path: pathlib.Path, conversation_id: str) -> dict | None:
    archived_at = now_iso()
    with connect(db_path) as conn:
        row = conn.execute(
            """
            select id, title, context_scope, created_at, updated_at, archived_at
            from conversations
            where id = ? and archived_at is null
            """,
            (conversation_id,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "update conversations set archived_at = ?, updated_at = ? where id = ? and archived_at is null",
            (archived_at, archived_at, conversation_id),
        )
    payload = row_to_dict(row)
    payload["archived_at"] = archived_at
    return payload


def get_recent_messages(db_path: pathlib.Path, conversation_id: str, limit: int = 8) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            select *
            from messages
            where conversation_id = ?
            order by created_at desc
            limit ?
            """,
            (conversation_id, limit),
        ).fetchall()
    return [row_to_dict(row) for row in reversed(rows)]


def search_messages(
    db_path: pathlib.Path,
    query: str,
    limit: int = 50,
    context_scope: str | None = None,
) -> list[dict]:
    like_query = f"%{query}%"
    scope_filter = "and c.context_scope = ?" if context_scope else ""
    with connect(db_path) as conn:
        try:
            params = []
            if context_scope:
                params.append(context_scope)
            params.append(query)
            params.append(limit)
            rows = conn.execute(
                f"""
                select
                  m.*,
                  c.title as conversation_title
                from messages_fts f
                join messages m on m.rowid = f.rowid
                join conversations c on c.id = m.conversation_id
                where c.archived_at is null
                  {scope_filter}
                  and messages_fts match ?
                order by bm25(messages_fts), m.created_at desc
                limit ?
                """,
                params,
            ).fetchall()
        except sqlite3.OperationalError:
            params = []
            if context_scope:
                params.append(context_scope)
            params.append(like_query)
            params.append(limit)
            rows = conn.execute(
                f"""
                select
                  m.*,
                  c.title as conversation_title
                from messages m
                join conversations c on c.id = m.conversation_id
                where c.archived_at is null
                  {scope_filter}
                  and m.text like ?
                order by m.created_at desc
                limit ?
                """,
                params,
            ).fetchall()
    return [row_to_dict(row) for row in rows]


def create_reminder(
    db_path: pathlib.Path,
    text: str,
    due_at: str | None = None,
    recurrence: str | None = None,
    context_scope: str = "personal",
    source_message_id: str | None = None,
    *,
    row_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    created_at = now_iso()
    reminder_id = row_id or new_id()

    def _write(active_conn: sqlite3.Connection) -> None:
        active_conn.execute(
            """
            insert into reminders(id, text, due_at, recurrence, context_scope, source_message_id, created_at)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (reminder_id, text, due_at, recurrence, context_scope, source_message_id, created_at),
        )

    if conn is not None:
        _write(conn)
    else:
        with connect(db_path) as owned_conn:
            _write(owned_conn)
            owned_conn.commit()
    return {
        "id": reminder_id,
        "text": text,
        "due_at": due_at,
        "recurrence": recurrence,
        "context_scope": context_scope,
        "source_message_id": source_message_id,
        "completed_at": None,
        "notified_at": None,
        "created_at": created_at,
    }


def list_reminders(
    db_path: pathlib.Path,
    include_completed: bool = False,
    limit: int = 100,
    context_scope: str | None = None,
) -> list[dict]:
    filters = []
    params = []
    if not include_completed:
        filters.append("completed_at is null")
    if context_scope:
        filters.append("context_scope = ?")
        params.append(context_scope)
    where = "where " + " and ".join(filters) if filters else ""
    params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            select *
            from reminders
            {where}
            order by
              case when due_at is null then 1 else 0 end,
              due_at asc,
              created_at desc
            limit ?
            """,
            params,
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def complete_reminder(db_path: pathlib.Path, reminder_id: str) -> dict | None:
    completed_at = now_iso()
    with connect(db_path) as conn:
        conn.execute(
            "update reminders set completed_at = ? where id = ? and completed_at is null",
            (completed_at, reminder_id),
        )
        row = conn.execute("select * from reminders where id = ?", (reminder_id,)).fetchone()
        conn.commit()
    return row_to_dict(row) if row else None


def update_reminder(
    db_path: pathlib.Path,
    reminder_id: str,
    text: str | None = None,
    due_at: str | None = None,
    recurrence: str | None = None,
) -> dict | None:
    with connect(db_path) as conn:
        current = conn.execute("select * from reminders where id = ?", (reminder_id,)).fetchone()
        if not current:
            return None
        next_text = text if text is not None else current["text"]
        next_due_at = due_at if due_at is not None else current["due_at"]
        next_recurrence = recurrence if recurrence is not None else current["recurrence"]
        conn.execute(
            "update reminders set text = ?, due_at = ?, recurrence = ?, notified_at = null where id = ?",
            (next_text, next_due_at, next_recurrence, reminder_id),
        )
        row = conn.execute("select * from reminders where id = ?", (reminder_id,)).fetchone()
        conn.commit()
    return row_to_dict(row) if row else None


def snooze_reminder(db_path: pathlib.Path, reminder_id: str, due_at: str) -> dict | None:
    with connect(db_path) as conn:
        conn.execute(
            "update reminders set due_at = ?, notified_at = null where id = ? and completed_at is null",
            (due_at, reminder_id),
        )
        row = conn.execute("select * from reminders where id = ?", (reminder_id,)).fetchone()
        conn.commit()
    return row_to_dict(row) if row else None


def reschedule_recurring_reminder(db_path: pathlib.Path, reminder_id: str, next_due_at: str) -> dict | None:
    with connect(db_path) as conn:
        conn.execute(
            "update reminders set due_at = ?, notified_at = null where id = ? and completed_at is null",
            (next_due_at, reminder_id),
        )
        row = conn.execute("select * from reminders where id = ?", (reminder_id,)).fetchone()
        conn.commit()
    return row_to_dict(row) if row else None


def due_reminders(db_path: pathlib.Path, now_iso_value: str, limit: int = 100) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            select *
            from reminders
            where completed_at is null
              and due_at is not null
              and due_at <= ?
              and notified_at is null
            order by due_at asc
            limit ?
            """,
            (now_iso_value, limit),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def mark_reminder_notified(db_path: pathlib.Path, reminder_id: str, notified_at: str) -> None:
    with connect(db_path) as conn:
        conn.execute("update reminders set notified_at = ? where id = ?", (notified_at, reminder_id))
        conn.commit()


def create_note(
    db_path: pathlib.Path,
    title: str,
    text: str,
    context_scope: str = "personal",
    source_message_id: str | None = None,
    *,
    source_type: str | None = None,
    import_batch_id: str | None = None,
    import_confidence: float | None = None,
    review_status: str | None = None,
    expires_at: str | None = None,
    fact_lifecycle: str | None = None,
    row_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    created_at = now_iso()
    note_id = row_id or new_id()
    clean_title = title_from_text(title) if title else title_from_text(text)

    def _write(active_conn: sqlite3.Connection) -> None:
        active_conn.execute(
            """
            insert into notes(
              id, title, text, context_scope, source_message_id, source_type,
              import_batch_id, import_confidence, review_status, expires_at,
              fact_lifecycle, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note_id, clean_title, text, context_scope, source_message_id,
                source_type, import_batch_id, import_confidence, review_status,
                expires_at, fact_lifecycle, created_at, created_at,
            ),
        )

    if conn is not None:
        _write(conn)
    else:
        with connect(db_path) as owned_conn:
            _write(owned_conn)
            owned_conn.commit()
    return {
        "id": note_id,
        "title": clean_title,
        "text": text,
        "context_scope": context_scope,
        "source_message_id": source_message_id,
        "source_type": source_type,
        "import_batch_id": import_batch_id,
        "import_confidence": import_confidence,
        "review_status": review_status,
        "expires_at": expires_at,
        "fact_lifecycle": fact_lifecycle,
        "created_at": created_at,
        "updated_at": created_at,
        "archived_at": None,
    }


def search_notes(db_path: pathlib.Path, query: str, limit: int = 50, context_scope: str | None = None) -> list[dict]:
    like_query = f"%{query}%"
    scope_filter = "and n.context_scope = ?" if context_scope else ""
    with connect(db_path) as conn:
        try:
            params = []
            if context_scope:
                params.append(context_scope)
            params.extend([query, limit])
            rows = conn.execute(
                f"""
                select n.*
                from notes_fts f
                join notes n on n.rowid = f.rowid
                where n.archived_at is null
                  {scope_filter}
                  and notes_fts match ?
                order by bm25(notes_fts), n.updated_at desc
                limit ?
                """,
                params,
            ).fetchall()
        except sqlite3.OperationalError:
            params = []
            if context_scope:
                params.append(context_scope)
            params.extend([like_query, like_query, limit])
            rows = conn.execute(
                f"""
                select *
                from notes n
                where n.archived_at is null
                  {scope_filter}
                  and (title like ? or text like ?)
                order by updated_at desc
                limit ?
                """,
                params,
            ).fetchall()
    return [row_to_dict(row) for row in rows]


def list_notes(db_path: pathlib.Path, limit: int = 100, context_scope: str | None = None) -> list[dict]:
    scope_filter = "and context_scope = ?" if context_scope else ""
    params = [limit] if not context_scope else [context_scope, limit]
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            select *
            from notes
            where archived_at is null
              {scope_filter}
            order by updated_at desc
            limit ?
            """,
            params,
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def update_note(db_path: pathlib.Path, note_id: str, title: str | None = None, text: str | None = None) -> dict | None:
    updated_at = now_iso()
    with connect(db_path) as conn:
        current = conn.execute("select * from notes where id = ? and archived_at is null", (note_id,)).fetchone()
        if not current:
            return None
        next_text = text if text is not None else current["text"]
        next_title = title_from_text(title) if title else current["title"]
        conn.execute(
            "update notes set title = ?, text = ?, updated_at = ? where id = ? and archived_at is null",
            (next_title, next_text, updated_at, note_id),
        )
        row = conn.execute("select * from notes where id = ?", (note_id,)).fetchone()
        conn.commit()
    return row_to_dict(row) if row else None


def archive_note(db_path: pathlib.Path, note_id: str) -> dict | None:
    archived_at = now_iso()
    with connect(db_path) as conn:
        conn.execute("update notes set archived_at = ?, updated_at = ? where id = ? and archived_at is null", (archived_at, archived_at, note_id))
        row = conn.execute("select * from notes where id = ?", (note_id,)).fetchone()
        conn.commit()
    return row_to_dict(row) if row else None


def create_task(
    db_path: pathlib.Path,
    text: str,
    priority: str | None = None,
    context_scope: str = "personal",
    source_message_id: str | None = None,
    *,
    row_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    created_at = now_iso()
    task_id = row_id or new_id()

    def _write(active_conn: sqlite3.Connection) -> None:
        active_conn.execute(
            """
            insert into tasks(id, text, priority, context_scope, source_message_id, created_at)
            values (?, ?, ?, ?, ?, ?)
            """,
            (task_id, text, priority, context_scope, source_message_id, created_at),
        )

    if conn is not None:
        _write(conn)
    else:
        with connect(db_path) as owned_conn:
            _write(owned_conn)
            owned_conn.commit()
    return {
        "id": task_id,
        "text": text,
        "priority": priority,
        "context_scope": context_scope,
        "source_message_id": source_message_id,
        "completed_at": None,
        "created_at": created_at,
    }


def list_tasks(
    db_path: pathlib.Path,
    include_completed: bool = False,
    limit: int = 100,
    context_scope: str | None = None,
) -> list[dict]:
    filters = []
    params = []
    if not include_completed:
        filters.append("completed_at is null")
    if context_scope:
        filters.append("context_scope = ?")
        params.append(context_scope)
    where = "where " + " and ".join(filters) if filters else ""
    params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            select *
            from tasks
            {where}
            order by created_at desc
            limit ?
            """,
            params,
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def complete_task(db_path: pathlib.Path, task_id: str) -> dict | None:
    completed_at = now_iso()
    with connect(db_path) as conn:
        conn.execute(
            "update tasks set completed_at = ? where id = ? and completed_at is null",
            (completed_at, task_id),
        )
        row = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
        conn.commit()
    return row_to_dict(row) if row else None


def update_task(db_path: pathlib.Path, task_id: str, text: str | None = None, priority: str | None = None) -> dict | None:
    with connect(db_path) as conn:
        current = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
        if not current:
            return None
        next_text = text if text is not None else current["text"]
        next_priority = priority if priority is not None else current["priority"]
        conn.execute(
            "update tasks set text = ?, priority = ? where id = ?",
            (next_text, next_priority, task_id),
        )
        row = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
        conn.commit()
    return row_to_dict(row) if row else None


def create_shopping_item(
    db_path: pathlib.Path,
    text: str,
    quantity: str | None = None,
    store: str | None = None,
    context_scope: str = "personal",
    source_message_id: str | None = None,
    *,
    row_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    created_at = now_iso()
    item_id = row_id or new_id()
    clean_text = " ".join(text.split())
    clean_quantity = " ".join(quantity.split()) if quantity else None
    clean_store = " ".join(store.split()) if store else None

    def _write(active_conn: sqlite3.Connection) -> None:
        active_conn.execute(
            """
            insert into shopping_items(id, text, quantity, store, context_scope, source_message_id, created_at)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (item_id, clean_text, clean_quantity, clean_store, context_scope, source_message_id, created_at),
        )

    if conn is not None:
        _write(conn)
    else:
        with connect(db_path) as owned_conn:
            _write(owned_conn)
            owned_conn.commit()
    return {
        "id": item_id,
        "text": clean_text,
        "quantity": clean_quantity,
        "store": clean_store,
        "context_scope": context_scope,
        "source_message_id": source_message_id,
        "completed_at": None,
        "created_at": created_at,
    }


def list_shopping_items(
    db_path: pathlib.Path,
    include_completed: bool = False,
    limit: int = 100,
    context_scope: str | None = None,
) -> list[dict]:
    filters = []
    params = []
    if not include_completed:
        filters.append("completed_at is null")
    if context_scope:
        filters.append("context_scope = ?")
        params.append(context_scope)
    where = "where " + " and ".join(filters) if filters else ""
    params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            select *
            from shopping_items
            {where}
            order by created_at desc
            limit ?
            """,
            params,
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def complete_shopping_item(db_path: pathlib.Path, item_id: str) -> dict | None:
    completed_at = now_iso()
    with connect(db_path) as conn:
        conn.execute(
            "update shopping_items set completed_at = ? where id = ? and completed_at is null",
            (completed_at, item_id),
        )
        row = conn.execute("select * from shopping_items where id = ?", (item_id,)).fetchone()
        conn.commit()
    return row_to_dict(row) if row else None


def update_shopping_item(
    db_path: pathlib.Path,
    item_id: str,
    text: str | None = None,
    quantity: str | None = None,
    store: str | None = None,
) -> dict | None:
    with connect(db_path) as conn:
        current = conn.execute("select * from shopping_items where id = ?", (item_id,)).fetchone()
        if not current:
            return None
        next_text = " ".join(text.split()) if text is not None else current["text"]
        next_quantity = " ".join(quantity.split()) if quantity is not None and quantity else current["quantity"]
        next_store = " ".join(store.split()) if store is not None and store else current["store"]
        conn.execute(
            "update shopping_items set text = ?, quantity = ?, store = ? where id = ?",
            (next_text, next_quantity, next_store, item_id),
        )
        row = conn.execute("select * from shopping_items where id = ?", (item_id,)).fetchone()
        conn.commit()
    return row_to_dict(row) if row else None


def create_rule(db_path: pathlib.Path, condition_text: str, action_text: str) -> dict:
    created_at = now_iso()
    rule_id = new_id()
    with connect(db_path) as conn:
        conn.execute(
            """
            insert into rules(id, condition_text, action_text, created_at)
            values (?, ?, ?, ?)
            """,
            (rule_id, condition_text, action_text, created_at),
        )
        conn.commit()
    return {
        "id": rule_id,
        "condition_text": condition_text,
        "action_text": action_text,
        "enabled": 1,
        "created_at": created_at,
    }


def list_rules(db_path: pathlib.Path, limit: int = 100) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            select *
            from rules
            order by created_at desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def update_rule(db_path: pathlib.Path, rule_id: str, enabled: bool | None = None) -> dict | None:
    with connect(db_path) as conn:
        current = conn.execute("select * from rules where id = ?", (rule_id,)).fetchone()
        if not current:
            return None
        next_enabled = int(enabled) if enabled is not None else current["enabled"]
        # DEF-001: if the owner explicitly re-enables a rule that previously
        # auto-disabled after firing (mark_rule_fired()), clear last_fired_at
        # so it becomes fireable again -- otherwise due_rules()'s
        # last_fired_at is null guard would silently keep excluding it even
        # though the owner just turned it back on, with no visible reason why.
        newly_enabled = next_enabled == 1 and current["enabled"] == 0
        if newly_enabled:
            conn.execute(
                "update rules set enabled = ?, last_fired_at = null where id = ?",
                (next_enabled, rule_id),
            )
        else:
            conn.execute("update rules set enabled = ? where id = ?", (next_enabled, rule_id))
        row = conn.execute("select * from rules where id = ?", (rule_id,)).fetchone()
        conn.commit()
    return row_to_dict(row) if row else None


def delete_rule(db_path: pathlib.Path, rule_id: str) -> bool:
    with connect(db_path) as conn:
        cursor = conn.execute("delete from rules where id = ?", (rule_id,))
        conn.commit()
    return cursor.rowcount > 0


def due_rules(
    db_path: pathlib.Path,
    now_iso_value: str,
    signal_routes: dict | None = None,
    limit: int = 100,
) -> list[dict]:
    # Two independent condition paths, kept deliberately separate rather than
    # unified into one query/branch, so the pre-existing DEF-001 stub
    # behavior is provably byte-for-byte unchanged:
    #
    # 1. Legacy literal 'always'/'now' stub (DEF-001 phase 1, unchanged since
    #    that fix): a stub condition can only be meaningfully true once --
    #    there is no way to detect "became newly true again" for a condition
    #    that is always true. Excluding already-fired rules (last_fired_at is
    #    not null) is what stops the re-queue-every-60s-tick-forever bug.
    #    See mark_rule_fired() below.
    #
    # 2. Track B1 structured (mini-grammar) conditions, evaluated against
    #    atlas_signals.evaluate_condition() and live signal_routes callables
    #    (built by the caller with real config -- see atlas_signals.py's
    #    module docstring for why this module stays config-free). Unlike the
    #    stub, a real condition can legitimately become true, resolve, and
    #    become true again -- so this path is edge-triggered rather than
    #    one-shot: due only on a false/null -> true transition, tracked via
    #    last_condition_state (updated on every evaluation, fire or not).
    #    See mark_condition_rule_fired() below, which records last_fired_at
    #    for observability WITHOUT auto-disabling (unlike mark_rule_fired()).
    signal_routes = signal_routes or {}
    with connect(db_path) as conn:
        rows = conn.execute(
            "select * from rules where enabled = 1 order by created_at asc",
        ).fetchall()
    due: list[dict] = []
    for row in rows:
        rule = row_to_dict(row)
        condition = (rule["condition_text"] or "").strip()
        if condition.lower() in atlas_signals.LEGACY_STUB_CONDITIONS:
            if rule.get("last_fired_at") is None:
                due.append(rule)
            continue
        try:
            result, status = atlas_signals.evaluate_condition(condition, signal_routes)
        except Exception:
            # A live-integration signal (Home Assistant, Nextcloud) can fail
            # -- network blip, misconfiguration, integration disabled after
            # the rule was created. A proactive alert derived from a failed
            # signal read is worse than silence (same principle B2's
            # freshness guarantee is built on): treat as "not due this tick"
            # rather than crash the whole evaluation loop for every other
            # rule, and rather than spuriously fire on bad data.
            continue
        if status != "structured":
            # Defensive skip, not a crash: a condition that isn't grammar-
            # shaped at all (free text) is silently never due, matching
            # today's existing behavior for any non-stub condition. A
            # grammar-shaped-but-invalid condition should have been rejected
            # at creation time (validate_condition_for_creation()); reaching
            # here means the registry changed after creation -- skip rather
            # than fail loudly a second time at evaluation.
            continue
        was_true = rule.get("last_condition_state") == "true"
        set_rule_condition_state(db_path, rule["id"], result)
        if result and not was_true:
            due.append(rule)
    return due[:limit]


def set_rule_condition_state(db_path: pathlib.Path, rule_id: str, state: bool) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "update rules set last_condition_state = ? where id = ?",
            ("true" if state else "false", rule_id),
        )
        conn.commit()


def mark_condition_rule_fired(db_path: pathlib.Path, rule_id: str, fired_at: str) -> None:
    """Track B1: records that a structured rule fired on a false->true
    condition transition, WITHOUT auto-disabling it (unlike mark_rule_fired(),
    reserved for the literal one-shot 'always'/'now' stub). A real condition
    can legitimately become true again later -- disabling here would
    silently stop a rule the owner still wants active, with no visible
    signal why.
    """
    with connect(db_path) as conn:
        conn.execute(
            "update rules set last_fired_at = ? where id = ?",
            (fired_at, rule_id),
        )
        conn.commit()


def mark_rule_fired(db_path: pathlib.Path, rule_id: str, fired_at: str) -> None:
    # Also disables the rule, not just marking it fired: a stub 'always'/'now'
    # condition cannot become newly true again (no real evaluator exists
    # yet), so leaving it enabled=1 forever after firing once would look
    # active in the PWA's Rules panel while silently never doing anything --
    # worse than disabling it outright, since the owner would have no signal
    # that anything happened. The owner can always re-enable via the existing
    # update_rule()/PATCH endpoint if they want the same one-shot to fire
    # again.
    with connect(db_path) as conn:
        conn.execute(
            "update rules set last_fired_at = ?, enabled = 0 where id = ?",
            (fired_at, rule_id),
        )
        conn.commit()


def add_audit_event(db_path: pathlib.Path, event_type: str, summary: str, details: dict | None = None) -> dict:
    created_at = now_iso()
    event_id = new_id()
    with connect(db_path) as conn:
        conn.execute(
            """
            insert into audit_events(id, event_type, summary, details_json, created_at)
            values (?, ?, ?, ?, ?)
            """,
            (event_id, event_type, summary, __import__("json").dumps(details or {}, sort_keys=True), created_at),
        )
        conn.commit()
    return {
        "id": event_id,
        "event_type": event_type,
        "summary": summary,
        "details": details or {},
        "created_at": created_at,
    }


def list_audit_events(db_path: pathlib.Path, limit: int = 100) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            select *
            from audit_events
            order by created_at desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    events = []
    for row in rows:
        event = row_to_dict(row)
        details_json = event.pop("details_json", "{}")
        try:
            event["details"] = __import__("json").loads(details_json or "{}")
        except ValueError:
            event["details"] = {}
        events.append(event)
    return events


def add_orchestration_shadow_trace(db_path: pathlib.Path, trace: dict) -> dict:
    """Store planner telemetry without storing the user request or model output."""
    created_at = now_iso()
    trace_id = new_id()
    capabilities = trace.get("capabilities") or []
    if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
        raise ValueError("capabilities must be a list of strings")
    status = trace.get("status") or "failed"
    if status not in {"completed", "invalid", "failed", "dropped"}:
        raise ValueError("invalid shadow trace status")
    values = {
        "id": trace_id,
        "conversation_id": trace.get("conversation_id"),
        "user_message_id": trace.get("user_message_id"),
        "planner_version": trace.get("planner_version") or "unknown",
        "model": trace.get("model"),
        "decision_source": trace.get("decision_source"),
        "context_scope": trace.get("context_scope") or "personal",
        "question_sha256": trace.get("question_sha256") or "",
        "question_chars": int(trace.get("question_chars") or 0),
        "decision_mode": trace.get("decision_mode"),
        "decision_freshness": trace.get("decision_freshness"),
        "decision_risk": trace.get("decision_risk"),
        "decision_scope": trace.get("decision_scope"),
        "capabilities_json": __import__("json").dumps(capabilities, sort_keys=True),
        "needs_verification": (
            int(trace["needs_verification"])
            if isinstance(trace.get("needs_verification"), bool)
            else None
        ),
        "reason_code": trace.get("reason_code"),
        "confidence": trace.get("confidence"),
        "current_route": trace.get("current_route"),
        "current_tool": trace.get("current_tool"),
        "planner_latency_ms": trace.get("planner_latency_ms"),
        "status": status,
        "error_type": trace.get("error_type"),
        "created_at": created_at,
    }
    with connect(db_path) as conn:
        conn.execute(
            """
            insert into orchestration_shadow_traces(
              id, conversation_id, user_message_id, planner_version, model, decision_source,
              context_scope, question_sha256, question_chars, decision_mode,
              decision_freshness, decision_risk, decision_scope,
              capabilities_json, needs_verification, reason_code, confidence,
              current_route, current_tool, planner_latency_ms, status,
              error_type, created_at
            ) values (
              :id, :conversation_id, :user_message_id, :planner_version, :model, :decision_source,
              :context_scope, :question_sha256, :question_chars, :decision_mode,
              :decision_freshness, :decision_risk, :decision_scope,
              :capabilities_json, :needs_verification, :reason_code, :confidence,
              :current_route, :current_tool, :planner_latency_ms, :status,
              :error_type, :created_at
            )
            """,
            values,
        )
        conn.commit()
    return _decode_orchestration_shadow_trace(values)


def _decode_orchestration_shadow_trace(row: dict) -> dict:
    item = dict(row)
    raw_capabilities = item.pop("capabilities_json", "[]")
    try:
        item["capabilities"] = __import__("json").loads(raw_capabilities or "[]")
    except ValueError:
        item["capabilities"] = []
    if item.get("needs_verification") is not None:
        item["needs_verification"] = bool(item["needs_verification"])
    return item


def list_orchestration_shadow_traces(db_path: pathlib.Path, limit: int = 100) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            select *
            from orchestration_shadow_traces
            order by created_at desc, rowid desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    return [_decode_orchestration_shadow_trace(row_to_dict(row)) for row in rows]


def orchestration_shadow_stats(db_path: pathlib.Path) -> dict:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            select status, count(*) as count
            from orchestration_shadow_traces
            group by status
            order by status
            """
        ).fetchall()
        mode_rows = conn.execute(
            """
            select coalesce(decision_mode, 'none') as mode, count(*) as count
            from orchestration_shadow_traces
            where status = 'completed'
            group by coalesce(decision_mode, 'none')
            order by count desc, mode
            """
        ).fetchall()
    statuses = {row["status"]: row["count"] for row in rows}
    return {
        "total": sum(statuses.values()),
        "statuses": statuses,
        "modes": [row_to_dict(row) for row in mode_rows],
    }


def create_notification(
    db_path: pathlib.Path,
    title: str,
    body: str,
    channel: str = "local",
    source_type: str | None = None,
    source_id: str | None = None,
) -> dict:
    created_at = now_iso()
    notification_id = new_id()
    with connect(db_path) as conn:
        conn.execute(
            """
            insert into notifications(id, channel, title, body, status, source_type, source_id, created_at)
            values (?, ?, ?, ?, 'queued', ?, ?, ?)
            """,
            (notification_id, channel, title, body, source_type, source_id, created_at),
        )
        conn.commit()
    return {
        "id": notification_id,
        "channel": channel,
        "title": title,
        "body": body,
        "status": "queued",
        "source_type": source_type,
        "source_id": source_id,
        "created_at": created_at,
        "delivered_at": None,
        "error": None,
    }


def get_briefing_run(db_path: pathlib.Path, briefing_kind: str, scheduled_for: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "select * from briefing_runs where briefing_kind = ? and scheduled_for = ?",
            (briefing_kind, scheduled_for),
        ).fetchone()
    return row_to_dict(row) if row else None


def put_signal_observation(
    db_path: pathlib.Path,
    signal_name: str,
    value,
    status: str = "ok",
    observed_at: str | None = None,
    error: str | None = None,
) -> dict:
    observed_at = observed_at or now_iso()
    value_json = __import__("json").dumps(value, sort_keys=True)
    with connect(db_path) as conn:
        conn.execute(
            """
            insert into signal_observations(signal_name, value_json, status, observed_at, error)
            values (?, ?, ?, ?, ?)
            on conflict(signal_name) do update set
              value_json=excluded.value_json,
              status=excluded.status,
              observed_at=excluded.observed_at,
              error=excluded.error
            """,
            (signal_name, value_json, status, observed_at, error),
        )
        conn.commit()
    return {"signal_name": signal_name, "value": value, "status": status, "observed_at": observed_at, "error": error}


def get_signal_observation(db_path: pathlib.Path, signal_name: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("select * from signal_observations where signal_name = ?", (signal_name,)).fetchone()
    if not row:
        return None
    item = row_to_dict(row)
    try:
        item["value"] = __import__("json").loads(item.pop("value_json"))
    except (TypeError, ValueError):
        item["value"] = None
        item.pop("value_json", None)
    return item


def get_fresh_signal_value(
    db_path: pathlib.Path,
    signal_name: str,
    max_age_seconds: int,
    now_value: datetime | None = None,
):
    observation = get_signal_observation(db_path, signal_name)
    if not observation or observation.get("status") != "ok":
        raise RuntimeError(f"{signal_name} observation unavailable")
    try:
        observed_at = datetime.fromisoformat(observation["observed_at"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{signal_name} observation timestamp invalid") from exc
    now_value = now_value or datetime.now(LOCAL_TZ)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=LOCAL_TZ)
    age = (now_value.astimezone(LOCAL_TZ) - observed_at.astimezone(LOCAL_TZ)).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise RuntimeError(f"{signal_name} observation stale")
    return observation.get("value")


def create_briefing_notification_once(
    db_path: pathlib.Path,
    briefing_kind: str,
    scheduled_for: str,
    title: str,
    body: str,
) -> dict | None:
    """Atomically record and queue one briefing per kind/local date."""
    created_at = now_iso()
    run_id = new_id()
    notification_id = new_id()
    with connect(db_path) as conn:
        try:
            conn.execute(
                """
                insert into notifications(id, channel, title, body, status, source_type, source_id, created_at)
                values (?, 'local', ?, ?, 'queued', 'briefing', ?, ?)
                """,
                (notification_id, title, body, run_id, created_at),
            )
            conn.execute(
                """
                insert into briefing_runs(id, briefing_kind, scheduled_for, status, notification_id, body, created_at)
                values (?, ?, ?, 'queued', ?, ?, ?)
                """,
                (run_id, briefing_kind, scheduled_for, notification_id, body, created_at),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            return None
    return {
        "id": run_id,
        "briefing_kind": briefing_kind,
        "scheduled_for": scheduled_for,
        "status": "queued",
        "notification_id": notification_id,
        "body": body,
        "created_at": created_at,
    }


def list_notifications(db_path: pathlib.Path, status: str | None = None, limit: int = 100) -> list[dict]:
    where = "where status = ?" if status else ""
    params = (status, limit) if status else (limit,)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            select *
            from notifications
            {where}
            order by created_at desc
            limit ?
            """,
            params,
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def mark_notification_delivered(db_path: pathlib.Path, notification_id: str, delivered_at: str) -> dict | None:
    with connect(db_path) as conn:
        conn.execute(
            "update notifications set status = 'delivered', delivered_at = ?, attempt_count = attempt_count + 1, error = null where id = ?",
            (delivered_at, notification_id),
        )
        row = conn.execute("select * from notifications where id = ?", (notification_id,)).fetchone()
        conn.commit()
    return row_to_dict(row) if row else None


def mark_notification_failed(db_path: pathlib.Path, notification_id: str, error: str) -> dict | None:
    with connect(db_path) as conn:
        conn.execute(
            "update notifications set status = case when attempt_count >= 1 then 'dead_letter' else 'queued' end, attempt_count = attempt_count + 1, error = ? where id = ?",
            (error, notification_id),
        )
        row = conn.execute("select * from notifications where id = ?", (notification_id,)).fetchone()
        conn.commit()
    return row_to_dict(row) if row else None


def request_confirmation(
    db_path: pathlib.Path,
    action_type: str,
    summary: str,
    risk_level: str,
    details: dict | None = None,
    execution_status: str = "not_applicable",
) -> dict:
    requested_at = now_iso()
    confirmation_id = new_id()
    with connect(db_path) as conn:
        conn.execute(
            """
            insert into confirmations(id, action_type, summary, risk_level, status, requested_at, details_json, execution_status)
            values (?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                confirmation_id,
                action_type,
                summary,
                risk_level,
                requested_at,
                __import__("json").dumps(details or {}, sort_keys=True),
                execution_status,
            ),
        )
        conn.commit()
    return {
        "id": confirmation_id,
        "action_type": action_type,
        "summary": summary,
        "risk_level": risk_level,
        "status": "pending",
        "requested_at": requested_at,
        "decided_at": None,
        "details": details or {},
        "execution_status": execution_status,
    }


def list_confirmations(db_path: pathlib.Path, status: str | None = None, limit: int = 100) -> list[dict]:
    where = "where status = ?" if status else ""
    params = (status, limit) if status else (limit,)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            select *
            from confirmations
            {where}
            order by requested_at desc
            limit ?
            """,
            params,
        ).fetchall()
    confirmations = []
    for row in rows:
        item = row_to_dict(row)
        details_json = item.pop("details_json", "{}")
        try:
            item["details"] = __import__("json").loads(details_json or "{}")
        except ValueError:
            item["details"] = {}
        confirmations.append(item)
    return confirmations


def decide_confirmation(db_path: pathlib.Path, confirmation_id: str, status: str) -> dict | None:
    decided_at = now_iso()
    with connect(db_path) as conn:
        conn.execute(
            """
            update confirmations
            set status = ?, decided_at = ?
            where id = ? and status = 'pending'
            """,
            (status, decided_at, confirmation_id),
        )
        row = conn.execute("select * from confirmations where id = ?", (confirmation_id,)).fetchone()
        conn.commit()
    if not row:
        return None
    item = row_to_dict(row)
    details_json = item.pop("details_json", "{}")
    try:
        item["details"] = __import__("json").loads(details_json or "{}")
    except ValueError:
        item["details"] = {}
    return item


# -- Fuzzy action-mode execution (orchestration v2 action authority) --------
#
# Deliberately narrow: only the four non-admin, already-reversible action
# capabilities (reminders/tasks/shopping/notes.manage) can ever execute here.
# Not exposed as a per-capability public API -- execute_confirmed_action is
# the only entry point, called after an owner approves a confirmation that
# was created by atlas_shortcuts_server.py's fuzzy-action path. A capability
# outside this map (e.g. a future admin/device-control action) is refused,
# not silently ignored -- keeping "first slice" a code-level fact, not just a
# documented intent.

def _execute_reminder_action(db_path: pathlib.Path, conn: sqlite3.Connection, row_id: str, details: dict) -> dict:
    create_reminder(
        db_path,
        text=details.get("text", ""),
        due_at=details.get("due_at"),
        recurrence=details.get("recurrence"),
        context_scope=details.get("context_scope", "personal"),
        source_message_id=details.get("source_message_id"),
        row_id=row_id,
        conn=conn,
    )
    row = conn.execute("select * from reminders where id = ?", (row_id,)).fetchone()
    if not row:
        return {"ok": False, "row": None, "mismatch": ["row_not_found"]}
    row_dict = row_to_dict(row)
    mismatch = [
        field
        for field, expected in (
            ("text", details.get("text", "")),
            ("due_at", details.get("due_at")),
            ("context_scope", details.get("context_scope", "personal")),
        )
        if row_dict.get(field) != expected
    ]
    return {"ok": not mismatch, "row": row_dict, "mismatch": mismatch}


def _execute_task_action(db_path: pathlib.Path, conn: sqlite3.Connection, row_id: str, details: dict) -> dict:
    create_task(
        db_path,
        text=details.get("text", ""),
        priority=details.get("priority"),
        context_scope=details.get("context_scope", "personal"),
        source_message_id=details.get("source_message_id"),
        row_id=row_id,
        conn=conn,
    )
    row = conn.execute("select * from tasks where id = ?", (row_id,)).fetchone()
    if not row:
        return {"ok": False, "row": None, "mismatch": ["row_not_found"]}
    row_dict = row_to_dict(row)
    mismatch = [
        field
        for field, expected in (
            ("text", details.get("text", "")),
            ("context_scope", details.get("context_scope", "personal")),
        )
        if row_dict.get(field) != expected
    ]
    return {"ok": not mismatch, "row": row_dict, "mismatch": mismatch}


def _execute_shopping_action(db_path: pathlib.Path, conn: sqlite3.Connection, row_id: str, details: dict) -> dict:
    create_shopping_item(
        db_path,
        text=details.get("text", ""),
        quantity=details.get("quantity"),
        store=details.get("store"),
        context_scope=details.get("context_scope", "personal"),
        source_message_id=details.get("source_message_id"),
        row_id=row_id,
        conn=conn,
    )
    row = conn.execute("select * from shopping_items where id = ?", (row_id,)).fetchone()
    if not row:
        return {"ok": False, "row": None, "mismatch": ["row_not_found"]}
    row_dict = row_to_dict(row)
    expected_text = " ".join((details.get("text") or "").split())
    mismatch = [
        field
        for field, expected in (
            ("text", expected_text),
            ("context_scope", details.get("context_scope", "personal")),
        )
        if row_dict.get(field) != expected
    ]
    return {"ok": not mismatch, "row": row_dict, "mismatch": mismatch}


def _execute_note_action(db_path: pathlib.Path, conn: sqlite3.Connection, row_id: str, details: dict) -> dict:
    create_note(
        db_path,
        title=details.get("title") or details.get("text", ""),
        text=details.get("text", ""),
        context_scope=details.get("context_scope", "personal"),
        source_message_id=details.get("source_message_id"),
        row_id=row_id,
        conn=conn,
    )
    row = conn.execute("select * from notes where id = ?", (row_id,)).fetchone()
    if not row:
        return {"ok": False, "row": None, "mismatch": ["row_not_found"]}
    row_dict = row_to_dict(row)
    mismatch = [
        field
        for field, expected in (
            ("text", details.get("text", "")),
            ("context_scope", details.get("context_scope", "personal")),
        )
        if row_dict.get(field) != expected
    ]
    return {"ok": not mismatch, "row": row_dict, "mismatch": mismatch}


def _execute_memory_correction_action(db_path: pathlib.Path, conn: sqlite3.Connection, row_id: str, details: dict) -> dict:
    if details.get("record_type") != "preference":
        return {"ok": False, "row": None, "mismatch": ["unsupported_record_type"]}
    outcome = correct_preference(
        db_path,
        details.get("key", ""),
        details.get("old_value", ""),
        details.get("new_value", ""),
        source=details.get("source", "owner_correction"),
        context_scope=details.get("context_scope", "personal"),
        confirmation_id=row_id,
        row_id=row_id,
        conn=conn,
    )
    row = conn.execute("select * from preferences where key = ?", (outcome["key"],)).fetchone()
    history = conn.execute("select * from preference_history where id = ?", (outcome["history_id"],)).fetchone()
    if not row or not history:
        return {"ok": False, "row": None, "mismatch": ["row_not_found"]}
    row_dict = row_to_dict(row)
    history_dict = row_to_dict(history)
    mismatch = []
    if row_dict.get("value") != details.get("new_value"):
        mismatch.append("active_value")
    if history_dict.get("old_value") != details.get("old_value"):
        mismatch.append("history_old_value")
    if history_dict.get("new_value") != details.get("new_value"):
        mismatch.append("history_new_value")
    if history_dict.get("status") != "superseded":
        mismatch.append("history_status")
    return {
        "ok": not mismatch,
        "row": {"preference": row_dict, "history": history_dict},
        "mismatch": mismatch,
    }


def _execute_comic_request_action(db_path: pathlib.Path, conn: sqlite3.Connection, row_id: str, details: dict) -> dict:
    """Trigger comic-bot via atlas_comic_bot.py and verify by file, not row.

    Unlike the four executors above, this action has no local database row
    to write and read back -- the thing being verified (a downloaded file
    landing in /opt/stacks/media/media/comics on docker-main) lives on a
    different host entirely. Verification relies on the trigger listener's
    own report of the specific file it confirmed on disk (`result_file`,
    forwarded from comic-bot's own `RESULT_FILE:` marker) -- deliberately
    NOT "did a new file appear," since comic-bot itself skips re-downloading
    (and never overwrites) a file whose name already exists. A second
    request for something already fetched is a legitimate success, not a
    "no new file" failure that would otherwise retry forever, one real hit
    against the external site per ~60s poll, without ever being able to
    verify (see DEC-2026-09-10-05).

    `conn` is accepted only to match the other executors' signature; nothing
    here writes to ATLAS's own database. A network error, timeout, or
    missing ATLAS_COMIC_BOT_TRIGGER_URL/_TOKEN raises, which
    execute_confirmed_action() catches and records as a retriable failure --
    matching the existing broad except-and-finalize contract, not
    reimplemented here.
    """
    payload = atlas_comic_bot.trigger_comic_request(
        COMIC_BOT_TRIGGER_URL,
        COMIC_BOT_TRIGGER_TOKEN,
        details.get("text", ""),
        result_index=details.get("result_index"),
    )
    files_before = set(payload.get("files_before") or [])
    files_after = set(payload.get("files_after") or [])
    new_files = sorted(files_after - files_before)
    result_file = payload.get("result_file")
    ok = bool(payload.get("ok")) and bool(result_file)
    row = {
        "text": details.get("text", ""),
        "result_file": result_file,
        "already_had_file": bool(result_file) and result_file not in new_files,
        "trigger_output": payload.get("output"),
    }
    mismatch = [] if ok else ["missing_result_file" if not result_file else "trigger_reported_failure"]
    return {"ok": ok, "row": row, "mismatch": mismatch}


_ACTION_EXECUTORS = {
    "reminders.manage": _execute_reminder_action,
    "tasks.manage": _execute_task_action,
    "shopping.manage": _execute_shopping_action,
    "notes.manage": _execute_note_action,
    "memory.correct": _execute_memory_correction_action,
    "media.comic_request": _execute_comic_request_action,
}


def _finalize_failed_action(db_path: pathlib.Path, confirmation_id: str, error: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            update confirmations
            set execution_status = 'failed', execution_error = ?
            where id = ?
            """,
            (error, confirmation_id),
        )
        conn.commit()
    add_audit_event(
        db_path,
        "confirmation.execution_failed",
        error,
        {"confirmation_id": confirmation_id},
    )


def execute_confirmed_action(db_path: pathlib.Path, confirmation_id: str) -> dict:
    """Idempotent, verified execution of an approved fuzzy-action confirmation.

    Contract: never writes anything unless status == "approved"; at-most-once
    per confirmation id (reused as the created row's primary key); verifies
    the written row matches what was proposed before marking executed; any
    mismatch or exception rolls back the write and durably records a failure
    reason via a separate transaction, so a retry is always safe.
    """
    with connect(db_path) as conn:
        row = conn.execute("select * from confirmations where id = ?", (confirmation_id,)).fetchone()
    if not row:
        return {"ok": False, "executed": False, "error": "confirmation_not_found"}
    confirmation = row_to_dict(row)

    if confirmation.get("execution_status") == "executed":
        cached = confirmation.get("execution_result_json")
        try:
            cached_row = __import__("json").loads(cached) if cached else None
        except ValueError:
            cached_row = None
        return {"ok": True, "executed": True, "already_executed": True, "row": cached_row}

    if confirmation.get("status") != "approved":
        return {"ok": False, "executed": False, "error": "not_approved"}

    try:
        details = __import__("json").loads(confirmation.get("details_json") or "{}")
    except ValueError:
        details = {}
    capability = details.get("capability")
    executor = _ACTION_EXECUTORS.get(capability)
    if not executor:
        _finalize_failed_action(db_path, confirmation_id, f"unsupported_capability:{capability}")
        return {"ok": False, "executed": False, "error": "unsupported_capability"}

    registry = {item.name: item for item in atlas_orchestrator_v2.CAPABILITIES}
    allowed_scopes = registry[capability].scopes if capability in registry else ()
    action_scope = details.get("context_scope")
    if action_scope not in allowed_scopes:
        _finalize_failed_action(db_path, confirmation_id, f"scope_not_allowed:{action_scope}")
        return {"ok": False, "executed": False, "error": "scope_not_allowed"}

    # Transaction A: atomic claim, committed alone. If the process crashes
    # between this and the write below, the confirmation is left in
    # "executing" -- inert (the scheduler poll only selects pending/failed),
    # not silently retried, requiring manual recovery. See design notes.
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            update confirmations
            set execution_status = 'executing'
            where id = ? and status = 'approved' and execution_status in ('pending', 'failed')
            """,
            (confirmation_id,),
        )
        conn.commit()
        if cursor.rowcount != 1:
            return {"ok": False, "executed": False, "error": "not_claimable"}

    # Transaction B: write + read-back verification + finalize, one commit.
    # Any exception (including a deliberate raise on verification mismatch)
    # triggers the connection context manager's automatic rollback, undoing
    # the write cleanly before the failure is durably recorded separately.
    try:
        with connect(db_path) as conn:
            outcome = executor(db_path, conn, confirmation_id, details)
            if not outcome["ok"]:
                raise ValueError(f"verification_mismatch:{outcome['mismatch']}")
            executed_at = now_iso()
            result_json = __import__("json").dumps(outcome["row"], sort_keys=True, default=str)
            conn.execute(
                """
                update confirmations
                set execution_status = 'executed', executed_at = ?, execution_result_json = ?
                where id = ?
                """,
                (executed_at, result_json, confirmation_id),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 -- a write-path failure must never escape unlogged
        _finalize_failed_action(db_path, confirmation_id, str(exc))
        return {"ok": False, "executed": False, "error": str(exc)}

    add_audit_event(
        db_path,
        "confirmation.executed",
        f"Executed {capability}",
        {"confirmation_id": confirmation_id, "capability": capability},
    )
    return {"ok": True, "executed": True, "row": outcome["row"]}


def set_preference(db_path: pathlib.Path, key: str, value: str) -> dict:
    updated_at = now_iso()
    with connect(db_path) as conn:
        conn.execute(
            """
            insert into preferences(key, value, updated_at)
            values (?, ?, ?)
            on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, updated_at),
        )
        conn.commit()
    return {"key": key, "value": value, "updated_at": updated_at}


def list_preferences(db_path: pathlib.Path) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute("select * from preferences order by key asc").fetchall()
    return [row_to_dict(row) for row in rows]


def delete_preference(db_path: pathlib.Path, key: str) -> bool:
    with connect(db_path) as conn:
        cursor = conn.execute("delete from preferences where key = ?", (key,))
        conn.commit()
    return cursor.rowcount > 0


def correct_preference(
    db_path: pathlib.Path,
    key: str,
    expected_old_value: str,
    new_value: str,
    *,
    source: str,
    context_scope: str = "personal",
    confirmation_id: str | None = None,
    row_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    if context_scope != "personal":
        raise PermissionError("memory corrections are personal-scope only")
    clean_key = " ".join(str(key or "").split())
    clean_old = " ".join(str(expected_old_value or "").split())
    clean_new = " ".join(str(new_value or "").split())
    if not clean_key or not clean_old or not clean_new:
        raise ValueError("missing preference correction field")
    history_id = row_id or new_id()
    created_at = now_iso()
    owns_conn = conn is None
    active_conn = conn or connect(db_path)
    try:
        current = active_conn.execute("select * from preferences where key = ?", (clean_key,)).fetchone()
        if not current:
            raise ValueError("preference_not_found")
        if current["value"] != clean_old:
            raise ValueError("stale_preference_correction")
        active_conn.execute(
            """
            insert into preference_history(
              id, preference_key, old_value, new_value, status, source,
              context_scope, confirmation_id, created_at
            )
            values (?, ?, ?, ?, 'superseded', ?, ?, ?, ?)
            """,
            (
                history_id,
                clean_key,
                clean_old,
                clean_new,
                source[:200],
                context_scope,
                confirmation_id,
                created_at,
            ),
        )
        active_conn.execute(
            "update preferences set value = ?, updated_at = ? where key = ?",
            (clean_new, created_at, clean_key),
        )
        if owns_conn:
            active_conn.commit()
        return {
            "history_id": history_id,
            "key": clean_key,
            "old_value": clean_old,
            "new_value": clean_new,
            "status": "superseded",
            "updated_at": created_at,
        }
    finally:
        if owns_conn:
            active_conn.close()


def rollback_preference_correction(db_path: pathlib.Path, history_id: str) -> dict | None:
    rolled_back_at = now_iso()
    with connect(db_path) as conn:
        history = conn.execute("select * from preference_history where id = ?", (history_id,)).fetchone()
        if not history:
            return None
        if history["status"] != "superseded":
            return row_to_dict(history)
        current = conn.execute("select * from preferences where key = ?", (history["preference_key"],)).fetchone()
        if not current or current["value"] != history["new_value"]:
            raise ValueError("rollback_preference_state_mismatch")
        conn.execute(
            "update preferences set value = ?, updated_at = ? where key = ?",
            (history["old_value"], rolled_back_at, history["preference_key"]),
        )
        conn.execute(
            "update preference_history set status = 'rolled_back', rolled_back_at = ? where id = ?",
            (rolled_back_at, history_id),
        )
        row = conn.execute("select * from preference_history where id = ?", (history_id,)).fetchone()
        conn.commit()
    add_audit_event(
        db_path,
        "memory.preference_rollback",
        f"Rolled back preference correction for {history['preference_key']}",
        {"history_id": history_id, "preference_key": history["preference_key"]},
    )
    return row_to_dict(row) if row else None


def list_preference_history(db_path: pathlib.Path, limit: int = 500) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            select *
            from preference_history
            order by created_at desc
            limit ?
            """,
            (max(1, min(int(limit), 1000)),),
        ).fetchall()
    return [row_to_dict(row) for row in rows]
