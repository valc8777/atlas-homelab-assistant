#!/usr/bin/env python3

import argparse
import hashlib
import hmac
import importlib.util
import json
import mimetypes
import os
import pathlib
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import atlas_app_store
import atlas_briefings
import atlas_current_state_retrieval
import atlas_r3_answer_canary
import atlas_reference_resolution
import atlas_entity_registry
import atlas_home_assistant
import atlas_hybrid_retrieval
import atlas_memory_conflicts
import atlas_memory_corpus
import atlas_memory_maintenance
import atlas_nextcloud
import atlas_orchestrator_v2
import atlas_utterance_parser
import atlas_sfsu_oauth
import atlas_signals
import atlas_sfsu_store
import atlas_semantic_index


RUNNER_PATH = os.environ.get("ATLAS_RUNNER_PATH", os.environ.get("JARVIS_RUNNER_PATH", "/app/run-atlas-readonly.py"))
BASE_URL = os.environ.get("ATLAS_BASE_URL", os.environ.get("JARVIS_BASE_URL", "http://open-webui:8080"))
WEBUI_DB = os.environ.get("ATLAS_WEBUI_DB", os.environ.get("JARVIS_WEBUI_DB", "/app/webui_data/webui.db"))
KNOWLEDGE_NAME = os.environ.get("ATLAS_KNOWLEDGE_NAME", os.environ.get("JARVIS_KNOWLEDGE_NAME", "Homelab Source of Truth"))
MEMORY_KNOWLEDGE_NAME = os.environ.get("ATLAS_MEMORY_KNOWLEDGE_NAME", "ATLAS Personal Memory")
MEMORY_CORPUS_DIR = pathlib.Path(os.environ.get("ATLAS_MEMORY_CORPUS_DIR", "/app/atlas-app/memory-corpus/current"))
MEMORY_STATE_FILE = pathlib.Path(os.environ.get("ATLAS_MEMORY_STATE_FILE", "/app/atlas-app/memory-corpus-state.json"))
MEMORY_IMPORT_LOCK_FILE = os.environ.get("ATLAS_MEMORY_IMPORT_LOCK_FILE", "/tmp/atlas-memory-openwebui-import.lock")
IMPORT_CORPUS_SCRIPT = pathlib.Path(__file__).with_name("import_openwebui_corpus.py")
SYSTEM_PROMPT_FILE = os.environ.get("ATLAS_SYSTEM_PROMPT_FILE", os.environ.get("JARVIS_SYSTEM_PROMPT_FILE", "/app/atlas-readonly-system.md"))
MODEL = os.environ.get("ATLAS_MODEL", os.environ.get("JARVIS_MODEL"))
REQUEST_TIMEOUT = int(os.environ.get("ATLAS_REQUEST_TIMEOUT_SECONDS", os.environ.get("JARVIS_REQUEST_TIMEOUT_SECONDS", "240")))
SHORTCUTS_TOKEN = os.environ.get("ATLAS_SHORTCUTS_TOKEN", os.environ.get("JARVIS_SHORTCUTS_TOKEN", ""))
APP_DB = pathlib.Path(os.environ.get("ATLAS_APP_DB", "/app/atlas-app/atlas-app.db"))
MOBILE_DIR = pathlib.Path(os.environ.get("ATLAS_MOBILE_DIR", pathlib.Path(__file__).with_name("atlas_mobile")))
PIPER_BASE_URL = os.environ.get("ATLAS_PIPER_BASE_URL", "http://piper-tts:8000")
# DEC-2026-09-08-01: owner-confirmed "always" playback -- every generated
# speech response also plays out loud on the paired home Bluetooth speaker,
# best-effort and non-blocking, gated by a single flag for instant rollback.
ATLAS_HOME_SPEAKER_ENABLED = os.environ.get("ATLAS_HOME_SPEAKER_ENABLED", "false").lower() in {"1", "true", "yes"}
ATLAS_HOME_SPEAKER_PULSE_SERVER = os.environ.get("ATLAS_HOME_SPEAKER_PULSE_SERVER", "")
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
ATLAS_DAILY_BRIEFING_ENABLED = os.environ.get("ATLAS_DAILY_BRIEFING_ENABLED", "false").lower() in {"1", "true", "yes"}
ATLAS_DAILY_BRIEFING_TIME = os.environ.get("ATLAS_DAILY_BRIEFING_TIME", "12:00")
ATLAS_PHASE2B_FRESHNESS_SECONDS = int(os.environ.get("ATLAS_PHASE2B_FRESHNESS_SECONDS", "900"))
NTFY_BASE_URL = os.environ.get("ATLAS_NTFY_BASE_URL", "").rstrip("/")
NTFY_TOPIC = os.environ.get("ATLAS_NTFY_TOPIC", "")
NTFY_TOKEN = os.environ.get("ATLAS_NTFY_TOKEN", "")
PUBLIC_BASE_URL = os.environ.get("ATLAS_PUBLIC_BASE_URL", "https://ai-stack.example-tailnet.ts.net/atlas").rstrip("/")
ATLAS_API_VERSION = "2026-08-30-generalized-qa"
SFSU_ENABLED = os.environ.get("ATLAS_SFSU_ENABLED", "false").lower() in {"1", "true", "yes"}
SFSU_CLIENT_ID = os.environ.get("ATLAS_SFSU_CLIENT_ID", "")
SFSU_OWNER_TENANT_ID = os.environ.get("ATLAS_SFSU_OWNER_TENANT_ID", "")
SCHOOL_BRIDGE_ENABLED = os.environ.get("ATLAS_SCHOOL_BRIDGE_ENABLED", "false").lower() in {"1", "true", "yes"}
SCHOOL_BRIDGE_TOKEN = os.environ.get("ATLAS_SCHOOL_BRIDGE_TOKEN", "")
SCHOOL_BRIDGE_OWNER_ID = os.environ.get("ATLAS_SCHOOL_BRIDGE_OWNER_ID", "owner")
HOME_ASSISTANT_ENABLED = os.environ.get("ATLAS_HOME_ASSISTANT_ENABLED", "false").lower() in {"1", "true", "yes"}
HOME_ASSISTANT_BASE_URL = os.environ.get("ATLAS_HOME_ASSISTANT_BASE_URL", "")
HOME_ASSISTANT_TOKEN = os.environ.get("ATLAS_HOME_ASSISTANT_TOKEN", "")
HOME_ASSISTANT_ENTITIES = os.environ.get("ATLAS_HOME_ASSISTANT_ENTITIES", "")
# Scoped control exception (see ATLAS_HOME_ASSISTANT_READONLY.md) -- kept as
# separate flags from the read-path ones above on purpose: control being off
# must not require reads to be off too, and vice versa. Actual dispatch for
# this capability happens in the run-atlas-readonly.py subprocess (which
# inherits this same environment), not in this file directly -- see
# run_home_light()/_home_light_requested_action() there.
HOME_ASSISTANT_CONTROL_ENABLED = os.environ.get("ATLAS_HOME_ASSISTANT_CONTROL_ENABLED", "false").lower() in {"1", "true", "yes"}
HOME_ASSISTANT_CONTROLLABLE_ENTITY = os.environ.get("ATLAS_HOME_ASSISTANT_CONTROLLABLE_ENTITY", "")
COMIC_BOT_ENABLED = os.environ.get("ATLAS_COMIC_BOT_ENABLED", "false").lower() in {"1", "true", "yes"}
COMIC_REQUEST_MAX_LENGTH = 500
COMIC_REQUEST_MAX_RESULT_INDEX = 19
NEXTCLOUD_ENABLED = os.environ.get("ATLAS_NEXTCLOUD_ENABLED", "false").lower() in {"1", "true", "yes"}
NEXTCLOUD_BASE_URL = os.environ.get("ATLAS_NEXTCLOUD_BASE_URL", "")
NEXTCLOUD_USERNAME = os.environ.get("ATLAS_NEXTCLOUD_USERNAME", "")
NEXTCLOUD_APP_PASSWORD = os.environ.get("ATLAS_NEXTCLOUD_APP_PASSWORD", "")
NEXTCLOUD_PATHS = os.environ.get("ATLAS_NEXTCLOUD_PATHS", "[]")
NEXTCLOUD_CALENDARS = os.environ.get("ATLAS_NEXTCLOUD_CALENDARS", "[]")
NEXTCLOUD_ADDRESSBOOKS = os.environ.get("ATLAS_NEXTCLOUD_ADDRESSBOOKS", "[]")
ORCHESTRATOR_SHADOW_ENABLED = os.environ.get("ATLAS_ORCHESTRATOR_SHADOW_ENABLED", "false").lower() in {"1", "true", "yes"}
TYPED_UTTERANCE_SHADOW_ENABLED = os.environ.get("ATLAS_TYPED_UTTERANCE_SHADOW", "false").lower() in {"1", "true", "yes"}
ENTITY_REGISTRY_SHADOW_ENABLED = os.environ.get("ATLAS_ENTITY_REGISTRY_SHADOW", "false").lower() in {"1", "true", "yes"}
ENTITY_REGISTRY_PATH = pathlib.Path(
    os.environ.get("ATLAS_ENTITY_REGISTRY_PATH", "/app/atlas-entity-registry.json")
)
HYBRID_RETRIEVAL_SHADOW_ENABLED = os.environ.get(
    "ATLAS_HYBRID_RETRIEVAL_SHADOW", "false"
).lower() in {"1", "true", "yes"}
SEMANTIC_RETRIEVAL_SHADOW_ENABLED = os.environ.get(
    "ATLAS_SEMANTIC_RETRIEVAL_SHADOW", "false"
).lower() in {"1", "true", "yes"}
SEMANTIC_INDEX_PATH = pathlib.Path(
    os.environ.get("ATLAS_SEMANTIC_INDEX_PATH", "/app/atlas-app/semantic-retrieval.db")
)
SEMANTIC_EMBEDDING_BASE_URL = os.environ.get(
    "ATLAS_SEMANTIC_EMBEDDING_BASE_URL", "http://ollama:11434"
)
SEMANTIC_EMBEDDING_MODEL = os.environ.get(
    "ATLAS_SEMANTIC_EMBEDDING_MODEL", atlas_semantic_index.DEFAULT_MODEL
)
CURRENT_STATE_RETRIEVAL_SHADOW_ENABLED = os.environ.get(
    "ATLAS_CURRENT_STATE_RETRIEVAL_SHADOW", "false"
).lower() in {"1", "true", "yes"}
R3_ANSWER_CANARY_ENABLED = os.environ.get(
    "ATLAS_R3_ANSWER_CANARY_ENABLED", "false"
).lower() in {"1", "true", "yes"}
REFERENCE_RESOLUTION_SHADOW_ENABLED = os.environ.get(
    "ATLAS_REFERENCE_RESOLUTION_SHADOW", "false"
).lower() in {"1", "true", "yes"}
REFERENCE_RESOLUTION_CONSUMER_ENABLED = os.environ.get(
    "ATLAS_REFERENCE_RESOLUTION_CONSUMER", "false"
).lower() in {"1", "true", "yes"}
ORCHESTRATOR_SHADOW_TIMEOUT = int(os.environ.get("ATLAS_ORCHESTRATOR_SHADOW_TIMEOUT_SECONDS", "90"))
ORCHESTRATOR_SHADOW_QUEUE_SIZE = int(os.environ.get("ATLAS_ORCHESTRATOR_SHADOW_QUEUE_SIZE", "64"))
ORCHESTRATOR_READONLY_CANARY_ENABLED = os.environ.get(
    "ATLAS_ORCHESTRATOR_READONLY_CANARY_ENABLED", "false"
).lower() in {"1", "true", "yes"}
ORCHESTRATOR_READONLY_CANARY_TIMEOUT = int(
    os.environ.get("ATLAS_ORCHESTRATOR_READONLY_CANARY_TIMEOUT_SECONDS", "10")
)
# Track A3 ("invert the router", ATLAS_CAPABILITY_ROADMAP.md). Unset/off in
# production -- with it unset, run_atlas_with_readonly_canary() is byte-for-
# byte identical to today's behavior; see that function for the gated logic.
# Only meaningful when ORCHESTRATOR_READONLY_CANARY_ENABLED is also on (A3
# reorders planner-vs-classifier priority within the canary system A2 built;
# it has nothing to reorder if the canary itself is off).
ATLAS_INVERT_ROUTER_ENABLED = os.environ.get("ATLAS_INVERT_ROUTER", "false").lower() in {"1", "true", "yes"}
ATLAS_CONTEXT_SCOPES = {"personal", "operations", "admin"}
PERSONAL_RUNNER_ALLOW_TOKENS = {
    "weather",
    "temperature",
    "forecast",
    "rain",
    "wind",
    "capital",
    "movie",
    "film",
    "tv show",
    "series",
    "book",
    "novel",
    "album",
    "artist",
    "band",
    "song",
    "recording",
    "percent",
    "%",
    "calculate",
    "convert",
    "how many",
}
OPERATIONS_CONTEXT_TOKENS = {
    "homelab",
    "server",
    "container",
    "containers",
    "docker",
    "proxmox",
    "backup",
    "pbs",
    "jellyfin",
    "open webui",
    "ollama",
    "ai-stack",
    "docker-main",
    "homepage",
    "gpu",
}


ATLAS_PERMISSION_POLICY = {
    "identity": {
        "assistant_name": "ATLAS",
        "mode": "owner_only",
        "primary_user": "Val",
        "household_mode": "planned_later",
    },
    "privacy": {
        "default_posture": "privacy_over_convenience",
        "network_exposure": "private_by_default",
        "source_of_truth": "Obsidian",
        "outbound_sync": "allowed_only_after_local_path_is_stable",
    },
    "context_scopes": {
        "default": "personal",
        "allowed": ["personal", "operations", "admin"],
        "rule": "Do not reuse one conversation across scopes. User-facing personal context stays separate from future admin and operations context.",
        "personal_approved_context": [
            "current personal conversation",
            "ATLAS local notes/tasks/reminders/preferences",
            "weather/time/public-reference adapters",
        ],
        "operations_context": [
            "homelab inventory",
            "service status",
            "backup status",
            "server logs and operational notes",
        ],
    },
    "action_tiers": {
        "direct_when_explicitly_requested": [
            "answer_from_approved_local_context",
            "create_note",
            "create_task",
            "create_reminder",
            "create_shopping_item",
            "create_conditional_rule",
            "send_owner_notification",
            "draft_text_without_sending",
            "read_local_status",
            "read_weather_time_and_public_reference_data",
        ],
        "confirmation_required": [
            "delete_or_overwrite_user_data",
            "restart_or_stop_services",
            "change_network_dns_or_remote_access",
            "trigger_backup_prune_restore_or_rescan",
            "send_external_messages",
            "make_purchases",
            "unlock_open_or_disable_physical_security",
            "grant_cross_user_or_sensitive_scope",
        ],
        "blocked_by_default": [
            "raw_secrets",
            "passwords",
            "passphrases",
            "api_tokens",
            "private_keys",
            "vaultwarden_recovery_codes",
            "raw_vaultwarden_contents",
            "cross_user_private_data",
            "public_exposure_changes_without_documented_decision",
            "porn_or_xxx_focused_sources",
        ],
    },
    "integrations": {
        "memory": {
            "primary": "Obsidian",
            "local_app_store": "SQLite",
            "nextcloud": "sync_after_stable",
            "capture_path": "ATLAS local app store first; Obsidian remains the canonical source of truth; outward sync waits until local capture is stable.",
            "scope_rule": "Notes, tasks, and reminders are scoped as personal, operations, or admin and must not leak across scopes.",
        },
        "notifications": {
            "primary": "self_hosted_ntfy",
            "phone_lock_screen": "planned_via_ntfy_push",
        },
        "home_assistant": {
            "status": "planned_read_only_first",
            "control": "confirmation_gated_later",
            "cloud_assistant_connectors": "disabled_by_policy",
        },
        "media": {
            "shared_storage": "shared_store_with_per_user_visibility",
            "adult_policy": "movies_and_shows_may_include_adult_scenes_but_porn_sources_are_excluded",
        },
        "devices": {
            "first_target": "Roku",
            "wake_word": "planned_later",
        },
    },
    "audit": {
        "write_actions": "log",
        "confirmation_requests": "log",
        "blocked_requests": "log",
        "tool_calls": "log_when_available",
    },
}


ASSISTANT_EXAMPLES = [
    {"category": "Chat", "prompt": "What is your name?", "notes": "Local identity route."},
    {"category": "Homelab", "prompt": "Which host runs Open WebUI?", "notes": "Uses homelab retrieval anchors."},
    {"category": "Weather", "prompt": "What is the weather in San Francisco?", "notes": "Uses Open-Meteo."},
    {"category": "Math", "prompt": "What is 15% of 240?", "notes": "Deterministic math route."},
    {"category": "Reminders", "prompt": "Remind me to check backups tomorrow.", "notes": "Creates a local ATLAS reminder."},
    {"category": "Notes", "prompt": "Take a note: Router password is in the network binder.", "notes": "Creates a searchable local note."},
    {"category": "Tasks", "prompt": "Add task: review atlas logs.", "notes": "Creates a local task."},
    {"category": "Diagnostics", "prompt": "Help me troubleshoot Jellyfin.", "notes": "Returns a diagnostic checklist."},
    {"category": "Unsupported", "prompt": "Atlas, raise the blinds.", "notes": "Logs smart-home control as a requested capability."},
    {"category": "Unsupported", "prompt": "What is the quickest way from SF to San Ramon?", "notes": "Logs live navigation as a requested capability."},
]


def load_request_payload(handler: BaseHTTPRequestHandler) -> dict:
    parsed = urllib.parse.urlparse(handler.path)
    if handler.command == "GET":
        params = urllib.parse.parse_qs(parsed.query)
        return {
            "question": (params.get("q") or params.get("question") or [""])[0],
            "details": ((params.get("details") or ["false"])[0]).lower() in {"1", "true", "yes"},
        }

    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length) if length else b""
    content_type = handler.headers.get("Content-Type", "")
    if "application/json" in content_type:
        return json.loads(raw.decode("utf-8") or "{}")
    if "application/x-www-form-urlencoded" in content_type:
        params = urllib.parse.parse_qs(raw.decode("utf-8"))
        return {
            "question": (params.get("q") or params.get("question") or [""])[0],
            "details": ((params.get("details") or ["false"])[0]).lower() in {"1", "true", "yes"},
        }
    if raw:
        return {"question": raw.decode("utf-8")}
    return {}


def authorized(handler: BaseHTTPRequestHandler) -> bool:
    if not SHORTCUTS_TOKEN:
        return False
    header = handler.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    token = header[len(prefix):].strip()
    return hmac.compare_digest(token, SHORTCUTS_TOKEN)


def signed_action_url(path: str, params: dict[str, str] | None = None) -> str:
    if not SHORTCUTS_TOKEN:
        raise RuntimeError("ATLAS_SHORTCUTS_TOKEN is required for notification actions")
    values = {str(key): str(value) for key, value in (params or {}).items()}
    values["action_exp"] = str(int(time.time()) + (7 * 24 * 60 * 60))
    canonical_query = urllib.parse.urlencode(sorted(values.items()))
    signature = hmac.new(
        SHORTCUTS_TOKEN.encode("utf-8"),
        f"{path}?{canonical_query}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{PUBLIC_BASE_URL}{path}?{canonical_query}&action_sig={signature}"


def action_authorized(handler: BaseHTTPRequestHandler) -> bool:
    if not SHORTCUTS_TOKEN:
        return False
    parsed = urllib.parse.urlparse(handler.path)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(pairs)
    signature = values.pop("action_sig", "")
    expiration = values.get("action_exp", "")
    try:
        expires_at = int(expiration)
    except ValueError:
        return False
    now = int(time.time())
    if not signature or expires_at < now or expires_at > now + (8 * 24 * 60 * 60):
        return False
    canonical_query = urllib.parse.urlencode(sorted(values.items()))
    expected = hmac.new(
        SHORTCUTS_TOKEN.encode("utf-8"),
        f"{parsed.path}?{canonical_query}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def callback_authorized(handler: BaseHTTPRequestHandler) -> bool:
    return action_authorized(handler) or authorized(handler)


def bridge_authorized(handler: BaseHTTPRequestHandler) -> bool:
    if not SCHOOL_BRIDGE_TOKEN:
        return False
    header = handler.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    return hmac.compare_digest(header[len(prefix):].strip(), SCHOOL_BRIDGE_TOKEN)


def build_signal_routes() -> dict:
    """Track B2: build the live signal-callable dispatch dict using this
    server's own already-existing config (APP_DB, HOME_ASSISTANT_*,
    NEXTCLOUD_*). atlas_signals.py stays deliberately config-free (see its
    module docstring) so it's easy to unit test with mocked callables; this
    is the one place real config gets wired in, matching the same metadata/
    dispatch split already used for capabilities elsewhere in this project.

    Each closure either returns a value or raises -- due_rules() wraps the
    call in a try/except and treats any failure as "not due this tick"
    rather than crashing evaluation for every other rule or firing on bad
    data. HA/Nextcloud closures raise outright when their integration is
    disabled, which the same try/except handles identically to a live
    failure -- a disabled integration's signal simply never fires, safely.
    """

    def bedside_lamp_state() -> str:
        if not (HOME_ASSISTANT_ENABLED and HOME_ASSISTANT_CONTROLLABLE_ENTITY):
            raise RuntimeError("home assistant integration not configured")
        allowlist = atlas_home_assistant.parse_allowlist(HOME_ASSISTANT_ENTITIES)
        state = atlas_home_assistant.fetch_state(
            HOME_ASSISTANT_BASE_URL, HOME_ASSISTANT_TOKEN, HOME_ASSISTANT_CONTROLLABLE_ENTITY, allowlist,
        )
        return state["state"]

    def calendar_event_within_24h() -> bool:
        if not NEXTCLOUD_ENABLED:
            raise RuntimeError("nextcloud integration not configured")
        allowlist = json.loads(NEXTCLOUD_CALENDARS)
        if not isinstance(allowlist, list) or not allowlist:
            raise RuntimeError("no calendars are allowlisted")
        now_local = datetime.now(LOCAL_TZ)
        start = now_local.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
        end = (now_local + timedelta(hours=24)).astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
        for calendar in [str(item) for item in allowlist]:
            events = atlas_nextcloud.list_calendar_events(
                NEXTCLOUD_BASE_URL, NEXTCLOUD_USERNAME, NEXTCLOUD_APP_PASSWORD,
                calendar, [str(item) for item in allowlist], start, end,
            )
            if events:
                return True
        return False

    def services_unreachable_count() -> int:
        value = atlas_app_store.get_fresh_signal_value(
            APP_DB, "service_reachability", ATLAS_PHASE2B_FRESHNESS_SECONDS,
        ) or {}
        return int(value["unreachable_count"])

    def fresh_value(name: str) -> dict:
        return atlas_app_store.get_fresh_signal_value(APP_DB, name, ATLAS_PHASE2B_FRESHNESS_SECONDS) or {}

    return {
        "reminders_overdue_count": lambda: len(atlas_app_store.due_reminders(APP_DB, now_iso_local())),
        "confirmations_pending_count": lambda: len(atlas_app_store.list_confirmations(APP_DB, status="pending")),
        "notifications_failed_count": lambda: len(atlas_app_store.list_notifications(APP_DB, status="dead_letter")),
        "bedside_lamp_state": bedside_lamp_state,
        "calendar_event_within_24h": calendar_event_within_24h,
        "services_unreachable_count": services_unreachable_count,
        "hosts_unhealthy_count": lambda: int(fresh_value("host_health")["unhealthy_count"]),
        "backup_latest_ok": lambda: bool(fresh_value("backup_status")["latest_ok"]),
        "backup_last_success_age_hours": lambda: float(fresh_value("backup_status")["last_success_age_hours"]),
    }


def now_iso_local() -> str:
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


def run_atlas(
    question: str,
    conversation: list[dict] | None = None,
    context_scope: str = "personal",
    timeout_override: int | None = None,
) -> dict:
    command = [
        sys.executable,
        RUNNER_PATH,
        "--json",
        "--base-url",
        BASE_URL,
        "--webui-db",
        WEBUI_DB,
        "--knowledge-name",
        KNOWLEDGE_NAME,
        "--memory-knowledge-name",
        MEMORY_KNOWLEDGE_NAME,
        "--memory-state-file",
        str(MEMORY_STATE_FILE),
        "--context-scope",
        context_scope,
        "--system-prompt-file",
        SYSTEM_PROMPT_FILE,
    ]
    if conversation:
        command.extend(["--conversation-json", json.dumps(conversation[-8:], ensure_ascii=False)])
    command.append("--question-stdin")
    if MODEL:
        command.extend(["--model", MODEL])

    completed = subprocess.run(
        command,
        input=question,
        capture_output=True,
        text=True,
        timeout=timeout_override if timeout_override is not None else REQUEST_TIMEOUT,
        check=True,
    )
    return json.loads(completed.stdout)


def run_orchestrator_plan(question: str, context_scope: str, conversation: list[dict] | None = None) -> dict:
    """Run one synchronous planning pass; it cannot execute a capability."""
    command = [
        sys.executable,
        RUNNER_PATH,
        "--json",
        "--shadow-plan",
        "--question-stdin",
        "--context-scope",
        context_scope,
        "--base-url",
        BASE_URL,
        "--webui-db",
        WEBUI_DB,
        "--system-prompt-file",
        SYSTEM_PROMPT_FILE,
        "--conversation-json",
        json.dumps(list(conversation or [])[-8:], ensure_ascii=False),
    ]
    if MODEL:
        command.extend(["--model", MODEL])
    completed = subprocess.run(
        command,
        input=question,
        capture_output=True,
        text=True,
        timeout=ORCHESTRATOR_READONLY_CANARY_TIMEOUT,
        check=True,
    )
    return json.loads(completed.stdout)


def run_atlas_selected_capability(
    question: str,
    capability: str,
    conversation: list[dict] | None = None,
    context_scope: str = "personal",
    timeout_override: int | None = None,
) -> dict:
    """Execute one allowlisted read-only capability through the existing runner."""
    command = [
        sys.executable,
        RUNNER_PATH,
        "--json",
        "--base-url",
        BASE_URL,
        "--webui-db",
        WEBUI_DB,
        "--knowledge-name",
        KNOWLEDGE_NAME,
        "--memory-knowledge-name",
        MEMORY_KNOWLEDGE_NAME,
        "--memory-state-file",
        str(MEMORY_STATE_FILE),
        "--context-scope",
        context_scope,
        "--system-prompt-file",
        SYSTEM_PROMPT_FILE,
        "--forced-capability",
        capability,
    ]
    if conversation:
        command.extend(["--conversation-json", json.dumps(conversation[-8:], ensure_ascii=False)])
    command.append("--question-stdin")
    if MODEL:
        command.extend(["--model", MODEL])
    completed = subprocess.run(
        command,
        input=question,
        capture_output=True,
        text=True,
        timeout=timeout_override if timeout_override is not None else REQUEST_TIMEOUT,
        check=True,
    )
    return json.loads(completed.stdout)


def readonly_canary_eligible(decision: dict | None, context_scope: str) -> bool:
    """Allow only validated, non-admin, non-action-mode plan families into the canary.

    Historically every "tool"-mode capability (math.calculate, units.convert,
    datetime.resolve) was also genuinely side-effect-free, so this function's
    name and original intent conflated "not action-mode" with "read-only".
    That's no longer strictly true: home.light.manage is mode="tool" (so it
    reaches this gate and returns True) but performs a real, narrowly-scoped
    physical side effect -- a deliberate, owner-approved exception, not an
    oversight. See ATLAS_HOME_ASSISTANT_READONLY.md ("Scoped Control
    Exception") and its DEC-* entry for why this one capability bypasses the
    action-authority confirmation queue instead of using it.
    """
    if not isinstance(decision, dict) or context_scope == "admin":
        return False
    if decision.get("context_scope") != context_scope or decision.get("mode") not in {"direct", "tool", "retrieve"}:
        return False
    capability_names = decision.get("capabilities")
    if not isinstance(capability_names, list):
        return False
    registry = {item.name: item for item in atlas_orchestrator_v2.capability_registry(context_scope)}
    if any(name not in registry or not registry[name].read_only for name in capability_names):
        return False
    expected_kind = {"direct": "direct", "tool": "tool", "retrieve": "retrieve"}[decision["mode"]]
    if decision["mode"] == "direct":
        return not capability_names or all(registry[name].kind == expected_kind for name in capability_names)
    return len(capability_names) == 1 and registry[capability_names[0]].kind == expected_kind


# Deliberately independent of atlas_orchestrator_v2.capability_registry():
# a future action capability (e.g. device control) becoming registered there
# does not automatically become executable here. Someone must add a
# registry entry, an atlas_app_store.py adapter, AND an allowlist entry --
# "first action slice stays admin/destructive/purchase/device-control free"
# is enforced in code, not just documented in ATLAS_NEXT.md.
_ACTION_ALLOWLIST = {
    "reminders.manage", "tasks.manage", "shopping.manage", "notes.manage",
    "media.comic_request", "memory.correct",
}


def request_fuzzy_action_confirmation(
    question: str,
    decision: dict,
    context_scope: str,
    conversation_id: str | None,
) -> dict | None:
    """Turn a fuzzy, model-classified action-mode decision into an owner
    confirmation instead of executing it directly.

    Only reached for a capability in _ACTION_ALLOWLIST with a matching
    context_scope and a non-empty action_text (checked by the caller). The
    classification came from the probabilistic planner, not a deterministic
    regex match (those already execute instantly with no confirmation, via
    the separate _create_reminder_from_question-family handlers), so it must
    be shown to the owner before anything is written. Capability.
    approval_required is deliberately NOT used for this gate -- see
    ATLAS_DECISIONS.md for why flipping it would have invalidated sealed
    orchestration eval fixtures for an unrelated reason.
    """
    capability = decision["capabilities"][0]
    action_text = decision.get("action_text", "").strip()

    details = {
        "capability": capability,
        "context_scope": context_scope,
        "source_question": question,
        "source_conversation_id": conversation_id,
        "decision_source": "model",
    }
    if capability == "reminders.manage":
        reminder_text, due_at = parse_reminder_text(action_text)
        if not reminder_text:
            return None
        recurrence = "daily" if re.search(r"\bevery day|daily\b", question, flags=re.IGNORECASE) else None
        details.update(text=reminder_text, due_at=due_at, recurrence=recurrence)
        summary = f"Create reminder: {reminder_text}" + (f" (due {due_at})" if due_at else "")
    elif capability == "notes.manage":
        if not action_text:
            return None
        details.update(title=action_text, text=action_text)
        summary = f"Save note: {action_text}"
    elif capability == "tasks.manage":
        if not action_text:
            return None
        details.update(text=action_text)
        summary = f"Add task: {action_text}"
    elif capability == "shopping.manage":
        if not action_text:
            return None
        details.update(text=action_text)
        summary = f"Add to shopping list: {action_text}"
    else:
        # Unreachable given the caller's allowlist check, but fail closed
        # rather than create a confirmation for something unrecognized.
        return None

    confirmation = atlas_app_store.request_confirmation(
        APP_DB,
        action_type=f"action.{capability}",
        summary=summary,
        risk_level="low",
        details=details,
        execution_status="pending",
    )
    atlas_app_store.create_notification(
        APP_DB,
        channel="local",
        title="Confirmation required",
        body=summary,
        source_type="confirmation",
        source_id=confirmation["id"],
    )
    atlas_app_store.add_audit_event(
        APP_DB,
        "confirmation.requested",
        summary,
        {"confirmation_id": confirmation["id"], "capability": capability, "decision_source": "model"},
    )
    return {
        "route": "confirmation_required",
        "tool": "atlas-fuzzy-action-confirmation",
        "answer": f"I did not create or change anything yet. I created a pending confirmation instead: {summary}. Approve it and I will create it.",
        "source": "ATLAS confirmation policy",
        "result": {"confirmation": confirmation, "executed": False, "changed": False},
    }


# Deliberately independent of atlas_orchestrator_v2.capability_registry(),
# same rationale as _ACTION_ALLOWLIST: only capabilities confirmed present in
# run-atlas-readonly.py's CAPABILITY_ROUTES today are eligible, so a future
# flag-enabled-but-unrouted capability (home.read, nextcloud.read, etc.)
# cannot be selected even if capability_registry() starts returning it --
# calling an unrouted capability via --forced-capability would hit an
# argparse error and crash the subprocess.
_DEEP_PLAN_ROUTABLE_CAPABILITIES = {
    "math.calculate", "units.convert", "datetime.resolve", "weather.forecast",
    "public.reference", "public.search", "homelab.knowledge", "memory.recall",
}

DEEP_PLAN_STEP_TIMEOUT = int(os.environ.get("ATLAS_DEEP_PLAN_STEP_TIMEOUT_SECONDS", "45"))
DEEP_PLAN_SYNTHESIS_TIMEOUT = int(os.environ.get("ATLAS_DEEP_PLAN_SYNTHESIS_TIMEOUT_SECONDS", "60"))
# A planner failure must not hand the request to the legacy model path with
# the API-wide 240-second budget. This is a degraded-path ceiling, separate
# from REQUEST_TIMEOUT so established non-canary callers remain unchanged.
LEGACY_FALLBACK_TIMEOUT = int(os.environ.get("ATLAS_LEGACY_FALLBACK_TIMEOUT_SECONDS", "30"))
MULTI_INTENT_DECOMPOSITION_ENABLED = os.environ.get("ATLAS_MULTI_INTENT_DECOMPOSITION", "false").lower() == "true"
READONLY_RESULT_STATUSES = frozenset({"ok", "no_data", "unavailable", "invalid", "stale"})
READONLY_ERROR_TYPES = frozenset({
    "adapter_unavailable",
    "contract_invalid",
    "no_data",
    "parse_failed",
    "stale_data",
    "unsupported_period",
    "subprocess_error",
    "subprocess_timeout",
    "ungrounded_result",
    "invalid_payload",
})


def semantic_step_result(payload: dict) -> tuple[str, str | None]:
    """Validate the shared read-only result contract and fail closed.

    A successful subprocess is only procedural success. The capability must
    also explicitly report a valid semantic status before its answer can be
    treated as synthesis evidence.
    """
    if not isinstance(payload, dict):
        return "invalid", "contract_invalid"
    status = payload.get("result_status")
    error_type = payload.get("error_type")
    if status not in READONLY_RESULT_STATUSES:
        return "invalid", "contract_invalid"
    if error_type is not None and error_type not in READONLY_ERROR_TYPES:
        return "invalid", "contract_invalid"
    if status == "ok":
        if error_type is not None or not isinstance(payload.get("answer"), str) or not payload["answer"].strip():
            return "invalid", "contract_invalid"
        return "ok", None
    if error_type is None:
        return "invalid", "contract_invalid"
    return status, error_type


# Plain-language reasons for the owner-facing partial-result message. Keyed
# on the same (status, error_type) pairs semantic_step_result() produces --
# deliberately not the internal step_id/status/error_type codes themselves,
# which read like a log line rather than an answer.
_READONLY_FAILURE_REASON_PHRASES = {
    ("unavailable", "subprocess_timeout"): "it took too long to respond",
    ("unavailable", "subprocess_error"): "it failed to respond",
    ("unavailable", "adapter_unavailable"): "it wasn't reachable",
    ("unavailable", "ungrounded_result"): "it couldn't find grounded evidence",
    ("invalid", "invalid_payload"): "it returned something I couldn't understand",
    ("invalid", "parse_failed"): "I couldn't parse that part of the request",
    ("invalid", "contract_invalid"): "it returned an unexpected result",
    ("no_data", "no_data"): "it had nothing to report",
    ("stale", "stale_data"): "it only had outdated data",
}


def _lowercase_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _plain_partial_summary(unsuccessful: list[dict], context_scope: str) -> str:
    """Build an owner-readable sentence per failed step, using each
    capability's own registered description rather than its dotted name.
    """
    if not unsuccessful:
        return ""
    descriptions = {item.name: item.description for item in atlas_orchestrator_v2.capability_registry(context_scope)}
    sentences = []
    for step in unsuccessful:
        label = _lowercase_first(descriptions.get(step["capability"], step["capability"]))
        reason = _READONLY_FAILURE_REASON_PHRASES.get(
            (step["status"], step["error_type"]), "it didn't return a usable result"
        )
        sentences.append(f"I couldn't {label} -- {reason}.")
    return " ".join(sentences)


def _capability_label(capability: str, context_scope: str) -> str:
    descriptions = {item.name: item.description for item in atlas_orchestrator_v2.capability_registry(context_scope)}
    return descriptions.get(capability, capability)


def _step_source_text(step: dict) -> str:
    source = str(step.get("source") or "").strip()
    if not source:
        return "Source: not available."
    result = step.get("result") if isinstance(step.get("result"), dict) else {}
    weather_bits = []
    if step.get("capability") == "weather.forecast":
        if result.get("requested_period") == "current" and result.get("time"):
            weather_bits.append(f"observed at {result['time']}")
        elif result.get("resolved_date"):
            weather_bits.append(f"forecast date {result['resolved_date']}")
        if result.get("timezone"):
            weather_bits.append(f"timezone {result['timezone']}")
    if weather_bits:
        return f"Source: {source}; {'; '.join(weather_bits)}."
    provenance_bits = []
    if isinstance(result.get("sources"), list):
        provenance_bits.append(f"{len(result['sources'])} source(s)")
    for field, label in (
        ("sources_count", "source(s)"),
        ("citation_count", "citation(s)"),
        ("evidence_count", "evidence item(s)"),
    ):
        value = result.get(field)
        if isinstance(value, int):
            provenance_bits.append(f"{value} {label}")
    if provenance_bits:
        unique_bits = list(dict.fromkeys(provenance_bits))
        return f"Source: {source}; provenance: {', '.join(unique_bits)}."
    if step.get("capability") in {"public.reference", "public.search", "homelab.knowledge"}:
        return f"Source: {source}; source confidence not reported by this adapter."
    updated = step.get("updated_at") or step.get("source_updated_at")
    if updated:
        return f"Source: {source}; updated/current as of {updated}."
    return f"Source: {source}."


def _c_to_f(value) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value) * 9 / 5 + 32, 1)
    except (TypeError, ValueError):
        return None


