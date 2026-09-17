#!/usr/bin/env python3
"""Fixture test for run-atlas-a3-real-traffic-review.py's query/join logic.

Seeds a temp SQLite DB matching atlas_app_store.py's real schema (not a
simplified stand-in) and checks: the client filter only matches real-device
user rows; the user-row-to-next-assistant-row join works despite the
assistant row's client always being the literal "atlas"; a user row with no
matching assistant row is reported as answer_found=False instead of being
silently dropped or crashing; and secrets in real text get redacted.
"""

import importlib.util
import pathlib
import sqlite3
import sys
import tempfile


MODULE_PATH = pathlib.Path(__file__).with_name("run-atlas-a3-real-traffic-review.py")
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("real_traffic_review", MODULE_PATH)
real_traffic_review = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(real_traffic_review)


SCHEMA = """
create table conversations (
  id text primary key, title text not null, context_scope text not null default 'personal',
  created_at text not null, updated_at text not null, archived_at text
);
create table messages (
  id text primary key, conversation_id text not null references conversations(id),
  role text not null check (role in ('user', 'assistant', 'system')),
  text text not null, speak_text text, route text, tool text, source text, input_mode text,
  client text, context_scope text not null default 'personal', latency_ms integer, error text,
  created_at text not null
);
"""


def seed(db_path: pathlib.Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.execute(
        "insert into conversations values ('c1','t','personal','2026-09-16T01:00:00+00:00','2026-09-16T01:00:00+00:00',null)"
    )
    # Real traffic: user asks, assistant answers (client hardcoded to "atlas" on the answer row).
    conn.execute(
        "insert into messages values ('m1','c1','user','Is my API token abc12345678901234567890123456789012 valid?',"
        "null,null,null,null,'typed','apple-app','personal',null,null,'2026-09-16T01:00:00+00:00')"
    )
    conn.execute(
        "insert into messages values ('m2','c1','assistant','Yes, that token pattern looks valid.',"
        "'Yes.','general_reference','openwebui-chat','ATLAS','typed','atlas','personal',350,null,'2026-09-16T01:00:05+00:00')"
    )
    # Synthetic traffic in the SAME window: must be excluded by the client filter.
    conn.execute(
        "insert into conversations values ('c2','t','personal','2026-09-16T01:05:00+00:00','2026-09-16T01:05:00+00:00',null)"
    )
    conn.execute(
        "insert into messages values ('m3','c2','user','synthetic probe question',null,null,null,null,'typed',"
        "'claude-def026-verify','personal',null,null,'2026-09-16T01:05:00+00:00')"
    )
    conn.execute(
        "insert into messages values ('m4','c2','assistant','synthetic answer',null,'countdown','local-calendar-math',"
        "'Built-in date math','typed','atlas','personal',10,null,'2026-09-16T01:05:01+00:00')"
    )
    # Real user row with NO matching assistant row (e.g. a request that errored before saving a reply).
    conn.execute(
        "insert into conversations values ('c3','t','personal','2026-09-16T01:10:00+00:00','2026-09-16T01:10:00+00:00',null)"
    )
    conn.execute(
        "insert into messages values ('m5','c3','user','question with no answer',null,null,null,null,'typed',"
        "'atlas-mobile-pwa','personal',null,null,'2026-09-16T01:10:00+00:00')"
    )
    conn.commit()
    conn.close()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = pathlib.Path(tmp) / "atlas-app.db"
        seed(db_path)

        exchanges = real_traffic_review.collect_real_exchanges(
            db_path,
            clients=("apple-app", "atlas-mobile-pwa", "shortcuts"),
            context_scope="personal",
            since="2026-09-16T00:00:00+00:00",
            until="2026-09-16T23:59:59+00:00",
        )

        # Exactly the two real-client user rows, synthetic ('claude-def026-verify') excluded.
        assert len(exchanges) == 2, exchanges
        by_conv = {e["conversation_id"]: e for e in exchanges}
        assert "c2" not in by_conv, "synthetic traffic must be excluded by the client filter"

        # The real answer was correctly joined despite the assistant row's client being "atlas".
        answered = by_conv["c1"]
        assert answered["answer_found"] is True, answered
        assert answered["route"] == "general_reference", answered
        assert "abc12345678901234567890123456789012" not in answered["question"], answered
        assert "[REDACTED]" in answered["question"], answered

        # A real question with no matching assistant row is reported, not dropped or crashed on.
        unanswered = by_conv["c3"]
        assert unanswered["answer_found"] is False, unanswered
        assert unanswered["answer"] is None, unanswered

        summary = real_traffic_review.summarize(exchanges)
        assert summary["count"] == 2, summary
        assert summary["answer_missing_count"] == 1, summary

    print("PASS: A3 real-traffic review query/join/redaction fixture test")


if __name__ == "__main__":
    main()
