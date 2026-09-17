#!/usr/bin/env python3

import argparse
import json
import pathlib
import re
import sqlite3
import sys


DEFAULT_DB = pathlib.Path("/opt/stacks/ai-stack/atlas-app/atlas-app.db")
SECRET_PATTERNS = [
    re.compile(r"(?i)\b(bearer|token|api[_ -]?key|password|secret)\b\s*[:=]\s*\S+"),
    re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
]


def redact(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def connect(db_path: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def export_examples(db_path: pathlib.Path, limit: int) -> list[dict]:
    with connect(db_path) as conn:
        conversations = conn.execute(
            """
            select id, title, created_at, updated_at
            from conversations
            where archived_at is null
            order by updated_at desc
            limit ?
            """,
            (limit,),
        ).fetchall()

        examples = []
        for conversation in conversations:
            messages = conn.execute(
                """
                select role, text, route, tool, source, latency_ms, error, created_at
                from messages
                where conversation_id = ?
                order by created_at asc
                """,
                (conversation["id"],),
            ).fetchall()
            examples.append(
                {
                    "conversation_id": conversation["id"],
                    "title": redact(conversation["title"]),
                    "created_at": conversation["created_at"],
                    "updated_at": conversation["updated_at"],
                    "messages": [
                        {
                            "role": row["role"],
                            "text": redact(row["text"]),
                            "route": row["route"],
                            "tool": row["tool"],
                            "source": row["source"],
                            "latency_ms": row["latency_ms"],
                            "error": row["error"],
                            "created_at": row["created_at"],
                        }
                        for row in messages
                    ],
                }
            )
        return examples


def main() -> int:
    parser = argparse.ArgumentParser(description="Export recent ATLAS chats as JSONL improvement examples.")
    parser.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()

    examples = export_examples(args.db, args.limit)
    lines = [json.dumps(example, ensure_ascii=False) for example in examples]
    payload = "\n".join(lines)
    if payload:
        payload += "\n"

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