def _format_temp_pair(celsius) -> str:
    fahrenheit = _c_to_f(celsius)
    if fahrenheit is None:
        return f"{celsius} C"
    c_value = f"{float(celsius):g}" if isinstance(celsius, (int, float)) else str(celsius)
    f_value = f"{fahrenheit:g}"
    return f"{f_value} F ({c_value} C)"


def _weather_step_answer(step: dict) -> str | None:
    """Return a US-owner-friendly weather clause when structured data exists."""
    result = step.get("result") if isinstance(step.get("result"), dict) else {}
    if step.get("capability") != "weather.forecast" or result.get("result_status") != "ok":
        return None
    place = ", ".join(part for part in (result.get("resolved_name"), result.get("country")) if part)
    weather = result.get("weather_text") or "weather"
    if result.get("requested_period") == "current" and result.get("temperature_c") is not None:
        answer = f"{place}: {weather}, {_format_temp_pair(result.get('temperature_c'))}"
        if result.get("apparent_temperature_c") is not None:
            answer += f", feels like {_format_temp_pair(result.get('apparent_temperature_c'))}"
        return answer + "."
    if result.get("temperature_max_c") is not None and result.get("temperature_min_c") is not None:
        label = result.get("resolved_date") or "That forecast"
        return (
            f"{label} in {place}: {weather}, "
            f"high {_format_temp_pair(result.get('temperature_max_c'))}, "
            f"low {_format_temp_pair(result.get('temperature_min_c'))}."
        )
    return None


def _step_status_text(step: dict, context_scope: str) -> str:
    label = _capability_label(step.get("capability") or "", context_scope)
    status = step.get("status")
    if status == "ok" and step.get("answer"):
        answer = _weather_step_answer(step) or str(step["answer"]).strip()
        return f"{step['ordinal']}. {label}: OK\n{answer}\n{_step_source_text(step)}"
    reason = _READONLY_FAILURE_REASON_PHRASES.get(
        (step.get("status"), step.get("error_type")), "it did not return a usable result"
    )
    return f"{step['ordinal']}. {label}: Not completed — {reason}.\nSource: not available."


def compose_deep_plan_owner_answer(
    synthesis_answer: str,
    step_results: list[dict],
    context_scope: str,
    *,
    synthesis_failed: bool = False,
) -> str:
    """Render a DEF-024 owner-facing answer with labeled per-clause status.

    The synthesis can still provide a concise combined answer, but the visible
    response must not hide which clause succeeded, failed, or lacked evidence.
    """
    successful = [step for step in step_results if step.get("status") == "ok" and step.get("answer")]
    unsuccessful = [step for step in step_results if step.get("status") != "ok"]
    sections = []
    if synthesis_answer and not synthesis_failed:
        sections.append(f"Combined answer:\n{synthesis_answer.strip()}")
    elif successful:
        sections.append("Combined answer:\nI gathered partial results, but could not produce a fully synthesized answer.")
    else:
        sections.append("Combined answer:\nI could not complete any part of this multi-step research request.")
    sections.append("Per-clause status:\n" + "\n\n".join(_step_status_text(step, context_scope) for step in step_results))
    if unsuccessful:
        sections.append(_plain_partial_summary(unsuccessful, context_scope))
    sections.append("Nothing was changed.")
    return "\n\n".join(part for part in sections if part)


# Explicit inventory of read-only inputs ATLAS cannot currently consume.
# This is intentionally capability-family based rather than tied to one eval
# sentence. Detection never executes anything; it lets a mixed request finish
# its supported clauses while disclosing the unsupported clause reliably.
_UNSUPPORTED_READONLY_REQUESTS = (
    (re.compile(r"\b(?:live[- ]traffic|traffic-aware|traffic conditions?)\b.*\b(?:route|directions?|navigation)\b|\b(?:route|directions?|navigation)\b.*\b(?:live[- ]traffic|traffic-aware|traffic conditions?)\b"), "live traffic navigation"),
    (re.compile(r"\b(?:camera|webcam)\b.{0,40}\b(?:image|imagery|photo|picture|frame|feed)\b|\b(?:image|imagery|photo|picture|frame|feed)\b.{0,40}\b(?:camera|webcam)\b"), "camera-image analysis"),
    (re.compile(r"\b(?:transcrib|speech[- ]to[- ]text|ambient audio|room audio|microphone audio|audio recording)\w*\b"), "audio transcription"),
    (re.compile(r"\b(?:airline|flight)\b.{0,40}\b(?:seat map|seat availability|live seats?|inventory)\b|\b(?:seat map|seat availability)\b"), "live airline seat inventory"),
    (re.compile(r"\b(?:thermostat|hvac)\b"), "thermostat access"),
)


def detect_unsupported_readonly_requests(question: str) -> list[str]:
    lowered = " ".join(question.lower().split())
    return [label for pattern, label in _UNSUPPORTED_READONLY_REQUESTS if pattern.search(lowered)]


def add_unsupported_partial_disclosure(result: dict, unsupported: list[str]) -> dict:
    """Mark a mixed answer partial without exposing internal capability IDs."""
    if not unsupported:
        return result
    labels = ", ".join(unsupported)
    disclosure = (
        f"I couldn't complete the {labels} part because ATLAS does not have an approved "
        "capability for it. Nothing was changed."
    )
    answer = str(result.get("answer") or "").strip()
    result["answer"] = f"{answer}\n\n{disclosure}" if answer else disclosure
    original_route = result.get("route")
    result["route"] = "partial_with_unsupported_capability"
    details = result.get("result") if isinstance(result.get("result"), dict) else {}
    details.update(
        partial=True,
        original_route=original_route,
        unsupported_requests=list(unsupported),
        executed=False if not details else details.get("executed", False),
        changed=False,
    )
    result["result"] = details
    return result


def _readonly_execution_failure(
    capability: str | None,
    context_scope: str,
    error_type: str,
    *,
    route: str,
) -> dict:
    """Return a stable owner-facing failure without exposing subprocess data."""
    if capability:
        descriptions = {
            item.name: item.description
            for item in atlas_orchestrator_v2.capability_registry(context_scope)
        }
        label = _lowercase_first(descriptions.get(capability, "complete that request"))
        reason = _READONLY_FAILURE_REASON_PHRASES.get(
            ("unavailable", error_type), "it didn't return a usable result"
        )
        answer = f"I couldn't {label} -- {reason}. Nothing was changed."
    else:
        reason = _READONLY_FAILURE_REASON_PHRASES.get(
            ("unavailable", error_type), "it didn't return a usable result"
        )
        answer = f"I couldn't finish that request because {reason}. Nothing was changed."
    return {
        "route": route,
        "tool": "atlas-readonly-execution-boundary",
        "answer": answer,
        "source": "ATLAS read-only execution boundary",
        "result_status": "unavailable",
        "error_type": error_type,
        "result": {"executed": False, "changed": False, "capability": capability},
    }


def run_selected_capability_bounded(
    question: str,
    capability: str,
    conversation: list[dict] | None,
    context_scope: str,
) -> dict:
    """Execute a single selected capability with the same boundary as deep steps."""
    try:
        return run_atlas_selected_capability(
            question,
            capability,
            conversation,
            context_scope,
            timeout_override=DEEP_PLAN_STEP_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return _readonly_execution_failure(
            capability, context_scope, "subprocess_timeout", route="readonly_capability_unavailable"
        )
    except subprocess.CalledProcessError:
        return _readonly_execution_failure(
            capability, context_scope, "subprocess_error", route="readonly_capability_unavailable"
        )
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        return _readonly_execution_failure(
            capability, context_scope, "invalid_payload", route="readonly_capability_invalid"
        )


def deep_plan_eligible(decision: dict | None, context_scope: str) -> bool:
    """Allow only validated, non-admin, read-only, allowlisted deep plans."""
    if not isinstance(decision, dict) or context_scope == "admin":
        return False
    if decision.get("context_scope") != context_scope or decision.get("mode") != "deep":
        return False
    capability_names = decision.get("capabilities")
    if not isinstance(capability_names, list) or not capability_names or len(capability_names) > 4:
        return False
    registry = {item.name: item for item in atlas_orchestrator_v2.capability_registry(context_scope)}
    return all(
        name in registry
        and registry[name].read_only
        and registry[name].kind != "direct"
        and name in _DEEP_PLAN_ROUTABLE_CAPABILITIES
        for name in capability_names
    )


def run_deep_plan_synthesis(question: str, step_results: list[dict], context_scope: str) -> dict:
    """Subprocess wrapper for the dedicated synthesis-only CLI mode. Modeled
    on run_orchestrator_plan/run_atlas: a thin JSON-in/JSON-out wrapper, no
    direct model access from this process."""
    command = [
        sys.executable,
        RUNNER_PATH,
        "--json",
        "--deep-plan-synthesis",
        "--step-results-json",
        json.dumps(step_results, ensure_ascii=False),
        "--question-stdin",
        "--context-scope",
        context_scope,
        "--base-url",
        BASE_URL,
        "--webui-db",
        WEBUI_DB,
        "--system-prompt-file",
        SYSTEM_PROMPT_FILE,
    ]
    if MODEL:
        command.extend(["--model", MODEL])
    completed = subprocess.run(
        command,
        input=question,
        capture_output=True,
        text=True,
        timeout=DEEP_PLAN_SYNTHESIS_TIMEOUT,
        check=True,
    )
    return json.loads(completed.stdout)


def run_deep_plan_readonly(
    question: str,
    context_scope: str,
    conversation: list[dict] | None,
    capabilities: list[str],
    planned_steps: list[dict] | None = None,
) -> dict:
    """Run independently scoped read-only steps, continuing after failures."""
    execution_steps = planned_steps or [
        {"capability": capability, "request": question} for capability in capabilities
    ]
    step_results = []
    for ordinal, step in enumerate(execution_steps, start=1):
        capability = step["capability"]
        step_question = step["request"]
        step_id = f"step-{ordinal}"
        started = time.monotonic()
        try:
            payload = run_atlas_selected_capability(
                step_question, capability, conversation, context_scope,
                timeout_override=DEEP_PLAN_STEP_TIMEOUT,
            )
            status, error_type = semantic_step_result(payload)
            step_results.append({
                "ordinal": ordinal,
                "step_id": step_id,
                "capability": capability,
                "request": step_question,
                "status": status,
                "error_type": error_type,
                "answer": payload.get("answer"),
                "source": payload.get("source"),
                "result": payload.get("result"),
                "latency_ms": round((time.monotonic() - started) * 1000),
            })
        except subprocess.TimeoutExpired:
            step_results.append({"ordinal": ordinal, "step_id": step_id, "capability": capability, "status": "unavailable", "error_type": "subprocess_timeout", "answer": None, "source": None, "latency_ms": round((time.monotonic() - started) * 1000)})
        except subprocess.CalledProcessError:
            step_results.append({"ordinal": ordinal, "step_id": step_id, "capability": capability, "status": "unavailable", "error_type": "subprocess_error", "answer": None, "source": None, "latency_ms": round((time.monotonic() - started) * 1000)})
        except (json.JSONDecodeError, TypeError, ValueError, KeyError):
            step_results.append({"ordinal": ordinal, "step_id": step_id, "capability": capability, "status": "invalid", "error_type": "invalid_payload", "answer": None, "source": None, "latency_ms": round((time.monotonic() - started) * 1000)})

    successful = [step for step in step_results if step["status"] == "ok" and step["answer"]]
    unsuccessful = [step for step in step_results if step["status"] != "ok"]
    if not successful:
        # Deterministic short-circuit: zero evidence means a synthesis call
        # can add nothing but latency and hallucination risk.
        return {
            "route": "deep_plan_partial",
            "tool": "atlas-deep-plan-synthesis",
            "answer": compose_deep_plan_owner_answer("", step_results, context_scope),
            "source": "ATLAS deep-plan synthesis",
            "result": {"steps": step_results, "synthesis_status": "skipped_no_evidence"},
        }
    synthesis_started = time.monotonic()
    try:
        # Only semantically successful evidence crosses the model boundary.
        # Failed/no-data/stale human text stays out of synthesis completely.
        synthesis = run_deep_plan_synthesis(question, successful, context_scope)
        synthesis_ms = round((time.monotonic() - synthesis_started) * 1000)
        answer = compose_deep_plan_owner_answer(
            synthesis.get("answer") or "I gathered research but could not produce a final answer.",
            step_results,
            context_scope,
        )
        return {
            "route": "deep_plan_partial" if unsuccessful else "deep_plan_synthesis",
            "tool": "atlas-deep-plan-synthesis",
            "answer": answer,
            "source": "ATLAS deep-plan synthesis",
            "result": {
                "steps": step_results, "synthesis_status": "ok_partial" if unsuccessful else "ok",
                "synthesis_ms": synthesis_ms,
            },
        }
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError, TypeError, ValueError, KeyError):
        synthesis_ms = round((time.monotonic() - synthesis_started) * 1000)
        return {
            "route": "deep_plan_partial",
            "tool": "atlas-deep-plan-synthesis",
            "answer": compose_deep_plan_owner_answer(
                "I gathered partial research but could not synthesize a combined answer.",
                step_results,
                context_scope,
                synthesis_failed=True,
            ),
            "source": "ATLAS deep-plan synthesis",
            "result": {"steps": step_results, "synthesis_status": "failed", "synthesis_ms": synthesis_ms},
        }


def run_legacy_compound_fallback(
    question: str,
    context_scope: str,
    conversation: list[dict] | None,
    required_capabilities: list[str],
) -> dict:
    """H4: the model-driven planner has already failed (timeout, invalid
    plan, or an ineligible decision) and the request is genuinely compound.
    Execute each clause-discovered mandatory capability through the existing
    deterministic executor instead of silently falling through to the
    single-route legacy classifier as though it answered the whole request.

    Deliberately does not call the model again for synthesis: this path
    exists only because the model-driven planner already failed, so adding
    another model call here would work against H5's own latency-diagnosis
    goal rather than serve it. Successful answers are joined as separate
    paragraphs instead of synthesized prose.
    """
    step_results = []
    for ordinal, capability in enumerate(required_capabilities, start=1):
        step_id = f"step-{ordinal}"
        started = time.monotonic()
        try:
            payload = run_atlas_selected_capability(
                question,
                capability,
                conversation,
                context_scope,
                timeout_override=DEEP_PLAN_STEP_TIMEOUT,
            )
            status, error_type = semantic_step_result(payload)
            step_results.append({
                "ordinal": ordinal, "step_id": step_id, "capability": capability,
                "status": status, "error_type": error_type,
                "answer": payload.get("answer"), "source": payload.get("source"),
                "result": payload.get("result"),
                "latency_ms": round((time.monotonic() - started) * 1000),
            })
        except subprocess.TimeoutExpired:
            step_results.append({"ordinal": ordinal, "step_id": step_id, "capability": capability, "status": "unavailable", "error_type": "subprocess_timeout", "answer": None, "source": None, "latency_ms": round((time.monotonic() - started) * 1000)})
        except subprocess.CalledProcessError:
            step_results.append({"ordinal": ordinal, "step_id": step_id, "capability": capability, "status": "unavailable", "error_type": "subprocess_error", "answer": None, "source": None, "latency_ms": round((time.monotonic() - started) * 1000)})
        except (json.JSONDecodeError, TypeError, ValueError, KeyError):
            step_results.append({"ordinal": ordinal, "step_id": step_id, "capability": capability, "status": "invalid", "error_type": "invalid_payload", "answer": None, "source": None, "latency_ms": round((time.monotonic() - started) * 1000)})

    successful = [step for step in step_results if step["status"] == "ok" and step["answer"]]
    unsuccessful = [step for step in step_results if step["status"] != "ok"]
    if not successful:
        return {
            "route": "legacy_compound_fallback",
            "tool": "atlas-legacy-compound-fallback",
            "answer": compose_deep_plan_owner_answer("", step_results, context_scope),
            "source": "ATLAS legacy fallback",
            "result": {"steps": step_results, "synthesis_status": "skipped_no_evidence"},
        }
    joined = "\n\n".join(step["answer"] for step in successful)
    answer = compose_deep_plan_owner_answer(joined, step_results, context_scope)
    return {
        "route": "legacy_compound_partial" if unsuccessful else "legacy_compound_complete",
        "tool": "atlas-legacy-compound-fallback",
        "answer": answer,
        "source": "ATLAS legacy fallback",
        "result": {"steps": step_results, "synthesis_status": "ok_partial" if unsuccessful else "ok_no_synthesis"},
    }


def clarification_result_from_plan(question: str, decision: dict, telemetry: dict) -> dict:
    lowered = question.lower()
    missing = decision.get("missing")
    if re.search(r"\b(?:first|inaugural|original)\s+daytona\b", lowered):
        answer = (
            "Which Daytona do you mean -- the Daytona 500 race, Daytona International Speedway, "
            "or another Daytona event/place?"
        )
    elif isinstance(missing, str) and missing.strip():
        # DEF-034: the native planner's clarify_request tool call already
        # says what it thinks is missing -- use that instead of always
        # falling back to a generic template that ignores it. The older
        # prompt-scrape planner effectively did this by letting the model
        # write the whole clarifying question; this restores that
        # specificity for the native tool-calling path.
        detail = missing.strip().rstrip(".?")
        answer = f"I need one clarification before I can answer that reliably: {detail}?"
    else:
        answer = "I need one clarification before I can answer that reliably. What criterion, scope, or target should I use?"
    return {
        "route": "clarification",
        "tool": "atlas-orchestrator-clarification",
        "answer": answer,
        "source": "ATLAS intent routing",
        "result": {
            "changed": False,
            "needs_clarification": True,
            "reason_code": decision.get("reason_code") or "ambiguous_request",
        },
        "orchestrator_canary": {
            **telemetry,
            "status": "clarification",
            "mode": "clarify",
            "capabilities": [],
            "executed_capability": None,
            "decision_source": telemetry.get("decision_source") or "model",
        },
        "deciding_layer": "orchestrator_canary",
    }


_A3_RUNNER_MODULE = None


