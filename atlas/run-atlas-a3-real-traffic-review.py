#!/usr/bin/env python3
"""Real-traffic review for A3 promotion evidence.

This is the counterpart to `run-atlas-a3-paraphrase-audit.py`, but for real
owner traffic instead of constructed paraphrases. Same house rule applies:
this script collects raw data and does NOT auto-grade it. A human (or a
disinterested session with no stake in the fixes being reviewed) must read
every answer by hand and record verdicts in a sibling write-up, exactly
like `atlas-a3-paraphrase-audit-20260915-reviewed.md`.

Gotcha this script exists to handle correctly (confirmed by reading
`atlas_app_store.py:save_exchange()` directly): the assistant reply's
`client` column is hardcoded to the literal string "atlas" regardless of
who asked. Real traffic can only be identified via the *user*-role row's
`client` (apple-app / atlas-mobile-pwa / shortcuts); the matching answer is
the next assistant-role row in the same conversation, not a client-filtered
assistant row (there are none to filter).

Every report this script writes contains REAL personal conversation text
(secrets are redacted; personal content is not). Reports belong under
ai-evals/reasoning/runs/real-traffic/, which is gitignored -- never commit
one of these.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timezone


DEFAULT_DB = pathlib.Path("/opt/stacks/ai-stack/atlas-app/atlas-app.db")
DEFAULT_CLIENTS = ("apple-app", "atlas-mobile-pwa", "shortcuts")

_EXPORT_MODULE_PATH = pathlib.Path(__file__).with_name("export-atlas-improvement-log.py")


def _load_redact():
    spec = importlib.util.spec_from_file_location("atlas_export_log", _EXPORT_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.redact


redact = _load_redact()


def connect(db_path: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def collect_real_exchanges(
    db_path: pathlib.Path,
    clients: tuple[str, ...],
    context_scope: str | None,
    since: str,
    until: str,
) -> list[dict]:
    placeholders = ",".join("?" for _ in clients)
    with connect(db_path) as conn:
        query = f"""
            select id, conversation_id, text, client, context_scope, created_at
            from messages
            where role = 'user'
              and client in ({placeholders})
              and created_at >= ?
              and created_at <= ?
        """
        params: list[str] = [*clients, since, until]
        if context_scope and context_scope != "all":
            query += " and context_scope = ?"
            params.append(context_scope)
        query += " order by created_at asc"
        user_rows = conn.execute(query, params).fetchall()

        exchanges = []
        for user_row in user_rows:
            # The matching answer is the next assistant-role row in the same
            # conversation -- NOT a client-filtered assistant row, since
            # save_exchange() hardcodes every assistant row's client to
            # "atlas" regardless of who asked.
            answer_row = conn.execute(
                """
                select text, route, tool, source, latency_ms, error, created_at
                from messages
                where conversation_id = ? and role = 'assistant' and created_at >= ?
                order by created_at asc
                limit 1
                """,
                (user_row["conversation_id"], user_row["created_at"]),
            ).fetchone()
            exchanges.append(
                {
                    "conversation_id": user_row["conversation_id"],
                    "client": user_row["client"],
                    "context_scope": user_row["context_scope"],
                    "question_created_at": user_row["created_at"],
                    "question": redact(user_row["text"] or ""),
                    "answer_found": answer_row is not None,
                    "answer": redact(answer_row["text"] or "") if answer_row else None,
                    "answer_created_at": answer_row["created_at"] if answer_row else None,
                    "route": answer_row["route"] if answer_row else None,
                    "tool": answer_row["tool"] if answer_row else None,
                    "source": answer_row["source"] if answer_row else None,
                    "latency_ms": answer_row["latency_ms"] if answer_row else None,
                    "error": answer_row["error"] if answer_row else None,
                }
            )
        return exchanges


def summarize(exchanges: list[dict]) -> dict:
    latencies = [e["latency_ms"] for e in exchanges if e["latency_ms"] is not None]
    return {
        "count": len(exchanges),
        "answer_missing_count": sum(1 for e in exchanges if not e["answer_found"]),
        "error_count": sum(1 for e in exchanges if e["error"]),
        "routes": dict(Counter(e["route"] or "none" for e in exchanges)),
        "tools": dict(Counter(e["tool"] or "none" for e in exchanges)),
        "clients": dict(Counter(e["client"] for e in exchanges)),
        "latency_ms": {
            "max": max(latencies) if latencies else None,
            "mean": round(statistics.mean(latencies), 1) if latencies else None,
            "p95": round(statistics.quantiles(latencies, n=20)[18], 1) if len(latencies) >= 20 else None,
        },
    }


def write_outputs(
    exchanges: list[dict],
    summary: dict,
    params: dict,
    output_json: pathlib.Path,
    output_md: pathlib.Path,
) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": params,
        "note": "Raw real-traffic export. NOT auto-graded -- read every answer by hand. Contains real personal text (secrets redacted only). Never commit this file.",
        "summary": summary,
        "exchanges": exchanges,
    }
    output_json.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# ATLAS A3 Real-Traffic Review (raw, ungraded)",
        "",
        "**This file contains real personal conversation text (secrets redacted only). Do not commit it.**",
        "",
        f"- Window: `{params['since']}` to `{params['until']}`",
        f"- Clients: `{params['clients']}`",
        f"- Context scope: `{params['context_scope']}`",
        f"- Exchanges: {summary['count']}",
        f"- Missing answer: {summary['answer_missing_count']}",
        f"- Errors: {summary['error_count']}",
        f"- Routes: `{summary['routes']}`",
        f"- Latency (ms): `{summary['latency_ms']}`",
        "",
        "No automated pass/fail: read every answer by hand and record verdicts in a sibling",
        "`-reviewed.md`, the same way `atlas-a3-paraphrase-audit-20260915-reviewed.md` does.",
        "",
        "## Exchanges",
        "",
    ]
    for exchange in exchanges:
        lines.extend(
            [
                f"### {exchange['question_created_at']} ({exchange['client']}, {exchange['context_scope']})",
                "",
                f"- Route/tool: `{exchange['route']}` / `{exchange['tool']}`",
                f"- Latency: `{exchange['latency_ms']}ms`",
                f"- Error: `{exchange['error']}`",
                "",
                f"Question: {exchange['question']}",
                "",
                "Answer:",
                "",
                (exchange["answer"] or "_No matching answer found_").strip(),
                "",
            ]
        )
    output_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    parser.add_argument("--since", required=True, help="ISO8601 start of the review window (inclusive)")
    parser.add_argument("--until", default=None, help="ISO8601 end of the review window (default: now, UTC)")
    parser.add_argument("--clients", default=",".join(DEFAULT_CLIENTS))
    parser.add_argument("--context-scope", default="personal", help="'personal', 'operations', 'admin', or 'all'")
    parser.add_argument("--output-json", type=pathlib.Path, required=True)
    parser.add_argument("--output-md", type=pathlib.Path, required=True)
    args = parser.parse_args()

    until = args.until or datetime.now(timezone.utc).isoformat(timespec="seconds")
    clients = tuple(c.strip() for c in args.clients.split(",") if c.strip())
    params = {"since": args.since, "until": until, "clients": list(clients), "context_scope": args.context_scope}

    exchanges = collect_real_exchanges(args.db, clients, args.context_scope, args.since, until)
    summary = summarize(exchanges)
    print(json.dumps(summary, indent=2, sort_keys=True))
    write_outputs(exchanges, summary, params, args.output_json, args.output_md)
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_md}")
    print("REMINDER: this report has NOT been graded and contains real personal text. Do not commit it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
