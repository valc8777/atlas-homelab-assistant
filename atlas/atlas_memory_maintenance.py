#!/usr/bin/env python3
"""Non-destructive ATLAS personal-memory maintenance report for R6."""

from __future__ import annotations

import pathlib
import re
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

import atlas_app_store
import atlas_hybrid_retrieval
import atlas_memory_conflicts
import atlas_semantic_index


_WORD = re.compile(r"[a-z0-9]+")
_REHEARSAL_VOCAB = ("note", "preference", "personal", "operations", "admin", "atlas")
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
BACKUP_POLICY_MAX_OBSERVATION_AGE_HOURS = 24
BACKUP_POLICY_MAX_SUCCESS_AGE_HOURS = 48


def _norm(value: str) -> str:
    return " ".join(_WORD.findall(str(value or "").lower()))


def _duplicate_notes(notes: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for note in notes:
        key = _norm(f"{note.get('title', '')} {note.get('text', '')}")
        if key:
            groups.setdefault(key, []).append(note)
    duplicates = []
    for group in groups.values():
        if len(group) <= 1:
            continue
        duplicates.append({
            "kind": "duplicate_notes",
            "affected_ids": sorted(str(item["id"]) for item in group),
            "count": len(group),
            "recommendation": "review_for_merge_or_retirement",
        })
    return sorted(duplicates, key=lambda item: item["affected_ids"])


def _history_summary(history: list[dict]) -> dict:
    statuses: dict[str, int] = {}
    for item in history:
        statuses[item.get("status") or "unknown"] = statuses.get(item.get("status") or "unknown", 0) + 1
    return {
        "history_count": len(history),
        "statuses": statuses,
        "affected_ids": sorted(str(item["id"]) for item in history),
    }


def _rehearsal_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic, offline embedder for the rebuild rehearsal only.

    Never calls the real Ollama embedder -- this report must stay fast,
    network-free, and reproducible, not represent the production index.
    """
    return [
        [float(text.lower().count(word)) for word in _REHEARSAL_VOCAB]
        for text in texts
    ]


def _embedding_rebuild_rehearsal(db_path) -> dict:
    """Prove index rebuild never touches canonical memory, without touching
    the real disposable index either -- this always rebuilds into a throwaway
    temp path with a fake embedder."""
    records = atlas_semantic_index.authorized_records(db_path)
    scopes: dict[str, int] = {}
    for record in records:
        scopes[record["context_scope"]] = scopes.get(record["context_scope"], 0) + 1
    with tempfile.TemporaryDirectory() as temp_dir:
        rehearsal_path = pathlib.Path(temp_dir) / "rehearsal-semantic.db"
        atlas_semantic_index.rebuild_index(
            rehearsal_path, records, _rehearsal_embed, model="rehearsal-v1"
        )
    return {
        "kind": "embedding_index_rebuild_rehearsal",
        "affected_ids": [],
        "records_indexed": len(records),
        "scopes": scopes,
        "canonical_memory_mutated": False,
        "recommendation": "safe_to_rebuild_from_canonical_records",
    }


def _record_id(record_type: str, record: dict) -> str:
    return f"{record_type}:{record.get('id') or record.get('key')}"


def _candidate_id(record: dict) -> str | None:
    record_type = record.get("record_type")
    if record_type == "note":
        return f"note:{record['id']}"
    if record_type == "preference":
        return f"preference:{record['id']}"
    return None


def _memory_records(preferences: list[dict], notes: list[dict], history: list[dict]) -> list[dict]:
    records = []
    for preference in preferences:
        records.append({
            "record_type": "preference",
            "id": preference["key"],
            "context_scope": "personal",
            "source": "preference",
            "updated_at": preference.get("updated_at"),
        })
    for note in notes:
        records.append({
            "record_type": "note",
            "id": note["id"],
            "context_scope": note.get("context_scope") or "personal",
            "source": note.get("source_type") or ("message" if note.get("source_message_id") else "manual_note"),
            "updated_at": note.get("updated_at"),
            "expires_at": note.get("expires_at"),
            "fact_lifecycle": note.get("fact_lifecycle"),
            "import_batch_id": note.get("import_batch_id"),
            "import_confidence": note.get("import_confidence"),
            "review_status": note.get("review_status"),
        })
    for item in history:
        records.append({
            "record_type": "preference_history",
            "id": item["id"],
            "context_scope": item.get("context_scope") or "personal",
            "source": item.get("source") or "preference_history",
            "updated_at": item.get("rolled_back_at") or item.get("created_at"),
        })
    return records


def _group_counts(records: list[dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value = str(record.get(field) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _export_plan(records: list[dict]) -> dict:
    """Describe what a scoped export would include without writing a file."""
    affected_ids = sorted(_record_id(record["record_type"], record) for record in records)
    return {
        "kind": "export_dry_run_plan",
        "affected_ids": affected_ids,
        "record_count": len(records),
        "by_scope": _group_counts(records, "context_scope"),
        "by_source": _group_counts(records, "source"),
        "by_entity": {"unlinked": len(records)},
        "would_write_export": False,
        "recommendation": "owner_review_then_create_explicit_export_artifact",
    }


def _purge_plan(records: list[dict], duplicate_findings: list[dict], conflict_findings: list[dict]) -> dict:
    """Describe purge candidates only; never deletes or archives records."""
    duplicate_ids = {
        affected_id
        for finding in duplicate_findings
        for affected_id in finding["affected_ids"][1:]
    }
    conflict_ids = {
        affected_id
        for finding in conflict_findings
        for affected_id in finding["affected_ids"]
    }
    candidate_ids = sorted(duplicate_ids)
    protected_ids = sorted(
        _record_id(record["record_type"], record)
        for record in records
        if _record_id(record["record_type"], record) not in candidate_ids
    )
    return {
        "kind": "purge_dry_run_plan",
        "affected_ids": candidate_ids,
        "record_count": len(candidate_ids),
        "protected_record_count": len(protected_ids),
        "blocked_conflict_ids": sorted(conflict_ids),
        "would_delete": False,
        "would_archive": False,
        "recommendation": "purge_requires_owner_confirmation_and_conflict_resolution",
    }


def _retrieval_use_tracking(records: list[dict], audit_events: list[dict]) -> dict:
    """Report retrieval use from privacy-preserving record fingerprints."""
    retrieval_events = [
        event for event in audit_events
        if str(event.get("event_type") or "").startswith("hybrid_retrieval")
        or event.get("event_type") == "memory.recall"
    ]
    matched_events = [
        event for event in retrieval_events
        if (event.get("details") or {}).get("status") == "matched"
        or (event.get("details") or {}).get("candidate_count", 0) > 0
    ]
    observed_fingerprints = set()
    fingerprint_events = 0
    for event in retrieval_events:
        details = event.get("details") or {}
        fingerprints = details.get("record_usage_fingerprints") or []
        if fingerprints:
            fingerprint_events += 1
        observed_fingerprints.update(str(item) for item in fingerprints if isinstance(item, str))
    trackable_records = []
    for record in records:
        candidate_id = _candidate_id(record)
        fingerprint = (
            atlas_hybrid_retrieval.usage_fingerprint(candidate_id, record["context_scope"])
            if candidate_id else None
        )
        if fingerprint:
            trackable_records.append({**record, "candidate_id": candidate_id, "usage_fingerprint": fingerprint})
    never_retrieved_ids = sorted(
        _record_id(record["record_type"], record)
        for record in trackable_records
        if record["usage_fingerprint"] not in observed_fingerprints
    )
    all_supported_events_have_fingerprints = len(retrieval_events) == fingerprint_events if retrieval_events else False
    return {
        "kind": "retrieval_use_tracking",
        "affected_ids": never_retrieved_ids,
        "record_count": len(records),
        "trackable_record_count": len(trackable_records),
        "never_retrieved_count": len(never_retrieved_ids),
        "retrieval_event_count": len(retrieval_events),
        "matched_retrieval_event_count": len(matched_events),
        "fingerprinted_retrieval_event_count": fingerprint_events,
        "can_identify_never_retrieved_records": bool(trackable_records),
        "historical_coverage_complete": all_supported_events_have_fingerprints,
        "privacy_boundary": "audit stores stable fingerprints for notes/preferences, not raw record IDs or content",
        "recommendation": (
            "review_exact_never_retrieved_records"
            if trackable_records else
            "add_trackable_record_types_before_classifying_never_retrieved"
        ),
    }


def _backup_policy_check(db_path, *, now_value: datetime | None = None) -> dict:
    """Read the cached backup signal; never probes or mutates infrastructure."""
    now_value = now_value or datetime.now(LOCAL_TZ)
    observation = atlas_app_store.get_signal_observation(db_path, "backup_status")
    base = {
        "kind": "backup_policy_check",
        "affected_ids": [],
        "policy": {
            "max_observation_age_hours": BACKUP_POLICY_MAX_OBSERVATION_AGE_HOURS,
            "max_success_age_hours": BACKUP_POLICY_MAX_SUCCESS_AGE_HOURS,
            "source": "cached_signal_observation_only",
        },
        "would_trigger_backup": False,
        "recommendation": "review_backup_signal_before_memory_cleanup",
    }
    if not observation:
        return {
            **base,
            "status": "unavailable",
            "reason": "backup_status_observation_missing",
            "latest_ok": None,
            "last_success_age_hours": None,
            "observation_age_hours": None,
        }
    if observation.get("status") != "ok":
        return {
            **base,
            "status": "unavailable",
            "reason": "backup_status_observation_error",
            "latest_ok": None,
            "last_success_age_hours": None,
            "observation_age_hours": None,
        }
    try:
        observed_at = datetime.fromisoformat(observation["observed_at"])
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=LOCAL_TZ)
        observation_age_hours = (
            now_value.astimezone(LOCAL_TZ) - observed_at.astimezone(LOCAL_TZ)
        ).total_seconds() / 3600
    except (TypeError, ValueError, KeyError):
        return {
            **base,
            "status": "unavailable",
            "reason": "backup_status_timestamp_invalid",
            "latest_ok": None,
            "last_success_age_hours": None,
            "observation_age_hours": None,
        }
    value = observation.get("value")
    if not isinstance(value, dict):
        return {
            **base,
            "status": "unavailable",
            "reason": "backup_status_value_invalid",
            "latest_ok": None,
            "last_success_age_hours": None,
            "observation_age_hours": round(observation_age_hours, 2),
        }
    latest_ok = value.get("latest_ok")
    success_age = value.get("last_success_age_hours")
    if not isinstance(latest_ok, bool) or (
        success_age is not None and (isinstance(success_age, bool) or not isinstance(success_age, (int, float)))
    ):
        return {
            **base,
            "status": "unavailable",
            "reason": "backup_status_shape_invalid",
            "latest_ok": latest_ok if isinstance(latest_ok, bool) else None,
            "last_success_age_hours": None,
            "observation_age_hours": round(observation_age_hours, 2),
        }
    stale_observation = observation_age_hours < 0 or observation_age_hours > BACKUP_POLICY_MAX_OBSERVATION_AGE_HOURS
    stale_success = success_age is None or float(success_age) > BACKUP_POLICY_MAX_SUCCESS_AGE_HOURS
    status = "ok" if latest_ok and not stale_observation and not stale_success else "attention"
    reasons = []
    if not latest_ok:
        reasons.append("latest_backup_not_ok")
    if stale_observation:
        reasons.append("backup_observation_stale_or_future")
    if stale_success:
        reasons.append("last_success_too_old_or_unknown")
    return {
        **base,
        "status": status,
        "reason": "ok" if status == "ok" else ",".join(reasons),
        "latest_ok": latest_ok,
        "last_success_age_hours": None if success_age is None else round(float(success_age), 2),
        "observation_age_hours": round(observation_age_hours, 2),
    }


def _metadata_quality_findings(records: list[dict], *, now_value: datetime | None = None) -> tuple[list[dict], dict]:
    """Classify metadata-backed quality issues and report remaining gaps."""
    now_value = now_value or datetime.now(LOCAL_TZ)
    expired_ids = []
    expiration_gap_ids = []
    low_confidence_ids = []
    import_gap_ids = []
    for record in records:
        record_id = _record_id(record["record_type"], record)
        if record["record_type"] != "note":
            expiration_gap_ids.append(record_id)
            import_gap_ids.append(record_id)
            continue
        expires_at = record.get("expires_at")
        lifecycle = record.get("fact_lifecycle")
        if expires_at:
            try:
                parsed = datetime.fromisoformat(str(expires_at))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=LOCAL_TZ)
                if parsed.astimezone(LOCAL_TZ) < now_value.astimezone(LOCAL_TZ):
                    expired_ids.append(record_id)
            except ValueError:
                expiration_gap_ids.append(record_id)
        elif lifecycle in {"expired", "stale", "retired"}:
            expired_ids.append(record_id)
        else:
            expiration_gap_ids.append(record_id)

        confidence = record.get("import_confidence")
        review_status = record.get("review_status")
        source = record.get("source")
        if confidence is None and not review_status and not record.get("import_batch_id"):
            import_gap_ids.append(record_id)
            continue
        if (
            isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and float(confidence) < 0.75
        ) or review_status in {"needs_review", "rejected", "unreviewed"}:
            low_confidence_ids.append(record_id)
        elif source in {"import", "imported"} and confidence is None:
            import_gap_ids.append(record_id)

    findings = []
    if expired_ids:
        findings.append({
            "kind": "expired_facts",
            "affected_ids": sorted(expired_ids),
            "record_count": len(expired_ids),
            "recommendation": "review_expired_facts_for_update_or_retirement",
        })
    findings.append({
        "kind": "expired_facts_schema_gap",
        "affected_ids": sorted(expiration_gap_ids),
        "record_count": len(expiration_gap_ids),
        "can_identify_expired_facts": bool(expired_ids),
        "missing_fields": ["expires_at", "valid_until", "fact_lifecycle"],
        "recommendation": "add_expiration_metadata_before_classifying_remaining_records",
    })
    if low_confidence_ids:
        findings.append({
            "kind": "low_confidence_imports",
            "affected_ids": sorted(low_confidence_ids),
            "record_count": len(low_confidence_ids),
            "recommendation": "review_or_reimport_low_confidence_records",
        })
    findings.append({
        "kind": "low_confidence_imports_schema_gap",
        "affected_ids": sorted(import_gap_ids),
        "record_count": len(import_gap_ids),
        "can_identify_low_confidence_imports": bool(low_confidence_ids),
        "missing_fields": ["source_type", "import_batch_id", "import_confidence", "review_status"],
        "recommendation": "add_import_provenance_and_review_metadata_before_classifying_remaining_records",
    })
    return findings, {
        "expired_fact_candidates": len(expired_ids),
        "low_confidence_import_candidates": len(low_confidence_ids),
    }


def build_maintenance_report(db_path) -> dict:
    """Return a dry-run report only. This function never mutates memory."""
    preferences = atlas_app_store.list_preferences(db_path)
    notes = atlas_app_store.list_notes(db_path, limit=500, context_scope="personal")
    history = atlas_app_store.list_preference_history(db_path)
    audit_events = atlas_app_store.list_audit_events(db_path, limit=1000)
    conflicts = atlas_memory_conflicts.detect_memory_conflicts("", preferences, notes)
    conflict_findings = [
        {
            "kind": "unresolved_conflict",
            "subject": item["subject"],
            "affected_ids": sorted(str(fact["record_id"]) for fact in item["facts"]),
            "record_count": item["record_count"],
            "recommendation": "ask_owner_which_fact_should_supersede",
        }
        for item in conflicts
    ]
    duplicate_findings = _duplicate_notes(notes)
    rehearsal_finding = _embedding_rebuild_rehearsal(db_path)
    records = _memory_records(preferences, notes, history)
    export_plan = _export_plan(records)
    purge_plan = _purge_plan(records, duplicate_findings, conflict_findings)
    retrieval_use = _retrieval_use_tracking(records, audit_events)
    backup_policy = _backup_policy_check(db_path)
    metadata_findings, metadata_counts = _metadata_quality_findings(records)
    findings = sorted(
        [
            *conflict_findings,
            *duplicate_findings,
            *metadata_findings,
            rehearsal_finding,
            export_plan,
            purge_plan,
            retrieval_use,
            backup_policy,
        ],
        key=lambda item: (item["kind"], item.get("subject") or "", item["affected_ids"]),
    )
    return {
        "status": "completed",
        "dry_run": True,
        "context_scope": "personal",
        "counts": {
            "preferences": len(preferences),
            "notes": len(notes),
            "exportable_records": export_plan["record_count"],
            "findings": len(findings),
            "purge_candidate_records": purge_plan["record_count"],
            "retrieval_events": retrieval_use["retrieval_event_count"],
            "matched_retrieval_events": retrieval_use["matched_retrieval_event_count"],
            "fingerprinted_retrieval_events": retrieval_use["fingerprinted_retrieval_event_count"],
            "never_retrieved_records": retrieval_use["never_retrieved_count"],
            "backup_policy_status": backup_policy["status"],
            **metadata_counts,
            "unresolved_conflicts": len(conflict_findings),
            "duplicate_note_groups": len(duplicate_findings),
            **_history_summary(history),
        },
        "findings": findings,
        "unsupported_checks": [
            "orphaned_aliases",
        ],
    }