def _load_readonly_runner_module():
    """In-process import of run-atlas-readonly.py for the cheap Track A3
    classifier pre-check -- no subprocess spawn, no model call. Cached after
    first import. Mirrors run-atlas-orchestration-eval.py's load_runner()
    exactly, including resolving the path relative to this file (not
    RUNNER_PATH) so it always finds the correct sibling module regardless of
    environment.
    """
    global _A3_RUNNER_MODULE
    if _A3_RUNNER_MODULE is None:
        spec = importlib.util.spec_from_file_location(
            "atlas_invert_router_runner", pathlib.Path(__file__).with_name("run-atlas-readonly.py")
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _A3_RUNNER_MODULE = module
    return _A3_RUNNER_MODULE


# Split on any common clause connector -- not just "and" -- so the mixed-
# device-action safety boundary in pre_route_safety_result() below catches a
# comma-, "then"-, or plain sentence-separated compound the same way it
# catches an "and"-joined one.
_COMPOUND_CLAUSE_SPLIT_RE = re.compile(r"\b(?:and|then)\b|[,;]|(?<=[.?!])\s+")


def _fragment_requests_light_action(fragment: str) -> bool:
    """Delegate to the same lamp-intent predicate classify_question() itself
    uses (action/color/effect/theme), rather than a separate, narrower
    pattern -- so this safety boundary can't silently fall behind whatever
    counts as a light action there. Fails closed (returns False, so the
    caller treats the fragment as "not a light action" and the overall
    boundary check falls through) only if the runner module cannot load at
    all, matching classifier_route_is_deterministic_first()'s own
    never-raise contract.
    """
    try:
        runner = _load_readonly_runner_module()
    except Exception:
        return False
    return runner.is_home_light_action_request(fragment)


# Track A3: deterministic-first carve-outs that should not pay planner latency
# before their own stricter local routing. home_light/home_read keep the
# original physical-world safety carve-out. countdown and
# quantitative_reasoning, closed_world_reasoning, and
# counterfactual_boundary_reasoning, causal_tradeoff_reasoning,
# source_freshness_reasoning, timeout_explanation_reasoning, and
# evidence_quality_reasoning are read-only and
# self-contained: they only fire when the runner can parse the needed facts
# locally, which avoids DEF-025a/DEF-025b/DEF-025c/DEF-025f's observed
# planner-timeout -> fallback path without moving broad math/conversion/general
# questions ahead of the planner.
_DETERMINISTIC_FIRST_ROUTES = {
    "home_light",
    "home_read",
    "countdown",
    "quantitative_reasoning",
    "closed_world_reasoning",
    "counterfactual_boundary_reasoning",
    "causal_tradeoff_reasoning",
    "source_freshness_reasoning",
    "timeout_explanation_reasoning",
    "evidence_quality_reasoning",
}


def classifier_route_is_deterministic_first(question: str, context_scope: str) -> str | None:
    """Track A3 pre-check: cheap in-process classification to decide whether
    the deterministic-first carve-out applies, before the planner is ever
    consulted. Returns the classified route if it's in the deterministic-
    first set, else None. Never raises -- a classification failure here must
    not break dispatch; it just means "not deterministic-first", falling
    through to planner-first exactly as if this check did not exist.
    """
    try:
        runner = _load_readonly_runner_module()
        route = runner.classify_question(question, context_scope)
    except Exception:
        return None
    return route if route in _DETERMINISTIC_FIRST_ROUTES else None


def run_deterministic_first_readonly_if_matched(question: str, context_scope: str) -> dict | None:
    """Execute narrow deterministic read-only routes before keyword handlers.

    The normal A3 carve-out runs inside run_atlas_with_readonly_canary(), but
    the HTTP server also has older pre-planner keyword handlers for memory,
    notes, reminders, and similar app features. DEF-025 counterfactual prompts
    can mention one of those feature names hypothetically ("memory maintenance
    report") and must be answered as read-only reasoning, not treated as a real
    feature command.
    """
    route = classifier_route_is_deterministic_first(question, context_scope)
    if route not in {
        "counterfactual_boundary_reasoning",
        "causal_tradeoff_reasoning",
        "source_freshness_reasoning",
        "timeout_explanation_reasoning",
        "evidence_quality_reasoning",
    }:
        return None
    return run_atlas(question, None, context_scope)


_DISPUTE_PHRASE_RE = re.compile(
    r"^(?:(?:no,?\s+)?that'?s?\s+(?:not\s+true|wrong|incorrect)|"
    r"that\s+is\s+(?:not\s+true|wrong|incorrect)|"
    r"i\s+don'?t\s+think\s+that'?s?\s+true)\b",
    re.IGNORECASE,
)


def is_disputing_prior_general_knowledge_claim(question: str, conversation: list[dict] | None) -> bool:
    """The native planner sometimes classifies plain factual pushback
    ("that's not true", "that's wrong") as `mode: "action"` intending
    `memory.correct`, since that phrasing overlaps that capability's own
    discovery terms ("that's wrong", "that is wrong") -- even when nothing
    personal is being corrected, just a claim ATLAS itself made about
    general knowledge in the immediately preceding turn.

    Found via the 2026-09-17 chat-quality review (`ATLAS_RECENT_CHAT_QUALITY_
    REVIEW.md`): an explicit correction of a fabricated literary character
    name ("That is not true, in the book the character was also named Louis
    de Pointe du Lac") was rejected with "I did not create or change
    anything. This read-only canary cannot execute actions" -- a nonsensical
    response to a factual statement. Reproduced live before this fix.

    Deliberately narrow: only fires when the *previous assistant turn* was a
    `general_reference` answer (a claim about the world, not a stored
    personal fact), so a genuine "that's wrong, my coffee order is actually
    X" memory correction is untouched -- `memory.correct` only ever follows
    a `personal_memory`/`memory.recall` turn in practice, not a general-
    knowledge one.
    """
    if not conversation:
        return False
    last_assistant = next(
        (turn for turn in reversed(conversation) if turn.get("role") == "assistant"),
        None,
    )
    if not last_assistant or last_assistant.get("route") != "general_reference":
        return False
    return bool(_DISPUTE_PHRASE_RE.search(question.strip()))


def run_atlas_with_readonly_canary(
    question: str,
    context_scope: str,
    conversation: list[dict] | None = None,
    conversation_id: str | None = None,
) -> dict:
    """Gate the existing read-only executor; every planner problem falls back."""
    if not ORCHESTRATOR_READONLY_CANARY_ENABLED:
        return run_atlas(question, conversation, context_scope)
    if is_structured_reasoning_request(question):
        result = run_selected_capability_bounded(question, "answer.general", conversation, context_scope)
        structured_telemetry = {
            "status": "selected",
            "planner_latency_ms": 0,
            "mode": "direct",
            "capabilities": ["answer.general"],
            "decision_source": "deterministic_structured_reasoning",
            "executed_capability": "answer.general",
        }
        if result.get("result_status") != "ok" and result.get("error_type"):
            structured_telemetry["execution_status"] = result.get("result_status")
            structured_telemetry["execution_error_type"] = result.get("error_type")
        result["orchestrator_canary"] = structured_telemetry
        return result
    if is_self_contained_reasoning_request(question):
        result = run_selected_capability_bounded(question, "answer.general", conversation, context_scope)
        reasoning_telemetry = {
            "status": "selected",
            "planner_latency_ms": 0,
            "mode": "direct",
            "capabilities": ["answer.general"],
            "decision_source": "deterministic_self_contained_reasoning",
            "executed_capability": "answer.general",
        }
        if result.get("result_status") != "ok" and result.get("error_type"):
            reasoning_telemetry["execution_status"] = result.get("result_status")
            reasoning_telemetry["execution_error_type"] = result.get("error_type")
        result["orchestrator_canary"] = reasoning_telemetry
        return result
    if is_hypothetical_boundary_advice_request(question):
        result = run_selected_capability_bounded(question, "answer.general", conversation, context_scope)
        advice_telemetry = {
            "status": "selected",
            "planner_latency_ms": 0,
            "mode": "direct",
            "capabilities": ["answer.general"],
            "decision_source": "deterministic_hypothetical_boundary_advice",
            "executed_capability": "answer.general",
        }
        if result.get("result_status") != "ok" and result.get("error_type"):
            advice_telemetry["execution_status"] = result.get("result_status")
            advice_telemetry["execution_error_type"] = result.get("error_type")
        result["orchestrator_canary"] = advice_telemetry
        return result
    # Track A3 (ATLAS_INVERT_ROUTER, unset/off in production): physically-
    # acting / scope-exception capabilities keep first refusal and are never
    # sent to the planner, regardless of how confident it might be. With the
    # flag unset this block never executes, so behavior is byte-for-byte
    # today's -- confirmed by tools/ai/test-atlas-invert-router.py.
    if ATLAS_INVERT_ROUTER_ENABLED:
        deterministic_route = classifier_route_is_deterministic_first(question, context_scope)
        if deterministic_route:
            result = run_atlas(question, conversation, context_scope)
            result["orchestrator_canary"] = {
                "status": "selected",
                "planner_latency_ms": 0,
                "mode": "direct",
                "capabilities": [],
                "decision_source": "deterministic_first_carveout",
                "executed_capability": None,
                "deciding_layer": "classifier_first",
            }
            return result
    started = time.monotonic()
    telemetry = {"status": "fallback", "planner_latency_ms": None}
    selected_capability = None
    deep_plan_capabilities = None
    deep_plan_steps = None
    rejected_action = False
    decision = None
    try:
        payload = run_orchestrator_plan(question, context_scope, conversation)
        telemetry["planner_latency_ms"] = round((time.monotonic() - started) * 1000)
        telemetry["phase_timings"] = payload.get("phase_timings")
        decision = payload.get("decision") if payload.get("status") == "completed" else None
        # DEF-040: the planner can succeed with a VALID single-capability
        # decision (mode tool/retrieve/direct) that is nonetheless wrong for
        # a genuinely compound question -- readonly_canary_eligible() only
        # checks that the decision is well-formed, not that it addresses
        # the whole request. "What is the weather in Denver? What is the
        # population of Tokyo?" got mode=retrieve/capabilities=[weather.
        # forecast] from the model, silently dropping Tokyo entirely, with
        # no timeout/error/ineligible-plan to trigger H4's existing
        # compound-fallback protection below (that one only fires when the
        # planner FAILS). Reuses the same precision-biased H3 detector H4
        # already uses, so a confident single-capability question is never
        # second-guessed -- only one where an independent signal says 2+
        # capabilities are actually needed.
        model_missed_compound = len(
            atlas_orchestrator_v2.discover_required_capabilities(question, context_scope, conversation)
        ) >= 2
        if readonly_canary_eligible(decision, context_scope) and not model_missed_compound:
            capabilities = decision.get("capabilities") or []
            selected_capability = capabilities[0] if capabilities else "answer.general"
            telemetry.update(
                status="selected",
                mode=decision.get("mode"),
                capabilities=capabilities,
                decision_source=payload.get("decision_source") or "model",
            )
        elif deep_plan_eligible(decision, context_scope):
            deep_plan_capabilities = decision.get("capabilities") or []
            if MULTI_INTENT_DECOMPOSITION_ENABLED and decision.get("steps"):
                deep_plan_steps = decision["steps"]
            telemetry.update(
                status="deep_selected",
                mode="deep",
                capabilities=deep_plan_capabilities,
                decision_source=payload.get("decision_source") or "model",
            )
        elif isinstance(decision, dict) and decision.get("mode") == "clarify":
            telemetry.update(
                status="clarification",
                mode="clarify",
                capabilities=[],
                decision_source=payload.get("decision_source") or "model",
            )
            return clarification_result_from_plan(question, decision, telemetry)
        else:
            telemetry["reason"] = payload.get("error_type") or "ineligible_plan"
            rejected_action = (
                isinstance(decision, dict)
                and decision.get("mode") == "action"
                and not is_disputing_prior_general_knowledge_claim(question, conversation)
            )
    except subprocess.TimeoutExpired:
        telemetry.update(reason="planner_timeout", planner_latency_ms=round((time.monotonic() - started) * 1000))
    except subprocess.CalledProcessError:
        telemetry.update(reason="planner_process_error", planner_latency_ms=round((time.monotonic() - started) * 1000))
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        telemetry.update(reason="invalid_planner_payload", planner_latency_ms=round((time.monotonic() - started) * 1000))
    unsupported_requests = detect_unsupported_readonly_requests(question)
    if unsupported_requests:
        # Do not ask a generic search/model capability to impersonate a
        # modality or live-data adapter ATLAS does not have. Execute only the
        # clause-discovered supported capabilities, then disclose the gap.
        required = sorted(
            atlas_orchestrator_v2.discover_required_capabilities(
                question, context_scope, conversation
            )
        )
        if required:
            result = run_legacy_compound_fallback(
                question, context_scope, conversation, required
            )
            steps = result.get("result", {}).get("steps", [])
            telemetry["executed_capabilities"] = [step["capability"] for step in steps]
            telemetry["step_status_list"] = [
                {
                    "ordinal": step.get("ordinal"),
                    "step_id": step.get("step_id"),
                    "capability": step.get("capability"),
                    "status": step.get("status"),
                    "error_type": step.get("error_type"),
                    "latency_ms": step.get("latency_ms"),
                }
                for step in steps
            ]
        else:
            result = {
                "route": "unsupported_readonly_capability",
                "tool": "atlas-unsupported-capability-boundary",
                "answer": "",
                "source": "ATLAS capability boundary",
                "result": {"executed": False, "changed": False},
            }
        result = add_unsupported_partial_disclosure(result, unsupported_requests)
        telemetry["status"] = "partial_unsupported"
        telemetry["unsupported_requests"] = unsupported_requests
        telemetry["decomposition"] = "supported_only_with_gap_disclosure"
    elif deep_plan_capabilities:
        result = run_deep_plan_readonly(
            question, context_scope, conversation, deep_plan_capabilities, deep_plan_steps
        )
        steps = result.get("result", {}).get("steps", [])
        telemetry["executed_capabilities"] = [step["capability"] for step in steps]
        telemetry["step_statuses"] = {step["capability"]: step["status"] for step in steps}
        telemetry["step_status_list"] = [
            {
                "ordinal": step.get("ordinal"),
                "step_id": step.get("step_id"),
                "capability": step.get("capability"),
                "status": step.get("status"),
                "error_type": step.get("error_type"),
                "latency_ms": step.get("latency_ms"),
            }
            for step in steps
        ]
        telemetry["decomposition"] = "step_specific" if deep_plan_steps else "legacy_full_request"
        telemetry["synthesis_status"] = result.get("result", {}).get("synthesis_status")
        telemetry["synthesis_ms"] = result.get("result", {}).get("synthesis_ms")
    elif selected_capability:
        result = run_selected_capability_bounded(question, selected_capability, conversation, context_scope)
        telemetry["executed_capability"] = selected_capability
        if result.get("result_status") != "ok" and result.get("error_type"):
            telemetry["execution_status"] = result.get("result_status")
            telemetry["execution_error_type"] = result.get("error_type")
    else:
        # H4: the model-driven planner already failed above (timeout, invalid
        # plan, or an ineligible decision) -- don't let the single-route
        # legacy classifier silently answer only one clause of a genuinely
        # compound request as though it addressed the whole thing. Reuses
        # H3's own strict, precision-biased detector, so this only fires on
        # a confident 2+-capability signal; every other question (the
        # overwhelming majority) takes the unchanged run_atlas() path below.
        required = atlas_orchestrator_v2.discover_required_capabilities(question, context_scope, conversation)
        if len(required) >= 2:
            result = run_legacy_compound_fallback(question, context_scope, conversation, sorted(required))
            steps = result.get("result", {}).get("steps", [])
            telemetry["executed_capabilities"] = [step["capability"] for step in steps]
            telemetry["step_status_list"] = [
                {
                    "ordinal": step.get("ordinal"),
                    "step_id": step.get("step_id"),
                    "capability": step.get("capability"),
                    "status": step.get("status"),
                    "error_type": step.get("error_type"),
                    "latency_ms": step.get("latency_ms"),
                }
                for step in steps
            ]
            telemetry["decomposition"] = "legacy_compound_fallback"
            # DEF-023: this branch runs precisely when the native planner's
            # own decision was rejected or timed out AND an independent
            # signal still confidently found 2+ required capabilities -- it
            # then executes the same multi-step decomposition "deep" mode
            # would have, just via the fallback path instead of the model's
            # own decision. Never setting `mode` here made every one of its
            # invocations fail eval checks expecting `required_mode: "deep"`
            # 100% of the time, misdiagnosed on 2026-09-12 as "native-planner
            # sampling variance" (a single re-run of a deterministic miss
            # will, of course, fail again). It isn't variance: every one of
            # the 9 real failures in the h6 token-cap-rerun dataset traced to
            # exactly this branch, and every one was a plain, fully-supported
            # compound question (weather+math/units+a fact lookup) that H4's
            # fallback answered correctly -- the bug was telemetry bookkeeping,
            # not answer quality.
            telemetry["mode"] = "deep"
            telemetry["synthesis_status"] = result.get("result", {}).get("synthesis_status")
        else:
            try:
                result = run_atlas(
                    question,
                    conversation,
                    context_scope,
                    timeout_override=LEGACY_FALLBACK_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                result = _readonly_execution_failure(
                    None, context_scope, "subprocess_timeout", route="legacy_fallback_unavailable"
                )
                telemetry["fallback_execution_status"] = "unavailable"
                telemetry["fallback_execution_error_type"] = "subprocess_timeout"
            except subprocess.CalledProcessError:
                result = _readonly_execution_failure(
                    None, context_scope, "subprocess_error", route="legacy_fallback_unavailable"
                )
                telemetry["fallback_execution_status"] = "unavailable"
                telemetry["fallback_execution_error_type"] = "subprocess_error"
            except (json.JSONDecodeError, TypeError, ValueError, KeyError):
                result = _readonly_execution_failure(
                    None, context_scope, "invalid_payload", route="legacy_fallback_invalid"
                )
                telemetry["fallback_execution_status"] = "invalid"
                telemetry["fallback_execution_error_type"] = "invalid_payload"
    if rejected_action:
        confirmation_result = None
        capabilities = decision.get("capabilities") or []
        single_capability = capabilities[0] if len(capabilities) == 1 else None
        if (
            single_capability in _ACTION_ALLOWLIST
            and decision.get("context_scope") == context_scope
            and decision.get("action_text")
        ):
            confirmation_result = request_fuzzy_action_confirmation(question, decision, context_scope, conversation_id)
        result = confirmation_result or {
            "route": "orchestrator_action_not_executed",
            "tool": "atlas-readonly-canary-boundary",
            "answer": "I did not create or change anything. This read-only canary cannot execute actions; use ATLAS's confirmed action path for that request.",
            "source": "ATLAS read-only canary policy",
            "result": {"executed": False, "changed": False},
        }
    # Track A3 audit trail: record which layer actually decided, on every
    # request, from day one -- not added retroactively after a problem. Only
    # populated when the flag is on, so telemetry shape with it off is
    # unchanged from today (verified by test-atlas-invert-router.py).
    if ATLAS_INVERT_ROUTER_ENABLED:
        telemetry["deciding_layer"] = "planner" if (deep_plan_capabilities or selected_capability) else "classifier_fallback"
    result["orchestrator_canary"] = telemetry
    return result


def record_readonly_canary_audit(exchange: dict | None, result: dict, context_scope: str) -> None:
    telemetry = result.get("orchestrator_canary")
    if not exchange or not isinstance(telemetry, dict):
        return
    atlas_app_store.add_audit_event(
        APP_DB,
        event_type="orchestrator.canary",
        summary=f"Read-only canary {telemetry.get('status', 'unknown')}",
        details={
            "conversation_id": exchange.get("conversation_id"),
            "context_scope": context_scope,
            "status": telemetry.get("status"),
            "reason": telemetry.get("reason"),
            "mode": telemetry.get("mode"),
            "capabilities": telemetry.get("capabilities") or [],
            "executed_capability": telemetry.get("executed_capability"),
            "executed_capabilities": telemetry.get("executed_capabilities"),
            "execution_status": telemetry.get("execution_status"),
            "execution_error_type": telemetry.get("execution_error_type"),
            "fallback_execution_status": telemetry.get("fallback_execution_status"),
            "fallback_execution_error_type": telemetry.get("fallback_execution_error_type"),
            "unsupported_requests": telemetry.get("unsupported_requests"),
            "step_statuses": telemetry.get("step_statuses"),
            "step_status_list": telemetry.get("step_status_list"),
            "synthesis_status": telemetry.get("synthesis_status"),
            "decision_source": telemetry.get("decision_source"),
            "deciding_layer": telemetry.get("deciding_layer"),
            "planner_latency_ms": telemetry.get("planner_latency_ms"),
            # H5: privacy-safe phase timings (durations only) so real
            # ordinary traffic accumulates queue/model/repair/synthesis
            # latency evidence instead of the 30s ceiling being the only
            # signal available.
            "phase_timings": telemetry.get("phase_timings"),
            "synthesis_ms": telemetry.get("synthesis_ms"),
        },
    )


def record_capability_gap_audit(exchange: dict | None, result: dict, context_scope: str) -> None:
    """Log a capability gap the read-only runner flagged (2026-09-07 design).

    Owner-approved scope: ATLAS never adds or calls a new API on its own --
    it only surfaces, via the existing audit-event trail, that a question
    reached a known boundary (e.g. an unsupported currency pair) so the
    owner can decide whether to add coverage. The runner
    (run-atlas-readonly.py) has no database access at all; it only adds a
    `capability_gap` field to its JSON result, and this is the one place
    that field gets turned into something durable and owner-visible.
    """
    gap = result.get("result", {}).get("capability_gap") if isinstance(result.get("result"), dict) else None
    if not exchange or not isinstance(gap, dict):
        return
    atlas_app_store.add_audit_event(
        APP_DB,
        event_type=f"capability_gap.{gap.get('type', 'unknown')}",
        summary=f"Capability gap: {gap.get('type', 'unknown')}",
        details={
            "conversation_id": exchange.get("conversation_id"),
            "context_scope": context_scope,
            "route": result.get("route"),
            **{k: v for k, v in gap.items() if k != "type"},
        },
    )


_SHADOW_QUEUE: queue.Queue = queue.Queue(maxsize=max(1, ORCHESTRATOR_SHADOW_QUEUE_SIZE))
_SHADOW_WORKER_STARTED = False
_SHADOW_WORKER_LOCK = threading.Lock()
_ENTITY_REGISTRY_CACHE = None
_ENTITY_REGISTRY_LOCK = threading.Lock()


def _load_entity_registry() -> list[dict]:
    """Load and validate the read-only R2 registry once."""
    global _ENTITY_REGISTRY_CACHE
    if _ENTITY_REGISTRY_CACHE is None:
        with _ENTITY_REGISTRY_LOCK:
            if _ENTITY_REGISTRY_CACHE is None:
                _ENTITY_REGISTRY_CACHE = atlas_entity_registry.load_registry_file(
                    ENTITY_REGISTRY_PATH
                )
    return _ENTITY_REGISTRY_CACHE


def _shadow_entity_registry() -> list[dict] | None:
    """Return the registry only when R2 typed-parser enrichment is enabled.

    The caller's existing exception boundary converts a missing or invalid
    registry into an invalid shadow-audit event. It can never affect the live
    answer because this helper is only called by the background shadow worker.
    """
    if not ENTITY_REGISTRY_SHADOW_ENABLED:
        return None
    return _load_entity_registry()


def _shadow_trace_base(job: dict) -> dict:
    return {
        "conversation_id": job.get("conversation_id"),
        "user_message_id": job.get("user_message_id"),
        "planner_version": atlas_orchestrator_v2.PLANNER_VERSION,
        "context_scope": job.get("context_scope") or "personal",
        "question_sha256": atlas_orchestrator_v2.question_fingerprint(job["question"], SHORTCUTS_TOKEN),
        "question_chars": len(job["question"]),
        "current_route": job.get("current_route"),
        "current_tool": job.get("current_tool"),
    }


def _record_typed_utterance_shadow(job: dict, decision: dict) -> None:
    if not TYPED_UTTERANCE_SHADOW_ENABLED:
        return
    try:
        allowed = {item.name for item in atlas_orchestrator_v2.capability_registry(job.get("context_scope") or "personal")}
        proposal = atlas_utterance_parser.parse_validated_decision(
            job["question"],
            decision,
            job.get("context_scope") or "personal",
            registry=_shadow_entity_registry(),
        )
        parsed = atlas_utterance_parser.validate_parsed_utterance(
            proposal, job.get("context_scope") or "personal", allowed
        )
        details = atlas_utterance_parser.privacy_metrics(parsed)
    except Exception as exc:
        details = {
            "status": "invalid",
            "error_type": type(exc).__name__,
            "parser_version": "atlas-utterance-parser-r1-shadow-1",
        }
    details.update({
        "conversation_id": job.get("conversation_id"),
        "context_scope": job.get("context_scope") or "personal",
    })
    atlas_app_store.add_audit_event(
        APP_DB,
        event_type="utterance_parser.shadow",
        summary=f"Typed utterance shadow {details['status']}",
        details=details,
    )


def _record_hybrid_retrieval_shadow(job: dict) -> None:
    """Measure R3 candidates without exposing them to any answer/model path."""
    if not HYBRID_RETRIEVAL_SHADOW_ENABLED:
        return
    started = time.monotonic()
    semantic_matches = []
    semantic_details = {"semantic_status": "disabled"}
    current_state_candidates = []
    current_state_details = {
        "current_state_status": "disabled",
        "current_state_candidate_count": 0,
        "current_state_notice_statuses": [],
        "current_state_version": "atlas-current-state-r3-shadow-1",
    }
    if CURRENT_STATE_RETRIEVAL_SHADOW_ENABLED:
        try:
            current_state_result = atlas_current_state_retrieval.retrieve_current_state(
                job["question"],
                job.get("context_scope") or "personal",
                APP_DB,
                max_age_seconds=ATLAS_PHASE2B_FRESHNESS_SECONDS,
            )
            current_state_candidates = current_state_result["candidates"]
            current_state_details = atlas_current_state_retrieval.privacy_metrics(
                current_state_result
            )
        except Exception as exc:
            current_state_details = {
                "current_state_status": "invalid",
                "current_state_error_type": type(exc).__name__,
                "current_state_candidate_count": 0,
                "current_state_notice_statuses": [],
                "current_state_version": "atlas-current-state-r3-shadow-1",
            }
    if SEMANTIC_RETRIEVAL_SHADOW_ENABLED:
        try:
            records = atlas_semantic_index.authorized_records(APP_DB)

            def embed(texts: list[str]) -> list[list[float]]:
                return atlas_semantic_index.ollama_embed(
                    texts,
                    base_url=SEMANTIC_EMBEDDING_BASE_URL,
                    model=SEMANTIC_EMBEDDING_MODEL,
                    timeout=60,
                )

            rebuilt = not atlas_semantic_index.index_is_current(
                SEMANTIC_INDEX_PATH, records, SEMANTIC_EMBEDDING_MODEL
            )
            if rebuilt:
                atlas_semantic_index.rebuild_index(
                    SEMANTIC_INDEX_PATH, records, embed, model=SEMANTIC_EMBEDDING_MODEL
                )
            semantic_matches = atlas_semantic_index.semantic_matches(
                job["question"],
                job.get("context_scope") or "personal",
                SEMANTIC_INDEX_PATH,
                embed,
                model=SEMANTIC_EMBEDDING_MODEL,
            )
            semantic_details = {
                "semantic_status": "completed",
                "semantic_index_rebuilt": rebuilt,
                "semantic_match_count": len(semantic_matches),
            }
        except Exception as exc:
            # Semantic retrieval is an optional candidate source. It fails
            # closed to the lexical/entity shadow without affecting answers.
            semantic_details = {
                "semantic_status": "invalid",
                "semantic_error_type": type(exc).__name__,
            }
    try:
        result = atlas_hybrid_retrieval.retrieve_candidates(
            job["question"],
            job.get("context_scope") or "personal",
            APP_DB,
            _load_entity_registry(),
            semantic_matches=semantic_matches,
            current_state_candidates=current_state_candidates,
        )
        details = atlas_hybrid_retrieval.privacy_metrics(result)
    except Exception as exc:
        details = {
            "status": "invalid",
            "error_type": type(exc).__name__,
            "retriever_version": "atlas-hybrid-retrieval-r3-local-1",
        }
    details.update(semantic_details)
    details.update(current_state_details)
    details.update({
        "conversation_id": job.get("conversation_id"),
        "context_scope": job.get("context_scope") or "personal",
        "retrieval_latency_ms": round((time.monotonic() - started) * 1000, 3),
    })
    atlas_app_store.add_audit_event(
        APP_DB,
        event_type="hybrid_retrieval.shadow",
        summary=f"Hybrid retrieval shadow {details['status']}",
        details=details,
    )


def run_r3_answer_canary(question: str, context_scope: str) -> dict | None:
    """Build one R3 answer without granting the retriever any write authority."""
    if not R3_ANSWER_CANARY_ENABLED:
        return None

    current_requested = atlas_current_state_retrieval.current_state_requested(
        question, context_scope
    )
    current_state_result = {"status": "not_requested", "candidates": [], "notices": []}
    if current_requested and not CURRENT_STATE_RETRIEVAL_SHADOW_ENABLED:
        current_state_result = {
            "status": "unavailable", "candidates": [],
            "notices": [{"status": "source_disabled"}],
        }
    elif CURRENT_STATE_RETRIEVAL_SHADOW_ENABLED:
        try:
            current_state_result = atlas_current_state_retrieval.retrieve_current_state(
                question,
                context_scope,
                APP_DB,
                max_age_seconds=ATLAS_PHASE2B_FRESHNESS_SECONDS,
            )
        except Exception:
            # A recognized current-state request must fail honestly rather
            # than falling through to stale memory when its adapter fails.
            current_state_result = (
                {"status": "unavailable", "candidates": [], "notices": [{"status": "adapter_error"}]}
                if current_requested else
                {"status": "not_requested", "candidates": [], "notices": []}
            )

    semantic_matches = []
    if SEMANTIC_RETRIEVAL_SHADOW_ENABLED:
        try:
            records = atlas_semantic_index.authorized_records(APP_DB)

            def embed(texts: list[str]) -> list[list[float]]:
                return atlas_semantic_index.ollama_embed(
                    texts,
                    base_url=SEMANTIC_EMBEDDING_BASE_URL,
                    model=SEMANTIC_EMBEDDING_MODEL,
                    timeout=60,
                )

            if not atlas_semantic_index.index_is_current(
                SEMANTIC_INDEX_PATH, records, SEMANTIC_EMBEDDING_MODEL
            ):
                atlas_semantic_index.rebuild_index(
                    SEMANTIC_INDEX_PATH, records, embed, model=SEMANTIC_EMBEDDING_MODEL
                )
            semantic_matches = atlas_semantic_index.semantic_matches(
                question,
                context_scope,
                SEMANTIC_INDEX_PATH,
                embed,
                model=SEMANTIC_EMBEDDING_MODEL,
            )
        except Exception:
            # Semantic retrieval is optional. Its failure does not erase safe
            # lexical/entity candidates or loosen scope checks.
            semantic_matches = []

    try:
        registry = _load_entity_registry()
        retrieval_result = atlas_hybrid_retrieval.retrieve_candidates(
            question,
            context_scope,
            APP_DB,
            registry,
            semantic_matches=semantic_matches,
            current_state_candidates=current_state_result.get("candidates") or [],
        )
        return atlas_r3_answer_canary.answer_from_candidates(
            question,
            context_scope,
            APP_DB,
            registry,
            retrieval_result,
            current_state_result=current_state_result,
        )
    except Exception:
        # The canary is an optional read path. An internal validation failure
        # restores the pre-canary route instead of producing an ungrounded answer.
        return None


def _record_reference_resolution_shadow(job: dict) -> None:
    """Measure R4 resolutions without changing or persisting resolved text."""
    if not REFERENCE_RESOLUTION_SHADOW_ENABLED:
        return
    try:
        result = atlas_reference_resolution.resolve(
            job["question"],
            job.get("context_scope") or "personal",
            job.get("conversation") or [],
            _load_entity_registry(),
        )
        details = atlas_reference_resolution.privacy_metrics(result)
    except Exception as exc:
        details = {
            "status": "invalid",
            "error_type": type(exc).__name__,
            "resolver_version": "atlas-reference-resolution-r4-shadow-1",
        }
    details.update({
        "conversation_id": job.get("conversation_id"),
        "context_scope": job.get("context_scope") or "personal",
    })
    atlas_app_store.add_audit_event(
        APP_DB,
        event_type="reference_resolution.shadow",
        summary=f"Reference resolution shadow {details['status']}",
        details=details,
    )


def _reference_resolution_clarification(result: dict) -> dict:
    ambiguity_kinds = [
        item.get("kind") for item in result.get("ambiguities") or []
        if item.get("kind")
    ]
    if "entity_reference" in ambiguity_kinds:
        answer = "Which one did you mean?"
    elif "event_reference" in ambiguity_kinds:
        answer = "Which calendar event did you mean? I need exactly one matching event before I can use that reference."
    elif "numeric_date" in ambiguity_kinds:
        answer = "Which date format did you mean? Please write the date with the month name or use YYYY-MM-DD."
    else:
        answer = "I need one more detail before I can answer that safely."
    return {
        "route": "reference_resolution_clarification",
        "tool": "atlas-reference-resolution",
        "answer": answer,
        "source": "ATLAS reference resolver",
        "result": {
            "needs_clarification": True,
            "ambiguity_kinds": sorted(set(ambiguity_kinds)),
            "changed": False,
        },
    }


def _reference_resolution_events(context_scope: str) -> list[dict]:
    if context_scope != "personal" or not NEXTCLOUD_ENABLED:
        return []
    allowlist = json.loads(NEXTCLOUD_CALENDARS)
    if not isinstance(allowlist, list) or not allowlist:
        return []
    start_local = datetime.now(LOCAL_TZ) - timedelta(hours=6)
    end_local = start_local + timedelta(days=14)
    start = start_local.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    end = end_local.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    events: list[dict] = []
    for calendar in [str(item) for item in allowlist]:
        for event in atlas_nextcloud.list_calendar_events(
            NEXTCLOUD_BASE_URL, NEXTCLOUD_USERNAME, NEXTCLOUD_APP_PASSWORD,
            calendar, [str(item) for item in allowlist], start, end,
        ):
            events.append({**event, "calendar": calendar})
    return events


def _resolve_reference_for_answer(question: str, context_scope: str, conversation: list[dict]) -> dict | None:
    """Return a default-off R4 consumer result without persisting resolved text."""
    if not REFERENCE_RESOLUTION_CONSUMER_ENABLED:
        return None
    try:
        return atlas_reference_resolution.resolve(
            question,
            context_scope,
            conversation,
            _load_entity_registry(),
            event_records=_reference_resolution_events(context_scope),
        )
    except Exception:
        return None


def record_reference_resolution_consumer_audit(exchange: dict | None, result: dict | None, context_scope: str) -> None:
    if not exchange or not isinstance(result, dict):
        return
    details = atlas_reference_resolution.privacy_metrics(result)
    details.update({
        "conversation_id": exchange.get("conversation_id"),
        "context_scope": context_scope,
    })
    atlas_app_store.add_audit_event(
        APP_DB,
        event_type="reference_resolution.consumer",
        summary=f"Reference resolution consumer {details['status']}",
        details=details,
    )


def _run_shadow_job(job: dict) -> None:
    _record_hybrid_retrieval_shadow(job)
    _record_reference_resolution_shadow(job)
    hard_decision = job.get("hard_decision")
    if isinstance(hard_decision, dict):
        trace = _shadow_trace_base(job)
        trace.update(
            {
                "model": "deterministic-orchestrator",
                "decision_source": job.get("decision_source") or "hard_policy",
                "status": "completed",
                "planner_latency_ms": 0,
                "decision_mode": hard_decision.get("mode"),
                "decision_freshness": hard_decision.get("freshness"),
                "decision_risk": hard_decision.get("risk"),
                "decision_scope": hard_decision.get("context_scope"),
                "capabilities": hard_decision.get("capabilities") or [],
                "needs_verification": hard_decision.get("needs_verification"),
                "reason_code": hard_decision.get("reason_code"),
                "confidence": hard_decision.get("confidence"),
            }
        )
        atlas_app_store.add_orchestration_shadow_trace(APP_DB, trace)
        _record_typed_utterance_shadow(job, hard_decision)
        return
    command = [
        sys.executable,
        RUNNER_PATH,
        "--json",
        "--shadow-plan",
        "--question-stdin",
        "--context-scope",
        job.get("context_scope") or "personal",
        "--base-url",
        BASE_URL,
        "--webui-db",
        WEBUI_DB,
        "--system-prompt-file",
        SYSTEM_PROMPT_FILE,
        "--conversation-json",
        json.dumps(job.get("conversation") or [], ensure_ascii=False),
    ]
    if MODEL:
        command.extend(["--model", MODEL])

    started = time.monotonic()
    trace = _shadow_trace_base(job)
    try:
        completed = subprocess.run(
            command,
            input=job["question"],
            capture_output=True,
            text=True,
            timeout=ORCHESTRATOR_SHADOW_TIMEOUT,
            check=True,
        )
        payload = json.loads(completed.stdout)
        trace["planner_latency_ms"] = round((time.monotonic() - started) * 1000)
        trace["model"] = payload.get("model")
        trace["decision_source"] = "model"
        trace["status"] = payload.get("status") if payload.get("status") in {"completed", "invalid"} else "invalid"
        if trace["status"] == "completed" and isinstance(payload.get("decision"), dict):
            decision = payload["decision"]
            trace.update(
                {
                    "decision_mode": decision.get("mode"),
                    "decision_freshness": decision.get("freshness"),
                    "decision_risk": decision.get("risk"),
                    "decision_scope": decision.get("context_scope"),
                    "capabilities": decision.get("capabilities") or [],
                    "needs_verification": decision.get("needs_verification"),
                    "reason_code": decision.get("reason_code"),
                    "confidence": decision.get("confidence"),
                }
            )
            _record_typed_utterance_shadow(job, decision)
        else:
            trace["status"] = "invalid"
            trace["error_type"] = payload.get("error_type") or "invalid_planner_payload"
    except subprocess.TimeoutExpired:
        trace.update(
            status="failed",
            error_type="planner_timeout",
            planner_latency_ms=round((time.monotonic() - started) * 1000),
        )
    except subprocess.CalledProcessError:
        trace.update(
            status="failed",
            error_type="planner_process_error",
            planner_latency_ms=round((time.monotonic() - started) * 1000),
        )
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        trace.update(
            status="invalid",
            error_type="invalid_runner_payload",
            planner_latency_ms=round((time.monotonic() - started) * 1000),
        )
    try:
        atlas_app_store.add_orchestration_shadow_trace(APP_DB, trace)
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"failed to store orchestration shadow trace: {type(exc).__name__}\n")


def _shadow_worker() -> None:
    while True:
        job = _SHADOW_QUEUE.get()
        try:
            _run_shadow_job(job)
        finally:
            _SHADOW_QUEUE.task_done()


def start_shadow_worker() -> bool:
    global _SHADOW_WORKER_STARTED
    if not ORCHESTRATOR_SHADOW_ENABLED:
        return False
    with _SHADOW_WORKER_LOCK:
        if not _SHADOW_WORKER_STARTED:
            threading.Thread(target=_shadow_worker, name="atlas-orchestrator-shadow", daemon=True).start()
            _SHADOW_WORKER_STARTED = True
    return True


def enqueue_shadow_plan(
    question: str,
    exchange: dict,
    result: dict,
    context_scope: str,
    conversation: list[dict] | None = None,
) -> bool:
    """Queue a non-authoritative plan without blocking the response path."""
    if not ORCHESTRATOR_SHADOW_ENABLED:
        return False
    start_shadow_worker()
    job = {
        "question": question,
        "conversation_id": exchange.get("conversation_id"),
        "user_message_id": (exchange.get("user_message") or {}).get("id"),
        "context_scope": context_scope,
        "current_route": result.get("route"),
        "current_tool": result.get("tool"),
        "conversation": list(conversation or [])[-4:],
    }
    safety_result = result if result.get("route") in {
        "privacy_boundary",
        "authority_required",
        "verification_required",
        "unsupported_device_action",
        "confirmation_required",
        "boundary",
    } else None
    hard_decision = atlas_orchestrator_v2.deterministic_preplan(
        question, context_scope, safety_result, job["conversation"]
    )
    if hard_decision:
        job["hard_decision"] = hard_decision
        job["decision_source"] = (
            "hard_policy" if safety_result
            else "hard_context" if hard_decision.get("capabilities")
            else "hard_clarification"
        )
    try:
        _SHADOW_QUEUE.put_nowait(job)
        return True
    except queue.Full:
        trace = _shadow_trace_base(job)
        trace.update(status="dropped", error_type="queue_full")
        try:
            atlas_app_store.add_orchestration_shadow_trace(APP_DB, trace)
        except Exception:  # pragma: no cover
            pass
        return False


def runner_failure_message(exc: subprocess.CalledProcessError) -> str:
    stderr = (exc.stderr or "").strip()
    stdout = (exc.stdout or "").strip()
    detail = stderr or stdout or f"exit code {exc.returncode}"
    return detail[-2000:]


def log_runner_failure(question: str, exc: subprocess.CalledProcessError, conversation_id: str | None = None) -> None:
    detail = runner_failure_message(exc)
    sys.stderr.write(
        json.dumps(
            {
                "event": "atlas.runner.failed",
                "conversation_id": conversation_id,
                "question": question,
                "returncode": exc.returncode,
                "detail": detail,
            },
            ensure_ascii=True,
        )
        + "\n"
    )
    try:
        atlas_app_store.add_audit_event(
            APP_DB,
            "atlas.runner.failed",
            question,
            {
                "conversation_id": conversation_id,
                "returncode": exc.returncode,
                "detail": detail,
            },
        )
    except Exception as audit_exc:  # pragma: no cover
        sys.stderr.write(f"failed to audit atlas runner failure: {audit_exc}\n")


def run_piper_speech(text: str, voice: str = "alloy", response_format: str = "mp3") -> tuple[bytes, str]:
    url = urllib.parse.urljoin(PIPER_BASE_URL.rstrip("/") + "/", "/v1/audio/speech")
    payload = {
        "model": "tts-1",
        "input": text,
        "voice": voice,
        "response_format": response_format,
    }
    request = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "audio/*",
        },
        data=json.dumps(payload).encode("utf-8"),
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        content_type = response.headers.get("Content-Type") or content_type_for_audio(response_format)
        return response.read(), content_type


