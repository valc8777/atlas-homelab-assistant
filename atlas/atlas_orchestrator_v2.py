#!/usr/bin/env python3

"""Typed, non-executing planner primitives for ATLAS Orchestration v2.

This module deliberately has no tool execution code.  In shadow mode it lets a
local model propose a broad handling mode and capabilities, then validates the
proposal against a server-owned registry.  The live router remains authoritative.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib
import re
from dataclasses import asdict, dataclass
from typing import Iterable

import atlas_entity_registry
import atlas_hybrid_retrieval


PLANNER_VERSION = "atlas-orchestrator-v2-shadow-2"

MODES = {"direct", "tool", "retrieve", "action", "clarify", "refuse", "deep"}
FRESHNESS_LEVELS = {"stable", "current", "live"}
RISK_LEVELS = {"low", "sensitive", "high_impact"}
CONTEXT_SCOPES = {"personal", "operations", "admin"}
REASON_CODES = {
    "stable_knowledge",
    "live_information",
    "exact_computation",
    "private_data",
    "supported_action",
    "ambiguous_request",
    "policy_boundary",
    "multi_step_task",
    "unsupported_capability",
}

DISCOVERY_TERMS = {
    "answer.general": ("explain", "why", "teach", "general knowledge", "plain language"),
    "math.calculate": ("calculate", "arithmetic", "percent", "multiply", "times", "divide", "exact", "%"),
    "units.convert": ("convert", "conversion", "measurement units"),
    "datetime.resolve": ("calendar date", "what date", "days after", "days before", "countdown", "time zone"),
    "weather.forecast": (
        "weather", "forecast", "rain", "raining", "temperature", "conditions", "umbrella", "jacket",
        # Environmental-condition vocabulary, kept as a category rather than
        # one observed sentence. These are all facts that require live
        # weather evidence even when the owner never says "weather".
        "fog", "foggy", "snow", "snowing", "snowy", "storm", "stormy", "overcast",
        "cloudy", "sleet", "hail", "precipitation", "air quality",
        # DEF-040: narrow temperature-question phrases, not bare adjectives
        # ("cold"/"hot"/"warm" alone are common in non-weather contexts,
        # e.g. "hot take" -- these three-word phrases are not).
        "how cold", "how hot", "how warm",
    ),
    "public.reference": (
        "reference", "metadata", "isbn", "author", "director", "release year", "capital",
        "currency", "which state", "what state",
        # DEF-040: "population of X" / "who wrote X" are common structured-
        # fact lookups this capability already serves, just missing their
        # own trigger words.
        "population", "wrote", "written by",
        # DEF-043: found live -- "director" (noun) didn't match "who
        # directed X" (verb form), and neither "wrote"/"written by" matched
        # "when was X published". A real two-part question ("who directed
        # dune part two" + "what year was the first dune novel published")
        # discovered zero mandatory capabilities on either clause, so the
        # compound-fallback path never triggered and the legacy classifier's
        # single-domain "book" route silently answered only the book half.
        "directed", "published",
    ),
    "public.search": (
        "current public", "current source", "live public", "up-to-date public", "public evidence",
        "latest", "today", "news", "price", "research", "best practices",
    ),
    "homelab.knowledge": (
        "homelab", "proxmox", "docker", "open webui", "server", "vm id", "gpu handoff",
        "private architecture", "jellyfin", "windows gaming guest", "documented setup",
    ),
    "memory.recall": ("recall", "remember", "earlier", "previous", "did i say", "did i choose", "did i mention"),
    "memory.correct": ("that is wrong", "that's wrong", "correct my memory", "update my memory"),
    "reminders.manage": ("remind me", "reminder"),
    "tasks.manage": ("add a task", "create a task", "task list"),
    "shopping.manage": ("shopping list", "add to my shopping"),
    "notes.manage": ("save a note", "create a note", "private note"),
    "home.read": ("home assistant", "entity state", "sensor state"),
    "nextcloud.read": ("nextcloud", "calendar collection", "address book"),
    "school.calendar_basic": ("school calendar", "class calendar"),
    "school.mail_basic": ("school mail metadata", "school email metadata"),
    "admin.request_confirmation": ("pending confirmation", "request confirmation", "before disabling security"),
}

DISCOVERY_DESCRIPTION_STOPWORDS = {
    "approved", "create", "current", "exact", "owner", "private", "public",
    "read", "supported", "update", "with", "without",
}
DISCOVERY_TOKEN_STOPWORDS = {"a", "an", "and", "for", "in", "me", "my", "of", "on", "or", "the", "to"}


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    kind: str
    scopes: tuple[str, ...]
    read_only: bool
    approval_required: bool
    status: str
    freshness: str
    requires_any_flag: tuple[str, ...] = ()


# This is capability metadata, not a phrase-to-answer table. A proposal may use
# only these names and still cannot execute them in shadow mode.
CAPABILITIES: tuple[Capability, ...] = (
    Capability("answer.general", "Answer from stable model knowledge", "direct", ("personal", "operations", "admin"), True, False, "deployed", "stable"),
    Capability("math.calculate", "Perform exact arithmetic", "tool", ("personal", "operations", "admin"), True, False, "live_verified", "stable"),
    Capability("units.convert", "Convert supported units", "tool", ("personal", "operations", "admin"), True, False, "live_verified", "stable"),
    Capability("datetime.resolve", "Resolve dates, times, and countdowns", "tool", ("personal", "operations", "admin"), True, False, "live_verified", "current"),
    Capability("weather.forecast", "Read current weather and forecasts", "retrieve", ("personal", "operations", "admin"), True, False, "live_verified", "live"),
    Capability("public.reference", "Look up public structured reference data", "retrieve", ("personal", "operations", "admin"), True, False, "deployed", "current"),
    Capability("public.search", "Search public web indexes for current evidence", "retrieve", ("personal", "operations", "admin"), True, False, "deployed", "live"),
    Capability("homelab.knowledge", "Retrieve owner homelab documentation", "retrieve", ("operations", "admin"), True, False, "live_verified", "current"),
    Capability("memory.recall", "Read conversation-local or approved ATLAS memory", "retrieve", ("personal", "operations", "admin"), True, False, "deployed", "current"),
    Capability("memory.correct", "Correct an owner-approved personal memory fact after confirmation", "action", ("personal",), False, False, "deployed", "current"),
    Capability("reminders.manage", "Create or update an owner reminder", "action", ("personal", "operations"), False, False, "live_verified", "current"),
    Capability("tasks.manage", "Create or update an owner task", "action", ("personal", "operations"), False, False, "live_verified", "current"),
    Capability("shopping.manage", "Create or update an owner shopping item", "action", ("personal",), False, False, "live_verified", "current"),
    Capability("notes.manage", "Create or update an owner note", "action", ("personal", "operations"), False, False, "live_verified", "current"),
    Capability("home.read", "Read an allowlisted Home Assistant entity", "retrieve", ("personal",), True, False, "deployed", "live", ("ATLAS_HOME_ASSISTANT_ENABLED",)),
    # Scoped control exception: exactly one light entity, on/off only, no
    # confirmation step (owner-approved, see ATLAS_HOME_ASSISTANT_READONLY.md
    # "Scoped Control Exception" and its own DEC-* entry). Registered as
    # mode="tool" like math.calculate/units.convert/datetime.resolve --
    # deliberately NOT "action", since the action-authority confirmation
    # queue (_ACTION_ALLOWLIST in atlas_shortcuts_server.py) is specifically
    # for owner-approved writes, and this capability is meant to execute
    # instantly instead. approval_required=True here matches its tool-mode
    # siblings' convention; validate_shadow_decision() only reads that field
    # when mode=="action", so it's inert for this capability either way.
    Capability("home.light.manage", "Turn the one approved light entity on or off", "tool", ("personal",), True, False, "planned", "current", ("ATLAS_HOME_ASSISTANT_CONTROL_ENABLED",)),
    Capability("nextcloud.read", "Read owner-approved Nextcloud resources", "retrieve", ("personal",), True, False, "deployed", "current", ("ATLAS_NEXTCLOUD_ENABLED",)),
    Capability("school.calendar_basic", "Read delegated basic school calendar metadata", "retrieve", ("personal",), True, False, "deployed", "current", ("ATLAS_SFSU_ENABLED", "ATLAS_SCHOOL_BRIDGE_ENABLED")),
    Capability("school.mail_basic", "Read delegated basic school mail metadata", "retrieve", ("personal",), True, False, "deployed", "current", ("ATLAS_SFSU_ENABLED",)),
    Capability("admin.request_confirmation", "Record a pending confirmation for a high-impact admin action", "action", ("admin",), False, True, "live_verified", "live"),
    # PWA-panel-only capability (DEC-2026-09-10-04): created directly by the
    # "Media Request > Comics" panel's Send action, never via chat/model
    # classification -- deliberately absent from request_fuzzy_action_
    # confirmation()'s capability handling in atlas_shortcuts_server.py, so
    # even if a future planner change ever proposed this name from free
    # text, that path's fail-closed else-branch refuses it rather than
    # creating a confirmation. approval_required=True here is accurate
    # (unlike home.light.manage's tool-mode carve-out): this is a real
    # action-mode write with a materially larger blast radius than the
    # existing reversible-action set, and it goes through the standard
    # confirm-then-poll-execute path, not an instant-execute exception.
    Capability("media.comic_request", "Trigger the comic-bot browser automation with an owner-typed value", "action", ("personal",), False, True, "planned", "current", ("ATLAS_COMIC_BOT_ENABLED",)),
)


def _flag_enabled(name: str) -> bool:
    return os.environ.get(name, "false").lower() in {"1", "true", "yes"}


def capability_registry(scope: str) -> tuple[Capability, ...]:
    """Return only capabilities the server allows for the selected scope."""
    if scope not in CONTEXT_SCOPES:
        raise ValueError(f"unsupported context scope: {scope}")
    return tuple(
        item
        for item in CAPABILITIES
        if scope in item.scopes
        and (not item.requires_any_flag or any(_flag_enabled(flag) for flag in item.requires_any_flag))
    )


def shortlist_capabilities(
    question: str,
    scope: str,
    conversation: list[dict] | None = None,
    limit: int = 4,
) -> tuple[Capability, ...]:
    """Discover a small capability set from registry metadata, never answer content."""
    history = " ".join(
        str(item.get("text") or item.get("content") or "")
        for item in (conversation or [])[-4:]
        if item.get("role") in {"user", "assistant"}
    )
    text = " ".join(f"{question} {history}".lower().split())
    tokens = set(re.findall(r"[a-z0-9]+", text))
    scored = []
    for capability in capability_registry(scope):
        score = 0
        for term in DISCOVERY_TERMS.get(capability.name, ()):
            normalized_term = term.lower()
            if normalized_term in text:
                score += 5 if " " in normalized_term else 3
            else:
                term_tokens = set(re.findall(r"[a-z0-9]+", normalized_term)) - DISCOVERY_TOKEN_STOPWORDS
                score += len(tokens & term_tokens)
        description_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", capability.description.lower())
            if len(token) >= 5 and token not in DISCOVERY_DESCRIPTION_STOPWORDS
        }
        score += len(tokens & description_tokens)
        if _semantic_discovery_match(text, capability.name, scope):
            score += 5
        # One shared generic word (for example, "current" or "private") is
        # too weak to expose a capability to the planner.
        if score >= 2:
            scored.append((score, capability.name, capability))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(item[2] for item in scored[:max(0, limit)])


# H3: capabilities whose evidence is exact, current, or private. Bypassing
# one of these with answer_directly risks a stale, fabricated, or ungrounded
# answer rather than the model's own stable knowledge being genuinely
# sufficient. Deliberately excludes answer.general itself and every write-
# capable capability (those already go through the separate confirmation/
# action authority gate, untouched by this read-only coverage check).
MANDATORY_TOOL_CAPABILITIES = frozenset({
    "math.calculate", "units.convert", "datetime.resolve",
    "weather.forecast", "public.reference", "public.search",
    "homelab.knowledge", "memory.recall",
})

_DISCOVERY_CLAUSE_SPLIT_RE = re.compile(r"\b(?:and|then|also)\b|[,;]|(?<=[.?!])\s+")

_HOMELAB_DOCUMENTATION_CUE_RE = re.compile(
    r"\b(?:document(?:ed|ation)?|configur(?:e|ed|ation)|deploy(?:ed|ment)?|"
    r"architecture|topology|host(?:ed|ing)?|where\s+.+\s+runs?)\b"
)
_HOMELAB_COMPONENT_CUE_RE = re.compile(
    r"\b(?:service|daemon|worker|scheduler|store|stack|container|host|vm|component|"
    r"runtime|reader|adapter|bridge|database|broker)\b"
)
_WEATHER_OUTDOOR_CUE_RE = re.compile(r"\b(?:outside|outdoors)\b")
_WEATHER_SUITABILITY_CUE_RE = re.compile(
    r"\b(?:comfortable|pleasant|safe|suitable|feel|feels|like|wear|walk|hike|"
    r"picnic|today|tonight|tomorrow|morning|afternoon|evening)\b"
)


_ENTITY_REGISTRY_DISCOVERY_CACHE: list[dict] | None = None


def _load_entity_registry_for_discovery() -> list[dict]:
    """Same registry, same env-gated rollout run-atlas-readonly.py's
    is_homelab_entity_mention()/enhance_homelab_query_with_entities() use --
    loaded independently here because this module has no CLI-argument path
    to receive it, matching how it already loads no other external state.
    Returns [] (never raises) on any missing/invalid/disabled condition, so
    a bad registry can never affect capability discovery."""
    global _ENTITY_REGISTRY_DISCOVERY_CACHE
    if _ENTITY_REGISTRY_DISCOVERY_CACHE is None:
        enabled = os.environ.get("ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED", "false").lower() in {"1", "true", "yes"}
        if not enabled:
            _ENTITY_REGISTRY_DISCOVERY_CACHE = []
        else:
            path = pathlib.Path(os.environ.get("ATLAS_ENTITY_REGISTRY_PATH", "/app/atlas-entity-registry.json"))
            try:
                _ENTITY_REGISTRY_DISCOVERY_CACHE = (
                    atlas_entity_registry.load_registry_file(path) if path.exists() else []
                )
            except (OSError, ValueError):
                _ENTITY_REGISTRY_DISCOVERY_CACHE = []
    return _ENTITY_REGISTRY_DISCOVERY_CACHE


def _semantic_discovery_match(text: str, capability_name: str, scope: str) -> bool:
    """Recognize bounded intent families that single exact terms miss.

    This remains discovery-only: it exposes a capability schema and, for the
    mandatory-tool gate, prevents unsupported direct answers. It never
    executes a tool or decides the final route.
    """
    lowered = text.lower()
    if capability_name == "weather.forecast":
        return bool(
            _WEATHER_OUTDOOR_CUE_RE.search(lowered)
            and _WEATHER_SUITABILITY_CUE_RE.search(lowered)
        )
    if capability_name == "homelab.knowledge" and scope in {"operations", "admin"}:
        if _HOMELAB_DOCUMENTATION_CUE_RE.search(lowered) and _HOMELAB_COMPONENT_CUE_RE.search(lowered):
            return True
        # A bare nickname the R2 entity registry actually knows about
        # ("qbit", "docker heavy", "HAOS") is exactly as strong a
        # homelab-domain signal as the two cue-regexes above, and unlike
        # them, doesn't require the question to also use a generic
        # documentation/component word. Confirmed live: without this,
        # "what's on docker heavy" and "is pve up" never offered
        # homelab.knowledge to the planner at all, so the model could not
        # have picked it regardless of its own judgment.
        registry = _load_entity_registry_for_discovery()
        if registry:
            candidates, _notices = atlas_hybrid_retrieval.resolve_query_entities(text, scope, registry)
            if any(candidate["lifecycle"] == "active" for candidate in candidates):
                return True
    return False


def split_discovery_clauses(question: str, max_clauses: int = 4) -> list[str]:
    """Deterministically split a question into 2-4 candidate clauses.

    This is a discovery-only split for the coverage check below -- it does
    not itself decide the plan. The model still chooses and orders the
    final steps; the server still validates every one of them.
    """
    parts = [part.strip() for part in _DISCOVERY_CLAUSE_SPLIT_RE.split(question) if part.strip()]
    if len(parts) <= 1:
        stripped = question.strip()
        return [stripped] if stripped else []
    if len(parts) > max_clauses:
        parts = parts[: max_clauses - 1] + [" ".join(parts[max_clauses - 1:])]
    return parts


# DEF-037: a later sentence that opens with a pronoun referring back to an
# earlier clause's subject ("What is the largest landmass? How much larger
# is IT than the smallest?") is a single linked comparison, not a second
# independent topic -- split_discovery_clauses() only knows about sentence
# boundaries, not reference dependence, so callers deciding "is this
# genuinely a multi-topic compound question" need this extra check too.
_DEPENDENT_CLAUSE_PRONOUN_RE = re.compile(
    r"^\s*(?:how|what|why|when|where)\s+(?:\w+\s+){0,3}(?:is|are|was|were|does|did|do)\s+"
    r"(?:it|that|this|they|those|he|she)\b",
    re.IGNORECASE,
)


def clauses_are_independent_topics(clauses: list[str]) -> bool:
    """True only when every clause after the first introduces its own
    subject rather than referring back to an earlier one -- the signal
    that a "2+ clauses" split is a genuinely independent multi-topic
    question, not one reasoning question spread across two sentences.
    """
    return len(clauses) >= 2 and not any(
        _DEPENDENT_CLAUSE_PRONOUN_RE.search(clause) for clause in clauses[1:]
    )


def _clause_mentions_mandatory_capability(clause: str, capability_name: str, scope: str) -> bool:
    """Strict, exact-term detector for the mandatory-tool gate below.

    Deliberately does not reuse shortlist_capabilities()'s generous
    description/token-fuzzy scoring: that scoring is tuned to expose a tool
    *definition* to the planner cheaply, where a false positive just means
    an unused schema. A false positive here causes an actual rejection and
    repair turn, so this only fires on a real DISCOVERY_TERMS phrase or
    whole-word match -- found necessary after `discover_required_capabilities`
    initially misfired on "What is 17 times 23": the generous scorer counted
    incidental token overlap between the clause's "times" and
    datetime.resolve's own description ("...dates, times, and countdowns...")
    plus "what" overlapping datetime.resolve's "what date" term, clearing the
    fuzzy threshold for a capability with no real date sub-intent at all.
    """
    lowered = clause.lower()
    if _semantic_discovery_match(lowered, capability_name, scope):
        return True
    for term in DISCOVERY_TERMS.get(capability_name, ()):
        if " " in term:
            if term in lowered:
                return True
        elif re.search(rf"\b{re.escape(term)}\b", lowered):
            return True
    return False


def discover_required_capabilities(
    question: str, scope: str, conversation: list[dict] | None = None
) -> frozenset[str]:
    """Union of mandatory-tool capabilities discovered across each clause.

    A bounded backstop for the coverage check below, not a constraint on
    what the model is shown -- shortlist_capabilities() run once against the
    whole question remains what's sent to the planner as tool definitions.
    """
    allowed_names = {item.name for item in capability_registry(scope)} & MANDATORY_TOOL_CAPABILITIES
    required: set[str] = set()
    for clause in split_discovery_clauses(question):
        for name in allowed_names:
            if _clause_mentions_mandatory_capability(clause, name, scope):
                required.add(name)
    return frozenset(required)


def check_mandatory_tool_coverage(
    question: str, decision: dict, scope: str, conversation: list[dict] | None = None
) -> str | None:
    """H3: return a rejection reason if `decision` bypasses or omits a
    clause-discovered mandatory-tool signal, else None.

    Two failure shapes, matching the plan's own vocabulary:
    - "mandatory_tool_bypass": mode is "direct" (answer_directly) while at
      least one clause discovered an exact/current/private capability.
    - "incomplete_plan": the decision's own chosen capabilities are missing
      one or more clause-discovered mandatory capabilities entirely -- a
      compound request that silently dropped an eligible intent.

    Deliberately not wired into the live planner call path yet -- see
    DEC-2026-09-11-09. Callers that do wire this in should raise
    ValueError(reason) exactly as plan_from_tool_calls() already does on
    rejection, so the existing bounded repair-turn handling picks it up
    without new branching.
    """
    required = discover_required_capabilities(question, scope, conversation)
    if not required:
        return None
    if decision.get("mode") == "direct":
        return "mandatory_tool_bypass"
    chosen = set(decision.get("capabilities") or [])
    if required - chosen:
        return "incomplete_plan"
    return None


def shortlist_capabilities_for_compound(
    question: str, scope: str, conversation: list[dict] | None = None, limit: int = 6
) -> tuple[Capability, ...]:
    """Union the whole-question shortlist with each clause's own shortlist.

    Fixes a real gap found running H0's fresh development set through the
    live planner: a compound question's dominant clause can starve a
    second clause's needed capability out of the whole-question shortlist
    entirely. "Find current public guidance about X, and tell me the
    documented role of Y" shortlisted only `public.search` for the whole
    question -- the model was never offered `homelab.knowledge` as a tool
    at all, so it answered both clauses with the one tool it had, and both
    steps came back `unavailable`. This was not a model reasoning failure;
    it was a discovery failure upstream of the model.

    Bounded at `limit` distinct capabilities (registry order breaks ties)
    so a many-clause question does not grow the tool schema unboundedly.
    Only ever adds candidates; never removes or reorders the ones the
    whole-question shortlist already found, and every added capability
    still passes through the same server-owned validation as before.
    """
    seen: dict[str, Capability] = {}
    for item in shortlist_capabilities(question, scope, conversation, limit=limit):
        seen.setdefault(item.name, item)
    for clause in split_discovery_clauses(question):
        for item in shortlist_capabilities(clause, scope, conversation, limit=2):
            seen.setdefault(item.name, item)
    return tuple(list(seen.values())[:limit])


def capability_catalog(
    scope: str,
    capability_names: set[str] | None = None,
) -> list[dict]:
    return [
        asdict(item)
        for item in capability_registry(scope)
        if capability_names is None or item.name in capability_names
    ]


def question_fingerprint(question: str, secret: str | None = None) -> str:
    normalized = " ".join(question.split()).casefold()
    if secret:
        return hmac.new(secret.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _history_text(conversation: Iterable[dict] | None) -> str:
    lines = []
    for item in list(conversation or [])[-4:]:
        role = item.get("role")
        text = " ".join(str(item.get("text") or item.get("content") or "").split())
        if role in {"user", "assistant"} and text:
            lines.append(f"{role}: {text[:800]}")
    return "\n".join(lines) or "None."


# DEF-027 Phase 2 (DEC-2026-09-16-11/-12): "longest game of foosball ever
# played" and similar are a *named*-domain sports/record superlative -- the
# domain is stated, so this isn't actually the unscoped-superlative shape
# deterministic_clarification_plan()'s generic "what is the X-est" gate
# below (and the native planner's own clarify_request tool, independently)
# exist to catch. The model's own free-text reasoning agrees these seem
# ambiguous (DEC-2026-09-16-10) and up to 4 resampling attempts never
# overturns it (DEC-2026-09-16-11) -- both are downstream of this same
# hard gate never letting the question reach a state where "answer
# directly" was ever actually chosen. Two independent sub-patterns cover
# "longest game of X" and "longest X game" word orders; both require a
# captured, non-empty domain so a genuinely unscoped superlative ("what's
# the best show") never matches either one.
_SUPERLATIVE_RECORD_VERBS = r"(?:played|recorded|held|run|raced|contested|happened|occurred|won|set)"
_SUPERLATIVE_RECORD_NOUNS = r"(?:game|match|contest|round|race|streak|record|run|tournament|marathon)"
_SUPERLATIVE_ADJECTIVES = r"(?:longest|shortest|highest|biggest|largest|smallest|most|fastest|slowest)"
_SUPERLATIVE_RECORD_PATTERN_OF = re.compile(
    rf"\b{_SUPERLATIVE_ADJECTIVES}\s+{_SUPERLATIVE_RECORD_NOUNS}\s+of\s+"
    rf"([a-z][a-z\s]{{2,30}}?)\s+(?:ever\s+)?{_SUPERLATIVE_RECORD_VERBS}\b",
    re.IGNORECASE,
)
_SUPERLATIVE_RECORD_PATTERN_ADJ = re.compile(
    rf"\b{_SUPERLATIVE_ADJECTIVES}\s+([a-z][a-z\s]{{2,30}}?)\s+{_SUPERLATIVE_RECORD_NOUNS}\s+"
    rf"(?:ever\s+)?{_SUPERLATIVE_RECORD_VERBS}\b",
    re.IGNORECASE,
)


def is_scoped_superlative_record_question(question: str) -> bool:
    """True for a named-domain sports/record superlative like "longest game
    of foosball ever played" or "fastest marathon ever run" -- scoped
    (the domain is stated), so the "unscoped superlative" contract that
    both deterministic_clarification_plan() and clarify_request's own tool
    description exist to catch does not actually apply here.
    """
    return bool(
        _SUPERLATIVE_RECORD_PATTERN_OF.search(question)
        or _SUPERLATIVE_RECORD_PATTERN_ADJ.search(question)
    )


# DEF-047: found live via Track A3 Gate 1 (DEC-2026-09-16-29). The hard gate
# below matches ANY bare "-est" adjective ("largest", "smallest", "longest",
# ...), but its only exemption (is_scoped_superlative_record_question, just
# above) is narrowly scoped to sports/game records -- it requires a record
# noun (game/match/contest/...) and a record verb (played/recorded/...),
# neither of which appears in "Which is the largest planet in the Solar
# System?" or "What is the largest artery in the human body?". Those got a
# spurious "what's your criterion for 'largest'?" clarification instead of
# an answer, even though `run-atlas-readonly.py`'s own legacy
# deterministic_comparison_clarification() already validates this exact
# "conventional facts" category (organ/bone/planet/ocean superlatives) as
# not needing clarification -- this orchestrator-path gate never had the
# equivalent exemption. Narrow, enumerated noun list, matching the same
# "named domain, not a fuzzy semantic judgment" pattern as the record-noun
# list above: physical/biological/astronomical categories where the
# superlative has one standard, universally-known metric (size/length/
# depth), unlike open-ended nouns ("best show", "most successful team")
# where different metrics genuinely give different answers.
# DEF-047 follow-up (DEC-2026-09-16-30): a fresh Gate 1 cycle found more
# of the same gap with different nouns -- "largest land animal", "tallest
# land animal", "fastest land animal", and "tallest waterfall" all still
# clarified. Widened the noun list to include these physical/biological
# categories. "largest species of shark" needed a separate pattern for the
# "{adjective}est species of {noun}" shape, which the original two
# patterns don't cover (an intervening "species of" separates the
# adjective from the noun). Deliberately still excludes "country" --
# `generate-atlas-mixed-acceptance-suite.py` already documents "largest
# country" as genuinely ambiguous (by area vs. population, which give
# different real answers, unlike "largest planet" where every metric
# agrees), so leaving it unexempted is intentional, not an oversight.
_CONVENTIONAL_SUPERLATIVE_DOMAIN_NOUNS = (
    r"(?:organ|bone|artery|vein|muscle|gland|chamber|planet|moon|star|ocean|sea|"
    r"continent|mountain|river|lake|desert|glacier|volcano|waterfall|land animal|shark)"
)
_CONVENTIONAL_SUPERLATIVE_PATTERN_ADJ_FIRST = re.compile(
    rf"\b(?:which|what)\s+is\s+the\s+[a-z]+est\s+{_CONVENTIONAL_SUPERLATIVE_DOMAIN_NOUNS}\b",
    re.IGNORECASE,
)
_CONVENTIONAL_SUPERLATIVE_PATTERN_NOUN_FIRST = re.compile(
    rf"\b(?:which|what)\s+{_CONVENTIONAL_SUPERLATIVE_DOMAIN_NOUNS}\s+is\s+[a-z]+est\b",
    re.IGNORECASE,
)
_CONVENTIONAL_SUPERLATIVE_PATTERN_SPECIES_OF = re.compile(
    rf"\b(?:which|what)\s+is\s+the\s+[a-z]+est\s+species\s+of\s+{_CONVENTIONAL_SUPERLATIVE_DOMAIN_NOUNS}\b",
    re.IGNORECASE,
)


def is_conventional_superlative_fact_question(question: str) -> bool:
    """True for a well-known physical/biological/astronomical superlative
    like "largest planet" or "longest bone" -- one standard metric, not a
    genuinely open-ended comparison.
    """
    return bool(
        _CONVENTIONAL_SUPERLATIVE_PATTERN_ADJ_FIRST.search(question)
        or _CONVENTIONAL_SUPERLATIVE_PATTERN_NOUN_FIRST.search(question)
        or _CONVENTIONAL_SUPERLATIVE_PATTERN_SPECIES_OF.search(question)
    )


def deterministic_clarification_plan(question: str, scope: str) -> dict | None:
    """Handle generic unscoped superlatives before model planning."""
    lowered = question.lower()
    # DEF-037: a genuinely independent multi-topic compound question (2+
    # real clauses, not one reasoning question spread across two sentences)
    # must not lose an unrelated, perfectly good answer just because ONE
    # clause is ambiguous -- "What is the largest state? What is a famous
    # dish from Cuba?" used to get a bare clarify with no attempt at the
    # Cuba half at all. Computed once and reused by the general superlative
    # check below.
    independent_compound = clauses_are_independent_topics(split_discovery_clauses(question))
    # DEF-024: "first Daytona" is materially ambiguous in ordinary owner
    # phrasing: it can mean the first Daytona 500 race, an event at Daytona
    # International Speedway, or the place itself. Do not let A4/public
    # reference answer by silently picking the famous referent.
    #
    # DEC-2026-09-16-20: tried exempting this in a compound question too
    # (same as the general superlative case below), to stop it blocking
    # unrelated sibling clauses. Reverted after live-testing the exact
    # DEF-024 repro case ("Who won the first Daytona, and what is the
    # weather in Martinez tomorrow?"): exempting it did not route to a
    # clean deep-plan decomposition -- it fell into a single-capability
    # weather shortcut that silently dropped the Daytona clause entirely,
    # with no answer and no acknowledgment at all. That is worse than
    # blocking the whole request, which is the exact failure mode DEF-024
    # was built to prevent. Unlike the general superlative case (proven
    # live on real questions to reach deep-plan cleanly), Daytona's
    # compound behavior is not safe to exempt -- keep it unconditional.
    if re.search(r"\b(?:first|inaugural|original)\s+daytona\b(?!\s*(?:500|race|races|speedway|international\s+speedway))", lowered):
        return {
            "mode": "clarify",
            "freshness": "stable",
            "risk": "low",
            "context_scope": scope,
            "capabilities": [],
            "needs_verification": False,
            "reason_code": "ambiguous_request",
            "confidence": 1.0,
        }
    match = re.search(r"\b(?:what|which)\s+(?:is|are)\s+the\s+(best|most\s+[a-z]+|least\s+[a-z]+|[a-z]+est)\b", lowered)
    if not match:
        return None
    comparison = match.group(1)
    if comparison in {"latest", "newest", "earliest", "oldest"}:
        return None
    relevant = lowered[match.start():]
    if re.search(r"\b(?:by|based on|according to|in terms of|measured by|using|among|within)\b", relevant):
        return None
    if is_scoped_superlative_record_question(question):
        return None
    if is_conventional_superlative_fact_question(question):
        return None
    if independent_compound:
        return None
    return {
        "mode": "clarify",
        "freshness": "stable",
        "risk": "low",
        "context_scope": scope,
        "capabilities": [],
        "needs_verification": False,
        "reason_code": "ambiguous_request",
        "confidence": 1.0,
        # DEF-034: this is a deterministic regex match, not a model call, so
        # there is no model-generated "missing" detail to thread through --
        # but the regex already captured the specific superlative
        # ("largest", "best", "most sold", ...), so naming it here is
        # strictly better than the fully generic template
        # clarification_result_from_plan() falls back to when this key is
        # absent, matching how the historical prompt-scrape planner quoted
        # the specific comparison word.
        "missing": f"the criterion or scope for '{comparison}'",
    }


# A5: found via real conversation telemetry (orchestration_shadow_traces,
# DEC-2026-09-16-23), not intuition. "prior_clarification" originally
# treated ANY "?" in a recent assistant reply as evidence the assistant had
# asked a genuine disambiguating question -- but most real assistant "?"s
# are rhetorical closers ("Would you like help with something else?"), not
# unresolved ambiguity. A real conversation had this exact shape: assistant
# said "...Would you like help with something else?", then the owner sent
# a short, unrelated meta-comment containing "that" ("What if I find that
# phrasing weird?") -- the vague-pronoun check alone made this look like a
# continuation of a disambiguation exchange that was never actually
# happening. Narrowed to require the prior text look like an actual
# disambiguation request, not just end in a question mark.
_GENUINE_CLARIFYING_QUESTION_RE = re.compile(
    r"\bcriterion\b|\bclarif|\bwhich\b.{0,40}\bmean\b|\bwhat specific\b",
    re.IGNORECASE,
)


def _looks_like_clarifying_question(text: str) -> bool:
    return "?" in text and bool(_GENUINE_CLARIFYING_QUESTION_RE.search(text))


def deterministic_context_plan(
    question: str,
    scope: str,
    conversation: list[dict] | None = None,
) -> dict | None:
    """Resolve generic recall intent and incomplete contextual follow-ups."""
    history = list(conversation or [])[-4:]
    if not history:
        return None
    lowered = question.lower()
    recall_intent = re.search(
        r"\b(?:did i (?:say|choose|mention|tell|give)|did we (?:discuss|decide)|"
        r"what (?:name|label|day|project) did i|where did i say|which .{0,24} did i mention)\b",
        lowered,
    )
    if recall_intent and any(item.get("role") == "user" for item in history):
        return {
            "mode": "retrieve",
            "freshness": "current",
            "risk": "sensitive",
            "context_scope": scope,
            "capabilities": ["memory.recall"],
            "needs_verification": True,
            "reason_code": "private_data",
            "confidence": 1.0,
        }

    compact = re.sub(r"^request\s+\d+\s*:\s*", "", lowered).strip()
    vague_reference = re.search(r"\b(?:it|that|them|there|later|the better one)\b", compact)
    prior_clarification = any(
        item.get("role") == "assistant"
        and _looks_like_clarifying_question(str(item.get("text") or item.get("content") or ""))
        for item in history
    )
    if len(compact.split()) <= 10 and vague_reference and prior_clarification:
        return {
            "mode": "clarify",
            "freshness": "stable",
            "risk": "low",
            "context_scope": scope,
            "capabilities": [],
            "needs_verification": False,
            "reason_code": "ambiguous_request",
            "confidence": 1.0,
        }
    return None


def deterministic_safety_plan(safety_result: dict | None, scope: str) -> dict | None:
    """Convert an authoritative pre-route safety result into a non-executing plan."""
    if not safety_result:
        return None
    route = safety_result.get("route")
    if route not in {
        "privacy_boundary",
        "authority_required",
        "verification_required",
        "unsupported_device_action",
        "confirmation_required",
        "boundary",
    }:
        return None
    sensitive_routes = {"privacy_boundary", "boundary"}
    return {
        "mode": "refuse",
        "freshness": "current",
        "risk": "sensitive" if route in sensitive_routes else "high_impact",
        "context_scope": scope,
        "capabilities": [],
        "needs_verification": False,
        "reason_code": "unsupported_capability" if route == "unsupported_device_action" else "policy_boundary",
        "confidence": 1.0,
    }


# DEF-038: ownership, leadership, and price facts about a company genuinely
# change over time -- they are not "stable knowledge" -- but the native
# planner's answer_directly vs. retrieve choice for this class of question
# was provably inconsistent (confirmed live, DEC-2026-09-16-19): "Is Valve
# owned by a different company? Who are the majority shareholders?" got a
# confidently wrong, fabricated answer (an invented Mark Cuban acquisition)
# via answer_directly on one call, and a complete search failure via
# deep-plan on another. A confidently wrong answer is worse than an honest
# failure, so this forces live retrieval deterministically rather than
# leaving it to a choice the model doesn't make reliably.
#
# DEF-052: population *rankings* are the same class of "changes over time,
# don't trust static knowledge" fact. Found live via the overnight stress
# run's actual-answer grading (DEC-2026-09-17-05): "Which country has the
# largest population in the world?" answered "China" from static model
# knowledge -- stale since a well-established 2023 shift to India. Scoped
# narrowly to superlative/ranking phrasing ("most populous", "largest
# population"), not bare "population of X" -- a historical population
# question ("population of Rome in 500 BC") is genuinely stable and must
# not be swept into forced live search just for containing the word.
_TIME_SENSITIVE_FACT_PATTERN = re.compile(
    r"\b(?:who\s+owns|who\s+controls|is\s+[a-z][a-z0-9 .'-]*?\s+owned\s+by|"
    r"who\s+is\s+the\s+(?:ceo|owner|founder|president)\s+of|current\s+ceo\s+of|"
    r"majority\s+shareholders?\s+(?:of|in)|current\s+(?:price|value|stock\s+price)\s+of|"
    r"most\s+populous|(?:largest|biggest|highest)\s+population)\b",
    re.IGNORECASE,
)


def is_time_sensitive_fact_question(question: str) -> bool:
    """Public predicate wrapping `_TIME_SENSITIVE_FACT_PATTERN`, for other
    modules that need the same "does this need live search" signal without
    going through the full orchestrator decision shape.

    DEF-050: found via an overnight unattended stress run -- the exact real
    Valve-ownership repro from DEF-038 still hallucinated (a fabricated
    "Microsoft acquired Valve" claim), because `run-atlas-readonly.py`'s
    OWN, separate `question_needs_live_verification()` heuristic (used by
    `run_general_reference()` whenever the legacy path handles a question,
    e.g. after a planner timeout, or if the native planner isn't consulted
    at all for a given request) was never updated when DEF-038 added this
    pattern -- it only ever changed the orchestrator's own decision, one of
    two independent "needs live search" checks in this codebase. Any
    request answered through the legacy path bypassed DEF-038's protection
    completely. Exposed here so `run-atlas-readonly.py` can reuse the exact
    same pattern (already the established cross-module pattern in this
    file, see `clauses_are_independent_topics`/`is_scoped_superlative_record_question`)
    instead of drifting a second, separately-maintained copy.
    """
    return bool(_TIME_SENSITIVE_FACT_PATTERN.search(question.lower()))


def deterministic_time_sensitive_fact_plan(question: str, scope: str) -> dict | None:
    """Force live public search for ownership/leadership/price/population-
    ranking questions instead of letting the model answer from static
    training data.
    """
    if not is_time_sensitive_fact_question(question):
        return None
    return {
        "mode": "retrieve",
        "freshness": "live",
        "risk": "low",
        "context_scope": scope,
        "capabilities": ["public.search"],
        "needs_verification": True,
        "reason_code": "live_information",
        "confidence": 1.0,
    }


def deterministic_preplan(
    question: str,
    scope: str,
    safety_result: dict | None = None,
    conversation: list[dict] | None = None,
) -> dict | None:
    return (
        deterministic_safety_plan(safety_result, scope)
        or deterministic_context_plan(question, scope, conversation)
        or deterministic_time_sensitive_fact_plan(question, scope)
        or deterministic_clarification_plan(question, scope)
    )


def build_shadow_prompt(
    question: str,
    scope: str,
    conversation: list[dict] | None = None,
    capabilities: tuple[Capability, ...] | None = None,
) -> str:
    """Build a planning-only prompt; it never asks the model for an answer."""
    selected = capabilities if capabilities is not None else shortlist_capabilities(question, scope, conversation)
    catalog = json.dumps(
        capability_catalog(scope, {item.name for item in selected}),
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return (
        "You are the planning stage of a private local assistant. Do not answer the user and do not call tools. "
        "Propose how a later, policy-controlled executor should handle only the current request. The server will reject "
        "unknown capabilities, cross-scope access, and unsafe actions. Choose the smallest sufficient plan. Apply this "
        "mode contract: 'direct' answers stable knowledge without evidence retrieval; 'tool' performs exact computation; "
        "'retrieve' obtains public evidence or approved private data for a single-step request; 'action' performs one "
        "supported state change; 'clarify' asks for a missing entity, target, criterion, scope, or authority that materially "
        "changes the result; 'refuse' stops at a policy or authority boundary; and 'deep' decomposes a request that combines "
        "multiple dependent stages such as research, comparison, dependency analysis, and a phased plan. An unscoped "
        "superlative or 'best' question with multiple ordinary measurements needs 'clarify', not a guessed default.\n\n"
        "Capability rules: 'clarify' and 'refuse' must use an empty capabilities list because they execute nothing. 'direct' "
        "may use [] or ['answer.general']. 'tool', 'retrieve', and 'action' must name the smallest registered capability set. "
        "'deep' may name only the read-only capabilities needed by its stages and cannot include writes.\n\n"
        "Use only exact schema enum strings. Freshness is 'stable' when time does not affect the result, 'current' for "
        "maintained reference or private state, and 'live' for changing conditions, prices, news, schedules, or officeholders. "
        "Risk is 'low' for public reads, exact computation, and reversible owner-local saves; 'sensitive' for approved "
        "private-data reads; and 'high_impact' only for destructive, security, financial, or external effects. Set "
        "needs_verification true for retrieve, action, and deep; set it false for direct, tool, clarify, and refuse. Use "
        "reason codes by purpose: stable_knowledge for direct, exact_computation for tool, live_information or private_data "
        "for retrieve, supported_action for action, ambiguous_request for clarify, policy_boundary or unsupported_capability "
        "for refuse, and multi_step_task for deep.\n\n"
        "Return exactly one JSON object with these keys and no prose: "
        '{"mode":"direct|tool|retrieve|action|clarify|refuse|deep",'
        '"freshness":"stable|current|live","risk":"low|sensitive|high_impact",'
        '"context_scope":"personal|operations|admin","capabilities":["registered.name"],'
        '"needs_verification":true,"reason_code":"allowed_code","confidence":0.0}. '
        "When mode is 'action', also include \"action_text\":\"<the exact content to save, in the owner's own words, "
        "with no additions or inferred details>\" so the proposal can be shown to the owner for confirmation before "
        "anything is written; omit it for every other mode. "
        "Confidence is only a rough observation, never authority. Allowed reason codes: "
        f"{','.join(sorted(REASON_CODES))}.\n\n"
        f"Authoritative active context scope (copy exactly; it is not a model choice): {scope}\n"
        f"Allowed capability registry: {catalog}\n\n"
        f"Recent conversation:\n{_history_text(conversation)}\n\nCurrent request:\n{question}\n"
    )


# --- Native tool-calling planner (Track A2) -------------------------------
#
# The prompt-and-scrape planner above asks the model to emit a JSON object as
# prose and then regex-extracts it. The native path below instead hands the
# scope-filtered capability registry to the inference API as tool definitions
# and consumes the structured tool_calls it returns.
#
# This works because validate_shadow_decision() only ever *reads* mode,
# capabilities, action_text, and confidence -- risk, freshness,
# needs_verification, and reason_code are all derived server-side from
# capability metadata. A tool call therefore carries everything the validator
# needs, and the validator itself is reused unchanged, so the safety and
# policy boundary of DEC-2026-09-01-03 is identical on both paths.

CLARIFY_TOOL = "clarify_request"
REFUSE_TOOL = "refuse_request"
DEEP_TOOL = "plan_multi_stage_request"
DIRECT_TOOL = "answer_directly"
PSEUDO_TOOLS = frozenset({CLARIFY_TOOL, REFUSE_TOOL, DEEP_TOOL, DIRECT_TOOL})


def build_planner_tools(
    scope: str,
    capabilities: tuple[Capability, ...] | None = None,
    question: str = "",
    conversation: list[dict] | None = None,
) -> list[dict]:
    """Emit the shortlisted capability registry as inference-API tool definitions."""
    selected = (
        capabilities
        if capabilities is not None
        else shortlist_capabilities(question, scope, conversation)
    )

    tools: list[dict] = []
    for item in selected:
        properties: dict = {}
        required: list[str] = []
        if item.kind == "action":
            # The owner has to be shown exactly what would be written, so the
            # model must supply it verbatim rather than the server inferring it.
            properties["action_text"] = {
                "type": "string",
                "description": (
                    "The exact content to save, in the owner's own words, with no "
                    "additions and no inferred details."
                ),
            }
            required.append("action_text")

        posture = "writes owner state" if not item.read_only else "read-only"
        tools.append({
            "type": "function",
            "function": {
                "name": item.name,
                "description": f"{item.description}. This capability {posture}.",
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })

    # Answering directly is an explicit choice, not the absence of one.
    # Treating "no tool call" as an implicit direct answer silently converted
    # missed actions into plausible-looking answers -- three `Save a note` /
    # `Add a task` requests on development-D produced no call at all and were
    # scored as mode=direct, meaning the owner's write would simply never have
    # happened. With this tool present, silence is an anomaly the repair turn
    # can catch instead of a default.
    tools.append({
        "type": "function",
        "function": {
            "name": DIRECT_TOOL,
            "description": (
                "Answer from stable general knowledge that does not change over "
                "time and needs no lookup, computation, private data, or action."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    })
    tools.append({
        "type": "function",
        "function": {
            "name": CLARIFY_TOOL,
            # DEF-027: this tool was already narrowly scoped in words, but a
            # repeated-call probe (DEC-2026-09-16-03) found the model choosing
            # it against its own stated scope on plain factual recall, with a
            # per-question failure rate as high as 80% on some real questions.
            # Added explicit negative examples and an explicit tie-breaking
            # rule, since the positive-only description apparently wasn't
            # enough signal on its own.
            "description": (
                "Ask the owner for a missing entity, target, criterion, scope, or "
                "authority that materially changes the result. Use this ONLY for an "
                "unscoped superlative or 'best'/'most' comparison where different, "
                "equally reasonable measurements would give different answers "
                "(example: 'highest player count' with no game named). Do NOT use "
                "this for plain factual, historical, definitional, or general-"
                "knowledge recall, even if the question is short or informally "
                "worded (example: 'what year did the civil war start', 'what "
                "happened in Nanking' -- answer_directly instead). When unsure "
                "whether clarification is really needed, prefer answer_directly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "missing": {
                        "type": "string",
                        "description": "What detail is missing.",
                    },
                },
                "required": [],
            },
        },
    })
    # A multi-stage request is a mode, not a capability, so it needs its own
    # signal. Without this the model can only ever name one capability per
    # turn and every decomposable request collapses to a single retrieve --
    # measured as 10 of 14 plan failures on development-D before it was added.
    # A4 adds an ordered, step-specific decomposition. The server still
    # validates every capability against its own shortlist and registry; the
    # model cannot widen authority by naming a capability here.
    tools.append({
        "type": "function",
        "function": {
            "name": DEEP_TOOL,
            "description": (
                "Handle one message containing two to four distinct read-only intents, "
                "or a request combining dependent research stages. Return one ordered "
                "step per intent with the smallest capable tool and only that intent's "
                "subrequest. Use this whenever the whole message cannot be satisfied "
                "by one capability."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "description": (
                            "Two to four independent read-only steps in execution order. "
                            "Each request must contain only the part of the owner's current "
                            "request that its capability should handle."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "capability": {
                                    "type": "string",
                                    "enum": [
                                        item.name for item in selected
                                        if item.read_only and item.kind != "direct"
                                    ],
                                },
                                "request": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 500,
                                },
                            },
                            "required": ["capability", "request"],
                        },
                    },
                },
                "required": ["steps"],
            },
        },
    })
    tools.append({
        "type": "function",
        "function": {
            "name": REFUSE_TOOL,
            "description": (
                "Stop at a policy or authority boundary: secrets, third-party "
                "private data, purchases, destructive or security-sensitive "
                "actions, or a capability that is not connected."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    })
    return tools


def build_planner_messages(
    question: str,
    scope: str,
    conversation: list[dict] | None = None,
) -> list[dict]:
    """Short planning framing. The tool schema now carries the contract."""
    return [
        {
            "role": "system",
            "content": (
                "You are the planning stage of a private, owner-only local assistant. "
                "Decide how the current request should be handled by calling exactly one "
                "of the provided tools. You must always call exactly one tool. Do not "
                "answer the request yourself and do not explain your choice.\n"
                "When the owner asks you to save, add, create, update, or be reminded of "
                "something, that is an action: call the matching capability and pass the "
                "exact text to store. Do not treat it as a question to answer.\n"
                # The prompt-scrape planner carries this rule in its system prompt and
                # scores better on clarification because of it. Stating it only in the
                # clarify tool's description was measurably weaker: six clarification
                # mis-plans across development suites A and D.
                "An unscoped superlative or 'best' question with several ordinary "
                "measurements needs clarification, not a guessed default.\n"
                # Deliberately narrow. A broader phrasing that also listed a missing
                # "authority" as grounds to clarify caused a real safety regression
                # (policy_acceptance_violations 0 -> 1 on development-A): a missing
                # authority is a refusal boundary, not a question to ask the owner.
                # Never let clarification guidance compete with a refusal.
                "For one intent, choose the single smallest sufficient capability. "
                "For several distinct read-only intents in one message, call "
                "plan_multi_stage_request and preserve their order as separate steps. A later "
                "policy-controlled executor performs the work; the server rejects "
                "unknown capabilities, cross-scope access, and unsafe actions, so never "
                "try to widen your own authority.\n"
                f"The active context scope is '{scope}' and is not your choice."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Recent conversation:\n{_history_text(conversation)}\n\n"
                f"Current request:\n{question}"
            ),
        },
    ]


def _tool_call_arguments(raw) -> dict:
    """Normalize tool-call arguments across API shapes.

    Open WebUI returns `arguments` as a JSON string; Ollama's native endpoint
    returns it as an object. Both are accepted, and anything unparseable
    degrades to {} rather than failing the plan -- arguments are optional for
    every capability except an action's action_text, which the validator
    already treats as best-effort.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def plan_from_tool_calls(
    tool_calls: list[dict] | None,
    scope: str,
    allowed_capability_names: set[str] | None = None,
) -> dict:
    """Turn structured tool calls into a validated plan.

    Reuses validate_shadow_decision() unchanged, so an unparseable or
    out-of-policy proposal fails exactly as it does on the prompt-scrape path.
    """
    registry = {item.name: item for item in capability_registry(scope)}

    named: list[tuple[str, dict]] = []
    for call in tool_calls or []:
        function = (call or {}).get("function") or {}
        name = function.get("name")
        if isinstance(name, str) and name:
            named.append((name, _tool_call_arguments(function.get("arguments"))))

    # Silence is not consent to answer directly. `answer_directly` exists for
    # that, so no call at all means the model failed to make a choice and the
    # caller's repair turn should ask again.
    if not named:
        raise ValueError("no_tool_call")

    first_name, first_args = named[0]
    if first_name == DIRECT_TOOL:
        return validate_shadow_decision(
            {"mode": "direct", "capabilities": []}, scope, allowed_capability_names
        )
    if first_name == CLARIFY_TOOL:
        decision = validate_shadow_decision(
            {"mode": "clarify", "capabilities": []}, scope, allowed_capability_names
        )
        # DEF-034: CLARIFY_TOOL's schema asks the model what detail is
        # missing, but that text was being silently discarded --
        # validate_shadow_decision() only ever returns its own fixed key
        # set, so the caller composing the owner-facing message always fell
        # back to a generic template regardless of what the model actually
        # said was ambiguous. Merge it in as a best-effort, sanitized extra
        # field (mirroring how action_text/confidence are already handled),
        # not a validated part of the decision contract.
        missing = first_args.get("missing")
        if isinstance(missing, str) and missing.strip():
            decision["missing"] = missing.strip()[:200]
        return decision
    if first_name == REFUSE_TOOL:
        return validate_shadow_decision(
            {"mode": "refuse", "capabilities": []}, scope, allowed_capability_names
        )
    if first_name == DEEP_TOOL:
        steps = first_args.get("steps")
        capabilities = [
            step.get("capability")
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("capability"), str)
        ] if isinstance(steps, list) else []
        return validate_shadow_decision(
            {"mode": "deep", "capabilities": capabilities, "steps": steps},
            scope,
            allowed_capability_names,
        )

    capability_calls = [
        (name, args) for name, args in named if name not in PSEUDO_TOOLS
    ]
    if not capability_calls:
        raise ValueError("invalid_capabilities")

    unknown = [name for name, _ in capability_calls if name not in registry]
    if unknown:
        raise ValueError("unknown_or_disallowed_capability")

    # Several read-only, non-direct capabilities in one turn is a multi-stage
    # request. Anything else collapses to the first call, which keeps a mixed
    # or contradictory proposal conservative instead of inventing a plan.
    distinct = list(dict.fromkeys(name for name, _ in capability_calls))
    if len(distinct) > 1 and all(
        registry[name].read_only and registry[name].kind != "direct" for name in distinct
    ):
        payload = {"mode": "deep", "capabilities": distinct}
    else:
        name, args = capability_calls[0]
        payload = {"mode": registry[name].kind, "capabilities": [name]}
        action_text = args.get("action_text")
        if isinstance(action_text, str):
            payload["action_text"] = action_text

    return validate_shadow_decision(payload, scope, allowed_capability_names)


