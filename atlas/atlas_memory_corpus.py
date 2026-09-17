#!/usr/bin/env python3

"""Assemble ATLAS personal-memory content (preferences + personal-scope notes)
into a corpus directory for import into an Open WebUI knowledge collection.

Pure functions only -- this module is imported directly by
atlas_shortcuts_server.py, not run as a standalone CLI. The actual Open WebUI
upload is delegated to import_openwebui_corpus.py (invoked as a subprocess),
reused unmodified.

Content sources are deliberately narrow: explicit preferences and
personal-scope notes only, both created by an explicit user statement
("I prefer X", "remember that X"). No conversation summarization and no
reminders/tasks -- those are transient/actionable, not durable facts about
the owner, and mixing them in would reintroduce the ungrounded-inference risk
this design exists to avoid.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re

import atlas_app_store


def memory_items(db_path: pathlib.Path) -> list[dict]:
    """Return normalized memory items: explicit preferences and personal-scope notes."""
    items = []
    for pref in atlas_app_store.list_preferences(db_path):
        items.append({
            "type": "preference",
            "id": pref["key"],
            "label": pref["key"],
            "text": pref["value"],
            "updated_at": pref["updated_at"],
        })
    for note in atlas_app_store.list_notes(db_path, limit=500, context_scope="personal"):
        items.append({
            "type": "note",
            "id": note["id"],
            "label": note["title"],
            "text": note["text"],
            "updated_at": note["updated_at"],
        })
    return items


def compute_fingerprint(items: list[dict]) -> str:
    """Cheap change-detection key: identity + last-updated only, not full text."""
    stable = sorted((item["type"], item["id"], item["updated_at"]) for item in items)
    encoded = json.dumps(stable, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_SAFE_ID_RE = re.compile(r"[^a-z0-9_-]+")


def _safe_filename(item: dict) -> str:
    slug = _SAFE_ID_RE.sub("-", str(item["id"]).lower()).strip("-") or "item"
    return f"{item['type']}-{slug}.md"


def write_corpus(items: list[dict], corpus_dir: pathlib.Path) -> int:
    """Wipe and rewrite corpus_dir with one file per memory item. Returns file count."""
    if corpus_dir.exists():
        for existing in corpus_dir.glob("*.md"):
            existing.unlink()
    corpus_dir.mkdir(parents=True, exist_ok=True)
    for item in items:
        path = corpus_dir / _safe_filename(item)
        header = f"Type: {item['type']}\nSaved: {item['updated_at']}\n\n"
        path.write_text(header + item["text"].strip() + "\n", encoding="utf-8")
    return len(items)


def write_state_cache(
    state_path: pathlib.Path,
    items: list[dict],
    fingerprint: str,
    knowledge_id: str | None,
) -> None:
    payload = {
        "fingerprint": fingerprint,
        "item_count": len(items),
        "synced_at": atlas_app_store.now_iso(),
        "knowledge_id": knowledge_id,
        "items": [
            {
                "type": item["type"],
                "label": item["label"],
                "text": item["text"],
                "updated_at": item["updated_at"],
            }
            for item in items
        ],
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_state_cache(state_path: pathlib.Path) -> dict:
    """Never raises. A missing or corrupt file fails closed to "no memory"."""
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"item_count": 0, "items": [], "fingerprint": None, "knowledge_id": None}


# -- Stage 4: blended context (cheap, in-process, no network call) ----------
#
# Used by run-atlas-readonly.py's blended general-answer tier. Kept as pure
# functions here (rather than duplicated as a method the way tier 1's
# deterministic handler in atlas_shortcuts_server.py does it) since this
# runs from a separate process with only the state-cache file, not a live
# database connection.

MEMORY_STOPWORDS = {
    "the", "a", "an", "is", "are", "do", "did", "you", "i", "my", "me", "to", "of", "in",
    "on", "for", "that", "this", "about", "what", "know", "tell", "say", "said", "says",
    "mention", "mentioned", "remember", "ever", "have", "has", "was", "were", "with", "and",
}


def memory_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9']+", text.lower())
        if token not in MEMORY_STOPWORDS and len(token) > 2
    }


def blended_context_block(question: str, items: list[dict], limit: int = 3) -> str | None:
    """Return a small provenance-labeled block only when the question's
    tokens overlap with something explicitly saved -- never a summary, never
    inferred. The common case (no overlap) costs a handful of set
    intersections and returns None.
    """
    question_tokens = memory_tokens(question)
    if not question_tokens:
        return None
    matches = []
    for item in items:
        item_tokens = memory_tokens(f"{item.get('label', '')} {item.get('text', '')}")
        if question_tokens & item_tokens:
            label = "Preference" if item.get("type") == "preference" else "Note"
            saved = str(item.get("updated_at") or "")[:10]
            matches.append(f"{label} ({item.get('label', '')}, saved {saved}): {item.get('text', '')}")
    if not matches:
        return None
    return "\n".join(matches[:limit])