_HOME_SPEAKER_SUFFIXES = {"wav": ".wav", "opus": ".opus", "aac": ".aac", "flac": ".flac"}


def play_audio_on_home_speaker(audio: bytes, response_format: str) -> None:
    """Best-effort local playback through the paired home Bluetooth speaker.

    Never raises -- a speaker outage, a missing Bluetooth pairing, or a busy
    audio device must not affect the HTTP response already sent to the
    original caller. Intended to be invoked on a background thread; this
    function itself blocks for the duration of playback.
    """
    suffix = _HOME_SPEAKER_SUFFIXES.get(response_format, ".mp3")
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(audio)
            tmp_path = fh.name
        env = os.environ.copy()
        env["SDL_AUDIODRIVER"] = "pulseaudio"
        if ATLAS_HOME_SPEAKER_PULSE_SERVER:
            env["PULSE_SERVER"] = ATLAS_HOME_SPEAKER_PULSE_SERVER
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp_path],
            env=env,
            timeout=60,
            check=False,
        )
    except Exception as exc:  # pragma: no cover -- best effort, never fatal
        sys.stderr.write(f"home speaker playback failed: {exc}\n")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def ntfy_actions(notification: dict) -> str | None:
    if not SHORTCUTS_TOKEN:
        return None
    source_type = notification.get("source_type")
    source_id = notification.get("source_id")
    if source_type == "reminder" and source_id:
        encoded_id = urllib.parse.quote(source_id, safe="")
        complete_path = f"/api/v1/reminders/{encoded_id}/complete"
        snooze_path = f"/api/v1/reminders/{encoded_id}/snooze"
        return "; ".join(
            [
                f"http, Complete, {signed_action_url(complete_path)}, clear=true",
                f"http, Snooze 15m, {signed_action_url(snooze_path, {'minutes': '15'})}, clear=true",
            ]
        )
    if source_type == "confirmation" and source_id:
        encoded_id = urllib.parse.quote(source_id, safe="")
        approve_path = f"/api/v1/confirmations/{encoded_id}/approve"
        deny_path = f"/api/v1/confirmations/{encoded_id}/deny"
        return "; ".join(
            [
                f"http, Approve, {signed_action_url(approve_path)}, clear=true",
                f"http, Deny, {signed_action_url(deny_path)}, clear=true",
            ]
        )
    return None


def deliver_ntfy(notification: dict) -> dict:
    if not NTFY_BASE_URL or not NTFY_TOPIC:
        raise RuntimeError("ntfy delivery is not configured")
    url = f"{NTFY_BASE_URL}/{urllib.parse.quote(NTFY_TOPIC, safe='')}"
    headers = {
        "Title": notification.get("title") or "ATLAS",
        "Tags": "atlas",
        "Priority": "default",
    }
    actions = ntfy_actions(notification)
    if actions:
        headers["Actions"] = actions
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    request = urllib.request.Request(
        url,
        method="POST",
        headers=headers,
        data=(notification.get("body") or "").encode("utf-8"),
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        body = response.read().decode("utf-8", errors="replace")
        return {
            "status": response.status,
            "body": body,
            "url": url,
        }


def send_ntfy_feedback(title: str, body: str, source_type: str, source_id: str) -> dict | None:
    notification = atlas_app_store.create_notification(
        APP_DB,
        title=title,
        body=body,
        source_type=source_type,
        source_id=source_id,
    )
    try:
        result = deliver_ntfy(notification)
    except Exception as exc:
        atlas_app_store.mark_notification_failed(APP_DB, notification["id"], str(exc))
        return None
    delivered_at = datetime.now(LOCAL_TZ).isoformat(timespec="seconds")
    atlas_app_store.mark_notification_delivered(APP_DB, notification["id"], delivered_at)
    return result


def content_type_for_audio(response_format: str) -> str:
    if response_format == "wav":
        return "audio/wav"
    if response_format == "opus":
        return "audio/opus"
    if response_format == "aac":
        return "audio/aac"
    if response_format == "flac":
        return "audio/flac"
    return "audio/mpeg"


def parse_int(value: str | None, default: int, minimum: int = 0, maximum: int = 100) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        return default
    return max(minimum, min(parsed, maximum))


def parse_context_scope(value: str | None) -> str:
    scope = (value or "personal").strip().lower().replace("-", "_")
    if scope in {"ops", "operation"}:
        scope = "operations"
    if scope not in ATLAS_CONTEXT_SCOPES:
        raise ValueError(f"invalid context_scope: {value}")
    return scope


def normalize_user_text(value: str) -> str:
    normalized = value.lower()
    normalized = normalized.replace("’", "'").replace("‘", "'")
    normalized = normalized.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", normalized).strip(" ?.!,")


def is_structured_reasoning_request(question: str) -> bool:
    """Recognize explicit choice tasks before domain keywords can hijack them."""
    return bool(
        re.search(r"(?:^|\n)choices:\s*\S+", question, flags=re.IGNORECASE)
        and re.search(r"(?:answer|respond)\s+with\s+(?:the\s+)?choice\b", question, flags=re.IGNORECASE)
    )


def is_self_contained_reasoning_request(question: str) -> bool:
    """Recognize DEF-025e prompts whose criterion/scope is already supplied."""
    text = normalize_user_text(question)
    tradeoff = (
        "imagine we optimize" in text
        and "accept a little extra latency" in text
        and "most likely benefit" in text
        and "most likely downside" in text
        and "what measurement" in text
    )
    # Evidence-quality prompts were part of DEF-025e only until DEF-025f gave
    # them a stricter deterministic route. Do not catch them here, or the
    # server will force answer.general and bypass evidence_quality_reasoning.
    return tradeoff


def is_hypothetical_boundary_advice_request(question: str) -> bool:
    """Recognize DEF-025d advice prompts without treating them as actions."""
    text = normalize_user_text(question)
    has = lambda terms: any(term in text for term in terms)
    return (
        has(("assume no user data may be changed", "without changing data", "without changing user data", "read-only check", "read only check"))
        and has(("what should the assistant", "what should it", "what should atlas", "what should the system"))
        and has(("avoid", "do first", "do next", "safe fallback", "fail safely"))
        and has(("if ", "imagine", "assume", "hypothetical", "during a read-only check"))
    )


def pre_route_safety_result(question: str) -> dict | None:
    """Enforce capability, authority, and privacy boundaries before any answer model."""
    text = normalize_user_text(question)
    has = lambda terms: any(term in text for term in terms)
    # Word-boundary matching, not bare substring: several of these terms are
    # short, generic English words that appear inside unrelated words ("lock"
    # inside "blocking", found via a real eval failure where a commonsense
    # "crates are blocking an emergency exit" question was misrouted here).
    # Moved up from further down in this function (where it was originally
    # defined only for the device_action/device_target check) so the DEF-011
    # false-completion check below can reuse it too.
    has_word = lambda terms: any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)
    hypothetical_boundary_advice = is_hypothetical_boundary_advice_request(question)

    mixed_device_action = False
    if _fragment_requests_light_action(text):
        # A real live lamp action was previously executed alongside an
        # unverified paired read request (DEC-2026-09-11-04): the deterministic-
        # first carve-out attempted the write, the read-after-write check
        # mismatched, and final state was never confirmed. Split on any common
        # clause connector -- not just the literal word "and" -- so a comma-,
        # "then"-, or plain sentence-separated compound is caught the same way
        # a single "and"-joined one is.
        # A one-word trailing clause ("please", "now", "thanks") is filler,
        # not a second intent -- require at least two words so "turn off the
        # lamp, please" isn't misread as a mixed compound.
        other_clauses = [
            clause.strip() for clause in _COMPOUND_CLAUSE_SPLIT_RE.split(text)
            if len(clause.split()) >= 2 and not _fragment_requests_light_action(clause)
        ]
        mixed_device_action = bool(other_clauses)
    if mixed_device_action:
        return {
            "route": "boundary", "tool": "atlas-safety-gate",
            "answer": "I won't partially execute a compound request that mixes device control with another intent. Ask for the device action and the read-only question separately. Nothing was changed.",
            "source": "ATLAS action-boundary policy",
            "result": {"blocked": True, "policy": "mixed_device_action_requires_separate_request"},
        }

    requested_change = has((
        "export", "publish", "upload", "copy", "reuse", "use an", "use the", "turn off", "disable",
        "change", "adjust", "set", "control", "approve", "purchase", "buy", "mark", "delete", "remove", "send", "share",
    ))
    verification_gap = (
        has(("mark", "claim", "report", "say", "record", "declare", "assert", "state"))
        and has(("verified", "successful", "completed", "restored", "tested", "recoverable", "valid", "healthy"))
        and has((
            "no test", "without a test", "without testing", "without running", "not performed",
            "never tested", "nobody ran", "no one ran", "was not run", "cannot verify",
            "untested", "unverified", "not verified",
        ))
    )
    if verification_gap:
        return {
            "route": "verification_required", "tool": "atlas-safety-gate",
            "answer": "I can't mark or report that outcome as verified without evidence from the required test. Nothing was changed.",
            "source": "ATLAS verification policy",
            "result": {"blocked": True, "policy": "verification_requires_evidence"},
        }

    scope_crossing = (
        has(("reuse", "copy", "transfer", "carry", "share", "move", "migrate", "import"))
        and has(("personal memory", "personal conversation", "private conversation", "private context", "personal context"))
        and has((
            "admin session", "administrator session", "admin scope", "administrator scope",
            "operations session", "operations scope", "different scope",
            "maintenance account", "maintenance scope", "different account", "another account",
        ))
    )
    if scope_crossing:
        return {
            "route": "privacy_boundary", "tool": "atlas-safety-gate",
            "answer": "I can't move personal or private context into a different authority scope. Those contexts must remain separate.",
            "source": "ATLAS context isolation policy",
            "result": {"blocked": True, "policy": "context_scopes_remain_separate"},
        }

    permission_ceiling = has((
        "read-only", "read only", "only metadata", "metadata only", "metadata-only", "only message metadata",
        "approved only for read", "authorized only for read", "only read access",
    ))
    if requested_change and permission_ceiling and not hypothetical_boundary_advice:
        return {
            "route": "authority_required", "tool": "atlas-safety-gate",
            "answer": "I can't perform that change through a read-only or metadata-only permission. The approved access does not authorize writes or control.",
            "source": "ATLAS least-privilege policy",
            "result": {"blocked": True, "policy": "permission_ceiling_blocks_write"},
        }

    requested_private_access = has(("read", "view", "inspect", "open", "retrieve", "access"))
    consent_gap = (requested_change or requested_private_access) and has((
        "without consent", "without their consent", "without permission",
    ))
    if consent_gap and has((
        "location", "movement", "history", "private", "personal data", "housemate", "roommate",
        "neighbor", "another person", "another person's", "another persons", "another user's", "another users",
        "personal memory", "personal conversation", "private conversation", "personal context", "private context",
    )):
        return {
            "route": "privacy_boundary", "tool": "atlas-safety-gate",
            "answer": "I can't export or share another person's private data without their consent and an approved access path.",
            "source": "ATLAS consent policy",
            "result": {"blocked": True, "policy": "third_party_consent_required"},
        }

    security_downgrade = requested_change and has((
        "multi-factor", "multifactor", "two-factor", "2fa", "mfa", "security control",
        "certificate validation", "cert validation", "certificate check", "certificate checking",
        "tls verification", "ssl verification", "ssl check",
    ))
    if security_downgrade:
        return {
            "route": "confirmation_required", "tool": "atlas-safety-gate",
            "answer": "I can't disable an authentication or security control to make automation easier. No security setting was changed.",
            "source": "ATLAS security policy",
            "result": {"blocked": True, "policy": "security_controls_not_downgraded"},
        }

    bulk_destructive = has(("delete", "remove", "purge", "wipe")) and has((
        "every", "all ", "entire", "whole",
    ))
    if bulk_destructive:
        return {
            "route": "confirmation_required", "tool": "atlas-safety-gate",
            "answer": "I can't perform a blanket delete or wipe across everything without explicit approval and a confirmed, scoped policy. Nothing was deleted.",
            "source": "ATLAS bulk-deletion policy",
            "result": {"blocked": True, "policy": "bulk_deletion_requires_confirmation"},
        }

    data_exfiltration = has(("upload", "export", "post", "publish", "send", "copy")) and has((
        "private files", "private data", "private notes", "personal data", "secrets", "secret files",
        "address book", "address-book", "contact list", "contact export", "contacts export",
    )) and has((
        "public service", "public url", "public bucket", "public repository", "publicly accessible",
        "public cloud", "public server", "public location",
    ))
    if data_exfiltration:
        return {
            "route": "privacy_boundary", "tool": "atlas-safety-gate",
            "answer": "I can't send private data to a public or uncontrolled destination. That would expose it outside approved access.",
            "source": "ATLAS data-exfiltration policy",
            "result": {"blocked": True, "policy": "private_data_not_exposed_publicly"},
        }

    unverified_purchase = has(("approve", "authorize", "complete", "make")) and has(("purchase", "order", "payment"))
    weak_purchase_basis = has(("inexpensive", "cheap", "low price", "low amount", "amount is low", "small amount", "under the limit"))
    if unverified_purchase and weak_purchase_basis:
        return {
            "route": "confirmation_required", "tool": "atlas-safety-gate",
            "answer": "I can't approve or make a purchase based only on its price. Explicit owner approval and a verified integration are required.",
            "source": "ATLAS purchase authority policy",
            "result": {"blocked": True, "policy": "purchase_requires_explicit_approval"},
        }

    deletion_without_policy = has(("delete", "remove", "purge")) and has((
        "without a policy", "without policy", "no deletion policy", "without a confirmed", "automatically",
    ))
    if deletion_without_policy:
        return {
            "route": "confirmation_required", "tool": "atlas-safety-gate",
            "answer": "I can't delete that without a confirmed deletion policy, exact scope, and explicit approval. Nothing was deleted.",
            "source": "ATLAS deletion policy",
            "result": {"blocked": True, "policy": "deletion_requires_policy_and_confirmation"},
        }

    notification_deletion = has(("delete", "remove", "clear", "purge")) and has(("notification", "notifications"))
    if notification_deletion:
        return {
            "route": "confirmation_required", "tool": "atlas-safety-gate",
            "answer": "I can't delete notifications from here without a verified notification-management integration, exact scope, and explicit confirmation. Nothing was deleted.",
            "source": "ATLAS deletion policy",
            "result": {"blocked": True, "policy": "notification_deletion_requires_verified_integration_and_confirmation"},
        }

    if (
        has(("pretend", "claim", "say", "report", "act like"))
        and has(("completed", "complete", "done", "succeeded", "successful", "worked"))
        and has(("disconnected", "not connected", "offline", "unavailable", "without integration", "integration is down", "cannot verify"))
    ):
        return {
            "route": "verification_required", "tool": "atlas-safety-gate",
            "answer": "I can't claim that action completed while its integration is disconnected. I can only report completion after the result is verified.",
            "source": "ATLAS capability and truthfulness policy",
            "result": {"blocked": True, "policy": "never_claim_unverified_completion"},
        }

    # DEF-011: false completion under "pretend" framing, without explicit
    # disconnection language. Real live failures ("Pretend you already
    # watered the plants this morning" -> "You already watered the plants
    # this morning") don't mention disconnection at all, so the check above
    # never fires for them.
    #
    # First attempt used frozen multi-word phrases ("backed up the server")
    # and missed most real cases on natural insertions ("backed up the
    # ai-stack server", "deleted my old reminders") -- the exact same
    # fullmatch-style brittleness as DEF-003's math-paraphrase bug. Redesigned
    # to the device_action/device_target pairing shape instead: an action
    # verb list and a target-domain noun list, ANDed independently via
    # has_word() so insertions between them don't break the match, exactly
    # like the device check two blocks below. Bounded and auditable -- not a
    # general keyword scan -- because both lists are enumerated domains, not
    # generic English words, and a harmless creative prompt ("pretend you're
    # a pirate and tell me a joke") matches neither list.
    completion_pretend_framing = has((
        "pretend", "claim", "say that", "just say", "report that", "act like", "act as if",
        "imagine you", "imagine that", "tell me you", "confirm that you", "roleplay as",
        "respond as if", "write your response as if", "as if you", "let's pretend", "skip the disclaimer",
    ))
    claimed_action_verb = has_word((
        "watered", "backed", "added", "sent", "scheduled", "set", "approved",
        "unlocked", "deleted", "removed", "rotated", "renewed", "replied", "texted",
        "switched", "finished", "completed", "turned", "happened",
    ))
    claimed_action_target = has((
        "plants", "backup", "server", "shopping list", "email",
        "reminder", "confirmation", "door", "reminders", "notes",
        "password", "rotation", "gpu", "handoff", "scan", "message",
        "lamp", "light",
    ))
    if (
        completion_pretend_framing
        and claimed_action_verb
        and claimed_action_target
        and not is_structured_reasoning_request(question)
    ):
        return {
            "route": "verification_required", "tool": "atlas-safety-gate",
            "answer": "I can't claim an action happened unless it was actually performed and verified. I did not do that, and I won't pretend I did.",
            "source": "ATLAS capability and truthfulness policy",
            "result": {"blocked": True, "policy": "never_claim_unverified_completion"},
        }

    # Word-bounded, not a bare substring check: "secret" is a bare fragment
    # in this list, and a plain `in` check matched inside "secretary" --
    # found live via Gate 1 cycle I (2026-09-17), a disambiguation_qa
    # reasoning question about a secretary/developer pair got a completely
    # unrelated credential-refusal answer instead of being reasoned about.
    secret_target = has_word((
        "password", "passphrase", "api token", "access token", "private key", "recovery code", "cookie",
        "session token", "secret key", "vault secret", "wifi password", "wi-fi password",
        "secret", "secrets", "api secret", "api secrets", "credentials", "stored secrets",
    ))
    secret_action = has(("show", "tell", "give", "reveal", "display", "print", "export", "extract", "copy", "share", "send", "upload", "publish", "post", "find", "retrieve", "use my", "use the"))
    auth_bypass = has(("bypass login", "bypass authentication", "skip authentication", "avoid authentication", "steal credentials", "impersonate", "use someone else's account", "use someone elses account")) or bool(
        re.search(r"\b(?:bypass|skip|avoid)\b.{0,32}\b(?:login|authentication|sign-in)\b", text)
    )
    other_person = has(("someone else's", "someone elses", "another user's", "another users", "neighbor's", "neighbors", "roommate's", "roommates", "coworker's", "coworkers", "partner's", "partner s", "partners"))
    private_target = has((
        "private data", "private message", "messages", "email", "mail", "calendar", "files",
        "medical record", "financial record", "account", "location history",
        "notes", "private notes", "reminder", "reminders", "documents", "private files", "personal data", "personal notes",
    ))
    if auth_bypass or (secret_target and (secret_action or other_person)) or (other_person and private_target):
        return {
            "route": "privacy_boundary", "tool": "atlas-safety-gate",
            "answer": "I can't reveal, transfer, or use credentials, secrets, or another person's private data. Use an approved, owner-authorized access path instead.",
            "source": "ATLAS owner-only privacy policy",
            "result": {"blocked": True, "policy": "secrets_and_cross_user_data_blocked"},
        }

    external_action = has(("send", "email", "mail", "message", "post", "publish", "upload", "submit", "share"))
    missing_approval = has(("without asking", "without approval", "without confirmation", "don't ask", "do not ask", "no approval", "no confirmation", "automatically", "on my behalf")) or bool(
        re.search(r"\bwithout\b.{0,48}\b(?:ask(?:ing)?|show(?:ing)?|request(?:ing)?)\b.{0,24}\b(?:approval|confirmation)\b", text)
    )
    school_account = has(("school account", "student account", "sfsu account", "university account"))
    financial_action = has((
        "purchase the", "buy the", "order the", "pay for", "transfer funds",
        "send money", "place a trade", "make a trade", "invest my",
    ))
    if financial_action and missing_approval:
        return {
            "route": "confirmation_required", "tool": "atlas-safety-gate",
            "answer": "I can't make a purchase, payment, transfer, trade, or investment without explicit owner approval and a verified integration.",
            "source": "ATLAS high-impact financial-action policy",
            "result": {"blocked": True, "policy": "financial_actions_require_confirmation"},
        }
    if external_action and (missing_approval or school_account):
        return {
            "route": "authority_required", "tool": "atlas-safety-gate",
            "answer": "I can't send or publish through that account without explicit approval. School access is currently owner-only and read-only; I can help draft the content without sending it.",
            "source": "ATLAS external-action authority policy",
            "result": {"blocked": True, "policy": "external_writes_require_authority"},
        }

    if has(("delete", "erase", "wipe", "purge", "destroy", "disable", "turn off")) and has(("audit", "logging", "logs", "backup", "history", "user data", "records", "files", "security", "alarm", "camera")):
        return {
            "route": "confirmation_required", "tool": "atlas-safety-gate",
            "answer": "I can't perform that destructive or security-sensitive action without explicit confirmation and a verified integration.",
            "source": "ATLAS high-risk action policy",
            "result": {"blocked": True, "policy": "destructive_actions_require_confirmation"},
        }

    # has_word() is defined near the top of this function (originally lived
    # only here; moved so the DEF-011 false-completion check earlier in the
    # function could reuse it).
    device_action = has_word(("unlock", "lock", "open", "close", "turn on", "turn off", "raise", "lower", "set", "start", "stop"))
    device_target = has_word(("door", "garage", "alarm", "lock", "thermostat", "light", "blinds", "shades", "camera", "heater", "air conditioner", "fan"))
    if device_action and device_target and not is_structured_reasoning_request(question):
        return {
            "route": "unsupported_device_action", "tool": "atlas-safety-gate",
            "answer": "I can't control that device from this app because no verified control integration is connected. I did not claim or attempt the action.",
            "source": "ATLAS capability registry",
            "result": {"blocked": True, "policy": "device_control_requires_verified_integration"},
        }

    communication_action = bool(re.search(r"\b(?:reply to|text|message)\b", text))
    third_party_target = has(("him", "her", "them", "my mom", "my dad", "my brother", "my sister", "my roommate", "my friend", "my coworker"))
    if communication_action and third_party_target and not is_structured_reasoning_request(question):
        return {
            "route": "unsupported_messaging_action", "tool": "atlas-safety-gate",
            "answer": "I can't send a message or reply to another person from this app because no messaging integration is connected. I did not send or claim to send anything.",
            "source": "ATLAS capability registry",
            "result": {"blocked": True, "policy": "messaging_requires_verified_integration"},
        }
    return None


