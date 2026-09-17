#!/usr/bin/env python3
"""Read-only conflict detection for ATLAS personal memory R5."""

from __future__ import annotations

import re


_WORD = re.compile(r"[a-z0-9]+")
_FAVORITE = re.compile(r"\bmy\s+favorite\s+([a-z0-9 _-]{2,60}?)\s+is\s+(.+?)\s*[.!?]*$", re.IGNORECASE)
_MY_IS = re.compile(r"\bmy\s+([a-z0-9 _-]{2,60}?)\s+is\s+(.+?)\s*[.!?]*$", re.IGNORECASE)
_CORRECTION_PREFIX = re.compile(
    r"^\s*(?:that(?:'s| is)\s+wrong|wrong|no|actually|correction)[:,.\s-]*(?P<fact>.+?)\s*$",
    re.IGNORECASE,
)


def _normalize_words(value: str) -> str:
    return " ".join(_WORD.findall(str(value).lower()))


def _value_norm(value: str) -> str:
    return _normalize_words(value)[:160]


def _subject_norm(value: str) -> str:
    text = _normalize_words(value)
    text = re.sub(r"^(?:favorite|preferred)\s+", "", text)
    return text[:120]


def _tokens(value: str) -> set[str]:
    return set(_WORD.findall(str(value).lower()))


def _note_fact(note: dict) -> dict | None:
    text = str(note.get("text") or "").strip()
    title = str(note.get("title") or "").strip()
    for candidate in (text, title):
        for pattern in (_FAVORITE, _MY_IS):
            match = pattern.search(candidate)
            if not match:
                continue
            subject, value = match.group(1).strip(), match.group(2).strip()
            if subject and value:
                return {
                    "record_type": "note",
                    "record_id": note.get("id") or title,
                    "label": title or subject,
                    "subject": _subject_norm(subject),
                    "value": value,
                    "value_norm": _value_norm(value),
                    "updated_at": note.get("updated_at") or note.get("created_at") or "",
                }
    return None


def _preference_fact(preference: dict) -> dict | None:
    key = str(preference.get("key") or "").strip()
    value = str(preference.get("value") or "").strip()
    if not key or not value:
        return None
    return {
        "record_type": "preference",
        "record_id": key,
        "label": key,
        "subject": _subject_norm(key),
        "value": value,
        "value_norm": _value_norm(value),
        "updated_at": preference.get("updated_at") or "",
    }


def detect_memory_conflicts(question: str, preferences: list[dict], notes: list[dict]) -> list[dict]:
    """Return same-subject active personal facts with disagreeing values."""
    question_tokens = _tokens(question)
    facts = []
    for preference in preferences:
        fact = _preference_fact(preference)
        if fact:
            facts.append(fact)
    for note in notes:
        fact = _note_fact(note)
        if fact:
            facts.append(fact)

    by_subject: dict[str, list[dict]] = {}
    for fact in facts:
        if not fact["subject"]:
            continue
        subject_tokens = _tokens(fact["subject"])
        if question_tokens and subject_tokens and not (question_tokens & subject_tokens):
            continue
        by_subject.setdefault(fact["subject"], []).append(fact)

    conflicts = []
    for subject, subject_facts in sorted(by_subject.items()):
        values = {fact["value_norm"] for fact in subject_facts if fact["value_norm"]}
        if len(values) <= 1:
            continue
        conflicts.append({
            "subject": subject,
            "facts": subject_facts,
            "value_count": len(values),
            "record_count": len(subject_facts),
        })
    return conflicts


def conflict_metrics(conflicts: list[dict]) -> dict:
    return {
        "conflict_count": len(conflicts),
        "record_count": sum(item.get("record_count", 0) for item in conflicts),
        "subjects": len({item.get("subject") for item in conflicts if item.get("subject")}),
    }


def parse_owner_correction(text: str) -> dict | None:
    match = _CORRECTION_PREFIX.match(str(text or "").strip())
    if not match:
        return None
    fact = match.group("fact").strip()
    for pattern in (_FAVORITE, _MY_IS):
        fact_match = pattern.search(fact)
        if not fact_match:
            continue
        subject, value = fact_match.group(1).strip(), fact_match.group(2).strip()
        if subject and value:
            return {
                "subject": _subject_norm(subject),
                "value": " ".join(value.split()).rstrip(".!?"),
            }
    return {"needs_replacement": True}
