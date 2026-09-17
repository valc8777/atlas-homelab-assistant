#!/usr/bin/env python3
"""Freshness-gated structured current-state candidates for ATLAS R3.

Reads only the existing operations-scope Phase 2b observation cache. It does
not probe hosts, call Home Assistant, or execute capabilities. Stale, future,
failed, malformed, and missing observations produce notices, never candidates.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import atlas_app_store


LOCAL_TZ = ZoneInfo("America/Los_Angeles")
DEFAULT_MAX_AGE_SECONDS = 900
_CURRENT_CUE = re.compile(
    r"\b(?:current(?:ly)?|right now|status|online|offline|up|down|healthy|unhealthy|"
    r"reachable|unreachable|latest|last|recent)\b",
    re.IGNORECASE,
)
_SOURCE_PATTERNS = {
    "service_reachability": re.compile(
        r"\b(?:service|services|homepage|nextcloud|jellyfin|navidrome|kavita|"
        r"open webui|piper|pbs|proxmox)\b",
        re.IGNORECASE,
    ),
    "host_health": re.compile(
        r"\b(?:host|hosts|server|servers|docker[- ]main|ai[- ]stack|proxmox|"
        r"load|memory|disk)\b",
        re.IGNORECASE,
    ),
    "backup_status": re.compile(r"\b(?:backup|backups|vzdump|pbs)\b", re.IGNORECASE),
}


def current_state_requested(query: str, context_scope: str) -> bool:
    """Return true only for a scoped request covered by this adapter."""
    return bool(
        context_scope == "operations"
        and isinstance(query, str)
        and _CURRENT_CUE.search(query)
        and any(pattern.search(query) for pattern in _SOURCE_PATTERNS.values())
    )


def _fresh_observation(
    db_path: Path,
    signal_name: str,
    *,
    now_value: datetime,
    max_age_seconds: int,
) -> tuple[dict | None, dict | None]:
    observation = atlas_app_store.get_signal_observation(db_path, signal_name)
    if not observation:
        return None, {"source": signal_name, "status": "unavailable"}
    if observation.get("status") != "ok":
        return None, {"source": signal_name, "status": "error"}
    try:
        observed_at = datetime.fromisoformat(observation["observed_at"])
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=LOCAL_TZ)
        age_seconds = (
            now_value.astimezone(LOCAL_TZ) - observed_at.astimezone(LOCAL_TZ)
        ).total_seconds()
    except (TypeError, ValueError, KeyError):
        return None, {"source": signal_name, "status": "invalid_timestamp"}
    if age_seconds < 0:
        return None, {"source": signal_name, "status": "future"}
    if age_seconds > max_age_seconds:
        return None, {"source": signal_name, "status": "stale"}
    if not isinstance(observation.get("value"), dict):
        return None, {"source": signal_name, "status": "invalid_value"}
    return observation, None


def _candidate(signal_name: str, observation: dict) -> dict:
    value = observation["value"]
    total = None
    affected = None
    if signal_name == "service_reachability":
        total = int(value["total"])
        affected = int(value["unreachable_count"])
        title = "Current monitored-service reachability"
        text = f"{affected} of {total} monitored services are unreachable."
    elif signal_name == "host_health":
        total = int(value["total"])
        affected = int(value["unhealthy_count"])
        title = "Current monitored-host health"
        text = f"{affected} of {total} monitored hosts are unhealthy."
    elif signal_name == "backup_status":
        latest_ok = value["latest_ok"]
        age = value.get("last_success_age_hours")
        if not isinstance(latest_ok, bool) or (age is not None and not isinstance(age, (int, float))):
            raise ValueError("invalid_backup_value")
        title = "Current Proxmox backup status"
        age_text = "unknown" if age is None else f"{float(age):g} hours"
        text = f"Latest backup successful: {'yes' if latest_ok else 'no'}. Last successful backup age: {age_text}."
    else:  # pragma: no cover - caller uses a fixed allowlist
        raise ValueError("unknown_current_state_source")
    if total is not None and (total < 0 or affected < 0 or affected > total):
        raise ValueError("invalid_count_value")
    return {
        "candidate_id": f"current_state:{signal_name}",
        "subject_id": f"current_state:{signal_name}",
        "source_type": "structured_current_state",
        "context_scope": "operations",
        "title": title,
        "text": text,
        "source": "ATLAS Phase 2b signal observation",
        "updated_at": observation["observed_at"],
        "lifecycle": "active",
        "freshness": "current",
        "match_method": "structured_current_state",
        # Fresh authoritative state should outrank alias/semantic/lexical
        # evidence for a time-sensitive request. An explicit canonical ID
        # still ranks first (R3's exact-ID gate scores above this).
        "score": 950.0,
    }


def retrieve_current_state(
    query: str,
    context_scope: str,
    db_path: Path,
    *,
    now_value: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict:
    """Return only fresh structured candidates for an explicit current-state intent."""
    if context_scope not in {"personal", "operations", "admin"}:
        raise ValueError("invalid_context_scope")
    if not current_state_requested(query, context_scope):
        return {"status": "not_requested", "candidates": [], "notices": []}
    requested = [name for name, pattern in _SOURCE_PATTERNS.items() if pattern.search(query)]
    if not requested:
        return {"status": "not_requested", "candidates": [], "notices": []}
    now_value = now_value or datetime.now(LOCAL_TZ)
    candidates = []
    notices = []
    for signal_name in requested:
        observation, notice = _fresh_observation(
            db_path, signal_name, now_value=now_value, max_age_seconds=max_age_seconds
        )
        if notice:
            notices.append(notice)
            continue
        try:
            candidates.append(_candidate(signal_name, observation))
        except (KeyError, TypeError, ValueError, OverflowError):
            notices.append({"source": signal_name, "status": "invalid_value"})
    status = "matched" if candidates else "unavailable"
    return {"status": status, "candidates": candidates, "notices": notices}


def privacy_metrics(result: dict) -> dict:
    """Return counts/types only; exclude current values and source names."""
    return {
        "current_state_status": result.get("status"),
        "current_state_candidate_count": len(result.get("candidates") or []),
        "current_state_notice_statuses": sorted({
            item["status"] for item in result.get("notices") or []
        }),
        "current_state_version": "atlas-current-state-r3-shadow-1",
    }