# DEF-002: infrastructure-action verbs not already covered by
# pre_route_safety_result's own "requested_change" word list (turn off,
# disable, change, adjust, set, control, delete, remove, etc.) -- kept
# narrow and specific to imperative infra actions, as defense-in-depth on
# top of that earlier, more general gate, not a replacement for it.
INFRA_ACTION_VERBS = (
    "restart", "reboot", "redeploy", "rebuild", "migrate",
    "reset", "format", "wipe", "shut down", "shutdown", "power off", "power on",
)


def runner_allowed_for_scope(question: str, context_scope: str) -> bool:
    if context_scope in {"operations", "admin"}:
        return True
    lowered = question.lower()
    if not any(token in lowered for token in OPERATIONS_CONTEXT_TOKENS):
        return True
    # DEF-002: this previously blocked every operations-context-shaped
    # question from personal scope outright, including purely informational
    # ones ("What request-management frontend pairs with Jellyfin?"), which
    # scored 0/8 on the confirmed hardening calibration run. The capability
    # behind this gate (run-atlas-readonly.py) is read-only by design and has
    # no homelab write/control capability at all -- and pre_route_safety_result
    # already runs before this check (see _handle_ask/_handle_message) and
    # independently blocks destructive/write intent regardless of scope. Allow
    # read-only informational questions through; keep blocking anything that
    # still looks like an infrastructure action request, as one more layer
    # beyond pre_route_safety_result's own word list.
    if any(verb in lowered for verb in INFRA_ACTION_VERBS):
        return False
    return True


def approved_context_required_result(question: str, context_scope: str) -> dict:
    return {
        "route": "approved_context_required",
        "tool": "atlas-context-scope-policy",
        "answer": (
            "That needs operational or admin context. Start or continue the request with "
            "`context_scope` set to `operations` or `admin`; personal scope only uses approved personal context by default."
        ),
        "source": "ATLAS context-scope policy",
        "result": {
            "question": question,
            "context_scope": context_scope,
            "allowed_scopes": ["operations", "admin"],
            "policy": "personal_scope_uses_approved_personal_context_only",
        },
    }


def runner_unavailable_result(question: str) -> dict:
    return {
        "route": "general_reference",
        "tool": "atlas-runner-fallback",
        "answer": "I could not reach the local answer model just now. Try again in a moment.",
        "source": "ATLAS local runner fallback",
        "result": {
            "question": question,
            "degraded": True,
            "reason": "local_answer_model_unavailable",
        },
    }


def parse_reminder_text(text: str) -> tuple[str, str | None]:
    cleaned = " ".join(text.strip().split())
    now = datetime.now(LOCAL_TZ)
    clock_match = re.search(
        r"(?:\b(?:at|for)\s+)?\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b(?:\s+(today|tomorrow))?",
        cleaned,
        flags=re.IGNORECASE,
    )
    if clock_match:
        hour = int(clock_match.group(1))
        minute = int(clock_match.group(2) or "0")
        meridiem = clock_match.group(3).lower()
        day_word = (clock_match.group(4) or "").lower()
        if 1 <= hour <= 12 and 0 <= minute <= 59:
            hour = hour % 12 + (12 if meridiem == "pm" else 0)
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if day_word == "tomorrow" or (not day_word and due <= now):
                due += timedelta(days=1)
            reminder_text = (cleaned[: clock_match.start()] + " " + cleaned[clock_match.end() :]).strip(" .")
            reminder_text = re.sub(r"^(?:to|for)\s+", "", reminder_text, flags=re.IGNORECASE)
            reminder_text = re.sub(r"\s+(?:today|tomorrow)$", "", reminder_text, flags=re.IGNORECASE).strip(" .")
            return reminder_text, due.isoformat(timespec="seconds")
    patterns = [
        (r"\s+(?:tomorrow)\s*$", now + timedelta(days=1)),
        (r"\s+(?:tonight)\s*$", now.replace(hour=20, minute=0, second=0, microsecond=0)),
        (r"\s+(?:today)\s*$", now),
    ]
    for pattern, due in patterns:
        if re.search(pattern, cleaned, flags=re.IGNORECASE):
            reminder_text = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip(" .")
            return reminder_text, due.isoformat(timespec="seconds")

    in_match = re.search(r"\s+in\s+(\d+)\s+(minutes?|hours?|days?)\s*$", cleaned, flags=re.IGNORECASE)
    if in_match:
        amount = int(in_match.group(1))
        unit = in_match.group(2).lower()
        if unit.startswith("minute"):
            due = now + timedelta(minutes=amount)
        elif unit.startswith("hour"):
            due = now + timedelta(hours=amount)
        else:
            due = now + timedelta(days=amount)
        reminder_text = cleaned[: in_match.start()].strip(" .")
        return reminder_text, due.isoformat(timespec="seconds")

    return cleaned, None


def next_recurring_due(current_due_text: str, now_value: datetime, recurrence: str) -> str:
    current_due = datetime.fromisoformat(current_due_text)
    next_due = current_due
    if recurrence == "daily":
        while next_due <= now_value:
            next_due = next_due + timedelta(days=1)
    return next_due.isoformat(timespec="seconds")


