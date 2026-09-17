#!/usr/bin/env python3

"""Isolated school metadata/cache storage; never store client credentials or bodies."""

import json
import pathlib
import sqlite3
from datetime import datetime, timezone


MAIL_FIELDS = {
    "message_id",
    "sender",
    "subject",
    "received_at",
    "importance",
    "is_read",
    "source_updated_at",
}
CALENDAR_FIELDS = {
    "event_id",
    "title",
    "course",
    "starts_at",
    "ends_at",
    "due_at",
    "url",
    "status",
    "source_updated_at",
}
FORBIDDEN_FIELDS = {
    "attachment",
    "attachments",
    "body",
    "body_preview",
    "bodypreview",
    "content",
    "cookie",
    "cookies",
    "password",
    "token",
}
SOURCES = {"canvas_ical", "thunderbird"}


def connect(path: pathlib.Path | str) -> sqlite3.Connection:
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    return db


def init_db(path: pathlib.Path | str) -> None:
    with connect(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS sfsu_connection (
          owner_id TEXT PRIMARY KEY,
          tenant_id TEXT,
          account_hint TEXT,
          scopes TEXT NOT NULL,
          encrypted_token_blob BLOB,
          connected_at TEXT,
          last_sync_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sfsu_calendar_event (
          owner_id TEXT NOT NULL, event_id TEXT NOT NULL, payload_json TEXT NOT NULL,
          fetched_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'canvas_ical',
          PRIMARY KEY (owner_id, event_id)
        );
        CREATE TABLE IF NOT EXISTS sfsu_mail_header (
          owner_id TEXT NOT NULL, message_id TEXT NOT NULL, payload_json TEXT NOT NULL,
          fetched_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'thunderbird',
          PRIMARY KEY (owner_id, message_id)
        );
        """)
        for statement in (
            "ALTER TABLE sfsu_calendar_event ADD COLUMN source TEXT NOT NULL DEFAULT 'canvas_ical'",
            "ALTER TABLE sfsu_mail_header ADD COLUMN source TEXT NOT NULL DEFAULT 'thunderbird'",
        ):
            try:
                db.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_record(record: dict, allowed_fields: set[str], id_field: str) -> dict:
    if not isinstance(record, dict):
        raise ValueError("record must be an object")
    lowered = {str(key).lower() for key in record}
    forbidden = sorted(lowered & FORBIDDEN_FIELDS)
    if forbidden:
        raise ValueError(f"forbidden school metadata fields: {forbidden}")
    unexpected = sorted(set(record) - allowed_fields)
    if unexpected:
        raise ValueError(f"unapproved school metadata fields: {unexpected}")
    record_id = str(record.get(id_field) or "").strip()
    if not record_id or len(record_id) > 512:
        raise ValueError(f"{id_field} is required and must be at most 512 characters")
    normalized = dict(record)
    normalized[id_field] = record_id
    for key, value in normalized.items():
        if isinstance(value, str) and len(value) > 4096:
            raise ValueError(f"{key} exceeds 4096 characters")
        if isinstance(value, (dict, list)):
            raise ValueError(f"{key} must be a scalar value")
    return normalized


def import_records(
    path: pathlib.Path | str,
    owner_id: str,
    source: str,
    kind: str,
    records: list[dict],
    fetched_at: str | None = None,
) -> dict:
    if not owner_id or len(owner_id) > 128:
        raise ValueError("owner_id is required and must be at most 128 characters")
    if source not in SOURCES:
        raise ValueError("unsupported school bridge source")
    if not isinstance(records, list) or not records or len(records) > 100:
        raise ValueError("records must contain between 1 and 100 items")
    if kind == "calendar_event" and source != "canvas_ical":
        raise ValueError("calendar events must use canvas_ical")
    if kind == "mail_metadata" and source != "thunderbird":
        raise ValueError("mail metadata must use thunderbird")

    if kind == "calendar_event":
        table, id_field, allowed = "sfsu_calendar_event", "event_id", CALENDAR_FIELDS
    elif kind == "mail_metadata":
        table, id_field, allowed = "sfsu_mail_header", "message_id", MAIL_FIELDS
    else:
        raise ValueError("unsupported school bridge record kind")

    normalized = [_validate_record(record, allowed, id_field) for record in records]
    stamp = fetched_at or _now()
    with connect(path) as db:
        for record in normalized:
            db.execute(
                f"""
                INSERT INTO {table}(owner_id, {id_field}, payload_json, fetched_at, source)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(owner_id, {id_field}) DO UPDATE SET
                  payload_json = excluded.payload_json,
                  fetched_at = excluded.fetched_at,
                  source = excluded.source
                """,
                (owner_id, record[id_field], json.dumps(record, sort_keys=True), stamp, source),
            )
    return {"kind": kind, "source": source, "accepted": len(normalized), "fetched_at": stamp}


def bridge_status(path: pathlib.Path | str, owner_id: str) -> dict:
    with connect(path) as db:
        calendar = db.execute(
            "SELECT COUNT(*) AS count, MAX(fetched_at) AS latest FROM sfsu_calendar_event WHERE owner_id = ?",
            (owner_id,),
        ).fetchone()
        mail = db.execute(
            "SELECT COUNT(*) AS count, MAX(fetched_at) AS latest FROM sfsu_mail_header WHERE owner_id = ?",
            (owner_id,),
        ).fetchone()
    return {
        "owner_id": owner_id,
        "calendar": {"count": calendar["count"], "latest_fetched_at": calendar["latest"]},
        "mail": {"count": mail["count"], "latest_fetched_at": mail["latest"]},
    }


def purge_owner(path: pathlib.Path | str, owner_id: str) -> None:
    with connect(path) as db:
        for table in ("sfsu_connection", "sfsu_calendar_event", "sfsu_mail_header"):
            db.execute(f"DELETE FROM {table} WHERE owner_id = ?", (owner_id,))