def _extract_json_object(text: str) -> dict | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
    try:
        payload = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def validate_shadow_decision(
    payload: dict,
    scope: str,
    allowed_capability_names: set[str] | None = None,
) -> dict:
    """Validate and normalize an untrusted model proposal."""
    mode = payload.get("mode")
    capabilities = payload.get("capabilities")

    if mode not in MODES:
        raise ValueError("invalid_mode")
    if scope not in CONTEXT_SCOPES:
        raise ValueError("scope_mismatch")
    if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
        raise ValueError("invalid_capabilities")

    allowed = {item.name: item for item in capability_registry(scope)}
    unique_capabilities = list(dict.fromkeys(capabilities))
    if len(unique_capabilities) > 4:
        raise ValueError("too_many_capabilities")
    unknown = [name for name in unique_capabilities if name not in allowed]
    if unknown:
        compatible = [
            item.name
            for item in capability_registry(scope)
            if allowed_capability_names is not None
            and item.name in allowed_capability_names
            and item.kind == mode
        ]
        if len(unknown) == len(unique_capabilities) and len(compatible) == 1:
            unique_capabilities = compatible
        else:
            raise ValueError("unknown_or_disallowed_capability")
    if allowed_capability_names is not None and any(name not in allowed_capability_names for name in unique_capabilities):
        raise ValueError("capability_not_shortlisted")

    # Repair only an omitted, unambiguous capability. The shortlist is already
    # scope- and feature-filtered; multiple compatible options remain invalid.
    if not unique_capabilities and mode in {"tool", "retrieve", "action"} and allowed_capability_names is not None:
        compatible = [
            item.name
            for item in capability_registry(scope)
            if item.name in allowed_capability_names and item.kind == mode
        ]
        if len(compatible) == 1:
            unique_capabilities = compatible

    # Capability kind is authoritative execution metadata. Normalize a model's
    # inconsistent mode instead of rejecting an otherwise safe plan.
    capability_kinds = {allowed[name].kind for name in unique_capabilities}
    if mode == "deep" and allowed_capability_names is not None:
        # Legacy deep proposals carry no steps and intentionally expand to
        # the complete safe shortlist. A4 step-bearing proposals preserve
        # only their explicit ordered capability selection.
        if payload.get("steps") is None:
            unique_capabilities = [
                item.name
                for item in capability_registry(scope)
                if item.name in allowed_capability_names and item.read_only and item.kind != "direct"
            ]
        capability_kinds = {allowed[name].kind for name in unique_capabilities}
    elif len(capability_kinds) == 1 and mode not in {"deep", "clarify", "refuse"}:
        mode = next(iter(capability_kinds))
    elif mode == "action" and not unique_capabilities:
        mode = "refuse"
    if mode in {"clarify", "refuse"} and unique_capabilities:
        raise ValueError("capability_not_allowed_for_mode")
    if mode == "direct" and any(allowed[name].kind != "direct" for name in unique_capabilities):
        raise ValueError("direct_mode_mismatch")
    if mode == "tool" and any(allowed[name].kind != "tool" for name in unique_capabilities):
        raise ValueError("tool_mode_mismatch")
    if mode == "retrieve" and any(allowed[name].kind not in {"retrieve"} for name in unique_capabilities):
        raise ValueError("retrieve_mode_mismatch")
    if mode == "action" and (not unique_capabilities or any(allowed[name].kind != "action" for name in unique_capabilities)):
        raise ValueError("action_mode_mismatch")
    if mode in {"tool", "retrieve"} and not unique_capabilities:
        raise ValueError("missing_capability")
    if mode != "action" and any(not allowed[name].read_only for name in unique_capabilities):
        raise ValueError("write_capability_requires_action")
    private_reads = {
        "homelab.knowledge", "memory.recall", "home.read", "nextcloud.read",
        "school.calendar_basic", "school.mail_basic",
    }
    if mode == "action" and any(allowed[name].approval_required for name in unique_capabilities):
        risk = "high_impact"
    elif any(name in private_reads for name in unique_capabilities):
        risk = "sensitive"
    else:
        risk = "low"

    freshness_rank = {"stable": 0, "current": 1, "live": 2}
    if unique_capabilities:
        freshness = max(
            (allowed[name].freshness for name in unique_capabilities),
            key=freshness_rank.__getitem__,
        )
    elif mode in {"direct", "tool", "clarify"}:
        freshness = "stable"
    else:
        freshness = "current"

    needs_verification = mode in {"retrieve", "action", "deep"}
    if mode == "retrieve":
        reason_code = "private_data" if risk == "sensitive" else "live_information"
    else:
        reason_code = {
            "direct": "stable_knowledge",
            "tool": "exact_computation",
            "action": "supported_action",
            "clarify": "ambiguous_request",
            "refuse": "policy_boundary",
            "deep": "multi_step_task",
        }[mode]

    confidence = payload.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = None
    elif confidence < 0 or confidence > 1:
        confidence = None
    else:
        confidence = round(float(confidence), 3)

    # Additive, best-effort only: never raises if absent or malformed, and
    # only meaningful when mode == "action" -- existing eval fixtures for
    # every other mode (including admin.request_confirmation) never supply
    # it and must keep scoring exactly as they do today.
    raw_action_text = payload.get("action_text")
    action_text = raw_action_text.strip() if mode == "action" and isinstance(raw_action_text, str) else ""

    normalized_steps: list[dict[str, str]] = []
    raw_steps = payload.get("steps")
    if mode == "deep" and raw_steps is not None:
        if not isinstance(raw_steps, list) or not 2 <= len(raw_steps) <= 4:
            raise ValueError("invalid_deep_steps")
        for step in raw_steps:
            if not isinstance(step, dict):
                raise ValueError("invalid_deep_step")
            capability = step.get("capability")
            request = step.get("request")
            if (
                not isinstance(capability, str)
                or capability not in allowed
                or capability not in unique_capabilities
                or not allowed[capability].read_only
                or allowed[capability].kind == "direct"
                or not isinstance(request, str)
                or not request.strip()
                or len(request.strip()) > 500
            ):
                raise ValueError("invalid_deep_step")
            normalized_steps.append({"capability": capability, "request": request.strip()})

    return {
        "mode": mode,
        "freshness": freshness,
        "risk": risk,
        "context_scope": scope,
        "capabilities": unique_capabilities,
        "needs_verification": needs_verification,
        "reason_code": reason_code,
        "confidence": confidence,
        "action_text": action_text,
        "steps": normalized_steps,
    }


def parse_shadow_decision(
    text: str,
    scope: str,
    allowed_capability_names: set[str] | None = None,
) -> dict:
    payload = _extract_json_object(text)
    if payload is None:
        raise ValueError("invalid_json")
    return validate_shadow_decision(payload, scope, allowed_capability_names)