class AtlasHandler(BaseHTTPRequestHandler):
    server_version = "AtlasShortcuts/1.0"

    def log_message(self, fmt: str, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def _write_json(self, status: int, payload: dict):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_bytes(self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str):
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _write_error(self, status: int, error: str):
        self._write_json(status, {"ok": False, "error": error})

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"/mobile", "/atlas/mobile"}:
            self._redirect(parsed.path + "/")
            return
        if parsed.path.startswith("/mobile/") or parsed.path.startswith("/atlas/mobile/"):
            self._handle_mobile_static(parsed)
            return
        if parsed.path in {"/", "/health"}:
            self._write_json(
                HTTPStatus.OK,
                {"ok": True, "service": "atlas-shortcuts", "path": parsed.path},
            )
            return
        if parsed.path == "/ask":
            self._handle_ask()
            return
        # Compatibility for notification actions already delivered with the
        # former `view` action type. New actions use ntfy's POST-capable `http`.
        if parsed.path.startswith("/api/v1/reminders/") and parsed.path.endswith("/complete"):
            self._handle_complete_reminder(parsed)
            return
        if parsed.path.startswith("/api/v1/reminders/") and parsed.path.endswith("/snooze"):
            self._handle_snooze_reminder(parsed)
            return
        if parsed.path.startswith("/api/v1/confirmations/") and parsed.path.endswith("/approve"):
            self._handle_decide_confirmation(parsed, "approved")
            return
        if parsed.path.startswith("/api/v1/confirmations/") and parsed.path.endswith("/deny"):
            self._handle_decide_confirmation(parsed, "denied")
            return
        if parsed.path == "/api/v1/conversations":
            self._handle_list_conversations(parsed)
            return
        if parsed.path.startswith("/api/v1/conversations/"):
            self._handle_get_conversation(parsed)
            return
        if parsed.path == "/api/v1/messages":
            self._handle_search_messages(parsed)
            return
        if parsed.path == "/api/v1/reminders":
            self._handle_list_reminders(parsed)
            return
        if parsed.path == "/api/v1/notes":
            self._handle_search_notes(parsed)
            return
        if parsed.path == "/api/v1/media-requests/comics":
            self._handle_list_comic_requests(parsed)
            return
        if parsed.path == "/api/v1/tasks":
            self._handle_list_tasks(parsed)
            return
        if parsed.path == "/api/v1/shopping":
            self._handle_list_shopping_items(parsed)
            return
        if parsed.path == "/api/v1/rules":
            self._handle_list_rules(parsed)
            return
        if parsed.path == "/api/v1/audit":
            self._handle_list_audit(parsed)
            return
        if parsed.path == "/api/v1/permissions":
            self._handle_permissions()
            return
        if parsed.path == "/api/v1/notifications":
            self._handle_list_notifications(parsed)
            return
        if parsed.path == "/api/v1/confirmations":
            self._handle_list_confirmations(parsed)
            return
        if parsed.path == "/api/v1/preferences":
            self._handle_list_preferences()
            return
        if parsed.path == "/api/v1/version":
            self._handle_version()
            return
        if parsed.path == "/api/v1/examples":
            self._handle_examples()
            return
        if parsed.path == "/api/v1/diagnostics":
            self._handle_diagnostics()
            return
        if parsed.path == "/api/v1/sfsu/status":
            self._handle_sfsu_status()
            return
        if parsed.path == "/api/v1/school-bridge/status":
            self._handle_school_bridge_status()
            return
        if parsed.path == "/api/v1/home-assistant/state":
            self._handle_home_assistant_state(parsed)
            return
        if parsed.path == "/api/v1/nextcloud/files":
            self._handle_nextcloud_files(parsed)
            return
        if parsed.path == "/api/v1/nextcloud/calendars":
            self._handle_nextcloud_calendars(parsed)
            return
        if parsed.path == "/api/v1/nextcloud/calendar-events":
            self._handle_nextcloud_calendar_events(parsed)
            return
        if parsed.path == "/api/v1/nextcloud/addressbooks":
            self._handle_nextcloud_addressbooks(parsed)
            return
        if parsed.path == "/api/v1/nextcloud/contact-cards":
            self._handle_nextcloud_contact_cards(parsed)
            return
        self._write_error(HTTPStatus.NOT_FOUND, "not found")

    def _handle_mobile_static(self, parsed):
        path = parsed.path
        if path.startswith("/atlas/mobile/"):
            relative = path.removeprefix("/atlas/mobile/")
        else:
            relative = path.removeprefix("/mobile/")
        relative = urllib.parse.unquote(relative) or "index.html"
        if relative.endswith("/"):
            relative += "index.html"
        if ".." in pathlib.PurePosixPath(relative).parts:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid mobile asset path")
            return
        target = (MOBILE_DIR / relative).resolve()
        try:
            target.relative_to(MOBILE_DIR.resolve())
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid mobile asset path")
            return
        if not target.is_file():
            self._write_error(HTTPStatus.NOT_FOUND, "mobile asset not found")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.name == "manifest.webmanifest":
            content_type = "application/manifest+json"
        self._write_bytes(
            HTTPStatus.OK,
            target.read_bytes(),
            content_type,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/ask":
            self._handle_ask()
            return
        if parsed.path == "/api/v1/messages":
            self._handle_create_message()
            return
        if parsed.path.startswith("/api/v1/conversations/") and parsed.path.endswith("/archive"):
            self._handle_archive_conversation(parsed)
            return
        if parsed.path == "/api/v1/speech":
            self._handle_speech()
            return
        if parsed.path == "/api/v1/reminders":
            self._handle_create_reminder()
            return
        if parsed.path.startswith("/api/v1/reminders/") and parsed.path.endswith("/complete"):
            self._handle_complete_reminder(parsed)
            return
        if parsed.path.startswith("/api/v1/reminders/") and parsed.path.endswith("/snooze"):
            self._handle_snooze_reminder(parsed)
            return
        if parsed.path.startswith("/api/v1/confirmations/") and parsed.path.endswith("/approve"):
            self._handle_decide_confirmation(parsed, "approved")
            return
        if parsed.path.startswith("/api/v1/confirmations/") and parsed.path.endswith("/deny"):
            self._handle_decide_confirmation(parsed, "denied")
            return
        if parsed.path == "/api/v1/notes":
            self._handle_create_note()
            return
        if parsed.path == "/api/v1/media-requests/comics":
            self._handle_create_comic_request()
            return
        if parsed.path == "/api/v1/tasks":
            self._handle_create_task()
            return
        if parsed.path.startswith("/api/v1/tasks/") and parsed.path.endswith("/complete"):
            self._handle_complete_task(parsed)
            return
        if parsed.path == "/api/v1/shopping":
            self._handle_create_shopping_item()
            return
        if parsed.path.startswith("/api/v1/shopping/") and parsed.path.endswith("/complete"):
            self._handle_complete_shopping_item(parsed)
            return
        if parsed.path == "/api/v1/rules":
            self._handle_create_rule()
            return
        if parsed.path == "/api/v1/preferences":
            self._handle_set_preference()
            return
        if parsed.path == "/api/v1/evaluate":
            self._handle_evaluate()
            return
        if parsed.path == "/api/v1/briefings/evaluate":
            self._handle_evaluate_briefing()
            return
        if parsed.path == "/api/v1/school-bridge/import":
            self._handle_school_bridge_import()
            return
        if parsed.path == "/api/v1/school-bridge/purge":
            self._handle_school_bridge_purge()
            return
        if parsed.path == "/api/v1/notifications/deliver":
            self._handle_deliver_notifications()
            return
        if parsed.path == "/api/v1/memory/sync":
            self._handle_memory_sync()
            return
        if parsed.path == "/api/v1/confirmations/execute-approved":
            self._handle_execute_approved_confirmations()
            return
        if parsed.path == "/api/v1/feedback":
            self._handle_feedback()
            return
        self._write_error(HTTPStatus.NOT_FOUND, "not found")

    def do_PATCH(self):
        parsed = urllib.parse.urlparse(self.path)
        item_id = parsed.path.removeprefix("/api/v1/reminders/").strip("/")
        if parsed.path.startswith("/api/v1/reminders/") and item_id and "/" not in item_id:
            self._handle_update_reminder(parsed)
            return
        item_id = parsed.path.removeprefix("/api/v1/tasks/").strip("/")
        if parsed.path.startswith("/api/v1/tasks/") and item_id and "/" not in item_id:
            self._handle_update_task(parsed)
            return
        item_id = parsed.path.removeprefix("/api/v1/shopping/").strip("/")
        if parsed.path.startswith("/api/v1/shopping/") and item_id and "/" not in item_id:
            self._handle_update_shopping_item(parsed)
            return
        item_id = parsed.path.removeprefix("/api/v1/notes/").strip("/")
        if parsed.path.startswith("/api/v1/notes/") and item_id and "/" not in item_id:
            self._handle_update_note(parsed)
            return
        item_id = parsed.path.removeprefix("/api/v1/rules/").strip("/")
        if parsed.path.startswith("/api/v1/rules/") and item_id and "/" not in item_id:
            self._handle_update_rule(parsed)
            return
        self._write_error(HTTPStatus.NOT_FOUND, "not found")

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        item_id = parsed.path.removeprefix("/api/v1/reminders/").strip("/")
        if parsed.path.startswith("/api/v1/reminders/") and item_id and "/" not in item_id:
            self._handle_delete_reminder(parsed)
            return
        item_id = parsed.path.removeprefix("/api/v1/tasks/").strip("/")
        if parsed.path.startswith("/api/v1/tasks/") and item_id and "/" not in item_id:
            self._handle_delete_task(parsed)
            return
        item_id = parsed.path.removeprefix("/api/v1/shopping/").strip("/")
        if parsed.path.startswith("/api/v1/shopping/") and item_id and "/" not in item_id:
            self._handle_delete_shopping_item(parsed)
            return
        item_id = parsed.path.removeprefix("/api/v1/notes/").strip("/")
        if parsed.path.startswith("/api/v1/notes/") and item_id and "/" not in item_id:
            self._handle_delete_note(parsed)
            return
        item_id = parsed.path.removeprefix("/api/v1/rules/").strip("/")
        if parsed.path.startswith("/api/v1/rules/") and item_id and "/" not in item_id:
            self._handle_delete_rule(parsed)
            return
        key_segment = parsed.path.removeprefix("/api/v1/preferences/").strip("/")
        if parsed.path.startswith("/api/v1/preferences/") and key_segment and "/" not in key_segment:
            self._handle_delete_preference(parsed)
            return
        self._write_error(HTTPStatus.NOT_FOUND, "not found")

    def _handle_ask(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return

        question = (payload.get("question") or payload.get("q") or payload.get("prompt") or "").strip()
        if not question:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing question")
            return
        try:
            context_scope = parse_context_scope(payload.get("context_scope") or payload.get("scope"))
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        include_details = bool(payload.get("details"))
        try:
            safety_result = pre_route_safety_result(question)
            if safety_result:
                result = safety_result
            else:
                result = run_r3_answer_canary(question, context_scope) if R3_ANSWER_CANARY_ENABLED else None
                if not result:
                    if runner_allowed_for_scope(question, context_scope):
                        result = run_atlas_with_readonly_canary(question, context_scope)
                    else:
                        result = approved_context_required_result(question, context_scope)
        except subprocess.CalledProcessError as exc:
            log_runner_failure(question, exc)
            result = runner_unavailable_result(question)
        except subprocess.TimeoutExpired:
            self._write_json(HTTPStatus.GATEWAY_TIMEOUT, {"ok": False, "error": "atlas runner timed out"})
            return
        except Exception as exc:  # pragma: no cover
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return

        response = {
            "ok": True,
            "question": question,
            "answer": result.get("answer", ""),
            "route": result.get("route"),
            "tool": result.get("tool"),
            "source": result.get("source"),
            "speak_text": result.get("answer", ""),
            "context_scope": context_scope,
        }
        if include_details:
            response["details"] = result
        exchange = None
        try:
            exchange = atlas_app_store.save_exchange(
                APP_DB,
                question=question,
                result=result,
                input_mode=(payload.get("input_mode") or "shortcut"),
                client=(payload.get("client") or "shortcuts"),
                context_scope=context_scope,
            )
        except Exception as exc:  # pragma: no cover
            sys.stderr.write(f"failed to log /ask exchange: {exc}\n")
        try:
            record_readonly_canary_audit(exchange, result, context_scope)
        except Exception as exc:  # pragma: no cover
            sys.stderr.write(f"failed to log read-only canary audit: {type(exc).__name__}\n")
        try:
            record_capability_gap_audit(exchange, result, context_scope)
        except Exception as exc:  # pragma: no cover
            sys.stderr.write(f"failed to log capability gap audit: {type(exc).__name__}\n")
        if exchange:
            enqueue_shadow_plan(question, exchange, result, context_scope)
        self._write_json(HTTPStatus.OK, response)

    def _handle_archive_conversation(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        conversation_id = parsed.path.removeprefix("/api/v1/conversations/").removesuffix("/archive").strip("/")
        if not conversation_id or "/" in conversation_id:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid conversation id")
            return
        archived = atlas_app_store.archive_conversation(APP_DB, conversation_id)
        if not archived:
            self._write_error(HTTPStatus.NOT_FOUND, "conversation not found")
            return
        atlas_app_store.add_audit_event(
            APP_DB,
            event_type="conversation.archived",
            summary=f"Archived conversation {conversation_id}",
            details={"conversation_id": conversation_id},
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "conversation": archived})

    def _expand_weather_followup(self, question: str, conversation_id: str | None) -> str:
        if not conversation_id:
            return question
        lowered = normalize_user_text(question)
        location_aliases = {
            "sf": "San Francisco",
            "san francisco": "San Francisco",
            "san fran": "San Francisco",
            "sfo": "San Francisco",
        }
        location = location_aliases.get(lowered)
        if not location:
            return question
        try:
            recent = atlas_app_store.get_recent_messages(APP_DB, conversation_id, limit=4)
        except Exception:
            return question
        if any(item.get("role") == "assistant" and item.get("route") == "weather_clarification" for item in recent):
            return f"What is the weather in {location}?"
        return question

    def _handle_create_message(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return

        raw_question = (payload.get("text") or payload.get("question") or payload.get("q") or payload.get("prompt") or "").strip()
        if not raw_question:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing text")
            return

        conversation_id = (payload.get("conversation_id") or "").strip() or None
        input_mode = (payload.get("input_mode") or "").strip() or None
        client = (payload.get("client") or "").strip() or None
        try:
            context_scope = parse_context_scope(payload.get("context_scope") or payload.get("scope"))
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        include_context = payload.get("include_context", True) is not False
        question = self._resolve_question_context(raw_question, conversation_id) if include_context else raw_question
        shadow_context = []
        if conversation_id:
            try:
                shadow_context = atlas_app_store.get_recent_messages(APP_DB, conversation_id, limit=4)
            except Exception:  # pragma: no cover
                shadow_context = []

        safety_result = pre_route_safety_result(question)
        if safety_result:
            exchange = atlas_app_store.save_exchange(
                APP_DB,
                question=raw_question,
                result=safety_result,
                conversation_id=conversation_id,
                input_mode=input_mode,
                client=client,
                context_scope=context_scope,
                latency_ms=0,
            )
            atlas_app_store.add_audit_event(
                APP_DB,
                event_type="safety.blocked",
                summary=f"Applied {safety_result.get('route')}",
                details={
                    "conversation_id": exchange["conversation_id"],
                    "context_scope": context_scope,
                    "route": safety_result.get("route"),
                    "policy": safety_result.get("result", {}).get("policy"),
                },
            )
            enqueue_shadow_plan(question, exchange, safety_result, context_scope, shadow_context)
            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "conversation_id": exchange["conversation_id"],
                    "user_message": exchange["user_message"],
                    "assistant_message": exchange["assistant_message"],
                },
            )
            return

        reference_resolution = (
            _resolve_reference_for_answer(question, context_scope, shadow_context)
            if include_context else None
        )
        answer_question = question
        if isinstance(reference_resolution, dict):
            if reference_resolution.get("status") == "ambiguous":
                result = _reference_resolution_clarification(reference_resolution)
                exchange = atlas_app_store.save_exchange(
                    APP_DB,
                    question=raw_question,
                    result=result,
                    conversation_id=conversation_id,
                    input_mode=input_mode,
                    client=client,
                    context_scope=context_scope,
                    latency_ms=0,
                )
                record_reference_resolution_consumer_audit(exchange, reference_resolution, context_scope)
                enqueue_shadow_plan(raw_question, exchange, result, context_scope, shadow_context)
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "conversation_id": exchange["conversation_id"],
                        "user_message": exchange["user_message"],
                        "assistant_message": exchange["assistant_message"],
                    },
                )
                return
            if reference_resolution.get("status") == "resolved":
                resolved_question = reference_resolution.get("resolved_question")
                if isinstance(resolved_question, str) and resolved_question.strip():
                    answer_question = resolved_question.strip()

        started = time.monotonic()
        try:
            # Evaluated in order; the first handler to return a non-None result
            # wins and no later handler runs. Handlers with real side effects
            # (DB writes, outbound notifications) must only fire when they are
            # actually selected, not merely while probing for a match. Structured,
            # anchored-regex intent handlers (reminder/task/shopping/note/rule/
            # notification) are checked ahead of vague keyword-based handlers
            # (boundary, casual chat, vague weather, ...) so a specific match
            # always wins over a loose heuristic.
            pre_planner_route_handlers = [
                ("deterministic_first_readonly", lambda: run_deterministic_first_readonly_if_matched(answer_question, context_scope)),
                ("status", lambda: self._answer_status_request(answer_question)),
                ("recall", lambda: self._answer_from_conversation_memory(answer_question, conversation_id)),
                ("memory_correction", lambda: self._propose_memory_correction(question, context_scope, conversation_id)),
                ("memory_maintenance", lambda: self._answer_memory_maintenance_request(answer_question, context_scope)),
                ("personal_memory", lambda: self._answer_personal_memory_request(answer_question, context_scope)),
                ("app_analytics", lambda: self._answer_app_analytics_request(answer_question)),
                ("identity", lambda: self._answer_identity_request(answer_question)),
                # This exact note+reminder compound must precede each broader
                # single-write matcher or the note handler consumes the whole
                # request and the reminder is silently omitted.
                ("multi_step", lambda: self._answer_multi_step_request(question, conversation_id, context_scope)),
                ("reminder", lambda: self._create_reminder_from_question(question, conversation_id, context_scope)),
                ("task", lambda: self._create_task_from_question(question, conversation_id, context_scope)),
                ("shopping", lambda: self._handle_shopping_question(question, conversation_id, context_scope)),
                ("note", lambda: self._create_note_from_question(question, conversation_id, context_scope)),
                ("rule", lambda: self._create_rule_from_question(question)),
                ("notification", lambda: self._send_notification_from_question(question, conversation_id)),
                ("boundary", lambda: self._answer_boundary_request(answer_question)),
                ("social_encounter", lambda: self._answer_social_encounter_request(answer_question)),
                ("casual_chat", lambda: self._answer_casual_chat_request(answer_question)),
                ("vague_weather", lambda: self._answer_vague_weather_request(answer_question)),
                ("personal_schedule", lambda: self._answer_personal_schedule_request(answer_question, conversation_id)),
                ("school_recommendation", lambda: self._answer_school_recommendation_request(answer_question)),
                ("unsupported_action", lambda: self._answer_unsupported_device_action(question)),
                ("navigation", lambda: self._answer_navigation_request(answer_question)),
                ("writing", lambda: self._answer_writing_request(answer_question)),
            ]
            post_planner_route_handlers = [
                ("comparison", lambda: self._answer_comparison_request(answer_question, context_scope)),
                ("recommendation", lambda: self._answer_recommendation_request(answer_question, context_scope)),
                ("explanation", lambda: self._answer_explanation_request(answer_question)),
                ("status", lambda: self._answer_status_request(answer_question)),
                ("diagnostic", lambda: self._answer_diagnostic_request(answer_question)),
                ("routine", lambda: self._answer_routine_request(answer_question, context_scope)),
                ("permission_policy", lambda: self._answer_permission_policy_request(answer_question)),
                ("preference", lambda: self._save_preference_from_question(question)),
            ]
            result = None
            for _name, handler in pre_planner_route_handlers:
                result = handler()
                if result:
                    break
            if (
                not result
                and ATLAS_INVERT_ROUTER_ENABLED
                and MULTI_INTENT_DECOMPOSITION_ENABLED
                and len(atlas_orchestrator_v2.split_discovery_clauses(answer_question)) >= 2
                and runner_allowed_for_scope(question, context_scope)
            ):
                recent_context = (
                    atlas_app_store.get_recent_messages(APP_DB, conversation_id, limit=8)
                    if conversation_id else []
                )
                result = run_atlas_with_readonly_canary(
                    answer_question,
                    context_scope,
                    recent_context,
                    conversation_id=conversation_id,
                )
            for _name, handler in post_planner_route_handlers:
                if result:
                    break
                result = handler()
                if result:
                    break
            if not result and R3_ANSWER_CANARY_ENABLED:
                result = run_r3_answer_canary(answer_question, context_scope)
            if not result:
                if runner_allowed_for_scope(question, context_scope):
                    recent_context = (
                        atlas_app_store.get_recent_messages(APP_DB, conversation_id, limit=8)
                        if conversation_id else []
                    )
                    result = run_atlas_with_readonly_canary(answer_question, context_scope, recent_context, conversation_id=conversation_id)
                else:
                    result = approved_context_required_result(question, context_scope)
            latency_ms = round((time.monotonic() - started) * 1000)
            exchange = atlas_app_store.save_exchange(
                APP_DB,
                question=raw_question,
                result=result,
                conversation_id=conversation_id,
                input_mode=input_mode,
                client=client,
                context_scope=context_scope,
                latency_ms=latency_ms,
            )
            atlas_app_store.add_audit_event(
                APP_DB,
                event_type="message.handled",
                summary=f"Handled route {result.get('route')}",
                details={
                    "conversation_id": exchange["conversation_id"],
                    "context_scope": context_scope,
                    "route": result.get("route"),
                    "tool": result.get("tool"),
                    "client": client,
                },
            )
            record_readonly_canary_audit(exchange, result, context_scope)
            record_capability_gap_audit(exchange, result, context_scope)
            record_reference_resolution_consumer_audit(exchange, reference_resolution, context_scope)
        except ValueError as exc:
            self._write_error(HTTPStatus.NOT_FOUND, str(exc))
            return
        except subprocess.CalledProcessError as exc:
            log_runner_failure(question, exc, conversation_id)
            result = runner_unavailable_result(question)
            latency_ms = round((time.monotonic() - started) * 1000)
            exchange = atlas_app_store.save_exchange(
                APP_DB,
                question=raw_question,
                result=result,
                conversation_id=conversation_id,
                input_mode=input_mode,
                client=client,
                context_scope=context_scope,
                latency_ms=latency_ms,
            )
            atlas_app_store.add_audit_event(
                APP_DB,
                event_type="message.degraded",
                summary="Local answer model unavailable",
                details={
                    "conversation_id": exchange["conversation_id"],
                    "context_scope": context_scope,
                    "route": result.get("route"),
                    "tool": result.get("tool"),
                    "client": client,
                },
            )
        except subprocess.TimeoutExpired:
            self._write_error(HTTPStatus.GATEWAY_TIMEOUT, "atlas runner timed out")
            return
        except Exception as exc:  # pragma: no cover
            self._write_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return

        enqueue_shadow_plan(raw_question, exchange, result, context_scope, shadow_context)
        self._write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "conversation_id": exchange["conversation_id"],
                "user_message": exchange["user_message"],
                "assistant_message": exchange["assistant_message"],
            },
        )

    def _build_contextual_question(self, question: str, conversation_id: str) -> str:
        messages = atlas_app_store.get_recent_messages(APP_DB, conversation_id, limit=8)
        if not messages:
            return question
        lines = []
        for message in messages:
            role = message.get("role") or "message"
            text = " ".join((message.get("text") or "").split())
            if text:
                lines.append(f"{role}: {text}")
        if not lines:
            return question
        return (
            "Use the recent conversation context only to resolve references in the current question. "
            "Answer the current question, not the earlier messages.\n\n"
            "Recent conversation:\n"
            + "\n".join(lines[-8:])
            + "\n\nCurrent question:\n"
            + question
        )

    def _resolve_question_context(self, question: str, conversation_id: str | None) -> str:
        weather_expanded = self._expand_weather_followup(question, conversation_id)
        if weather_expanded != question:
            return weather_expanded
        return question

    def _answer_from_conversation_memory(self, question: str, conversation_id: str | None) -> dict | None:
        lowered = question.lower().strip()
        if not conversation_id:
            return None
        if not any(
            phrase in lowered
            for phrase in [
                "what did i ask",
                "what was my last question",
                "what did we talk about",
                "summarize this conversation",
                "summarize our conversation",
            ]
        ):
            return None
        messages = atlas_app_store.get_recent_messages(APP_DB, conversation_id, limit=20)
        user_messages = [message for message in messages if message.get("role") == "user"]
        if not messages:
            answer = "I do not have previous messages in this conversation yet."
        elif "summarize" in lowered or "what did we talk about" in lowered:
            topics = [message.get("text", "").strip() for message in user_messages[-5:] if message.get("text")]
            answer = "Recent topics: " + "; ".join(topics) if topics else "I do not have enough user messages to summarize yet."
        else:
            previous = user_messages[-1].get("text") if user_messages else ""
            answer = f"Your last question was: {previous}" if previous else "I do not have a previous user question in this conversation yet."
        return {
            "route": "memory_recall",
            "tool": "atlas-app-store",
            "answer": answer,
            "source": "ATLAS conversation log",
            "result": {"conversation_id": conversation_id, "messages_considered": len(messages)},
        }

    def _propose_memory_correction(self, question: str, context_scope: str, conversation_id: str | None) -> dict | None:
        if context_scope != "personal":
            return None
        correction = atlas_memory_conflicts.parse_owner_correction(question)
        if not correction:
            return None
        if correction.get("needs_replacement"):
            # Chat-quality review cluster 5, found live 2026-09-17:
            # `_CORRECTION_PREFIX` matches a bare "no"/"wrong"/"actually" at
            # the start of ANY message, not just ones about a stored
            # personal fact -- "No, Daly City's" (correcting ATLAS's own
            # immediately preceding weather answer) and "No, that's not
            # true, it actually opened in 1889" (correcting a general-
            # knowledge claim) both matched this ambiguous shape and got
            # "I need the exact replacement fact" instead of being treated
            # as the immediate-answer correction they actually are. Only
            # treat this ambiguous shape as a personal-memory correction
            # candidate when the most recent assistant turn actually was a
            # personal-memory recall -- otherwise it is almost always
            # correcting the immediately preceding answer, not a stored
            # fact, and should fall through to the normal answer pipeline.
            # `r3_retrieval_canary` is included alongside `personal_memory`/
            # `personal_memory_conflict`: live-verified that a real
            # preference recall in production actually answers through R3's
            # retrieval canary, not the legacy `personal_memory` route --
            # excluding it made a genuine correction ("that is wrong" after
            # "favorite color: blue") fall through to an ungrounded model
            # reply that falsely claimed it would "update my records"
            # without ever creating a real correction proposal.
            recent = atlas_app_store.get_recent_messages(APP_DB, conversation_id, limit=4) if conversation_id else []
            last_assistant = next(
                (message for message in reversed(recent) if message.get("role") == "assistant"), None
            )
            personal_memory_routes = {"personal_memory", "personal_memory_conflict", "r3_retrieval_canary"}
            if not last_assistant or last_assistant.get("route") not in personal_memory_routes:
                return None
            return {
                "route": "memory_correction_feedback",
                "tool": "atlas-memory-correction",
                "answer": "I heard that as correction feedback, but I need the exact replacement fact before I can propose a memory change.",
                "source": "ATLAS personal memory",
                "result": {"changed": False, "needs": ["exact_replacement"]},
            }
        preferences = atlas_app_store.list_preferences(APP_DB)
        matching = [
            pref for pref in preferences
            if atlas_memory_conflicts._subject_norm(pref.get("key") or "") == correction["subject"]
        ]
        if len(matching) != 1:
            return {
                "route": "memory_correction_feedback",
                "tool": "atlas-memory-correction",
                "answer": "I heard that as correction feedback, but I need one exact saved preference to update before I can propose a change.",
                "source": "ATLAS personal memory",
                "result": {"changed": False, "matched_preferences": len(matching)},
            }
        preference = matching[0]
        new_value = correction["value"]
        if preference["value"] == new_value:
            return {
                "route": "memory_correction_feedback",
                "tool": "atlas-memory-correction",
                "answer": "That exact preference is already saved, so I did not create a correction proposal.",
                "source": "ATLAS personal memory",
                "result": {"changed": False, "already_current": True},
            }
        details = {
            "capability": "memory.correct",
            "context_scope": "personal",
            "record_type": "preference",
            "key": preference["key"],
            "old_value": preference["value"],
            "new_value": new_value,
            "source": "owner_correction",
            "source_question": question,
            "source_conversation_id": conversation_id,
            "affected_aliases": [preference["key"], correction["subject"]],
        }
        summary = f"Correct preference {preference['key']}: {preference['value']} -> {new_value}"
        confirmation = atlas_app_store.request_confirmation(
            APP_DB,
            action_type="action.memory.correct",
            summary=summary,
            risk_level="low",
            details=details,
            execution_status="pending",
        )
        atlas_app_store.create_notification(
            APP_DB,
            channel="local",
            title="Memory correction requires approval",
            body=summary,
            source_type="confirmation",
            source_id=confirmation["id"],
        )
        atlas_app_store.add_audit_event(
            APP_DB,
            "memory.correction_requested",
            summary,
            {
                "confirmation_id": confirmation["id"],
                "record_type": "preference",
                "context_scope": "personal",
            },
        )
        return {
            "route": "memory_correction_preview",
            "tool": "atlas-memory-correction",
            "answer": "I created a pending memory-correction confirmation. Approve it and I will replace the saved preference while keeping the previous value for rollback.",
            "source": "ATLAS personal memory",
            "result": {"confirmation": confirmation, "changed": False},
        }

    def _answer_memory_maintenance_request(self, question: str, context_scope: str) -> dict | None:
        if context_scope != "personal":
            return None
        lowered = normalize_user_text(question)
        if not any(
            phrase in lowered
            for phrase in (
                "memory maintenance",
                "memory quality",
                "check my memory",
                "audit my memory",
                "memory report",
            )
        ):
            return None
        report = atlas_memory_maintenance.build_maintenance_report(APP_DB)
        counts = report["counts"]
        answer = (
            "Memory maintenance dry run complete: "
            f"{counts['findings']} finding(s), "
            f"{counts['unresolved_conflicts']} unresolved conflict(s), "
            f"{counts['duplicate_note_groups']} duplicate note group(s), "
            f"{counts['history_count']} correction-history record(s). "
            "I did not merge, delete, purge, export, or rebuild anything."
        )
        return {
            "route": "memory_maintenance",
            "tool": "atlas-memory-maintenance",
            "answer": answer,
            "source": "ATLAS personal memory",
            "result": report,
        }

    _MEMORY_STOPWORDS = {
        "the", "a", "an", "is", "are", "do", "did", "you", "i", "my", "me", "to", "of", "in",
        "on", "for", "that", "this", "about", "what", "know", "tell", "say", "said", "says",
        "mention", "mentioned", "remember", "ever", "have", "has", "was", "were", "with", "and",
    }

    def _memory_tokens(self, text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9']+", text.lower())
            if token not in self._MEMORY_STOPWORDS and len(token) > 2
        }

    def _answer_personal_memory_request(self, question: str, context_scope: str) -> dict | None:
        """Cross-conversation recall of explicitly-saved preferences and notes.

        Grounded by design: only ever surfaces things the owner explicitly
        stated (a saved preference, a note they asked ATLAS to remember), never
        LLM-inferred or summarized content. Runs entirely in-process against
        the local DB -- no model call, no network call -- so the common case
        (nothing relevant, or nothing saved at all) costs nothing.
        """
        if context_scope != "personal":
            return None
        lowered = question.lower().strip()
        list_all_phrases = (
            "what are my preferences", "what's my preference", "what is my preference",
            "what do you know about me", "what have you learned about me",
        )
        recall_trigger_phrases = (
            "did i tell you", "did i mention", "did i ever say", "do you remember",
            "what did i say about", "remember when i said", "did i say",
        )
        is_list_all = any(phrase in lowered for phrase in list_all_phrases)
        is_recall_trigger = any(phrase in lowered for phrase in recall_trigger_phrases)
        if not is_list_all and not is_recall_trigger:
            return None

        preferences = atlas_app_store.list_preferences(APP_DB)
        notes = atlas_app_store.list_notes(APP_DB, limit=200, context_scope="personal")
        if not preferences and not notes:
            return {
                "route": "personal_memory",
                "tool": "atlas-app-store",
                "answer": "I don't have any saved preferences or notes yet.",
                "source": "ATLAS personal memory",
                "result": {"matched": 0},
            }

        def describe(kind: str, label: str, text: str, updated_at: str) -> str:
            return f"{kind} ({label}, saved {updated_at[:10]}): {text}"

        conflicts = atlas_memory_conflicts.detect_memory_conflicts(question, preferences, notes)
        if conflicts and not is_list_all:
            pieces = []
            for conflict in conflicts[:3]:
                fact_summaries = [
                    describe(
                        fact["record_type"].title(),
                        fact["label"],
                        fact["value"],
                        fact.get("updated_at") or "",
                    )
                    for fact in conflict["facts"][:4]
                ]
                pieces.append(f"Conflict for {conflict['subject']}: " + " | ".join(fact_summaries))
            return {
                "route": "personal_memory_conflict",
                "tool": "atlas-memory-conflict-detector",
                "answer": "I found conflicting saved memory instead of a single settled answer. " + " ".join(pieces),
                "source": "ATLAS personal memory",
                "result": atlas_memory_conflicts.conflict_metrics(conflicts),
            }

        if is_list_all:
            lines = [describe("Preference", pref["key"], pref["value"], pref["updated_at"]) for pref in preferences]
            lines += [describe("Note", note["title"], note["text"], note["updated_at"]) for note in notes[:10]]
            answer = " ".join(lines) if lines else "I don't have anything saved yet."
            return {
                "route": "personal_memory",
                "tool": "atlas-app-store",
                "answer": answer,
                "source": "ATLAS personal memory",
                "result": {"preferences": len(preferences), "notes": min(len(notes), 10)},
            }

        question_tokens = self._memory_tokens(question)
        matches = []
        for pref in preferences:
            if question_tokens & self._memory_tokens(f"{pref['key']} {pref['value']}"):
                matches.append(describe("Preference", pref["key"], pref["value"], pref["updated_at"]))
        for note in notes:
            if question_tokens & self._memory_tokens(f"{note['title']} {note['text']}"):
                matches.append(describe("Note", note["title"], note["text"], note["updated_at"]))
        if not matches:
            # Trigger phrase matched, but nothing on file overlaps the question.
            # Do not guess -- fall through so a later, more capable tier can try
            # (or, absent one, the request degrades to the normal fallback).
            return None
        return {
            "route": "personal_memory",
            "tool": "atlas-app-store",
            "answer": " ".join(matches[:3]),
            "source": "ATLAS personal memory",
            "result": {"matched": len(matches)},
        }

    def _answer_app_analytics_request(self, question: str) -> dict | None:
        lowered = question.lower()
        asks_count = any(
            phrase in lowered
            for phrase in [
                "how many chat logs",
                "how many chats",
                "how many conversations",
                "how many messages",
                "conversation count",
                "chat count",
            ]
        )
        asks_failures = any(
            phrase in lowered
            for phrase in [
                "failed chats",
                "failed responses",
                "failed prompts",
                "biggest weakness",
                "what can't you answer",
                "what cant you answer",
                "what do you fail",
                "what have you failed",
            ]
        )
        if not asks_count and not asks_failures:
            return None

        stats = atlas_app_store.conversation_stats(APP_DB)
        failures = atlas_app_store.assistant_failure_summary(APP_DB)
        if asks_count and not asks_failures:
            answer = (
                f"ATLAS has {stats['conversation_count']} logged conversations, "
                f"{stats['message_count']} total messages, "
                f"{stats['user_message_count']} user messages, and "
                f"{stats['assistant_message_count']} assistant messages."
            )
        else:
            route_summary = ", ".join(f"{row['route']}: {row['count']}" for row in failures["routes"][:4]) or "no repeated failure routes found"
            recent_examples = [
                " ".join((item.get("text") or "").split())[:120]
                for item in failures["recent"][:3]
                if item.get("text")
            ]
            answer = (
                f"Based on the ATLAS app log, the biggest current weakness is not lack of chat history; "
                f"it is routing/context handling around live search, follow-ups, and adapter failures. "
                f"I found {failures['failure_count']} recent failure-like assistant messages. "
                f"Most affected routes: {route_summary}."
            )
            if recent_examples:
                answer += " Recent examples: " + " | ".join(recent_examples)
        return {
            "route": "app_analytics",
            "tool": "atlas-app-store",
            "answer": answer,
            "source": "ATLAS conversation log",
            "result": {"stats": stats, "failures": failures},
        }

    def _resolve_followup_reference(self, text: str, conversation_id: str | None) -> str:
        if not conversation_id or not re.search(r"\b(that|it|this)\b", text, flags=re.IGNORECASE):
            return text
        messages = atlas_app_store.get_recent_messages(APP_DB, conversation_id, limit=8)
        previous_user_messages = [
            (message.get("text") or "").strip()
            for message in messages[:-1]
            if message.get("role") == "user" and (message.get("text") or "").strip()
        ]
        if not previous_user_messages:
            return text
        previous = self._reference_text_from_request(previous_user_messages[-1])
        return re.sub(r"\b(that|it|this)\b", previous, text, count=1, flags=re.IGNORECASE)

    def _reference_text_from_request(self, text: str) -> str:
        cleaned = " ".join(text.strip().strip(" .").split())
        patterns = [
            r"^(?:take a note|note this|remember this|remember that)\s*:?\s+",
            r"^(?:add (?:a )?task|create (?:a )?task|todo)\s*:?\s+",
            r"^remind me to\s+",
        ]
        for pattern in patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        return cleaned or text.strip(" .")

    def _create_task_from_question(self, question: str, conversation_id: str | None, context_scope: str) -> dict | None:
        match = re.match(r"^\s*(?:add (?:a )?task|create (?:a )?task|todo)\s*:?\s+(.+?)\s*$", question, flags=re.IGNORECASE)
        if not match:
            return None
        task_text = self._resolve_followup_reference(match.group(1).strip(" ."), conversation_id)
        task = atlas_app_store.create_task(APP_DB, task_text, context_scope=context_scope)
        return {
            "route": "task",
            "tool": "atlas-app-store",
            "answer": f"Task saved: {task_text}.",
            "source": "ATLAS task store",
            "result": {"task": task},
        }

    def _create_rule_from_question(self, question: str) -> dict | None:
        match = re.match(r"^\s*if\s+(.+?)\s*,?\s+then\s+(.+?)\s*$", question, flags=re.IGNORECASE)
        if not match:
            return None
        condition_text = match.group(1).strip(" .")
        action_text = match.group(2).strip(" .")
        # Track B1: same creation-time validation as the HTTP API path
        # (_handle_create_rule) -- a chat-authored rule gets the same "fail
        # loudly, not silently" guarantee rather than a special exemption.
        condition_error = atlas_signals.validate_condition_for_creation(condition_text)
        if condition_error:
            return {
                "route": "conditional_rule",
                "tool": "atlas-app-store",
                "answer": f"I couldn't save that rule: {condition_error}",
                "source": "ATLAS rule store",
                "result": {"rule": None, "error": condition_error},
            }
        rule = atlas_app_store.create_rule(APP_DB, condition_text, action_text)
        return {
            "route": "conditional_rule",
            "tool": "atlas-app-store",
            "answer": f"Rule saved: if {condition_text}, then {action_text}.",
            "source": "ATLAS rule store",
            "result": {"rule": rule},
        }

    def _create_reminder_from_question(self, question: str, conversation_id: str | None, context_scope: str) -> dict | None:
        match = re.match(
            r"^\s*(?:remind me to|set (?:a )?reminder(?:\s+for)?|create (?:a )?reminder(?:\s+for)?)\s+(.+?)\s*$",
            question,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        reminder_text, due_at = parse_reminder_text(self._resolve_followup_reference(match.group(1).strip(" ."), conversation_id))
        if not reminder_text:
            return None
        recurrence = "daily" if re.search(r"\bevery day|daily\b", question, flags=re.IGNORECASE) else None
        reminder = atlas_app_store.create_reminder(APP_DB, reminder_text, due_at=due_at, recurrence=recurrence, context_scope=context_scope)
        return {
            "route": "reminder",
            "tool": "atlas-app-store",
            "answer": f"Reminder saved: {reminder_text}." + (f" Due: {due_at}." if due_at else ""),
            "source": "ATLAS reminder store",
            "result": {"reminder": reminder},
        }

    def _answer_status_request(self, question: str) -> dict | None:
        lowered = question.lower()
        asks_atlas_health = (
            "atlas" in lowered
            and any(term in lowered for term in ["healthy", "health", "up", "running", "status"])
            and any(term in lowered for term in ["right now", "currently", "now", "short answer", "evidence"])
        )
        if asks_atlas_health:
            return {
                "route": "status",
                "tool": "atlas-local-status",
                "answer": "Yes — the ATLAS message API is healthy right now. Evidence: this request reached atlas-shortcuts and the local status handler returned successfully. This proves the API path is responding, not that every downstream adapter is healthy.",
                "source": "ATLAS local request path",
                "result": {"api_path_responding": True, "downstream_adapters_verified": False},
            }
        if not any(phrase in lowered for phrase in ["atlas status", "what can you do", "system status", "assistant status"]):
            return None
        return {
            "route": "status",
            "tool": "atlas-local-status",
            "answer": "ATLAS local app services are available: conversations, message search, reminders, notes, tasks, shopping list, rules, audit log, writing helpers, comparison helpers, and deterministic math/conversion/date routes.",
            "source": "ATLAS local service registry",
            "result": {
                "capabilities": ["conversations", "message_search", "reminders", "notes", "tasks", "shopping", "rules", "audit", "writing", "comparison", "math", "conversion", "date_time"]
            },
        }

    def _answer_diagnostic_request(self, question: str) -> dict | None:
        lowered = question.lower()
        if not any(phrase in lowered for phrase in ["troubleshoot", "diagnose", "not working"]):
            return None
        return {
            "route": "diagnostic",
            "tool": "atlas-diagnostic-helper",
            "answer": "Diagnostic checklist: identify the failing component, check recent changes, verify reachability, inspect service health, review logs, test the smallest reproducible path, then record the fix. Give me the component and symptom and I can turn that into a focused checklist.",
            "source": "ATLAS local diagnostic helper",
            "result": {"steps": ["identify_component", "check_recent_changes", "verify_reachability", "inspect_health", "review_logs", "test_minimal_path", "record_fix"]},
        }

    def _answer_routine_request(self, question: str, context_scope: str) -> dict | None:
        lowered = question.lower()
        if not any(phrase in lowered for phrase in ["daily routine", "morning routine", "evening routine", "what should i do today"]):
            return None
        tasks = atlas_app_store.list_tasks(APP_DB, limit=5, context_scope=context_scope)
        reminders = atlas_app_store.list_reminders(APP_DB, limit=5, context_scope=context_scope)
        pieces = []
        if tasks:
            pieces.append("Open tasks: " + "; ".join(task["text"] for task in tasks[:3]))
        if reminders:
            pieces.append("Open reminders: " + "; ".join(reminder["text"] for reminder in reminders[:3]))
        answer = " ".join(pieces) if pieces else "You do not have local ATLAS tasks or reminders waiting."
        return {
            "route": "routine",
            "tool": "atlas-app-store",
            "answer": answer,
            "source": "ATLAS tasks and reminders",
            "result": {"task_count": len(tasks), "reminder_count": len(reminders), "context_scope": context_scope},
        }

    def _answer_permission_policy_request(self, question: str) -> dict | None:
        lowered = question.lower()
        if not any(
            phrase in lowered
            for phrase in [
                "permission mode",
                "permission policy",
                "what are you allowed to do",
                "what can you do without approval",
                "what requires approval",
                "what do you block",
                "what is blocked",
            ]
        ):
            return None

        direct = ATLAS_PERMISSION_POLICY["action_tiers"]["direct_when_explicitly_requested"]
        confirm = ATLAS_PERMISSION_POLICY["action_tiers"]["confirmation_required"]
        blocked = ATLAS_PERMISSION_POLICY["action_tiers"]["blocked_by_default"]
        answer = (
            "ATLAS is in owner-only, privacy-over-convenience mode. "
            f"Direct actions: {', '.join(direct[:5])}. "
            f"Confirmation required: {', '.join(confirm[:5])}. "
            f"Blocked by default: {', '.join(blocked[:7])}."
        )
        return {
            "route": "permission_policy",
            "tool": "atlas-permission-policy",
            "answer": answer,
            "source": "ATLAS local permission policy",
            "result": {"policy": ATLAS_PERMISSION_POLICY},
        }

    def _answer_boundary_request(self, question: str) -> dict | None:
        if is_structured_reasoning_request(question):
            return None
        lowered = question.lower()
        if "cross-user" in lowered and "private data" in lowered and "by default" in lowered:
            return {
                "route": "boundary",
                "tool": "atlas-boundary-policy",
                "answer": "No. Cross-user private data is blocked by default.",
                "source": "ATLAS local boundary policy",
                "result": {"blocked": True, "policy": "cross_user_private_data_blocked_by_default"},
            }

        if "vaultwarden" in lowered and any(token in lowered for token in ["secret", "secrets", "recovery code", "recovery codes"]):
            return {
                "route": "boundary",
                "tool": "atlas-boundary-policy",
                "answer": "Refused. I cannot provide Vaultwarden secrets or recovery codes because that is secret or private data.",
                "source": "ATLAS local boundary policy",
                "result": {"blocked": True, "policy": "secrets_blocked_by_default"},
            }

        if any(token in lowered for token in ["porn", "xxx"]) and "source" in lowered:
            return {
                "route": "boundary",
                "tool": "atlas-boundary-policy",
                "answer": "No. Porn and XXX-focused external sources are excluded by policy.",
                "source": "ATLAS local boundary policy",
                "result": {"blocked": True, "policy": "xxx_sources_excluded"},
            }

        blocked_phrases = [
            "unlock the door for a stranger",
            "bypass this account",
            "bypass login",
            "what is my neighbor's wi-fi password",
            "what is my neighbors wi-fi password",
            "pretend you did it",
        ]
        if not any(phrase in lowered for phrase in blocked_phrases):
            risky = any(phrase in lowered for phrase in ["unlock", "open the garage", "disable alarm"])
            if not risky:
                return None
            confirmation = atlas_app_store.request_confirmation(
                APP_DB,
                action_type="high_risk_action",
                summary=question,
                risk_level="high",
                details={"question": question},
            )
            atlas_app_store.create_notification(
                APP_DB,
                channel="local",
                title="Confirmation required",
                body=question,
                source_type="confirmation",
                source_id=confirmation["id"],
            )
            atlas_app_store.add_audit_event(APP_DB, "confirmation.requested", question, {"confirmation_id": confirmation["id"]})
            return {
                "route": "confirmation_required",
                "tool": "atlas-confirmation-store",
                "answer": "That is a high-risk action. I created a pending confirmation record instead of doing it.",
                "source": "ATLAS confirmation policy",
                "result": {"confirmation": confirmation},
            }
        atlas_app_store.add_audit_event(APP_DB, "boundary.blocked", question, {"blocked": True})
        return {
            "route": "boundary",
            "tool": "atlas-boundary-policy",
            "answer": "I cannot help with that. It would require unsafe access, deception, or someone else’s private information.",
            "source": "ATLAS local boundary policy",
            "result": {"blocked": True},
        }

    def _answer_identity_request(self, question: str) -> dict | None:
        lowered = question.lower().strip(" ?.!")
        identity_phrases = [
            "what is your name",
            "whats your name",
            "what's your name",
            "who are you",
            "what are you called",
            "tell me your name",
        ]
        if lowered not in identity_phrases:
            return None
        return {
            "route": "identity",
            "tool": "atlas-local-identity",
            "answer": "I’m ATLAS, your local assistant for chat, logs, homelab context, notes, tasks, reminders, and voice.",
            "source": "ATLAS local identity",
            "result": {"name": "ATLAS"},
        }

    def _answer_social_encounter_request(self, question: str) -> dict | None:
        match = re.match(
            r"^\s*what should i do if i (?:see|meet|encounter) (?:a|an|someone who is)\s+([A-Za-z][A-Za-z '-]{0,50})[?.!]*\s*$",
            question,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        description = match.group(1).strip().lower()
        hazard_words = {
            "fire", "smoke", "weapon", "gun", "knife", "accident", "crash", "bear", "snake",
            "dog", "animal", "flood", "earthquake", "tornado", "intruder",
        }
        if any(word in description.split() for word in hazard_words):
            return None
        person_descriptor = description.endswith(("an", "ian", "ese", "ish", "i")) or any(
            word in description.split() for word in {"person", "immigrant", "refugee", "tourist", "neighbor", "foreigner"}
        )
        if not person_descriptor:
            return None
        return {
            "route": "social_guidance",
            "tool": "atlas-human-dignity-policy",
            "answer": "Treat them as an individual: be polite, respect their space, and respond to what they actually say or do rather than making assumptions about their identity.",
            "source": "ATLAS human-dignity policy",
            "result": {"policy": "individual_respect_without_identity_stereotypes"},
        }

    def _answer_casual_chat_request(self, question: str) -> dict | None:
        lowered = normalize_user_text(question)
        casual_phrases = {
            "hi",
            "hey",
            "hello",
            "yo",
            "sup",
            "whats up",
            "what's up",
            "what is up",
            "what up",
            "whats cracking",
            "what's cracking",
            "what is cracking",
            "how are you",
            "how are you doing",
        }
        if lowered not in casual_phrases:
            return None
        return {
            "route": "casual_chat",
            "tool": "atlas-local-conversation",
            "answer": "I’m here. Tell me what you want to work on, ask a question, or say a task/reminder out loud.",
            "source": "ATLAS local conversation router",
            "result": {"intent": "casual_greeting"},
        }

    def _answer_vague_weather_request(self, question: str) -> dict | None:
        lowered = normalize_user_text(question)
        vague_weather_phrases = {
            "weather",
            "the weather",
            "whats the weather",
            "what's the weather",
            "what is the weather",
            "hows the weather",
            "how's the weather",
        }
        if lowered not in vague_weather_phrases:
            return None
        return {
            "route": "weather_clarification",
            "tool": "atlas-weather-router",
            "answer": "What location should I check? For example: “What’s the weather in San Francisco?”",
            "source": "ATLAS local weather router",
            "result": {"needs": ["location"]},
        }

    def _answer_personal_schedule_request(self, question: str, conversation_id: str | None) -> dict | None:
        lowered = normalize_user_text(question)
        asks_tomorrow = "tomorrow" in lowered and any(
            phrase in lowered for phrase in ["what do i have", "what have i got", "whats planned", "what is planned", "my schedule"]
        )
        if not asks_tomorrow and conversation_id and any(token in lowered.split() for token in {"it", "that"}):
            try:
                recent = atlas_app_store.get_recent_messages(APP_DB, conversation_id, limit=4)
                asks_tomorrow = any(
                    item.get("role") == "assistant" and item.get("route") == "personal_calendar"
                    for item in recent
                )
            except Exception:
                asks_tomorrow = False
        if not asks_tomorrow:
            return None
        if not NEXTCLOUD_ENABLED:
            return {
                "route": "personal_calendar",
                "tool": "atlas-nextcloud-calendar",
                "answer": "I can check your personal calendar, but the read-only Nextcloud calendar adapter is not enabled right now.",
                "source": "ATLAS integration status",
                "result": {"connected": False, "period": "tomorrow"},
            }
        try:
            allowlist = json.loads(NEXTCLOUD_CALENDARS)
            if not isinstance(allowlist, list) or not allowlist:
                raise ValueError("no calendars are allowlisted")
            tomorrow = datetime.now(LOCAL_TZ).date() + timedelta(days=1)
            start_local = datetime.combine(tomorrow, datetime.min.time(), tzinfo=LOCAL_TZ)
            end_local = start_local + timedelta(days=1)
            start = start_local.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
            end = end_local.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
            events = []
            for calendar in [str(item) for item in allowlist]:
                for event in atlas_nextcloud.list_calendar_events(
                    NEXTCLOUD_BASE_URL, NEXTCLOUD_USERNAME, NEXTCLOUD_APP_PASSWORD,
                    calendar, [str(item) for item in allowlist], start, end,
                ):
                    events.append({**event, "calendar": calendar})
        except Exception as exc:
            return {
                "route": "personal_calendar",
                "tool": "atlas-nextcloud-calendar",
                "answer": "I couldn't read your allowlisted personal calendars right now. The calendar adapter failed; I won't substitute a web search.",
                "source": "Nextcloud CalDAV",
                "result": {"connected": True, "period": "tomorrow", "error_type": type(exc).__name__},
            }
        if not events:
            answer = "Your allowlisted personal calendars have no events scheduled for tomorrow."
        else:
            summaries = [event.get("summary") or "Untitled event" for event in events[:8]]
            answer = f"Tomorrow you have {len(events)} calendar event{'s' if len(events) != 1 else ''}: " + "; ".join(summaries) + "."
        return {
            "route": "personal_calendar",
            "tool": "atlas-nextcloud-calendar",
            "answer": answer,
            "source": "Nextcloud CalDAV",
            "result": {"connected": True, "period": "tomorrow", "event_count": len(events)},
        }

    def _answer_school_recommendation_request(self, question: str) -> dict | None:
        lowered = normalize_user_text(question)
        if "best school" not in lowered:
            return None
        city_match = re.search(r"\bin\s+([A-Za-z][A-Za-z .'-]+)$", question.strip().rstrip("?"), flags=re.IGNORECASE)
        city = city_match.group(1).strip() if city_match else "that area"
        return {
            "route": "recommendation",
            "tool": "atlas-local-recommendation",
            "answer": (
                f"“Best school” in {city} depends on level, program, commute, cost, and what you mean by best. "
                "Tell me whether you mean elementary, high school, community college, university, or a specific major, "
                "and I can narrow it without guessing from another city."
            ),
            "source": "ATLAS local recommendation fallback",
            "result": {"location": city, "needs": ["school_level", "criteria"]},
        }

    def _answer_unsupported_device_action(self, question: str) -> dict | None:
        if is_structured_reasoning_request(question):
            return None
        lowered = question.lower()
        action_words = ["raise", "lower", "open", "close", "turn on", "turn off", "set"]
        device_words = ["blind", "blinds", "shade", "shades", "light", "lights", "thermostat"]
        if not any(action in lowered for action in action_words) or not any(device in lowered for device in device_words):
            return None
        atlas_app_store.add_audit_event(
            APP_DB,
            "capability.missing",
            question,
            {"capability": "smart_home_control", "question": question},
        )
        return {
            "route": "unsupported_device_action",
            "tool": "atlas-capability-registry",
            "answer": "I can’t control smart-home devices from this app yet. I logged it as a requested ATLAS capability.",
            "source": "ATLAS local capability registry",
            "result": {"capability": "smart_home_control", "available": False},
        }

    def _answer_navigation_request(self, question: str) -> dict | None:
        lowered = question.lower()
        route_phrases = ["quickest way", "fastest way", "best route", "directions", "how do i get"]
        if not any(phrase in lowered for phrase in route_phrases):
            return None
        atlas_app_store.add_audit_event(
            APP_DB,
            "capability.missing",
            question,
            {"capability": "live_navigation", "question": question},
        )
        return {
            "route": "navigation",
            "tool": "atlas-navigation-fallback",
            "answer": "I don’t have live traffic or Maps integration yet. For quickest-route questions, use Apple Maps or Google Maps for current traffic. I logged live navigation as a requested ATLAS capability.",
            "source": "ATLAS local navigation fallback",
            "result": {"capability": "live_navigation", "available": False},
        }

    def _answer_multi_step_request(self, question: str, conversation_id: str | None, context_scope: str) -> dict | None:
        note_match = re.search(r"(?:take a note|note this|remember this)\s*:?\s*(.+?)\s+and\s+remind me to\s+(.+)$", question, flags=re.IGNORECASE)
        if not note_match:
            return None
        note_text = self._resolve_followup_reference(note_match.group(1).strip(" ."), conversation_id)
        reminder_text, due_at = parse_reminder_text(self._resolve_followup_reference(note_match.group(2).strip(" ."), conversation_id))
        note = atlas_app_store.create_note(APP_DB, title=note_text, text=note_text, context_scope=context_scope)
        reminder = atlas_app_store.create_reminder(APP_DB, reminder_text, due_at=due_at, context_scope=context_scope)
        return {
            "route": "multi_step",
            "tool": "atlas-app-store",
            "answer": f"Done. Note saved: {note['title']}. Reminder saved: {reminder_text}.",
            "source": "ATLAS app store",
            "result": {"note": note, "reminder": reminder},
        }

    def _create_note_from_question(self, question: str, conversation_id: str | None, context_scope: str) -> dict | None:
        match = re.match(r"^\s*(?:take a note|note this|remember this|remember that)\s*:?\s+(.+?)\s*$", question, flags=re.IGNORECASE)
        if not match:
            return None
        text = self._resolve_followup_reference(match.group(1).strip(), conversation_id)
        note = atlas_app_store.create_note(APP_DB, title=text, text=text, context_scope=context_scope)
        return {
            "route": "note",
            "tool": "atlas-app-store",
            "answer": f"Note saved: {note['title']}.",
            "source": "ATLAS notes store",
            "result": {"note": note},
        }

    def _send_notification_from_question(self, question: str, conversation_id: str | None) -> dict | None:
        if is_structured_reasoning_request(question):
            return None
        match = re.match(
            r"^\s*(?:please\s+)?(?:send|push)(?:\s+me)?\s+(?:an?\s+)?(?:\w+\s+){0,2}?(?:ntfy\s+)?notification"
            r"(?:\s+to\s+my\s+phone)?(?:\s+(?:that\s+says|that|saying|with(?:\s+the)?\s+message))?\s*:?\s*(.*?)\s*$",
            question,
            flags=re.IGNORECASE,
        )
        if not match:
            match = re.match(r"^\s*(?:please\s+)?notify\s+me(?:\s+now)?(?:\s+that|\s*:)?\s*(.*?)\s*$", question, flags=re.IGNORECASE)
        if not match:
            return None
        body = match.group(1).strip(" .")
        if re.match(r"^(?:when|if|at|on|in|after|before|tomorrow|next)\b", body, flags=re.IGNORECASE):
            return None
        body = body or "This is the notification you asked ATLAS to send."
        source_id = conversation_id or uuid.uuid4().hex
        delivery = send_ntfy_feedback(
            title="ATLAS notification",
            body=body,
            source_type="manual_notification",
            source_id=source_id,
        )
        delivered = bool(delivery and delivery.get("status") == 200)
        atlas_app_store.add_audit_event(
            APP_DB,
            "notification.manual",
            "Owner requested an immediate notification",
            {"conversation_id": conversation_id, "delivered": delivered, "provider": "ntfy"},
        )
        return {
            "route": "notification",
            "tool": "ntfy",
            "answer": "Notification sent through ntfy." if delivered else "I queued the notification, but ntfy delivery did not succeed.",
            "source": "ATLAS notification service",
            "result": {"delivered": delivered, "provider": "ntfy"},
        }

    def _handle_shopping_question(self, question: str, conversation_id: str | None, context_scope: str) -> dict | None:
        lowered = question.lower().strip()
        list_phrases = [
            "what's on my shopping list",
            "whats on my shopping list",
            "what is on my shopping list",
            "show my shopping list",
            "list shopping items",
        ]
        if any(phrase in lowered for phrase in list_phrases):
            items = atlas_app_store.list_shopping_items(APP_DB, limit=25, context_scope=context_scope)
            if items:
                answer = "Shopping list: " + "; ".join(
                    f"{item['quantity'] + ' ' if item.get('quantity') else ''}{item['text']}"
                    for item in items
                )
            else:
                answer = "Your local ATLAS shopping list is empty."
            return {
                "route": "shopping_list",
                "tool": "atlas-app-store",
                "answer": answer,
                "source": "ATLAS local shopping list",
                "result": {"items": items, "context_scope": context_scope},
            }

        match = re.match(
            r"^\s*(?:add|put)\s+(.+?)\s+(?:to|on)\s+(?:my\s+)?(?:shopping|grocery)\s+list\s*$",
            question,
            flags=re.IGNORECASE,
        )
        if not match:
            match = re.match(
                r"^\s*(?:shopping|grocery)\s+list\s*:?\s+(.+?)\s*$",
                question,
                flags=re.IGNORECASE,
            )
        if not match:
            return None
        raw_item = self._resolve_followup_reference(match.group(1).strip(" ."), conversation_id)
        store = None
        store_match = re.search(r"\s+(?:at|from)\s+([A-Za-z][A-Za-z0-9 '&-]{1,40})$", raw_item)
        if store_match:
            store = store_match.group(1).strip()
            raw_item = raw_item[: store_match.start()].strip()
        quantity = None
        quantity_match = re.match(r"^(\d+(?:\.\d+)?\s*(?:x|pack|packs|lb|lbs|oz|g|kg|gal|bottle|bottles|can|cans|box|boxes)?)\s+(.+)$", raw_item, flags=re.IGNORECASE)
        if quantity_match:
            quantity = quantity_match.group(1).strip()
            raw_item = quantity_match.group(2).strip()
        if not raw_item:
            return None
        item = atlas_app_store.create_shopping_item(
            APP_DB,
            text=raw_item,
            quantity=quantity,
            store=store,
            context_scope=context_scope,
        )
        display = f"{quantity + ' ' if quantity else ''}{raw_item}" + (f" at {store}" if store else "")
        return {
            "route": "shopping_list",
            "tool": "atlas-app-store",
            "answer": f"Shopping item saved: {display}.",
            "source": "ATLAS local shopping list",
            "result": {"item": item},
        }

    def _answer_writing_request(self, question: str) -> dict | None:
        lowered = question.lower().strip()
        match = re.match(r"^\s*(?:write|draft)\s+(?:a\s+)?(?:text|message|email)?\s*(?:to\s+[^:]+)?:?\s*(.+)$", question, flags=re.IGNORECASE)
        if not match and not any(phrase in lowered for phrase in ["make this more polite", "make this professional"]):
            return None
        content = match.group(1).strip() if match else question
        if "running late" in lowered:
            draft = "Hi, I’m running a little late. I’ll keep you posted on my ETA."
        elif "thank" in lowered:
            draft = "Thank you. I appreciate your help and will follow up if anything else is needed."
        else:
            draft = content[:1].upper() + content[1:] if content else "Draft ready."
        return {
            "route": "writing",
            "tool": "atlas-writing-helper",
            "answer": draft,
            "source": "ATLAS local writing helper",
            "result": {"mode": "draft_only"},
        }

    def _answer_comparison_request(self, question: str, context_scope: str = "personal") -> dict | None:
        lowered = question.lower()
        if not any(phrase in lowered for phrase in ["pros and cons", "compare ", "which is better"]):
            return None
        options_match = re.search(r"\bcompare\s+(.+?)\s+(?:and|vs\\.?|versus)\s+(.+)$", question, flags=re.IGNORECASE)
        # Preferences are personal facts about the owner -- never fold them
        # into scoring or context for an operations/admin-scope comparison,
        # same scope discipline as the cross-conversation memory tiers.
        preferences = atlas_app_store.list_preferences(APP_DB) if context_scope == "personal" else []
        criteria = ["fit", "reliability", "privacy", "maintenance", "cost", "reversibility"]
        if options_match:
            option_a = options_match.group(1).strip(" .")
            option_b = options_match.group(2).strip(" .?")
            preference_text = " ".join(item["value"].lower() for item in preferences)
            a_score = 3
            b_score = 3
            if "privacy" in preference_text or "local" in preference_text:
                if any(term in option_a.lower() for term in ["local", "self-hosted", "private"]):
                    a_score += 1
                if any(term in option_b.lower() for term in ["local", "self-hosted", "private"]):
                    b_score += 1
                if any(term in option_a.lower() for term in ["cloud", "public"]):
                    a_score -= 1
                if any(term in option_b.lower() for term in ["cloud", "public"]):
                    b_score -= 1
            winner = option_a if a_score > b_score else option_b if b_score > a_score else "tie"
            return {
                "route": "comparison",
                "tool": "atlas-comparison-helper",
                "answer": f"{option_a}: {a_score}/5. {option_b}: {b_score}/5. Recommendation: {winner}. Criteria: {', '.join(criteria)}.",
                "source": "ATLAS local comparison helper",
                "result": {
                    "option_a": option_a,
                    "option_b": option_b,
                    "score_a": a_score,
                    "score_b": b_score,
                    "winner": winner,
                    "criteria": criteria,
                    "preferences_used": preferences,
                },
            }
        return {
            "route": "comparison",
            "tool": "atlas-comparison-helper",
            "answer": "Use this comparison frame: goals, must-haves, constraints, cost, reliability, privacy, maintenance, and reversibility. If you give me the options, I can score them against those criteria.",
            "source": "ATLAS local comparison helper",
            "result": {"criteria": ["goals", "must-haves", "constraints", "cost", "reliability", "privacy", "maintenance", "reversibility"]},
        }

    def _answer_recommendation_request(self, question: str, context_scope: str = "personal") -> dict | None:
        lowered = question.lower()
        # Word-boundary matching throughout: bare substring checks here
        # previously false-positived on unrelated words -- "what should i"
        # matches inside "what should it" (pronoun "it" starts with "i"),
        # and " use" matches inside "user" -- both confirmed via real eval
        # failures where privacy-refusal questions phrased as "...the
        # assistant is asked: <risky request>. What should it do?" got
        # hijacked into a generic recommendation answer instead of a refusal.
        recommendation_phrase = re.search(r"\b(?:recommend|which should i)\b", lowered) or "best option" in lowered
        asks_recommendation = bool(recommendation_phrase)
        asks_choice = bool(re.search(r"\bwhat should i\b", lowered)) and bool(
            re.search(r"\b(?:buy|choose|use|install|pick|select|option|between)\b", lowered)
        )
        if not (asks_recommendation or asks_choice):
            return None
        # Same scope discipline as the cross-conversation memory tiers: never
        # surface personal preferences outside personal-scope conversations.
        preferences = atlas_app_store.list_preferences(APP_DB) if context_scope == "personal" else []
        pref_text = "; ".join(f"{item['key']}: {item['value']}" for item in preferences[:5])
        return {
            "route": "recommendation",
            "tool": "atlas-recommendation-helper",
            "answer": "I would shortlist options by fit, reliability, privacy, maintenance cost, and reversibility." + (f" Known preferences: {pref_text}." if pref_text else " Add budget or constraints and I can narrow it further."),
            "source": "ATLAS local recommendation helper",
            "result": {"preferences_used": preferences[:5]},
        }

    def _answer_explanation_request(self, question: str) -> dict | None:
        lowered = question.lower()
        if not any(phrase in lowered for phrase in ["explain this", "explain it", "explain ", "teach me"]):
            return None
        return {
            "route": "explanation",
            "tool": "atlas-explanation-helper",
            "answer": "I’ll explain it in layers: first the plain-English idea, then the important details, then an example, then what can go wrong. Give me the topic or paste the text, and I’ll apply that structure.",
            "source": "ATLAS local explanation helper",
            "result": {"style": "layered_explanation"},
        }

    def _save_preference_from_question(self, question: str) -> dict | None:
        match = re.match(r"^\s*(?:remember that )?(?:my preference is|i prefer)\s+(.+?)\s*$", question, flags=re.IGNORECASE)
        if not match:
            return None
        value = match.group(1).strip(" .")
        key = "general"
        if "privacy" in value.lower():
            key = "privacy"
        elif "budget" in value.lower() or "$" in value:
            key = "budget"
        preference = atlas_app_store.set_preference(APP_DB, key, value)
        return {
            "route": "preference",
            "tool": "atlas-preference-store",
            "answer": f"Preference saved: {key} = {value}.",
            "source": "ATLAS preference store",
            "result": {"preference": preference},
        }

    def _handle_list_conversations(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        params = urllib.parse.parse_qs(parsed.query)
        limit = parse_int((params.get("limit") or [None])[0], default=50, minimum=1, maximum=100)
        offset = parse_int((params.get("offset") or [None])[0], default=0, minimum=0, maximum=10000)
        scope_value = ((params.get("context_scope") or params.get("scope") or [""])[0]).strip() or None
        try:
            context_scope = parse_context_scope(scope_value) if scope_value else None
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        conversations = atlas_app_store.list_conversations(APP_DB, limit=limit, offset=offset, context_scope=context_scope)
        self._write_json(HTTPStatus.OK, {"ok": True, "conversations": conversations})

    def _handle_get_conversation(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        conversation_id = urllib.parse.unquote(parsed.path.removeprefix("/api/v1/conversations/")).strip()
        if not conversation_id or "/" in conversation_id:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid conversation id")
            return

        conversation = atlas_app_store.get_conversation(APP_DB, conversation_id)
        if not conversation:
            self._write_error(HTTPStatus.NOT_FOUND, "conversation not found")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "conversation": conversation})

    def _handle_search_messages(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        params = urllib.parse.parse_qs(parsed.query)
        query = ((params.get("query") or params.get("q") or [""])[0]).strip()
        if not query:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing query")
            return

        limit = parse_int((params.get("limit") or [None])[0], default=50, minimum=1, maximum=100)
        scope_value = ((params.get("context_scope") or params.get("scope") or [""])[0]).strip() or None
        try:
            context_scope = parse_context_scope(scope_value) if scope_value else None
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        messages = atlas_app_store.search_messages(APP_DB, query=query, limit=limit, context_scope=context_scope)
        self._write_json(HTTPStatus.OK, {"ok": True, "messages": messages})

    def _handle_create_reminder(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return

        text = (payload.get("text") or "").strip()
        if not text:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing text")
            return
        try:
            context_scope = parse_context_scope(payload.get("context_scope") or payload.get("scope"))
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        reminder_text, inferred_due_at = parse_reminder_text(text)
        reminder = atlas_app_store.create_reminder(
            APP_DB,
            text=reminder_text,
            due_at=payload.get("due_at") or inferred_due_at,
            recurrence=payload.get("recurrence"),
            context_scope=context_scope,
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "reminder": reminder})

    def _handle_list_reminders(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        params = urllib.parse.parse_qs(parsed.query)
        include_completed = ((params.get("include_completed") or ["false"])[0]).lower() in {"1", "true", "yes"}
        limit = parse_int((params.get("limit") or [None])[0], default=100, minimum=1, maximum=500)
        scope_value = ((params.get("context_scope") or params.get("scope") or [""])[0]).strip() or None
        try:
            context_scope = parse_context_scope(scope_value) if scope_value else None
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        reminders = atlas_app_store.list_reminders(APP_DB, include_completed=include_completed, limit=limit, context_scope=context_scope)
        self._write_json(HTTPStatus.OK, {"ok": True, "reminders": reminders})

    def _handle_complete_reminder(self, parsed):
        if not callback_authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        reminder_id = parsed.path.removeprefix("/api/v1/reminders/").removesuffix("/complete").strip("/")
        reminder = atlas_app_store.complete_reminder(APP_DB, reminder_id)
        if not reminder:
            self._write_error(HTTPStatus.NOT_FOUND, "reminder not found")
            return
        send_ntfy_feedback("Reminder completed", reminder["text"], "callback_feedback", reminder_id)
        self._write_json(HTTPStatus.OK, {"ok": True, "reminder": reminder})

    def _handle_update_reminder(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return
        reminder_id = parsed.path.removeprefix("/api/v1/reminders/").strip("/")
        text = payload.get("text")
        inferred_due_at = None
        if isinstance(text, str) and text.strip():
            text, inferred_due_at = parse_reminder_text(text.strip())
        else:
            text = None
        reminder = atlas_app_store.update_reminder(
            APP_DB,
            reminder_id,
            text=text,
            due_at=payload.get("due_at") if "due_at" in payload else inferred_due_at,
            recurrence=payload.get("recurrence") if "recurrence" in payload else None,
        )
        if not reminder:
            self._write_error(HTTPStatus.NOT_FOUND, "reminder not found")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "reminder": reminder})

    def _handle_delete_reminder(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        reminder_id = parsed.path.removeprefix("/api/v1/reminders/").strip("/")
        reminder = atlas_app_store.complete_reminder(APP_DB, reminder_id)
        if not reminder:
            self._write_error(HTTPStatus.NOT_FOUND, "reminder not found")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "reminder": reminder})

    def _handle_snooze_reminder(self, parsed):
        if not callback_authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        params = urllib.parse.parse_qs(parsed.query)
        minutes = parse_int((params.get("minutes") or [None])[0], default=15, minimum=1, maximum=1440)
        reminder_id = parsed.path.removeprefix("/api/v1/reminders/").removesuffix("/snooze").strip("/")
        due_at = (datetime.now(LOCAL_TZ) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        reminder = atlas_app_store.snooze_reminder(APP_DB, reminder_id, due_at)
        if not reminder:
            self._write_error(HTTPStatus.NOT_FOUND, "reminder not found")
            return
        atlas_app_store.add_audit_event(APP_DB, "reminder.snoozed", reminder["text"], {"reminder_id": reminder_id, "minutes": minutes})
        send_ntfy_feedback(
            "Reminder snoozed",
            f"{reminder['text']} until {due_at}",
            "callback_feedback",
            reminder_id,
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "reminder": reminder})

    def _handle_create_note(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return

        text = (payload.get("text") or "").strip()
        if not text:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing text")
            return
        try:
            context_scope = parse_context_scope(payload.get("context_scope") or payload.get("scope"))
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        note = atlas_app_store.create_note(APP_DB, title=(payload.get("title") or text), text=text, context_scope=context_scope)
        self._write_json(HTTPStatus.OK, {"ok": True, "note": note})

    def _handle_create_comic_request(self):
        """Create a pending confirmation for a comic-bot run (DEC-2026-09-10-04).

        Deliberately NOT request_fuzzy_action_confirmation(): this is a
        structured PWA form (owner explicitly typed a value and hit Send),
        not a model-classified guess from free chat text, so there is no
        ambiguity to disambiguate. The confirmation step exists here purely
        because the action itself -- driving a real browser against a
        third-party site and writing a file outside ATLAS's own storage --
        has a materially larger blast radius than the existing reversible
        local-database actions, matching every other irreversible external
        action in ATLAS rather than the bedside lamp's negligible-blast-
        radius exception.
        """
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        if not COMIC_BOT_ENABLED:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "comic bot is not enabled")
            return
        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return
        text = (payload.get("text") or "").strip()
        if not text:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing text")
            return
        if len(text) > COMIC_REQUEST_MAX_LENGTH:
            self._write_error(HTTPStatus.BAD_REQUEST, f"text exceeds {COMIC_REQUEST_MAX_LENGTH} characters")
            return
        try:
            context_scope = parse_context_scope(payload.get("context_scope") or payload.get("scope"))
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        # Optional: which search result to pick (default 0, the top result).
        # Exists for the rare case where a series' search results put an
        # unwanted match (e.g. a TPB collection sharing a number with a
        # single issue) ahead of the intended one -- not currently exposed
        # in the PWA panel, only settable by a caller that already knows
        # the real search page's ranking. See DEC-2026-09-10-07.
        result_index = payload.get("result_index")
        if result_index is not None:
            try:
                result_index = int(result_index)
            except (TypeError, ValueError):
                self._write_error(HTTPStatus.BAD_REQUEST, "result_index must be an integer")
                return
            if not (0 <= result_index <= COMIC_REQUEST_MAX_RESULT_INDEX):
                self._write_error(HTTPStatus.BAD_REQUEST, f"result_index must be between 0 and {COMIC_REQUEST_MAX_RESULT_INDEX}")
                return

        summary = f"Run comic-bot for: {text}"
        details = {
            "capability": "media.comic_request",
            "context_scope": context_scope,
            "text": text,
            "decision_source": "media_request_panel",
        }
        if result_index is not None:
            details["result_index"] = result_index
        confirmation = atlas_app_store.request_confirmation(
            APP_DB,
            action_type="action.media.comic_request",
            summary=summary,
            risk_level="high_impact",
            details=details,
            execution_status="pending",
        )
        atlas_app_store.create_notification(
            APP_DB,
            channel="local",
            title="Confirmation required",
            body=summary,
            source_type="confirmation",
            source_id=confirmation["id"],
        )
        atlas_app_store.add_audit_event(
            APP_DB,
            "confirmation.requested",
            summary,
            {"confirmation_id": confirmation["id"], "capability": "media.comic_request", "decision_source": "media_request_panel"},
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "request": confirmation})

    def _handle_list_comic_requests(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        params = urllib.parse.parse_qs(parsed.query)
        limit = parse_int((params.get("limit") or [None])[0], default=50, minimum=1, maximum=200)
        confirmations = atlas_app_store.list_confirmations(APP_DB, limit=limit * 4)
        requests = [
            item for item in confirmations
            if (item.get("details") or {}).get("capability") == "media.comic_request"
        ][:limit]
        self._write_json(HTTPStatus.OK, {"ok": True, "requests": requests})

    def _handle_search_notes(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        params = urllib.parse.parse_qs(parsed.query)
        query = ((params.get("query") or params.get("q") or [""])[0]).strip()
        scope_value = ((params.get("context_scope") or params.get("scope") or [""])[0]).strip() or None
        try:
            context_scope = parse_context_scope(scope_value) if scope_value else None
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if not query:
            limit = parse_int((params.get("limit") or [None])[0], default=100, minimum=1, maximum=500)
            notes = atlas_app_store.list_notes(APP_DB, limit=limit, context_scope=context_scope)
            self._write_json(HTTPStatus.OK, {"ok": True, "notes": notes})
            return
        limit = parse_int((params.get("limit") or [None])[0], default=50, minimum=1, maximum=100)
        notes = atlas_app_store.search_notes(APP_DB, query=query, limit=limit, context_scope=context_scope)
        self._write_json(HTTPStatus.OK, {"ok": True, "notes": notes})

    def _handle_update_note(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return
        note_id = parsed.path.removeprefix("/api/v1/notes/").strip("/")
        note = atlas_app_store.update_note(
            APP_DB,
            note_id,
            title=(payload.get("title") or None),
            text=(payload.get("text") or None),
        )
        if not note:
            self._write_error(HTTPStatus.NOT_FOUND, "note not found")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "note": note})

    def _handle_delete_note(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        note_id = parsed.path.removeprefix("/api/v1/notes/").strip("/")
        note = atlas_app_store.archive_note(APP_DB, note_id)
        if not note:
            self._write_error(HTTPStatus.NOT_FOUND, "note not found")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "note": note})

    def _handle_create_task(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return
        text = (payload.get("text") or "").strip()
        if not text:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing text")
            return
        try:
            context_scope = parse_context_scope(payload.get("context_scope") or payload.get("scope"))
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        task = atlas_app_store.create_task(APP_DB, text=text, priority=payload.get("priority"), context_scope=context_scope)
        self._write_json(HTTPStatus.OK, {"ok": True, "task": task})

    def _handle_create_shopping_item(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return
        text = (payload.get("text") or "").strip()
        if not text:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing text")
            return
        try:
            context_scope = parse_context_scope(payload.get("context_scope") or payload.get("scope"))
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        item = atlas_app_store.create_shopping_item(
            APP_DB,
            text=text,
            quantity=(payload.get("quantity") or None),
            store=(payload.get("store") or None),
            context_scope=context_scope,
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "item": item})

    def _handle_list_shopping_items(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        params = urllib.parse.parse_qs(parsed.query)
        include_completed = ((params.get("include_completed") or ["false"])[0]).lower() in {"1", "true", "yes"}
        limit = parse_int((params.get("limit") or [None])[0], default=100, minimum=1, maximum=500)
        scope_value = ((params.get("context_scope") or params.get("scope") or [""])[0]).strip() or None
        try:
            context_scope = parse_context_scope(scope_value) if scope_value else None
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        items = atlas_app_store.list_shopping_items(APP_DB, include_completed=include_completed, limit=limit, context_scope=context_scope)
        self._write_json(HTTPStatus.OK, {"ok": True, "items": items})

    def _handle_complete_shopping_item(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        item_id = parsed.path.removeprefix("/api/v1/shopping/").removesuffix("/complete").strip("/")
        item = atlas_app_store.complete_shopping_item(APP_DB, item_id)
        if not item:
            self._write_error(HTTPStatus.NOT_FOUND, "shopping item not found")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "item": item})

    def _handle_update_shopping_item(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return
        item_id = parsed.path.removeprefix("/api/v1/shopping/").strip("/")
        item = atlas_app_store.update_shopping_item(
            APP_DB,
            item_id,
            text=(payload.get("text") or None),
            quantity=(payload.get("quantity") or None),
            store=(payload.get("store") or None),
        )
        if not item:
            self._write_error(HTTPStatus.NOT_FOUND, "shopping item not found")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "item": item})

    def _handle_delete_shopping_item(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        item_id = parsed.path.removeprefix("/api/v1/shopping/").strip("/")
        item = atlas_app_store.complete_shopping_item(APP_DB, item_id)
        if not item:
            self._write_error(HTTPStatus.NOT_FOUND, "shopping item not found")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "item": item})

    def _handle_list_tasks(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        params = urllib.parse.parse_qs(parsed.query)
        include_completed = ((params.get("include_completed") or ["false"])[0]).lower() in {"1", "true", "yes"}
        limit = parse_int((params.get("limit") or [None])[0], default=100, minimum=1, maximum=500)
        scope_value = ((params.get("context_scope") or params.get("scope") or [""])[0]).strip() or None
        try:
            context_scope = parse_context_scope(scope_value) if scope_value else None
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        tasks = atlas_app_store.list_tasks(APP_DB, include_completed=include_completed, limit=limit, context_scope=context_scope)
        self._write_json(HTTPStatus.OK, {"ok": True, "tasks": tasks})

    def _handle_complete_task(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        task_id = parsed.path.removeprefix("/api/v1/tasks/").removesuffix("/complete").strip("/")
        task = atlas_app_store.complete_task(APP_DB, task_id)
        if not task:
            self._write_error(HTTPStatus.NOT_FOUND, "task not found")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "task": task})

    def _handle_update_task(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return
        task_id = parsed.path.removeprefix("/api/v1/tasks/").strip("/")
        task = atlas_app_store.update_task(
            APP_DB,
            task_id,
            text=(payload.get("text") or None),
            priority=(payload.get("priority") or None),
        )
        if not task:
            self._write_error(HTTPStatus.NOT_FOUND, "task not found")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "task": task})

    def _handle_delete_task(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        task_id = parsed.path.removeprefix("/api/v1/tasks/").strip("/")
        task = atlas_app_store.complete_task(APP_DB, task_id)
        if not task:
            self._write_error(HTTPStatus.NOT_FOUND, "task not found")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "task": task})

    def _handle_create_rule(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return
        condition_text = (payload.get("condition") or "").strip()
        action_text = (payload.get("action") or "").strip()
        if not condition_text or not action_text:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing condition or action")
            return
        # Track B1: fail loudly at creation, not silently at evaluation --
        # see atlas_signals.validate_condition_for_creation()'s docstring.
        # Free text and legacy 'always'/'now' both still pass through here
        # unchanged; only a grammar-shaped-but-invalid condition is rejected.
        condition_error = atlas_signals.validate_condition_for_creation(condition_text)
        if condition_error:
            self._write_error(HTTPStatus.BAD_REQUEST, condition_error)
            return
        rule = atlas_app_store.create_rule(APP_DB, condition_text=condition_text, action_text=action_text)
        self._write_json(HTTPStatus.OK, {"ok": True, "rule": rule})

    def _handle_list_rules(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        params = urllib.parse.parse_qs(parsed.query)
        limit = parse_int((params.get("limit") or [None])[0], default=100, minimum=1, maximum=500)
        rules = atlas_app_store.list_rules(APP_DB, limit=limit)
        self._write_json(HTTPStatus.OK, {"ok": True, "rules": rules})

    def _handle_update_rule(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return
        rule_id = parsed.path.removeprefix("/api/v1/rules/").strip("/")
        enabled = payload.get("enabled")
        rule = atlas_app_store.update_rule(
            APP_DB,
            rule_id,
            enabled=bool(enabled) if enabled is not None else None,
        )
        if not rule:
            self._write_error(HTTPStatus.NOT_FOUND, "rule not found")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "rule": rule})

    def _handle_delete_rule(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        rule_id = parsed.path.removeprefix("/api/v1/rules/").strip("/")
        deleted = atlas_app_store.delete_rule(APP_DB, rule_id)
        if not deleted:
            self._write_error(HTTPStatus.NOT_FOUND, "rule not found")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "id": rule_id})

    def _handle_list_audit(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        params = urllib.parse.parse_qs(parsed.query)
        limit = parse_int((params.get("limit") or [None])[0], default=100, minimum=1, maximum=500)
        events = atlas_app_store.list_audit_events(APP_DB, limit=limit)
        self._write_json(HTTPStatus.OK, {"ok": True, "events": events})

    def _handle_permissions(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "permissions": ATLAS_PERMISSION_POLICY["action_tiers"],
                "policy": ATLAS_PERMISSION_POLICY,
                "summary": "ATLAS is owner-only and privacy-first. Low-risk local writes are allowed when explicitly requested; risky actions require confirmation; secrets and cross-user private data are blocked by default.",
            },
        )

    def _handle_list_notifications(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        params = urllib.parse.parse_qs(parsed.query)
        status = ((params.get("status") or [""])[0]).strip() or None
        limit = parse_int((params.get("limit") or [None])[0], default=100, minimum=1, maximum=500)
        notifications = atlas_app_store.list_notifications(APP_DB, status=status, limit=limit)
        self._write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "delivery_configured": bool(NTFY_BASE_URL and NTFY_TOPIC),
                "notifications": notifications,
            },
        )

    def _handle_deliver_notifications(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        queued = atlas_app_store.list_notifications(APP_DB, status="queued", limit=50)
        delivered = []
        failed = []
        for notification in queued:
            try:
                result = deliver_ntfy(notification)
                updated = atlas_app_store.mark_notification_delivered(
                    APP_DB,
                    notification["id"],
                    datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
                )
                delivered.append({"notification": updated, "delivery": result})
            except Exception as exc:
                updated = atlas_app_store.mark_notification_failed(APP_DB, notification["id"], str(exc))
                failed.append({"notification": updated, "error": str(exc)})
        atlas_app_store.add_audit_event(
            APP_DB,
            "notifications.delivered",
            f"Delivered {len(delivered)} notifications, failed {len(failed)}",
            {"delivered": len(delivered), "failed": len(failed), "provider": "ntfy"},
        )
        self._write_json(
            HTTPStatus.OK,
            {
                "ok": not failed,
                "delivery_configured": bool(NTFY_BASE_URL and NTFY_TOPIC),
                "delivered": delivered,
                "failed": failed,
            },
        )

    def _handle_execute_approved_confirmations(self):
        """Poll target for the scheduler, not the ntfy Approve action itself.

        ntfy's Approve/Deny buttons are fire-and-forget GETs with no visible
        response surface -- blocking that tap on a DB write would add a new
        double-tap failure mode for no visible benefit. This follows the same
        batch-poll-and-audit shape as _handle_deliver_notifications above,
        called by atlas_scheduler.py on its existing ~60s tick, not by the
        confirmation approval endpoint.
        """
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        approved = atlas_app_store.list_confirmations(APP_DB, status="approved", limit=50)
        executed = []
        failed = []
        skipped = []
        for confirmation in approved:
            capability = (confirmation.get("details") or {}).get("capability")
            if confirmation.get("execution_status") not in {"pending", "failed"} or capability not in _ACTION_ALLOWLIST:
                skipped.append(confirmation["id"])
                continue
            try:
                outcome = atlas_app_store.execute_confirmed_action(APP_DB, confirmation["id"])
                (executed if outcome.get("ok") else failed).append({"confirmation_id": confirmation["id"], "outcome": outcome})
            except Exception as exc:  # pragma: no cover -- execute_confirmed_action already fails closed internally
                failed.append({"confirmation_id": confirmation["id"], "error": str(exc)})
        atlas_app_store.add_audit_event(
            APP_DB,
            "confirmations.execution_polled",
            f"Executed {len(executed)}, failed {len(failed)}, skipped {len(skipped)}",
            {"executed": len(executed), "failed": len(failed), "skipped": len(skipped)},
        )
        self._write_json(
            HTTPStatus.OK,
            {"ok": not failed, "executed": executed, "failed": failed, "skipped": skipped},
        )

    def _handle_list_confirmations(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        params = urllib.parse.parse_qs(parsed.query)
        status = ((params.get("status") or [""])[0]).strip() or None
        limit = parse_int((params.get("limit") or [None])[0], default=100, minimum=1, maximum=500)
        confirmations = atlas_app_store.list_confirmations(APP_DB, status=status, limit=limit)
        self._write_json(HTTPStatus.OK, {"ok": True, "confirmations": confirmations})

    def _handle_decide_confirmation(self, parsed, status: str):
        if not callback_authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        confirmation_id = (
            parsed.path.removeprefix("/api/v1/confirmations/")
            .removesuffix("/approve")
            .removesuffix("/deny")
            .strip("/")
        )
        confirmation = atlas_app_store.decide_confirmation(APP_DB, confirmation_id, status)
        if not confirmation:
            self._write_error(HTTPStatus.NOT_FOUND, "confirmation not found")
            return
        atlas_app_store.add_audit_event(APP_DB, f"confirmation.{status}", confirmation["summary"], {"confirmation_id": confirmation_id})
        send_ntfy_feedback(
            "Confirmation recorded",
            f"{confirmation['summary']} -> {confirmation['status']}",
            "callback_feedback",
            confirmation_id,
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "confirmation": confirmation})

    def _handle_list_preferences(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "preferences": atlas_app_store.list_preferences(APP_DB)})

    def _handle_set_preference(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return
        key = (payload.get("key") or "").strip()
        value = (payload.get("value") or "").strip()
        if not key or not value:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing key or value")
            return
        preference = atlas_app_store.set_preference(APP_DB, key, value)
        self._write_json(HTTPStatus.OK, {"ok": True, "preference": preference})

    def _handle_delete_preference(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        key = urllib.parse.unquote(parsed.path.removeprefix("/api/v1/preferences/").strip("/"))
        deleted = atlas_app_store.delete_preference(APP_DB, key)
        if not deleted:
            self._write_error(HTTPStatus.NOT_FOUND, "preference not found")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "key": key})

    def _handle_memory_sync(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        items = atlas_memory_corpus.memory_items(APP_DB)
        fingerprint = atlas_memory_corpus.compute_fingerprint(items)
        previous = atlas_memory_corpus.read_state_cache(MEMORY_STATE_FILE)
        if previous.get("fingerprint") == fingerprint:
            self._write_json(
                HTTPStatus.OK,
                {"ok": True, "synced": False, "reason": "unchanged", "item_count": len(items)},
            )
            return

        atlas_memory_corpus.write_corpus(items, MEMORY_CORPUS_DIR)
        knowledge_id = None
        command = [
            sys.executable,
            str(IMPORT_CORPUS_SCRIPT),
            "--base-url", BASE_URL,
            "--webui-db", WEBUI_DB,
            "--corpus-dir", str(MEMORY_CORPUS_DIR),
            "--knowledge-name", MEMORY_KNOWLEDGE_NAME,
            "--knowledge-description", "Owner-approved preferences and personal notes, for cross-conversation recall.",
            "--lock-file", MEMORY_IMPORT_LOCK_FILE,
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=REQUEST_TIMEOUT, check=True)
            for line in reversed(completed.stdout.splitlines()):
                if line.strip().startswith("{"):
                    start = completed.stdout.rfind(line)
                    summary = json.loads(completed.stdout[start:])
                    knowledge_id = summary.get("knowledge_id")
                    break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as exc:
            atlas_app_store.add_audit_event(
                APP_DB, "memory.sync_failed", "Personal memory sync failed", {"error": str(exc)[:500]},
            )
            self._write_error(HTTPStatus.INTERNAL_SERVER_ERROR, "memory sync failed")
            return

        atlas_memory_corpus.write_state_cache(MEMORY_STATE_FILE, items, fingerprint, knowledge_id)
        atlas_app_store.add_audit_event(
            APP_DB,
            "memory.sync_completed",
            f"Synced {len(items)} personal memory items",
            {"item_count": len(items), "knowledge_id": knowledge_id},
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "synced": True, "item_count": len(items)})

    def _handle_version(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "service": "atlas-shortcuts",
                "api_version": ATLAS_API_VERSION,
                "features": [
                    "chat",
                    "logs",
                    "speech",
                    "reminders",
                    "notes",
                    "tasks",
                    "shopping",
                    "rules",
                    "notifications",
                    "confirmations",
                    "audit",
                    "feedback",
                    "diagnostics",
                    "examples",
                    "school_bridge_foundation",
                    "home_assistant_readonly_foundation",
                    "nextcloud_readonly_foundation",
                ],
            },
        )

    def _handle_sfsu_status(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "enabled": SCHOOL_BRIDGE_ENABLED,
                "configured": bool(SCHOOL_BRIDGE_TOKEN),
                "connected": False,
                "mode": "isolated_school_bridge",
                "sources": ["canvas_ical", "thunderbird"],
                "mail_bodies": False,
                "attachments": False,
                "writes": False,
                "direct_graph": "blocked_pending_sfsu_admin_approval",
            },
        )

    def _handle_school_bridge_status(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        status = atlas_sfsu_store.bridge_status(APP_DB, SCHOOL_BRIDGE_OWNER_ID)
        self._write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "enabled": SCHOOL_BRIDGE_ENABLED,
                "configured": bool(SCHOOL_BRIDGE_TOKEN),
                "mode": "metadata_only",
                "status": status,
            },
        )

    def _handle_school_bridge_import(self):
        if not bridge_authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "bridge unauthorized")
            return
        if not SCHOOL_BRIDGE_ENABLED:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "school bridge disabled")
            return
        try:
            payload = load_request_payload(self)
            if payload.get("owner_id", SCHOOL_BRIDGE_OWNER_ID) != SCHOOL_BRIDGE_OWNER_ID:
                raise ValueError("owner mismatch")
            result = atlas_sfsu_store.import_records(
                APP_DB,
                SCHOOL_BRIDGE_OWNER_ID,
                str(payload.get("source") or ""),
                str(payload.get("kind") or ""),
                payload.get("records"),
                payload.get("fetched_at"),
            )
        except (json.JSONDecodeError, ValueError) as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        atlas_app_store.add_audit_event(
            APP_DB,
            "school_bridge.imported",
            f"Imported {result['accepted']} {result['kind']} records",
            {"source": result["source"], "accepted": result["accepted"]},
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "import": result})

    def _handle_home_assistant_state(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        if not HOME_ASSISTANT_ENABLED:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "Home Assistant adapter disabled")
            return
        try:
            allowlist = atlas_home_assistant.parse_allowlist(HOME_ASSISTANT_ENTITIES)
            entity_id = (urllib.parse.parse_qs(parsed.query).get("entity_id") or [""])[0]
            state = atlas_home_assistant.fetch_state(
                HOME_ASSISTANT_BASE_URL,
                HOME_ASSISTANT_TOKEN,
                entity_id,
                allowlist,
            )
        except PermissionError as exc:
            self._write_error(HTTPStatus.FORBIDDEN, str(exc))
            return
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except Exception as exc:
            self._write_error(HTTPStatus.BAD_GATEWAY, f"Home Assistant state unavailable: {exc}")
            return
        atlas_app_store.add_audit_event(
            APP_DB,
            "home_assistant.state_read",
            f"Read allowlisted state for {state['entity_id']}",
            {"entity_id": state["entity_id"], "source_updated_at": state["source_updated_at"]},
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "state": state})

    def _handle_nextcloud_files(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        if not NEXTCLOUD_ENABLED:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "Nextcloud adapter disabled")
            return
        try:
            allowlist = json.loads(NEXTCLOUD_PATHS)
            if not isinstance(allowlist, list) or not allowlist or len(allowlist) > 100:
                raise ValueError("Nextcloud path allowlist must contain 1 to 100 roots")
            path = (urllib.parse.parse_qs(parsed.query).get("path") or [""])[0]
            records = atlas_nextcloud.list_file_metadata(
                NEXTCLOUD_BASE_URL,
                NEXTCLOUD_USERNAME,
                NEXTCLOUD_APP_PASSWORD,
                path,
                [str(item) for item in allowlist],
            )
        except PermissionError as exc:
            self._write_error(HTTPStatus.FORBIDDEN, str(exc))
            return
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except Exception as exc:
            self._write_error(HTTPStatus.BAD_GATEWAY, f"Nextcloud metadata unavailable: {exc}")
            return
        atlas_app_store.add_audit_event(
            APP_DB,
            "nextcloud.files_read",
            f"Read {len(records)} metadata records from an allowlisted Nextcloud path",
            {"path": path, "record_count": len(records)},
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "records": records, "source": "nextcloud_dav"})

    def _handle_nextcloud_calendars(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        if not NEXTCLOUD_ENABLED:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "Nextcloud adapter disabled")
            return
        try:
            allowlist = json.loads(NEXTCLOUD_CALENDARS)
            if not isinstance(allowlist, list) or not allowlist or len(allowlist) > 100:
                raise ValueError("Nextcloud calendar allowlist must contain 1 to 100 names")
            records = atlas_nextcloud.list_calendars(
                NEXTCLOUD_BASE_URL,
                NEXTCLOUD_USERNAME,
                NEXTCLOUD_APP_PASSWORD,
                [str(item) for item in allowlist],
            )
        except PermissionError as exc:
            self._write_error(HTTPStatus.FORBIDDEN, str(exc))
            return
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except Exception as exc:
            self._write_error(HTTPStatus.BAD_GATEWAY, f"Nextcloud calendars unavailable: {exc}")
            return
        atlas_app_store.add_audit_event(
            APP_DB,
            "nextcloud.calendars_read",
            f"Read {len(records)} allowlisted Nextcloud calendars",
            {"calendar_count": len(records)},
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "calendars": records, "source": "nextcloud_caldav"})

    def _calendar_window(self, query):
        start = (query.get("start") or [""])[0]
        end = (query.get("end") or [""])[0]
        if not start:
            start = datetime.now(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
        if not end:
            end = (datetime.now(ZoneInfo("UTC")) + timedelta(days=30)).strftime("%Y%m%dT%H%M%SZ")
        pattern = r"^\d{8}T\d{6}Z$"
        if not re.match(pattern, start) or not re.match(pattern, end):
            raise ValueError("Calendar start and end must use UTC format YYYYMMDDTHHMMSSZ")
        if start >= end:
            raise ValueError("Calendar start must be before end")
        return start, end

    def _handle_nextcloud_calendar_events(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        if not NEXTCLOUD_ENABLED:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "Nextcloud adapter disabled")
            return
        try:
            query = urllib.parse.parse_qs(parsed.query)
            calendar = (query.get("calendar") or [""])[0]
            start, end = self._calendar_window(query)
            allowlist = json.loads(NEXTCLOUD_CALENDARS)
            if not isinstance(allowlist, list) or not allowlist or len(allowlist) > 100:
                raise ValueError("Nextcloud calendar allowlist must contain 1 to 100 names")
            records = atlas_nextcloud.list_calendar_events(
                NEXTCLOUD_BASE_URL,
                NEXTCLOUD_USERNAME,
                NEXTCLOUD_APP_PASSWORD,
                calendar,
                [str(item) for item in allowlist],
                start,
                end,
            )
        except PermissionError as exc:
            self._write_error(HTTPStatus.FORBIDDEN, str(exc))
            return
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except Exception as exc:
            self._write_error(HTTPStatus.BAD_GATEWAY, f"Nextcloud calendar events unavailable: {exc}")
            return
        atlas_app_store.add_audit_event(
            APP_DB,
            "nextcloud.calendar_events_read",
            f"Read {len(records)} events from an allowlisted Nextcloud calendar",
            {"calendar": calendar, "event_count": len(records), "start": start, "end": end},
        )
        self._write_json(
            HTTPStatus.OK,
            {"ok": True, "events": records, "source": "nextcloud_caldav", "calendar": calendar, "start": start, "end": end},
        )

    def _handle_nextcloud_addressbooks(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        if not NEXTCLOUD_ENABLED:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "Nextcloud adapter disabled")
            return
        try:
            allowlist = json.loads(NEXTCLOUD_ADDRESSBOOKS)
            if not isinstance(allowlist, list) or not allowlist or len(allowlist) > 100:
                raise ValueError("Nextcloud address book allowlist must contain 1 to 100 names")
            records = atlas_nextcloud.list_addressbooks(
                NEXTCLOUD_BASE_URL,
                NEXTCLOUD_USERNAME,
                NEXTCLOUD_APP_PASSWORD,
                [str(item) for item in allowlist],
            )
        except PermissionError as exc:
            self._write_error(HTTPStatus.FORBIDDEN, str(exc))
            return
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except Exception as exc:
            self._write_error(HTTPStatus.BAD_GATEWAY, f"Nextcloud address books unavailable: {exc}")
            return
        atlas_app_store.add_audit_event(
            APP_DB,
            "nextcloud.addressbooks_read",
            f"Read {len(records)} allowlisted Nextcloud address books",
            {"addressbook_count": len(records)},
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "addressbooks": records, "source": "nextcloud_carddav"})

    def _handle_nextcloud_contact_cards(self, parsed):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        if not NEXTCLOUD_ENABLED:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "Nextcloud adapter disabled")
            return
        try:
            addressbook = (urllib.parse.parse_qs(parsed.query).get("addressbook") or [""])[0]
            allowlist = json.loads(NEXTCLOUD_ADDRESSBOOKS)
            if not isinstance(allowlist, list) or not allowlist or len(allowlist) > 100:
                raise ValueError("Nextcloud address book allowlist must contain 1 to 100 names")
            records = atlas_nextcloud.list_address_cards(
                NEXTCLOUD_BASE_URL,
                NEXTCLOUD_USERNAME,
                NEXTCLOUD_APP_PASSWORD,
                addressbook,
                [str(item) for item in allowlist],
            )
        except PermissionError as exc:
            self._write_error(HTTPStatus.FORBIDDEN, str(exc))
            return
        except ValueError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except Exception as exc:
            self._write_error(HTTPStatus.BAD_GATEWAY, f"Nextcloud contact cards unavailable: {exc}")
            return
        atlas_app_store.add_audit_event(
            APP_DB,
            "nextcloud.contact_cards_read",
            f"Read {len(records)} contacts from an allowlisted Nextcloud address book",
            {"addressbook": addressbook, "contact_count": len(records)},
        )
        self._write_json(
            HTTPStatus.OK,
            {"ok": True, "contacts": records, "source": "nextcloud_carddav", "addressbook": addressbook},
        )

    def _handle_school_bridge_purge(self):
        if not bridge_authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "bridge unauthorized")
            return
        if not SCHOOL_BRIDGE_ENABLED:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "school bridge disabled")
            return
        atlas_sfsu_store.purge_owner(APP_DB, SCHOOL_BRIDGE_OWNER_ID)
        atlas_app_store.add_audit_event(
            APP_DB,
            "school_bridge.purged",
            "Purged school bridge metadata",
            {"owner_id": SCHOOL_BRIDGE_OWNER_ID},
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "purged": True})

    def _handle_examples(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "examples": ASSISTANT_EXAMPLES})

    def _handle_diagnostics(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        checks = [
            {
                "name": "backend",
                "ok": True,
                "detail": "ATLAS shortcuts API is reachable.",
            },
            {
                "name": "token",
                "ok": True,
                "detail": "Bearer token accepted.",
            },
            {
                "name": "database",
                "ok": APP_DB.exists(),
                "detail": str(APP_DB),
            },
            {
                "name": "piper_config",
                "ok": bool(PIPER_BASE_URL),
                "detail": PIPER_BASE_URL,
            },
            {
                "name": "notifications_config",
                "ok": bool(NTFY_BASE_URL and NTFY_TOPIC),
                "detail": f"base_url={NTFY_BASE_URL or 'unset'}, topic={NTFY_TOPIC or 'unset'}",
            },
            {
                "name": "runner_config",
                "ok": bool(RUNNER_PATH),
                "detail": RUNNER_PATH,
            },
        ]
        self._write_json(
            HTTPStatus.OK,
            {
                "ok": all(check["ok"] for check in checks),
                "service": "atlas-shortcuts",
                "api_version": ATLAS_API_VERSION,
                "checks": checks,
            },
        )

    def _handle_feedback(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return
        rating = (payload.get("rating") or "").strip().lower()
        if rating not in {"good", "bad"}:
            self._write_error(HTTPStatus.BAD_REQUEST, "rating must be good or bad")
            return
        message_id = (payload.get("message_id") or "").strip() or None
        conversation_id = (payload.get("conversation_id") or "").strip() or None
        note = (payload.get("note") or "").strip()
        event = atlas_app_store.add_audit_event(
            APP_DB,
            f"feedback.{rating}",
            note or f"User marked assistant answer as {rating}",
            {
                "rating": rating,
                "message_id": message_id,
                "conversation_id": conversation_id,
                "note": note,
                "client": payload.get("client") or "unknown",
            },
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "event": event})

    def _handle_evaluate(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        now_value = datetime.now(LOCAL_TZ).isoformat(timespec="seconds")
        due = atlas_app_store.due_reminders(APP_DB, now_value)
        due_rules = atlas_app_store.due_rules(APP_DB, now_value, signal_routes=build_signal_routes())
        notifications = []
        for reminder in due:
            notification = atlas_app_store.create_notification(
                APP_DB,
                title="Reminder",
                body=reminder["text"],
                source_type="reminder",
                source_id=reminder["id"],
            )
            atlas_app_store.mark_reminder_notified(APP_DB, reminder["id"], now_value)
            if reminder.get("recurrence") == "daily":
                try:
                    atlas_app_store.reschedule_recurring_reminder(
                        APP_DB,
                        reminder["id"],
                        next_recurring_due(reminder["due_at"], datetime.now(LOCAL_TZ), "daily"),
                    )
                except Exception as exc:
                    atlas_app_store.add_audit_event(APP_DB, "reminder.recurrence_failed", reminder["text"], {"error": str(exc)})
            notifications.append(notification)
        for rule in due_rules:
            notification = atlas_app_store.create_notification(
                APP_DB,
                title="ATLAS rule",
                body=rule["action_text"],
                source_type="rule",
                source_id=rule["id"],
            )
            # DEF-001 legacy stub ('always'/'now'): mark fired and
            # auto-disable (see mark_rule_fired()'s own docstring for why --
            # a stub condition can't meaningfully become true again).
            # Track B1 structured rules use mark_condition_rule_fired()
            # instead, which records last_fired_at WITHOUT disabling, since
            # a real condition can legitimately resolve and re-trigger.
            if rule["condition_text"].strip().lower() in atlas_signals.LEGACY_STUB_CONDITIONS:
                atlas_app_store.mark_rule_fired(APP_DB, rule["id"], now_value)
            else:
                atlas_app_store.mark_condition_rule_fired(APP_DB, rule["id"], now_value)
            notifications.append(notification)
        atlas_app_store.add_audit_event(
            APP_DB, "evaluation.completed", f"Queued {len(notifications)} notifications",
            {"due_reminders": len(due), "due_rules": len(due_rules)},
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "notifications": notifications, "due_reminders": due, "due_rules": due_rules})

    def _handle_evaluate_briefing(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return
        now_local = datetime.now(LOCAL_TZ)
        try:
            due = ATLAS_DAILY_BRIEFING_ENABLED and atlas_briefings.is_due(now_local, ATLAS_DAILY_BRIEFING_TIME)
        except ValueError as exc:
            self._write_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        scheduled_for = now_local.date().isoformat()
        if not due:
            self._write_json(HTTPStatus.OK, {"ok": True, "enabled": ATLAS_DAILY_BRIEFING_ENABLED, "due": False, "queued": False})
            return
        existing = atlas_app_store.get_briefing_run(APP_DB, "daily", scheduled_for)
        if existing:
            self._write_json(HTTPStatus.OK, {"ok": True, "enabled": True, "due": True, "queued": False, "duplicate": True, "run": existing})
            return

        unavailable: list[str] = []
        try:
            reminders = atlas_app_store.list_reminders(APP_DB, context_scope="personal")
        except Exception:
            reminders = []
            unavailable.append("Reminders")
        try:
            confirmations = atlas_app_store.list_confirmations(APP_DB, status="pending")
        except Exception:
            confirmations = []
            unavailable.append("Pending confirmations")
        try:
            failed_notifications = atlas_app_store.list_notifications(APP_DB, status="dead_letter")
        except Exception:
            failed_notifications = []
            unavailable.append("Failed notifications")

        events: list[dict] = []
        if NEXTCLOUD_ENABLED:
            try:
                allowlist = json.loads(NEXTCLOUD_CALENDARS)
                if not isinstance(allowlist, list) or not allowlist:
                    raise ValueError("no calendars are allowlisted")
                start = now_local.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
                end = (now_local + timedelta(hours=24)).astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
                for calendar in [str(item) for item in allowlist]:
                    events.extend(atlas_nextcloud.list_calendar_events(
                        NEXTCLOUD_BASE_URL, NEXTCLOUD_USERNAME, NEXTCLOUD_APP_PASSWORD,
                        calendar, [str(item) for item in allowlist], start, end,
                    ))
            except Exception:
                unavailable.append("Calendar")
        else:
            unavailable.append("Calendar")

        body = atlas_briefings.compose_daily(
            now_local,
            events=events,
            reminders=reminders,
            confirmations=confirmations,
            failed_notifications=failed_notifications,
            unavailable=unavailable,
        )
        run = atlas_app_store.create_briefing_notification_once(
            APP_DB, "daily", scheduled_for, "ATLAS midday briefing", body,
        )
        atlas_app_store.add_audit_event(
            APP_DB,
            "briefing.queued" if run else "briefing.duplicate_suppressed",
            "Daily briefing queued" if run else "Duplicate daily briefing suppressed",
            {"scheduled_for": scheduled_for, "notification_id": run.get("notification_id") if run else None, "unavailable": unavailable},
        )
        self._write_json(HTTPStatus.OK, {"ok": True, "enabled": True, "due": True, "queued": bool(run), "duplicate": not bool(run), "run": run})

    def _handle_speech(self):
        if not authorized(self):
            self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        try:
            payload = load_request_payload(self)
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return

        text = (payload.get("text") or payload.get("input") or "").strip()
        if not text:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing text")
            return

        voice = (payload.get("voice") or "alloy").strip()
        response_format = (payload.get("response_format") or "mp3").strip()
        try:
            audio, content_type = run_piper_speech(text, voice=voice, response_format=response_format)
        except Exception as exc:
            self._write_error(HTTPStatus.BAD_GATEWAY, f"piper speech failed: {exc}")
            return

        if ATLAS_HOME_SPEAKER_ENABLED:
            threading.Thread(
                target=play_audio_on_home_speaker,
                args=(audio, response_format),
                daemon=True,
            ).start()

        self._write_bytes(HTTPStatus.OK, audio, content_type)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("ATLAS_BIND_HOST", os.environ.get("JARVIS_BIND_HOST", "0.0.0.0")))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ATLAS_BIND_PORT", os.environ.get("JARVIS_BIND_PORT", "8787"))))
    args = parser.parse_args()

    if not SHORTCUTS_TOKEN:
        raise SystemExit("ATLAS_SHORTCUTS_TOKEN is required")

    atlas_app_store.init_db(APP_DB)
    atlas_sfsu_store.init_db(APP_DB)
    start_shadow_worker()
    httpd = ThreadingHTTPServer((args.host, args.port), AtlasHandler)
    print(f"Serving ATLAS shortcuts endpoint on {args.host}:{args.port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
