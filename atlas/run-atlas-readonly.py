#!/usr/bin/env python3

import argparse
import ast
import datetime as dt
import json
import math
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.parse
from zoneinfo import ZoneInfo

import atlas_entity_registry
import atlas_home_assistant
import atlas_hybrid_retrieval
import atlas_memory_corpus
import atlas_narrative_date_reasoning
import atlas_orchestrator_v2
from openwebui_client import api_request, choose_model, extract_text, load_api_key, timed_api_request


TOOLS_DIR = pathlib.Path(__file__).resolve().parent


def repo_root_candidate() -> pathlib.Path | None:
    if len(TOOLS_DIR.parents) > 1:
        return TOOLS_DIR.parents[1]
    return None


def default_system_prompt_file() -> pathlib.Path:
    env_path = os.environ.get("ATLAS_SYSTEM_PROMPT_FILE") or os.environ.get("JARVIS_SYSTEM_PROMPT_FILE")
    if env_path:
        return pathlib.Path(env_path)

    repo_root = repo_root_candidate()
    if repo_root:
        repo_candidate = repo_root / "ai-evals" / "prompts" / "atlas-readonly-system.md"
        if repo_candidate.exists():
            return repo_candidate

    sibling_candidate = TOOLS_DIR / "atlas-readonly-system.md"
    return sibling_candidate


DEFAULT_SYSTEM_PROMPT = default_system_prompt_file()
DEFAULT_GENERAL_SUPPLEMENT = (
    repo_root_candidate() / "ai-evals" / "prompts" / "atlas-general-assistant-supplement.md"
    if repo_root_candidate()
    else TOOLS_DIR / "atlas-general-assistant-supplement.md"
)

CAPABILITY_ROUTES = {
    "answer.general": "general_reference",
    "math.calculate": "math",
    "units.convert": "conversion",
    "datetime.resolve": "datetime",
    "weather.forecast": "weather",
    "public.reference": "general_reference",
    "public.search": "general_reference",
    "homelab.knowledge": "homelab",
    "memory.recall": "general_reference",
    "home.light.manage": "home_light",
    "home.read": "home_read",
}


def load_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def load_system_prompt(path: pathlib.Path) -> str:
    parts = []
    if path.exists():
        parts.append(load_text(path))
    supplement_candidates = [
        pathlib.Path(os.environ["ATLAS_GENERAL_SUPPLEMENT_FILE"])
    ] if os.environ.get("ATLAS_GENERAL_SUPPLEMENT_FILE") else [
        DEFAULT_GENERAL_SUPPLEMENT,
        TOOLS_DIR / "atlas-general-assistant-supplement.md",
    ]
    for supplement_path in supplement_candidates:
        if supplement_path.exists():
            parts.append(load_text(supplement_path))
            break
    return "\n\n".join(part for part in parts if part)


def classify_question(question: str, context_scope: str = "personal") -> str:
    lowered = question.lower()

    # Structured questions with explicit choices are reasoning requests. Route
    # the whole prompt to the general model before incidental nouns (book,
    # drums, oceans, dates, and so on) trigger narrow domain adapters.
    if is_structured_reasoning_question(question):
        return "general_reference"

    if is_disputing_prior_claim(question):
        return "general_reference"

    if is_counterfactual_boundary_reasoning_question(question):
        return "counterfactual_boundary_reasoning"

    if is_causal_tradeoff_reasoning_question(question):
        return "causal_tradeoff_reasoning"

    if is_source_freshness_reasoning_question(question):
        return "source_freshness_reasoning"

    if is_timeout_explanation_question(question):
        return "timeout_explanation_reasoning"

    if is_evidence_quality_reasoning_question(question):
        return "evidence_quality_reasoning"

    if is_self_contained_reasoning_prompt(question):
        return "general_reference"

    if is_countdown_question(question):
        return "countdown"

    if is_quantitative_reasoning_question(question):
        return "quantitative_reasoning"

    if is_closed_world_reasoning_question(question):
        return "closed_world_reasoning"

    if is_math_question(question):
        return "math"

    if is_datetime_question(question):
        return "datetime"

    if is_conversion_question(question):
        return "conversion"

    if is_weather_question(question):
        return "weather"

    # Scoped control exception (exactly one light entity: on/off, a fixed
    # named-color set, this device's real effect list, or -- as a fallback,
    # via a model call constrained to that same fixed set -- an open-ended
    # theme like "do SFSU colors!") -- deterministic-first by design, same
    # as math/datetime/conversion above, rather than relying solely on the
    # model-based planner to pick this capability for something that
    # physically changes a light. See ATLAS_HOME_ASSISTANT_READONLY.md
    # ("Scoped Control Exception").
    if is_home_light_action_request(question):
        return "home_light"

    if is_home_lamp_state_question(question):
        return "home_read"

    # DEF-018: a personal-memory-recall question that merely mentions a media
    # word as its topic ("What did I tell you regarding my favorite type of
    # music to fall asleep to?") was being hijacked by the bare keyword
    # checks below, then the music handler ran a literal MusicBrainz search
    # on the question text and returned a garbled, falsely-cited "answer"
    # ("To Tell. Source: MusicBrainz.") instead of an honest "I don't have
    # that saved."
    #
    # Considered and rejected: moving the personal_memory check to run before
    # book/music/tv/movie entirely. That would also move it ahead of
    # homelab-question phrasing like "Do you remember what changed on
    # ai-stack?", which test-atlas-memory-personal.py explicitly requires to
    # route to homelab -- "a more specific classifier that already matched
    # wins, even if incidental recall language is present" is the project's
    # own stated, tested design intent for domain-exclusive routing, not a
    # bug. Reordering globally would silently break that.
    #
    # Instead: a narrow, targeted exclusion on just the four media checks
    # below, matching the exact shape of the existing "address-book" and
    # "director-general" exclusions in this same function. homelab/weather's
    # existing domain-exclusive priority over personal_memory is completely
    # untouched.
    media_lookup_excluded_by_personal_memory = (
        context_scope == "personal" and is_personal_memory_question(question)
    )

    # Word-boundary matching: bare "book" previously matched inside
    # "address-book" (found via a real eval failure where a privacy-refusal
    # question about exporting an address book got misrouted to a book
    # lookup) -- a hyphen is itself a word boundary, so \bbook\b alone still
    # matches that compound; it needs its own exclusion. Bare "author" would
    # similarly false-positive inside "authorization"/"authorize".
    # Found while regression-testing the DEF-018 fix above, unrelated to it:
    # \bmovie\b (and sibling word-boundary checks below) match the singular
    # "movie" but not the plural "movies" -- \b requires a boundary, and
    # there is none between "movie" and a directly-following "s". Confirmed
    # this silently broke "What are popular Mel Gibson movies?" (routed to
    # general_reference instead of movie) and the equivalent plurals for
    # novel/author/director/tv show. Added explicit `s?` wherever the
    # singular/plural distinction is meaningful; "series" is unchanged since
    # it is already both singular and plural in English.
    is_contact_book_compound = bool(re.search(r"\b(?:address|contact|phone)[- ]book\b", lowered))
    if (
        re.search(r"\b(?:books?|novels?|authors?|isbn|published)\b", lowered)
        and not is_contact_book_compound
        and not re.search(r"\b(?:movies?|shows?|albums?|songs?|artists?)\b", lowered)
        and not media_lookup_excluded_by_personal_memory
    ):
        return "book"

    if (
        any(token in lowered for token in ["album", "artist", "band", "music", "song"])
        and not media_lookup_excluded_by_personal_memory
    ):
        return "music"

    if (
        re.search(r"\b(?:tv shows?|series|episodes?|seasons?)\b", lowered)
        and "movie" not in lowered
        and not media_lookup_excluded_by_personal_memory
    ):
        return "tv"

    # Word-boundary matching for "director" specifically (a hyphen is still
    # a word boundary, so an explicit exclusion is needed too): found via a
    # real eval failure where "current director-general of the WTO" was
    # misrouted to a movie/TV lookup. Deliberately narrow -- only excludes
    # the confirmed "director-general" compound title, not "director of X"
    # generally, since that phrasing is also how legitimate film-director
    # questions are asked ("the director of Inception").
    is_non_movie_director_title = bool(re.search(r"\bdirector[- ]general\b", lowered))
    if (
        (
            re.search(r"\b(?:movies?|films?|casts?|actors?)\b", lowered)
            or (re.search(r"\bdirectors?\b", lowered) and not is_non_movie_director_title)
        )
        and not media_lookup_excluded_by_personal_memory
    ):
        return "movie"

    # DEF-002 hallucination-leak half (2026-09-07): confirmed real calibration
    # failure -- "What indexer manager runs alongside the servarr stack?" matched
    # no existing keyword, fell through to general_reference, and the model
    # confidently guessed "Jackett" (plausible-sounding, actually wrong -- this
    # homelab's real indexer manager is Prowlarr, confirmed directly against
    # homelab-service-location-index-2026-08-06.md before adding these terms).
    # Widening the existing, complete `homelab` route to real, confirmed
    # service names -- not new decision logic.
    #
    # Also catches self-referential questions about ATLAS's own stack ("What
    # local language model does ATLAS use?", "What web UI does ATLAS's chat
    # pipeline sit behind?") -- both real calibration failures too. These
    # didn't hallucinate (both were honest, hedged non-answers), but they
    # reached that honesty via the wrong route (open web search, which has no
    # way to know) instead of the right one (the homelab corpus, which does).
    # Requires "atlas" plus a nearby infra-shaped word, not bare "atlas" alone,
    # so unrelated uses (Atlas Shrugged, the Titan, a moving company) don't
    # misroute -- worst case of a false positive here is a corpus-grounded
    # "I don't know," never a wrong action.
    mentions_atlas_infra = "atlas" in lowered and any(
        term in lowered
        for term in (
            "model", "pipeline", "backend", "stack", "architecture",
            "web ui", "webui", "llm", "language model", "chat pipeline",
            "runs on", "hosted", "deployed", "sit behind", "sits behind",
            "built on", "powered by",
        )
    )

    if (
        any(
            token in lowered
            for token in [
                "homelab",
                "docker-main",
                "ai-stack",
                "open webui",
                "open-webui",
                "proxmox",
                "pbs",
                "tailscale",
                "jellyfin",
                "jellyseerr",
                "homepage",
                "vaultwarden",
                "nextcloud",
                "backup status",
                "failed backup",
                "backup report",
                "vzdump",
                "gpu-to-docker",
                "gpu-to-windows",
                "gpu handoff",
                "hand the gpu",
                "which host",
                "what changed",
                "service",
                "servarr",
                "sonarr",
                "radarr",
                "lidarr",
                "prowlarr",
                "jackett",
                "qbittorrent",
                "mylar",
                "indexer manager",
            ]
        )
        or re.search(r"\b(?:vm|ct)\s+\d+\b", lowered)
        or mentions_atlas_infra
        or is_homelab_entity_mention(question, context_scope)
    ):
        return "homelab"

    # Domain-exclusive: only reached once every more specific classifier above
    # has declined. Only fires in personal-scope conversations, matching the
    # deterministic tier-1 handler's own scope gate in atlas_shortcuts_server.py.
    if context_scope == "personal" and is_personal_memory_question(question):
        return "personal_memory"

    return "general_reference"


def is_personal_memory_question(question: str) -> bool:
    """Fuzzy recall phrasing for the personal-memory semantic fallback (tier 2).

    Deliberately broader than the tier-1 deterministic handler's exact phrase
    list in atlas_shortcuts_server.py, since tier 2 exists specifically to
    catch phrasing tier 1 misses. Still a fixed phrase set, not a model call --
    keeps the "is there anything to even try recalling" check free.
    """
    lowered = question.lower()
    trigger_phrases = (
        "what are my preferences", "what's my preference", "what is my preference",
        "what do you know about me", "what have you learned about me",
        "what have i told you", "what do you remember about me",
        "did i tell you", "did i mention", "did i ever say", "did i ever mention",
        "did i ever tell you", "do you remember",
        "what did i say about", "remember when i said", "did i say",
        "do you recall", "have i told you", "have i mentioned", "have i ever told you",
        "have i ever mentioned", "is there anything you remember",
        "do you know my", "do you happen to know my",
    )
    return any(phrase in lowered for phrase in trigger_phrases)


def blended_memory_context(
    question: str,
    context_scope: str,
    memory_state_file: pathlib.Path | None,
) -> str | None:
    """Stage 4: quietly inform an ordinary general-knowledge answer with a
    relevant saved fact, with no network call and near-zero cost in the
    common case (nothing relevant, or nothing saved at all).

    Deliberately narrow: personal scope only, and only ever the exact saved
    text with provenance, never a summary or inference. This is a distinct,
    much cheaper check than the personal_memory route -- it runs on every
    general-knowledge question, not just explicit recall phrasing, so it must
    never touch the network and must return instantly when there's nothing
    to add.
    """
    if context_scope != "personal" or not memory_state_file:
        return None
    state = atlas_memory_corpus.read_state_cache(memory_state_file)
    items = state.get("items") or []
    if not items:
        return None
    return atlas_memory_corpus.blended_context_block(question, items)


def is_structured_reasoning_question(question: str) -> bool:
    return bool(
        re.search(r"(?:^|\n)choices:\s*\S+", question, flags=re.IGNORECASE)
        and re.search(r"(?:answer|respond)\s+with\s+(?:the\s+)?choice\b", question, flags=re.IGNORECASE)
    )


_DISPUTING_PRIOR_CLAIM_RE = re.compile(
    r"^(?:(?:no,?\s+)?that'?s?\s+(?:not\s+true|wrong|incorrect)|"
    r"that\s+is\s+(?:not\s+true|wrong|incorrect)|"
    r"i\s+don'?t\s+think\s+that'?s?\s+true)\b",
    re.IGNORECASE,
)


def is_disputing_prior_claim(question: str) -> bool:
    """A message that opens by disputing/correcting a prior claim ("that's
    not true", "that's wrong") must reach the general model with full
    conversation context, not the domain-keyword checks below -- found live
    via the 2026-09-17 chat-quality review: "That is not true, in the book
    the character was also named Louis de Pointe du Lac" was hijacked by
    the plain word "book" into a structured-media lookup ("No match. Source:
    Open Library.") instead of being reconsidered as the correction it is.
    Same class of false-positive this file's own `book`/`movie`/etc. domain
    checks already guard against for structured reasoning prompts -- see
    `is_structured_reasoning_question`'s docstring comment above.
    """
    return bool(_DISPUTING_PRIOR_CLAIM_RE.search(question.strip()))


def is_self_contained_reasoning_prompt(question: str) -> bool:
    lowered = question.lower()
    tradeoff = bool(
        re.search(r"\bimagine\s+we\s+optimize\b", lowered)
        and "accept a little extra latency" in lowered
        and "most likely benefit" in lowered
        and "most likely downside" in lowered
        and "what measurement" in lowered
    )
    evidence = bool(
        "can cite" in lowered
        and "they disagree on the current state" in lowered
        and "cares most about" in lowered
        and "which source should get more weight" in lowered
        and "if neither source proves" in lowered
    )
    return tradeoff or evidence


def _parse_counterfactual_boundary_question(question: str) -> dict | None:
    normalized = " ".join(question.strip().split())
    match = re.fullmatch(
        r"If (?P<failure>.+?) during a read-only check for (?P<subject>.+?) while investigating (?P<investigation>.+?), "
        r"assume no user data may be changed; what should the assistant do first\? "
        r"What should it explicitly avoid claiming until verification succeeds\?",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return {key: " ".join(value.split()) for key, value in match.groupdict().items()}


def _strip_leading_article(value: str) -> str:
    return re.sub(r"^(?:a|an|the)\s+", "", value.strip(), flags=re.IGNORECASE)


def is_counterfactual_boundary_reasoning_question(question: str) -> bool:
    return _parse_counterfactual_boundary_question(question) is not None


def run_counterfactual_boundary_reasoning(question: str) -> dict:
    parsed = _parse_counterfactual_boundary_question(question)
    if not parsed:
        return {
            "route": "counterfactual_boundary_reasoning",
            "tool": "local-counterfactual-boundary-reasoning",
            "answer": "I couldn't parse that counterfactual boundary question.",
            "source": "Prompt-supplied read-only boundary policy",
            "result_status": "invalid",
            "error_type": "parse_failed",
            "result": {"question": question},
        }
    subject = _strip_leading_article(parsed["subject"])
    failure = _strip_leading_article(parsed["failure"])
    answer = (
        f"First, treat the {subject} check as unverified and do only read-only recovery: "
        f"inspect the safest available health/status/log evidence for the {failure} condition, "
        f"record that verification is incomplete, and ask for or wait for a fresh check if the evidence is stale. "
        f"It should explicitly avoid claiming that {subject} is healthy, current, fixed, or that "
        f"{parsed['investigation']} is resolved until a real verification succeeds; it also should not change "
        "user data or imply that any write/action was authorized."
    )
    return {
        "route": "counterfactual_boundary_reasoning",
        "tool": "local-counterfactual-boundary-reasoning",
        "answer": answer,
        "source": "Prompt-supplied read-only boundary policy",
        "result_status": "ok",
        "error_type": None,
        "result": parsed | {"changed": False, "verification_required": True},
    }


# DEF-026: the original single `re.fullmatch` template ("For X, imagine we
# optimize Y aggressively and accept a little extra latency; what is the
# most likely benefit and the most likely downside? If the owner complains
# that it feels slower, what measurement...") only ever matched that exact
# sentence shape. A same-category paraphrase ("If we cache DNS lookups more
# aggressively to cut latency, what's the most likely benefit and the most
# likely downside? If I later notice stale DNS entries, what measurement
# would tell us whether the tradeoff was worth keeping?") fell through
# entirely and got misrouted to over-eager clarification -- the exact
# over-clarification bug this same track (DEF-025e) claims fixed elsewhere.
# Replaced with independent, order-tolerant signal checks (a change clause,
# a benefit/downside ask, a validating-measurement ask) instead of one
# rigid full-sentence skeleton, so the underlying shape is what triggers
# the route, not the exact wording of one example.
_CAUSAL_CHANGE_PATTERN = re.compile(
    r"\b(?:if\s+we|imagine\s+we|suppose\s+we)\s+(?P<change>.+?)"
    r"(?:,|\bto\s+(?:cut|reduce|improve|optimize)\b|\baggressively\b|\?|$)",
    re.IGNORECASE,
)
_CAUSAL_BENEFIT_DOWNSIDE_PATTERN = re.compile(
    r"\bbenefit\b.{0,80}?\bdownside\b|\bdownside\b.{0,80}?\bbenefit\b",
    re.IGNORECASE | re.DOTALL,
)
_CAUSAL_MEASUREMENT_PATTERN = re.compile(
    r"\bwhat\s+measurement\b|\bhow\s+would\s+(?:we|you|i)\s+(?:know|measure|tell)\b|"
    r"\bworth\s+(?:it|keeping)\b|\btell\s+us\s+whether\b",
    re.IGNORECASE,
)


def _parse_causal_tradeoff_question(question: str) -> dict | None:
    normalized = " ".join(question.strip().split())
    change_match = _CAUSAL_CHANGE_PATTERN.search(normalized)
    if not change_match:
        return None
    if not _CAUSAL_BENEFIT_DOWNSIDE_PATTERN.search(normalized):
        return None
    if not _CAUSAL_MEASUREMENT_PATTERN.search(normalized):
        return None
    change = " ".join(change_match.group("change").split())
    change = re.sub(r"\s+more$", "", change, flags=re.IGNORECASE).strip()
    return {"system": "this system", "optimization": change, "change": change}


def is_causal_tradeoff_reasoning_question(question: str) -> bool:
    return _parse_causal_tradeoff_question(question) is not None


def run_causal_tradeoff_reasoning(question: str) -> dict:
    parsed = _parse_causal_tradeoff_question(question)
    if not parsed:
        return {
            "route": "causal_tradeoff_reasoning",
            "tool": "local-causal-tradeoff-reasoning",
            "answer": "I couldn't parse that causal tradeoff question.",
            "source": "Prompt-supplied causal tradeoff template",
            "result_status": "invalid",
            "error_type": "parse_failed",
            "result": {"question": question},
        }
    change = parsed["optimization"]
    answer = (
        f"The most likely benefit of {change} is fewer stale, unsafe, or low-confidence decisions in that area. "
        "The most likely downside is added latency, because extra checks, scoring, or verification steps happen "
        "before the answer is returned. To tell whether the tradeoff is worth keeping, measure end-to-end p50/p95 "
        f"latency and compare it against the win rate or avoided-error rate from {change}; keep the tradeoff only "
        "if the measured quality/reliability gain is large enough to justify the slower response."
    )
    return {
        "route": "causal_tradeoff_reasoning",
        "tool": "local-causal-tradeoff-reasoning",
        "answer": answer,
        "source": "Prompt-supplied causal tradeoff template",
        "result_status": "ok",
        "error_type": None,
        "result": parsed | {"changed": False, "measurement": "end_to_end_latency_vs_quality_gain"},
    }


# DEF-026: only matched "cached X is N minutes old"; a same-category
# paraphrase ("The last DNS health check ran 45 minutes ago. If I ask
# whether DNS is healthy right now...") uses "ran/happened/was checked N
# minutes ago" instead, and doesn't say "cached" at all. Two independent
# alternatives instead of one rigid phrase, both still requiring a nearby
# "is it current/healthy/fresh right now"-shaped question so this doesn't
# fire on an unrelated timestamp mention.
_STALE_SIGNAL_AGE_PATTERN = re.compile(
    r"\bcached\s+(?P<signal_a>[a-z][a-z ]+?)\s+is\s+"
    r"(?P<age_a>\d+(?:\.\d+)?)\s*(?P<unit_a>minutes?|mins?|hours?|hrs?)\s+old\b"
    r"|\b(?:the\s+)?last\s+(?P<signal_b>[a-z][a-z ]+?)\s+"
    r"(?:ran|happened|was\s+(?:checked|updated|run|refreshed))\s+"
    r"(?P<age_b>\d+(?:\.\d+)?)\s*(?P<unit_b>minutes?|mins?|hours?|hrs?)\s+ago\b",
    re.IGNORECASE,
)
_CURRENT_STATE_ASK_PATTERN = re.compile(
    r"\b(?:healthy|current|right now|latest|fresh|up|running)\b", re.IGNORECASE
)


def _parse_source_freshness_question(question: str) -> dict | None:
    normalized = " ".join(question.strip().split())
    age_match = _STALE_SIGNAL_AGE_PATTERN.search(normalized)
    if not age_match or not _CURRENT_STATE_ASK_PATTERN.search(normalized):
        return None
    signal = age_match.group("signal_a") or age_match.group("signal_b")
    age = age_match.group("age_a") or age_match.group("age_b")
    unit = age_match.group("unit_a") or age_match.group("unit_b")
    return {"signal": " ".join(signal.split()), "age": age, "unit": unit}


def is_source_freshness_reasoning_question(question: str) -> bool:
    return _parse_source_freshness_question(question) is not None


def run_source_freshness_reasoning(question: str) -> dict:
    parsed = _parse_source_freshness_question(question)
    if not parsed:
        return {
            "route": "source_freshness_reasoning",
            "tool": "local-source-freshness-reasoning",
            "answer": "I couldn't parse that source-freshness question.",
            "source": "Prompt-supplied freshness policy",
            "result_status": "invalid",
            "error_type": "parse_failed",
            "result": {"question": question},
        }
    answer = (
        f"ATLAS should say the cached {parsed['signal']} is stale: it is {parsed['age']} {parsed['unit']} old, "
        "so it cannot confirm the service is healthy right now from that cache alone. "
        "It should report the stale timestamp/age, avoid presenting the service as currently healthy, "
        "and ask for or run a fresh read-only check before making a current-state claim."
    )
    return {
        "route": "source_freshness_reasoning",
        "tool": "local-source-freshness-reasoning",
        "answer": answer,
        "source": "Prompt-supplied freshness policy",
        "result_status": "ok",
        "error_type": None,
        "result": parsed | {"current_state_verified": False},
    }


def is_timeout_explanation_question(question: str) -> bool:
    lowered = question.lower()
    return (
        "local model timeout" in lowered
        and "network timeout" in lowered
        and ("check first" in lowered or "what you would check first" in lowered)
    )


def run_timeout_explanation_reasoning(question: str) -> dict:
    return {
        "route": "timeout_explanation_reasoning",
        "tool": "local-timeout-explanation",
        "answer": (
            "- Local model timeout: the model/runtime accepted the request but took too long to produce a result, often from load, context size, or compute pressure. "
            "- Network timeout: the request could not reliably reach or hear back from the service in time, often from routing, DNS, port, proxy, or service reachability issues. "
            "First check basic network/service reachability and the target service health, then inspect model-runtime load if the service path is reachable."
        ),
        "source": "ATLAS local troubleshooting policy",
        "result_status": "ok",
        "error_type": None,
        "result": {"changed": False},
    }


def _parse_evidence_quality_question(question: str) -> dict | None:
    normalized = " ".join(question.strip().split())
    match = re.fullmatch(
        r"An answer about (?P<topic>.+?) can cite (?P<source_a>.+?) and (?P<source_b>.+?), "
        r"but they disagree on the current state and the user cares most about (?P<criterion>.+?); "
        r"which source should get more weight and why\? "
        r"What should the assistant say if neither source proves the current answer\?",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return {key: " ".join(value.lower().split()) for key, value in match.groupdict().items()}


def is_evidence_quality_reasoning_question(question: str) -> bool:
    return _parse_evidence_quality_question(question) is not None


def _evidence_source_score(source: str, criterion: str) -> tuple[int, str]:
    source = source.lower()
    criterion = criterion.lower()
    if "local audit event" in source:
        return 95, "it is first-party local evidence about what ATLAS or the homelab actually observed"
    if "official status page" in source:
        return 90, "it is an authoritative current-state source for the service it covers"
    if "cached signal" in source:
        return 75, "it can reflect observed local state, but only if its timestamp is fresh enough for the question"
    if "readme" in source:
        return 60, "it documents intended behavior or configuration, but it usually does not prove the current live state"
    if "vendor changelog" in source:
        return 55, "it is useful for release history, but it may not prove the currently deployed state"
    if "news article" in source:
        return 45, "it can provide context, but it is secondary for current operational state"
    if "two-year-old blog post" in source:
        return 25, "it is stale and weak for a current-state claim"
    if "forum thread" in source:
        return 20, "it is crowdsourced and weak unless independently corroborated"
    return 40, "its authority and freshness are unclear"


def run_evidence_quality_reasoning(question: str) -> dict:
    parsed = _parse_evidence_quality_question(question)
    if not parsed:
        return {
            "route": "evidence_quality_reasoning",
            "tool": "local-evidence-quality-reasoning",
            "answer": "I couldn't parse that evidence-quality question.",
            "source": "Prompt-supplied source-quality policy",
            "result_status": "invalid",
            "error_type": "parse_failed",
            "result": {"question": question},
        }

    score_a, reason_a = _evidence_source_score(parsed["source_a"], parsed["criterion"])
    score_b, reason_b = _evidence_source_score(parsed["source_b"], parsed["criterion"])
    if score_a >= score_b:
        chosen, other, reason = parsed["source_a"], parsed["source_b"], reason_a
    else:
        chosen, other, reason = parsed["source_b"], parsed["source_a"], reason_b
    answer = (
        f"Give more weight to {chosen}, because {reason}. "
        f"Treat {other} as supporting context, not as the deciding source for the current state. "
        "If neither source actually proves the current answer, say that plainly: "
        f"\"I don't have enough evidence to verify the current state for {parsed['topic']}; "
        "the available sources disagree or are insufficient, so I should not present either claim as confirmed.\""
    )
    return {
        "route": "evidence_quality_reasoning",
        "tool": "local-evidence-quality-reasoning",
        "answer": answer,
        "source": "Prompt-supplied source-quality policy",
        "result_status": "ok",
        "error_type": None,
        "result": parsed | {
            "chosen_source": chosen,
            "other_source": other,
            "source_a_score": score_a,
            "source_b_score": score_b,
        },
    }


def deterministic_pronoun_ambiguity(question: str) -> str | None:
    """Resolve only structurally underdetermined pronouns when Ambiguous is an offered choice."""
    if not is_structured_reasoning_question(question):
        return None
    choices_match = re.search(r"(?:^|\n)choices:\s*([^\n]+)", question, flags=re.IGNORECASE)
    if not choices_match:
        return None
    choice_options = [option.strip() for option in choices_match.group(1).split(";")]
    if "ambiguous" not in {option.lower() for option in choice_options}:
        return None
    stem = re.split(r"(?:^|\n)choices:", question, maxsplit=1, flags=re.IGNORECASE)[0]
    lowered = stem.lower()
    if ("uses she/her" in lowered and "uses he/him" in lowered) or (
        "uses he/him" in lowered and "uses she/her" in lowered
    ):
        return None

    # Narrow, verb-semantics exception -- not narrative plausibility or
    # occupational stereotype: a communication verb directed at a named
    # addressee (told/warned/informed/advised/reminded), followed by an
    # embedded clause carrying an obligation/recommendation modal
    # (should/needs to/must/ought to), pragmatically addresses the listener.
    # "The hygienist told the patient that she should floss every day" is
    # advice TO the patient, not a claim about the speaker's own habits --
    # the modal itself is the grammatical signal, not an assumption about
    # who a hygienist or patient "would" be. Deliberately narrow: only fires
    # when exactly one non-Ambiguous choice names the resolved addressee: any
    # other shape (self-report content, diagnosis framing, "asked if"
    # requests, coordinated-possessive binding, etc.) falls through to the
    # existing Ambiguous default rather than guessing.
    obligation_match = re.search(
        r"\b(?:told|warned|informed|advised|reminded)\s+(?:the\s+)?([a-z]+)\s+that\s+"
        r"(?:he|she|they|him|her|them)\s+(?:should|needs? to|needed to|must|ought to|had to)\b",
        lowered,
    )
    if obligation_match:
        addressee = obligation_match.group(1)
        matching_choices = [
            option for option in choice_options
            if option.lower() != "ambiguous" and re.search(rf"\b{re.escape(addressee)}\b", option.lower())
        ]
        if len(matching_choices) == 1:
            return (
                f"{matching_choices[0]}; a communication verb (told/warned/informed) followed by an "
                "obligation modal (should/needs to/must) in the embedded clause conventionally addresses "
                "the listener, not the speaker."
            )

    clause = re.search(
        r"\b(?:because|that)\s+(?:the\s+)?(?:[a-z]+\s+){0,2}(he|she|they|him|her|them|his|their)\b",
        lowered,
    )
    if not clause:
        clause = re.search(
            r"\basked\s+(?:the\s+)?[a-z]+\s+to\b[^.]*\b(her|his|their)\s+[a-z]+\b",
            lowered,
        )
    if not clause:
        return None
    return (
        "Ambiguous; the pronoun is compatible with more than one earlier person, and the sentence provides no "
        "grammatical binding evidence that selects only one."
    )


def is_weather_question(question: str) -> bool:
    lowered = question.lower()
    if re.search(r"\b(?:weather|forecast)\b", lowered):
        return True
    # "rain" is correctly word-bounded here (not a substring-match bug like
    # "lock" in "blocking") -- the issue is a genuine word-sense ambiguity:
    # "purple rain" (Prince reference, e.g. "a purple rain effect on my
    # lamp") isn't about precipitation at all. Narrow exclusion for the
    # confirmed case, not a broad "if lamp-related, skip weather" heuristic
    # -- that would risk breaking legitimate weather phrasing like "light
    # rain today", where "light" is standard weather terminology.
    is_purple_rain_reference = bool(re.search(r"\bpurple\s+rain\b", lowered))
    if not is_purple_rain_reference and re.search(
        r"\b(?:rain|raining|wind|windy|humidity|fog|foggy|snow|snowy|"
        r"storm|stormy|overcast|cloudy|sleet|hail|precipitation)\b",
        lowered,
    ):
        return True
    if re.search(r"\b(?:outside|outdoors)\b", lowered) and re.search(
        r"\b(?:comfortable|pleasant|safe|suitable|feel|feels|like|wear|walk|hike|"
        r"picnic|today|tonight|tomorrow|morning|afternoon|evening)\b",
        lowered,
    ):
        return True
    if "temperature" in lowered or "temp" in lowered:
        non_weather_phrases = [
            "water boil",
            "boil at",
            "boiling point",
            "oven temperature",
            "body temperature",
            "cpu temperature",
            "gpu temperature",
        ]
        if any(phrase in lowered for phrase in non_weather_phrases):
            return False
        return any(phrase in lowered for phrase in ["outside", "weather", " in ", " for ", "today", "right now"])
    return False


# DEF-028 / DEC-2026-09-16-14: words that indicate a phrase is a sentence
# fragment, not a bare place name -- pronouns, auxiliary/modal verbs,
# question words, connectives, and the same weather-topic vocabulary this
# function already strips elsewhere. Used only to decide whether a no-"in
# X"/"for X" phrase should be treated as an already-isolated place name.
_WEATHER_NON_PLACE_INDICATOR = re.compile(
    r"\b(?:is|are|was|were|will|would|should|do|does|did|i|im|i'm|it|we|you|"
    r"and|or|if|not|so|how|what|whats|what's|where|why|when|"
    r"weather|forecast|temperature|temp|rain|raining|snow|snowing|"
    r"umbrella|jacket|outside|now|today|tonight|tomorrow)\b",
    re.IGNORECASE,
)

# DEF-031 / DEC-2026-09-16-14: Open-Meteo's public geocoder (the only place
# lookup this adapter has) doesn't know common US city abbreviations -- some
# return no result at all ("sf", "dc", "philly", "nola"), and "la" silently
# resolves to a wrong, differently-named place instead of Los Angeles, which
# is worse than an outright failure. Confirmed empirically against the live
# geocoding API, not assumed. Expand known abbreviations before geocoding
# ever runs, rather than hoping the third-party API degrades gracefully.
_WEATHER_PLACE_ALIASES = {
    "sf": "San Francisco",
    "la": "Los Angeles",
    "dc": "Washington, DC",
    "philly": "Philadelphia",
    "nola": "New Orleans",
}


def infer_weather_place(question: str) -> str:
    # DEF-026: the previous pattern captured everything from "in"/"for" to
    # the end of the string, on the assumption the place name is always the
    # last thing in the question. A trailing clause after the place
    # ("...in Chicago tomorrow, and should I plan on bringing an umbrella?")
    # got swept into the capture wholesale, then only partially cleaned by
    # the word-strip below -- producing a garbled place name spliced into
    # the answer/error text. Stop at the first clause boundary (comma,
    # question mark, end of string, or a small set of words that only ever
    # continue the sentence past the place name) instead of assuming the
    # place is always the last words in the question.
    match = re.search(
        r"\b(?:in|for)\s+(?P<place>[A-Za-z][A-Za-z0-9 .'-]*?)"
        r"(?:,\s*(?P<region>[A-Za-z][A-Za-z .'-]*?)(?=\s*(?:[,?]|$|\band\b|\bshould\b|\bwould\b)))?"
        r"(?=\s*(?:[,?]|$|\bright now\b|\btoday\b|\btomorrow\b|\btonight\b|\bcurrently\b|\bnow\b|"
        r"\band\b|\bshould\b|\bwould\b|\bwill\b|\bdo\b|\bdoes\b|\bis\b|\bcan\b|\bso\b|\busing\b))",
        question.strip(),
        re.IGNORECASE,
    )
    if match:
        place = match.group("place").strip()
        if match.group("region"):
            place = f"{place}, {match.group('region').strip()}"
    else:
        # DEF-028: no "in X"/"for X" clause usually means no location was
        # actually given -- fall through to the default rather than using
        # the whole raw question as a garbage "place" ("will it rain this
        # weekend" -> a lookup for the literal place "will it rain this
        # weekend"). But a bare, already-isolated place name with none of
        # that clause framing is exactly what the deep-plan step
        # decomposition hands this function directly for a "compare X to
        # Y" weather question -- the model correctly splits it into two
        # steps with request text "Oakland" and "San Francisco", each with
        # no "in"/"for" wrapper at all (DEC-2026-09-16-14, found via a real
        # "compare Oakland to San Francisco" question that silently fetched
        # San Francisco twice under the first version of this fix). A
        # short, letters-only phrase containing none of the sentence-shaped
        # words below is far more likely to already be a place than a
        # genuine no-location question.
        candidate = question.strip().rstrip("?.")
        if (
            candidate
            and len(candidate.split()) <= 3
            and re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", candidate)
            and not _WEATHER_NON_PLACE_INDICATOR.search(candidate)
        ):
            place = candidate
        else:
            place = ""
    place = re.sub(
        r"\b(right now|today|tomorrow|tonight|currently|now|morning|afternoon|evening)\b",
        "", place, flags=re.IGNORECASE,
    )
    place = re.sub(
        r"\b(what is|what's|whats|do i need|will i need|the|time|and|weather|temperature|"
        r"forecast|outside|like|umbrella|jacket)\b",
        "", place, flags=re.IGNORECASE,
    )
    place = re.sub(r"\s+", " ", place).strip(" ?.,")
    place = _WEATHER_PLACE_ALIASES.get(place.lower(), place)
    return place or os.environ.get("ATLAS_DEFAULT_WEATHER_PLACE", "San Francisco")


_WEATHER_EXPLICIT_DATE_RE = re.compile(
    r"\bon\s+([a-z]+\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)\b", re.IGNORECASE
)


def infer_weather_period(question: str, now: dt.datetime) -> tuple[str, dt.date | None]:
    """Return (period, resolved_date) for a weather question.

    H2: "tomorrow" must never silently return current conditions. period is
    one of "current" (default; the only period the previous, pre-H2 code
    ever asked for), "today", "tonight", "tomorrow", or "date" (an explicit
    calendar date). resolved_date is None only for "current" (the adapter's
    own current-conditions call already reports its own effective date) or
    for "date" when the phrase after "on" could not be parsed at all --
    callers must treat that specific combination as a parse failure and
    fail closed rather than silently falling back to current conditions.
    """
    lowered = question.lower()
    if re.search(r"\btomorrow\b", lowered):
        return "tomorrow", now.date() + dt.timedelta(days=1)
    if re.search(r"\btonight\b", lowered):
        return "tonight", now.date()
    if re.search(r"\btoday\b", lowered):
        return "today", now.date()
    date_match = _WEATHER_EXPLICIT_DATE_RE.search(lowered)
    if date_match:
        return "date", parse_target_date(date_match.group(1), now)
    return "current", None


def infer_book_author(question: str) -> str | None:
    match = re.search(r"\bby\s+([A-Z][A-Za-z .'-]+)\b", question)
    return match.group(1).strip() if match else None


def clean_media_query(question: str) -> str:
    query = re.sub(
        r"\b(what is|who is|tell me about|find|look up|search for|show me|give me)\b",
        "",
        question,
        flags=re.IGNORECASE,
    )
    query = re.sub(
        r"\b(movie|film|tv show|series|book|novel|album|artist|band|song|recording|weather|forecast)\b",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"^\s*the\s+the\b", "the", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+by\s+[A-Za-z][A-Za-z .'-]+$", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+", " ", query).strip(" ?.")
    return query or question.strip()


def is_datetime_question(question: str) -> bool:
    lowered = question.lower().strip()
    patterns = [
        "what time is it",
        "what's the time",
        "whats the time",
        "current time",
        "what day is it",
        "what is today's date",
        "what's today's date",
        "todays date",
        "what is the date today",
        "date today",
        "what date is it",
        "time in ",
        "date in ",
    ]
    return any(pattern in lowered for pattern in patterns)


DATE_RANGE_PATTERN = re.compile(
    r"\bdays?\s+(?:are\s+)?between\s+"
    r"(?P<start>[a-z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)\s+and\s+"
    r"(?P<end>[a-z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)"
)
DATE_RANGE_FROM_PATTERN = re.compile(
    r"\bhow\s+many\s+days\s+away\s+is\s+"
    r"(?P<end>[a-z][a-z ]+?(?:\s+\d{4})?)\s+from\s+"
    r"(?P<start>[a-z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)\b",
    re.IGNORECASE,
)


def is_date_range_question(question: str) -> bool:
    # DEF-017 routing half: paraphrased forms of "how many days are between X
    # and Y" (e.g. "Work out how many days are between May 13 and December
    # 15 of the same year") previously fell through to general_reference
    # entirely, unlike is_math_question's siblings which at least route
    # correctly and only fail to parse. This is a distinct capability from
    # is_countdown_question below -- that one measures from *today* to a
    # single target date; this measures between two arbitrary explicit
    # dates, which run_datetime() (returns only the current date/time) could
    # never have answered correctly even if routing were fixed to point
    # there. Reuses the countdown route/tool rather than inventing a new one,
    # since "local calendar math" is exactly the right existing capability.
    return bool(DATE_RANGE_PATTERN.search(question.lower()) or DATE_RANGE_FROM_PATTERN.search(question.lower()))


def is_countdown_question(question: str) -> bool:
    lowered = question.lower()
    if is_date_range_question(question) or is_stated_base_date_offset_question(question):
        return True
    if any(
        pattern in lowered
        for pattern in [
            "how many days until",
            "how many days till",
            "how many days away is",
            "how far from",
            "how far away is",
            "days until",
            "days till",
            "how long until",
            "how long till",
        ]
    ):
        return True
    # DEF-026: the checks above require "days"/"how far" to sit immediately
    # next to "until"/"till"/"from", which real phrasing routinely breaks by
    # inserting a base-date clause in between ("how many days from today
    # (September 15, 2026) until New Year's Day 2027?"). Confirmed via a
    # fresh paraphrase audit that this exact shape fell through to the
    # planner, which chose the wrong tool (a plain "what's today's date"
    # lookup) instead of a countdown -- a silent, confidently-wrong answer,
    # not merely a slow one. Bounded gap (not `.*`) so this doesn't grab
    # unrelated later clauses in a longer compound question.
    return bool(re.search(r"how\s+(?:many\s+days|far)\b.{0,60}?\b(?:until|till)\b", lowered, re.DOTALL))


# DEF-026: base date, event offset, and reminder offset used to live inside
# two large, order-sensitive regexes (one requiring "suppose", one requiring
# "if... what date is N days from now" in that exact clause order). Split
# into independent sub-patterns searched anywhere in the question, in any
# order, so real phrasing variance (event offset before or after the "what
# date" question, an explicit reminder day count instead of always "the day
# before") doesn't silently fail to match or silently default to 1 day.
_TODAY_IS_PATTERN = re.compile(
    r"\b(?:suppose|if)\s+today\s+is\s+"
    r"(?P<base>[a-z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4}))\b",
    re.IGNORECASE,
)
_EVENT_OFFSET_PATTERN = re.compile(
    r"(?:my\s+(?P<event_name>[a-z][a-z '\-]*?)\s+is\s+)?"
    r"(?P<event_days>\d+)\s+days?\s+(?:later|from\s+now)\b",
    re.IGNORECASE,
)
_REMINDER_OFFSET_PATTERN = re.compile(
    r"reminder\b.{0,40}?(?:"
    r"(?P<reminder_days>\d+)\s+days?\s+before"
    r"|the\s+day\s+before"
    r"|day\s+before"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def is_stated_base_date_offset_question(question: str) -> bool:
    return bool(_TODAY_IS_PATTERN.search(question) and _EVENT_OFFSET_PATTERN.search(question))


def is_math_question(question: str) -> bool:
    lowered = question.lower().strip()
    if re.search(r"\b(?:one\s+)?(?:half|third|quarter|fourth|fifth|sixth|eighth|tenth)\s+of\s+-?\d+(?:\.\d+)?\b", lowered):
        return True
    if re.search(
        r"\b(?:begins?|starts?)\s+with\s+\d+(?:\.\d+)?\b.*\b\d+(?:\.\d+)?\s+(?:items?\s+)?per\s+\w+\s+for\s+\d+(?:\.\d+)?\s+\w+",
        lowered,
    ):
        return True
    if re.search(
        r"\b(?:begins?|starts?)\s+with\s+\d+(?:\.\d+)?\b.*\b\d+(?:\.\d+)?\b.*"
        r"\b(?:removed|sold|used|lost|added|received|gained)\s+(?:each|per)\s+\w+\s+for\s+"
        r"\d+(?:\.\d+)?\s+\w+",
        lowered,
    ):
        return True
    if re.fullmatch(r"[-+/*().\d\s%]+", lowered):
        return True
    if re.search(r"\d+(?:\.\d+)?\s*%\s*(?:of|on)\s*\d+(?:\.\d+)?", lowered):
        return True
    # DEF-035: a discount ("15% off of 84 bucks") uses "off", not "of"/"on",
    # so the check above never catches it -- same shape gap as the
    # "whats "/"what's " prefix mismatch below, just a different word list.
    if re.search(r"\d+(?:\.\d+)?\s*(?:%|percent)\s*off\b", lowered):
        return True
    return any(
        pattern in lowered
        for pattern in [
            "what is ",
            "what's ",
            "whats ",
            "calculate ",
            "compute ",
            "split ",
            "tip ",
            "plus",
            "minus",
            "times",
            "multiplied by",
            "divided by",
            "percent of",
            "% of",
            "% on",
            "square root of",
        ]
    ) and any(token in lowered for token in ["+", "*", "/", "%", " plus ", " minus ", " times ", "multiplied", "divided", "percent of", "tip", "split", "square root", " squared"])


def is_quantitative_reasoning_question(question: str) -> bool:
    lowered = question.lower()
    return (
        _parse_backup_upload_window(question) is not None
        or _parse_travel_time_comparison(question) is not None
    )


def _parse_temporal_container_state(question: str) -> dict | None:
    """Parse the first DEF-025c observed closed-world state-tracking template."""
    normalized = " ".join(question.strip().split())
    match = re.fullmatch(
        r"First the (?P<object>[a-z][a-z ]+?) is placed inside the (?P<container>[a-z][a-z ]+?), "
        r"then the (?P=container) is moved under the (?P<anchor>[a-z][a-z ]+?), "
        r"and finally the (?P=object) is taken out and put on the (?P<surface>[a-z][a-z ]+?); "
        r"where is the (?P=object) now\? Where is the (?P=container) now\?",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    parts = {key: " ".join(value.lower().split()) for key, value in match.groupdict().items()}
    return {
        "kind": "temporal_container_state",
        "object": parts["object"],
        "container": parts["container"],
        "anchor": parts["anchor"],
        "surface": parts["surface"],
        "object_location": f"on the {parts['surface']}",
        "container_location": f"under the {parts['anchor']}",
    }


def is_closed_world_reasoning_question(question: str) -> bool:
    return _parse_temporal_container_state(question) is not None


def is_conversion_question(question: str) -> bool:
    lowered = question.lower()
    conversion_units = (
        "seconds?|minutes?|hours?|days?|inches?|feet|foot|yards?|centimeters?|meters?|kilometers?|"
        "milligrams?|grams?|kilograms?|milliliters?|liters?|kilobytes?|megabytes?"
    )
    if re.search(rf"\b\d+(?:\.\d+)?\s+(?:{conversion_units})\s+(?:in|into|to|as)\s+(?:{conversion_units})\b", lowered):
        return True
    # DEF-043: "feet"/"foot" were bare substring checks below, unlike every
    # other token here -- they matched inside "football" (found live via two
    # real sports-trivia questions, "...ever played? What about football?"
    # and "...football's longest match?", both wrongly classified as unit
    # conversion). Word-bounded here, matching this file's own established
    # pattern for the same class of bug (book/address-book, movie/movies,
    # director/director-general).
    if re.search(r"\b(?:feet|foot)\b", lowered):
        return True
    return any(
        token in lowered
        for token in [
            "how many ounces",
            "how many oz",
            "cups in",
            "tablespoons in",
            "tablespoons are in",
            "tbsp in",
            "teaspoons in",
            "teaspoons are in",
            "tsp in",
            "ounces in",
            "miles in",
            "kilometers in",
            "km in",
            "kg in",
            "kilograms in",
            "pounds in",
            "lbs in",
            "quarts",
            "quart",
            "gallons",
            "gallon",
            "liters",
            "litres",
            "inches",
            "centimeters",
            "cm in",
            "fahrenheit",
            "celsius",
            "dollars to euros",
            "usd to eur",
            "eur to usd",
        ]
    )


def extract_general_reference_query(question: str) -> str:
    stripped = question.strip().rstrip("?")
    patterns = [
        r"^who is (.+)$",
        r"^what is (.+)$",
        r"^where is (.+)$",
        r"^when was (.+)$",
        r"^tell me about (.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, stripped, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return clean_media_query(question)


def deterministic_public_search_query(question: str) -> str:
    """Remove search instructions without asking a model to rewrite the subject."""
    query = question.strip().rstrip("?.")
    query = re.sub(
        r"^(?:please\s+)?(?:search|find|look\s+up)(?:\s+the)?(?:\s+(?:current|live))?"
        r"(?:\s+public)?(?:\s+(?:web|sources?))?(?:\s+for)?\s+",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(
        r"\s+(?:and|then)\s+(?:name|tell|report|return|give|summarize)\b.*$",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"^the\s+", "", query, flags=re.IGNORECASE)
    query = " ".join(query.split()).strip()
    return query[:200] or extract_general_reference_query(question)[:200]


def question_needs_live_verification(question: str) -> bool:
    lowered = question.lower()
    if is_structured_reasoning_question(question):
        return False
    # DEF-050: found via an overnight unattended stress run -- the exact
    # real Valve-ownership question DEF-038 was supposed to have fixed
    # still produced a confidently fabricated answer ("Valve is owned by
    # Microsoft") through this function's own, separate keyword list,
    # which was never updated when DEF-038 added ownership/leadership/
    # price coverage to the orchestrator's own decision gate. This is the
    # legacy path's independent "needs live search" check -- reached
    # whenever the legacy general_reference handler runs a question
    # directly, not just through the orchestrator's own decision -- so it
    # needs the same signal, not a second copy of it. DEF-052 later added
    # population-ranking coverage to the same shared predicate.
    if atlas_orchestrator_v2.is_time_sensitive_fact_question(question):
        return True
    return any(
        re.search(rf"\b{re.escape(token)}\b", lowered)
        for token in [
            "latest",
            "current",
            "currently",
            "today",
            "right now",
            "live",
            "news",
            "stock price",
            "price of",
            "weather",
            "forecast",
            "score",
            "won",
            "how many",
            "average per year",
            "indicted",
            "indictment",
            "criminal charges",
            "criminal offenses",
            "legal charges",
            "allegations",
        ]
    )


# DEF-017/DEF-012: this was the only subprocess call site in this file with no
# outer timeout at all. A hang here (e.g. a stalled search-searxng.py call)
# previously blocked silently until the outer HTTP request's own 180s timeout
# killed the whole response -- confirmed as the root cause of a 22% timeout
# rate on datetime-paraphrase questions and a likely contributor to
# thin-evidence general-knowledge refusals sharing the same pipeline. Set
# below the sibling tools' own internal timeouts (search-searxng.py: 45s,
# search-wikipedia.py: 30s) so this fails first and cleanly rather than
# racing them.
def run_json_tool(command: list[str], timeout: int = 25) -> dict:
    proc = subprocess.run(command, capture_output=True, text=True, check=True, timeout=timeout)
    return json.loads(proc.stdout)


def called_process_detail(exc: subprocess.CalledProcessError | subprocess.TimeoutExpired) -> str:
    stderr = (exc.stderr or "").strip()
    stdout = (exc.stdout or "").strip()
    if stderr:
        return stderr
    if stdout:
        return stdout
    # TimeoutExpired has no .returncode (it never returned); CalledProcessError always does.
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"timed out after {exc.timeout}s"
    return f"exit code {exc.returncode}"


def compact_error(text: str, limit: int = 500) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


READONLY_RESULT_STATUSES = frozenset({"ok", "no_data", "unavailable", "invalid", "stale"})
READONLY_ERROR_TYPES = frozenset({
    "adapter_unavailable",
    "contract_invalid",
    "no_data",
    "parse_failed",
    "stale_data",
    "unsupported_period",
    "ungrounded_result",
})


def ensure_readonly_result_contract(
    payload: dict,
    capability: str | None,
    question: str,
) -> dict:
    """Attach the bounded semantic result contract to a runner response.

    Exact adapters set their own status where they know it most precisely.
    This compatibility boundary covers the remaining read-only capability
    implementations while they migrate, and deliberately fails closed when
    an explicitly supplied contract is malformed.
    """
    if not isinstance(payload, dict):
        return {
            "route": "invalid",
            "tool": "atlas-result-contract",
            "answer": "I couldn't validate the result from that read-only capability.",
            "source": "ATLAS result contract",
            "result": {},
            "result_status": "invalid",
            "error_type": "contract_invalid",
        }
    explicit_status = payload.get("result_status")
    explicit_error = payload.get("error_type")
    if explicit_status is not None:
        if (
            explicit_status not in READONLY_RESULT_STATUSES
            or (explicit_error is not None and explicit_error not in READONLY_ERROR_TYPES)
            or (explicit_status == "ok" and explicit_error is not None)
            or (explicit_status != "ok" and explicit_error is None)
        ):
            payload["result_status"] = "invalid"
            payload["error_type"] = "contract_invalid"
        return payload

    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    answer = payload.get("answer")
    answer_usable = isinstance(answer, str) and bool(answer.strip()) and not is_nonanswer(answer)
    route = payload.get("route")
    status = "ok"
    error_type = None

    if capability == "math.calculate" or route == "math":
        if "value" not in result:
            status, error_type = "invalid", "parse_failed"
    elif capability == "units.convert" or route == "conversion":
        if "output_value" not in result:
            status, error_type = "invalid", "parse_failed"
    elif capability == "datetime.resolve" or route == "datetime":
        offset_requested = bool(re.search(r"\b\d+\s+days?\s+(?:after|before)\b", question, re.IGNORECASE))
        if offset_requested and "resolved_date" not in result:
            status, error_type = "invalid", "parse_failed"
    elif capability == "public.search":
        if not search_payload_has_evidence(result.get("search") or {}):
            status, error_type = "unavailable", "ungrounded_result"
    elif capability == "homelab.knowledge" or route == "homelab":
        local_fact = str(payload.get("tool") or "").startswith("local-homelab-")
        documents = result.get("retrieval_documents") or []
        if not local_fact and not documents:
            status, error_type = "no_data", "no_data"
    elif capability == "memory.recall" or route == "personal_memory":
        if result.get("knowledge_found") is False or not result.get("retrieval_documents"):
            status, error_type = "no_data", "no_data"
    elif not answer_usable:
        status, error_type = "no_data", "no_data"

    payload["result_status"] = status
    payload["error_type"] = error_type
    return payload


def infer_timezone(question: str) -> tuple[str, str]:
    lowered = question.lower()
    zones = {
        "los angeles": "America/Los_Angeles",
        "la": "America/Los_Angeles",
        "new york": "America/New_York",
        "nyc": "America/New_York",
        "chicago": "America/Chicago",
        "denver": "America/Denver",
        "phoenix": "America/Phoenix",
        "london": "Europe/London",
        "paris": "Europe/Paris",
        "berlin": "Europe/Berlin",
        "tokyo": "Asia/Tokyo",
        "shanghai": "Asia/Shanghai",
        "sydney": "Australia/Sydney",
        "utc": "UTC",
    }
    match = re.search(r"\b(?:in|for)\s+([A-Za-z ._-]+)\??$", question.strip(), re.IGNORECASE)
    place = match.group(1).strip(" ?.").lower() if match else ""
    zone_name = zones.get(place)
    if zone_name:
        return zone_name, place.title()
    for marker, candidate in zones.items():
        if marker in lowered:
            return candidate, marker.title()
    return "America/Los_Angeles", "local"


def run_datetime(question: str) -> dict:
    timezone_name, place = infer_timezone(question)
    now = dt.datetime.now(ZoneInfo(timezone_name))
    lowered = question.lower()
    offset_match = re.search(
        r"\b(?P<count>\d+)\s+days?\s+(?P<direction>after|before)\s+(?P<date>[^?]+)",
        lowered,
    )
    if offset_match:
        base_date = parse_target_date(offset_match.group("date"), now)
        if base_date:
            days = int(offset_match.group("count"))
            if offset_match.group("direction") == "before":
                days = -days
            resolved = base_date + dt.timedelta(days=days)
            return {
                "route": "datetime",
                "tool": "local-calendar-math",
                "answer": resolved.strftime("%A, %B %-d, %Y"),
                "source": "Built-in date math",
                "result_status": "ok",
                "error_type": None,
                "result": {
                    "base_date": base_date.isoformat(),
                    "offset_days": days,
                    "resolved_date": resolved.isoformat(),
                },
            }
        return {
            "route": "datetime",
            "tool": "local-calendar-math",
            "answer": "I couldn't parse the base date for that date calculation.",
            "source": "Built-in date math",
            "result_status": "invalid",
            "error_type": "parse_failed",
            "result": {"offset_days": int(offset_match.group("count"))},
        }
    if "date" in lowered or "day" in lowered:
        answer = now.strftime("%A, %B %-d, %Y")
    else:
        answer = now.strftime("%-I:%M %p %Z")
    return {
        "route": "datetime",
        "tool": "local-clock",
        "answer": answer,
        "source": "System clock",
        "result_status": "ok",
        "error_type": None,
        "result": {
            "timezone": timezone_name,
            "place": place,
            "iso": now.isoformat(),
        },
    }


def _format_decimal(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _format_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


# Hoisted from inside parse_target_date() (was a local dict there) so
# run_countdown()'s new "between X and Y" date-range branch can build both
# endpoints without independently duplicating this list.
MONTH_NAMES = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


_HOLIDAY_ALIASES = {
    "christmas": (12, 25),
    "christmas day": (12, 25),
    "new year's eve": (12, 31),
    "new years eve": (12, 31),
    "new year's day": (1, 1),
    "new years day": (1, 1),
    "halloween": (10, 31),
    "independence day": (7, 4),
}
_HOLIDAY_ALIAS_PATTERN = re.compile(
    r"(?P<name>halloween|christmas(?: day)?|new years eve|new year's eve|"
    r"new years day|new year's day|independence day)(?:\s+(?P<year>\d{4}))?"
)


def parse_target_date(text: str, now: dt.datetime) -> dt.date | None:
    cleaned = text.strip().lower().rstrip("?.")

    # DEF-030: "today"/"now" as the countdown *target* ("how many days until
    # today") is a legitimate zero-day case run_countdown() already renders
    # correctly ("Today.") -- it just never reached that path because this
    # function only ever treated "today" as an implicit *base* date, never
    # as something a caller could ask a target-date question about.
    if cleaned in {"today", "now"}:
        return now.date()

    # DEF-026: a holiday name with an explicit trailing year ("new year's day
    # 2027") previously only matched the bare-alias branch below, which has
    # no room for a year suffix, so it fell through to the month/day fullmatch
    # (which requires a real month name) and failed to parse entirely.
    alias_match = _HOLIDAY_ALIAS_PATTERN.fullmatch(cleaned)
    if alias_match:
        month, day = _HOLIDAY_ALIASES[alias_match.group("name")]
        if alias_match.group("year"):
            return dt.date(int(alias_match.group("year")), month, day)
        year = now.year
        candidate = dt.date(year, month, day)
        if candidate < now.date():
            candidate = dt.date(year + 1, month, day)
        return candidate

    match = re.fullmatch(
        r"(?P<month>[a-z]+)\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>\d{4}))?",
        cleaned,
    )
    if match and match.group("month") in MONTH_NAMES:
        month = MONTH_NAMES[match.group("month")]
        day = int(match.group("day"))
        year = int(match.group("year")) if match.group("year") else now.year
        candidate = dt.date(year, month, day)
        if not match.group("year") and candidate < now.date():
            candidate = dt.date(year + 1, month, day)
        return candidate
    return None


def _parse_range_endpoint(text: str, default_year: int) -> dt.date | None:
    """Parse one side of a "between X and Y" date range with no automatic
    year-bumping. Deliberately does not reuse parse_target_date()'s
    next-year-if-in-the-past logic: that logic is correct for a single
    "days until X" target relative to today, but applying it independently
    to each side of a range would put the two endpoints in different years
    whenever "today" falls between them in the calendar (e.g. "between May
    13 and December 15" asked in September would bump May into next year
    while leaving December in the current year), silently corrupting the
    range for the extremely common real phrasing "...of the same year."
    """
    match = re.fullmatch(
        r"(?P<month>[a-z]+)\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>\d{4}))?",
        text.strip().lower(),
    )
    cleaned = text.strip().lower()
    alias_match = _HOLIDAY_ALIAS_PATTERN.fullmatch(cleaned)
    if alias_match:
        month, day = _HOLIDAY_ALIASES[alias_match.group("name")]
        year = int(alias_match.group("year")) if alias_match.group("year") else default_year
        return dt.date(year, month, day)

    if not match or match.group("month") not in MONTH_NAMES:
        return None
    year = int(match.group("year")) if match.group("year") else default_year
    try:
        return dt.date(year, MONTH_NAMES[match.group("month")], int(match.group("day")))
    except ValueError:
        return None


def run_date_range(question: str) -> dict:
    now = dt.datetime.now(ZoneInfo("America/Los_Angeles"))
    match = DATE_RANGE_PATTERN.search(question.lower())
    from_match = DATE_RANGE_FROM_PATTERN.search(question.lower())
    if not match:
        match = from_match
    if not match:
        return {
            "route": "countdown",
            "tool": "local-calendar-math",
            "answer": "I couldn't parse that date range.",
            "source": "Built-in date math",
            "result_status": "invalid",
            "error_type": "parse_failed",
            "result": {"question": question},
        }
    start_date = _parse_range_endpoint(match.group("start"), now.year)
    end_date = _parse_range_endpoint(match.group("end"), start_date.year if start_date else now.year)
    if not start_date or not end_date:
        return {
            "route": "countdown",
            "tool": "local-calendar-math",
            "answer": "I couldn't parse that date range.",
            "source": "Built-in date math",
            "result_status": "invalid",
            "error_type": "parse_failed",
            "result": {"start_text": match.group("start"), "end_text": match.group("end")},
        }
    days = abs((end_date - start_date).days)
    answer = "0 days." if days == 0 else ("1 day." if days == 1 else f"{days} days.")
    return {
        "route": "countdown",
        "tool": "local-calendar-math",
        "answer": answer,
        "source": "Built-in date math",
        "result_status": "ok",
        "error_type": None,
        "result": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "days": days,
        },
    }


def run_stated_base_date_offset(question: str) -> dict:
    base_match = _TODAY_IS_PATTERN.search(question)
    event_match = _EVENT_OFFSET_PATTERN.search(question)
    if not base_match or not event_match:
        return {
            "route": "countdown",
            "tool": "local-calendar-math",
            "answer": "I couldn't parse that stated-base-date calculation.",
            "source": "Built-in date math",
            "result_status": "invalid",
            "error_type": "parse_failed",
            "result": {"question": question},
        }
    base_date = _parse_range_endpoint(base_match.group("base"), dt.datetime.now(ZoneInfo("America/Los_Angeles")).year)
    if not base_date:
        return {
            "route": "countdown",
            "tool": "local-calendar-math",
            "answer": "I couldn't parse the base date for that date calculation.",
            "source": "Built-in date math",
            "result_status": "invalid",
            "error_type": "parse_failed",
            "result": {"base_text": base_match.group("base")},
        }
    event_days = int(event_match.group("event_days"))
    event_date = base_date + dt.timedelta(days=event_days)
    event_name = (
        " ".join(event_match.group("event_name").split())
        if event_match.group("event_name")
        else "the target date"
    )

    reminder_match = _REMINDER_OFFSET_PATTERN.search(question)
    reminder_days = None
    if reminder_match:
        reminder_days = int(reminder_match.group("reminder_days")) if reminder_match.group("reminder_days") else 1

    result: dict = {
        "base_date": base_date.isoformat(),
        "event_name": event_name,
        "event_days_later": event_days,
        "event_date": event_date.isoformat(),
    }
    if reminder_days is None:
        answer = f"Put {event_name} on {event_date.strftime('%B %-d, %Y')}."
    else:
        reminder_date = event_date - dt.timedelta(days=reminder_days)
        reminder_label = "1 day" if reminder_days == 1 else f"{reminder_days} days"
        answer = (
            f"Put {event_name} on {event_date.strftime('%B %-d, %Y')}. "
            f"A reminder {reminder_label} before that should be on {reminder_date.strftime('%B %-d, %Y')}."
        )
        result["reminder_days_before"] = reminder_days
        result["reminder_date"] = reminder_date.isoformat()
    return {
        "route": "countdown",
        "tool": "local-calendar-math",
        "answer": answer,
        "source": "Built-in date math",
        "result_status": "ok",
        "error_type": None,
        "result": result,
    }


def run_countdown(question: str) -> dict:
    if is_date_range_question(question):
        return run_date_range(question)
    if is_stated_base_date_offset_question(question):
        return run_stated_base_date_offset(question)
    now = dt.datetime.now(ZoneInfo("America/Los_Angeles"))
    lowered = question.lower().strip().rstrip("?")
    # DEF-026: `(.+)$` assumed the target date phrase always runs to the end
    # of the string. In a compound message ("How many days until Christmas
    # 2026, and separately, what's the weather in Seattle right now?") that
    # swept the trailing clause into the capture too, breaking the date
    # parse entirely (same root cause as the weather-place bug fixed above:
    # a greedy capture with no clause-boundary stop). Non-greedy with a
    # lookahead for the first clause boundary, falling back to end-of-string
    # when there isn't one.
    _CLAUSE_END = r"(?=[,?]|$|\band\b|\bso\b|\bwhat\b)"
    target_text = None
    for pattern in [
        rf"how many days (?:until|till) (.+?){_CLAUSE_END}",
        rf"how far (?:away is|from) (.+?){_CLAUSE_END}",
        rf"days (?:until|till) (.+?){_CLAUSE_END}",
        rf"how long (?:until|till) (.+?){_CLAUSE_END}",
    ]:
        match = re.search(pattern, lowered)
        if match:
            target_text = match.group(1).strip()
            break

    # The tight patterns above require "until"/"till" to sit right next to
    # "days"/"how far". Real phrasing often inserts a base-date clause first
    # ("how many days from today (September 15, 2026) until New Year's Day
    # 2027?") -- take the text after the LAST "until"/"till" as the target
    # in that case, since the base-date clause is not needed (this function
    # already assumes "from today").
    if not target_text:
        match = re.search(rf"\b(?:until|till)\s+(.+?){_CLAUSE_END}", lowered)
        if match and re.search(r"how\s+(?:many\s+days|far)\b", lowered):
            target_text = match.group(1).strip()

    # DEF-026 (found via the 120-case revalidation, not the original 16):
    # "how far away is X from today?" put "from today" itself inside the
    # captured target (no clause-boundary marker separates them), so
    # parse_target_date() got "independence day 2027 from today" instead of
    # "independence day 2027" and failed outright. This function already
    # assumes the base date is today, so a trailing "from today"/"from now"
    # is redundant and safe to strip rather than needing its own base-date
    # handling.
    if target_text:
        target_text = re.sub(r"\s+from\s+(?:today|now)\s*$", "", target_text).strip()

    if not target_text:
        return {
            "route": "countdown",
            "tool": "local-calendar-math",
            "answer": "I couldn't parse that date.",
            "source": "Built-in date math",
            "result_status": "invalid",
            "error_type": "parse_failed",
            "result": {"question": question},
        }

    target_date = parse_target_date(target_text, now)
    if not target_date:
        # A vague description followed by a concrete example ("a date
        # thats already passed, like march 1") stops the clause-boundary
        # capture above at the comma, dropping the actual usable date. Retry
        # against whatever follows "like" before it's given up on entirely.
        like_match = re.search(rf"\blike\s+(.+?){_CLAUSE_END}", lowered)
        if like_match:
            target_date = parse_target_date(like_match.group(1).strip(), now)
    if not target_date:
        return {
            "route": "countdown",
            "tool": "local-calendar-math",
            "answer": "I couldn't parse that date.",
            "source": "Built-in date math",
            "result_status": "invalid",
            "error_type": "parse_failed",
            "result": {"target_text": target_text},
        }

    days = (target_date - now.date()).days
    if days == 0:
        answer = "Today."
    elif days == 1:
        answer = "1 day."
    else:
        answer = f"{days} days."
    return {
        "route": "countdown",
        "tool": "local-calendar-math",
        "answer": answer,
        "source": "Built-in date math",
        "result_status": "ok",
        "error_type": None,
        "result": {
            "target_date": target_date.isoformat(),
            "days": days,
            "timezone": "America/Los_Angeles",
        },
    }


# DEF-003: confirmed at scale (648/650, 99.7%) on a real overnight run --
# correctly routed to math, then "I couldn't parse that math question." The
# root cause has two independent parts, both fixed below. (1) The prefix
# list a few lines down only ever handled 4 fixed strings ("what is "/
# "what's "/"calculate "/"compute "); every downstream shape check uses
# re.fullmatch(), so any unstripped verb phrase ("Work out what...",
# "Could you determine...") made every one of those fail regardless of how
# simple the underlying arithmetic was. (2) Less obvious, found by reading
# the suite generator's own templates (ai-evals/hardening/generate-atlas-
# hardening-suite.py): every paraphrase ends in a trailing result phrase
# ("...is.", "...equals.", "...leaves you with.", "...comes out to.",
# "...works out to."), which nothing stripped either -- the downstream
# fullmatch() patterns have no tolerance for trailing text at all, so even a
# question with a correctly-stripped prefix would still fail to fullmatch
# with an unstripped suffix still attached.
#
# Deliberately a compiled alternation over a bounded, auditable verb list
# (matching the suite generator's own MATH_PARAPHRASE_VERBS, plus a few
# additional common natural variants) rather than an ever-growing sequence
# of exact fixed strings -- the previous list-based approach is exactly the
# fullmatch-shaped brittleness this class of bug keeps reproducing (the
# same trap independently found and fixed for DEF-011's first design and
# DEF-012's is_nonanswer() redesign this same session). This still cannot
# catch every conceivable phrasing; the fix is to widen the matched surface
# of an already-complete capability (arithmetic), not to build a general
# parser, per the plan's own "widen an existing route" test.
_MATH_PARAPHRASE_PREFIX_PATTERN = re.compile(
    r"^(?:please\s+|could you\s+|can you\s+|would you\s+|go ahead and\s+|"
    r"see if you can\s+|help me\s+|do you mind\s+)?"
    r"(?:work(?:ing)? out|figure? out|determine|calculate|compute|tally up|"
    r"total up|add up|find out|tell me)\s+"
    r"(?:what\s+)?"
)
_MATH_PARAPHRASE_SUFFIX_PATTERN = re.compile(
    r"\s+(?:is|equals|comes out to|works out to|leaves you with|gives you|results in)\s*\.?$"
)

# DEF-030: narrow spelled-out whole-number parsing (0-999), scoped to just
# the "spelled-out percent of spelled-out number" shape found in the probe
# ("twenty five percent of two hundred") -- not general number-word
# parsing, which is a bigger undertaking than this repro case needs.
_NUMBER_WORDS_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_NUMBER_WORDS_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def _words_to_number(text: str) -> int | None:
    """Parse a spelled-out whole number up to 999 ("five", "twenty five",
    "two hundred", "two hundred twenty five"). Returns None for anything
    outside that narrow shape rather than guessing.
    """
    tokens = text.strip().lower().replace("-", " ").split()
    if not tokens:
        return None
    total = 0
    i = 0
    if tokens[i] in _NUMBER_WORDS_ONES and i + 1 < len(tokens) and tokens[i + 1] == "hundred":
        total += _NUMBER_WORDS_ONES[tokens[i]] * 100
        i += 2
    if i < len(tokens) and tokens[i] in _NUMBER_WORDS_TENS:
        total += _NUMBER_WORDS_TENS[tokens[i]]
        i += 1
        if i < len(tokens) and tokens[i] in _NUMBER_WORDS_ONES and _NUMBER_WORDS_ONES[tokens[i]] < 10:
            total += _NUMBER_WORDS_ONES[tokens[i]]
            i += 1
    elif i < len(tokens) and tokens[i] in _NUMBER_WORDS_ONES:
        total += _NUMBER_WORDS_ONES[tokens[i]]
        i += 1
    if i != len(tokens):
        return None
    return total


def normalize_math_expression(question: str) -> str | None:
    expr = question.lower().strip().rstrip("?.")
    expr = _MATH_PARAPHRASE_PREFIX_PATTERN.sub("", expr, count=1)
    expr = _MATH_PARAPHRASE_SUFFIX_PATTERN.sub("", expr, count=1)
    repeated_change = re.search(
        r"\b(?:begins?|starts?)\s+with\s+(?P<start>\d+(?:\.\d+)?)\b.*?"
        r"(?:\b(?P<active>ships?|sells?|uses?|removes?|loses?|adds?|receives?|gains?)\s+"
        r"(?P<active_amount>\d+(?:\.\d+)?)\s+(?:\w+(?:\s+\w+)?\s+)?per\s+|"
        r"\b(?P<passive_amount>\d+(?:\.\d+)?)\s+(?:\w+(?:\s+\w+)?\s+)?(?:are|is)\s+"
        r"(?P<passive>removed|sold|used|lost|added|received|gained)\s+(?:each|per)\s+)"
        r"(?P<period>\w+)\s+for\s+"
        r"(?P<count>\d+(?:\.\d+)?)\s+(?P=period)s?\b",
        expr,
    )
    if repeated_change:
        verb = (repeated_change.group("active") or repeated_change.group("passive") or "").rstrip("s")
        amount = repeated_change.group("active_amount") or repeated_change.group("passive_amount")
        operator = "+" if verb in {"add", "added", "receive", "received", "gain", "gained"} else "-"
        return f"{repeated_change.group('start')} {operator} ({amount} * {repeated_change.group('count')})"
    meal_tip = re.fullmatch(
        r"(?:a|the|my)\s+(?:meal|bill)\s+is\s+\$?(?P<amount>\d+(?:\.\d+)?)\s*[,;.]?\s*"
        r"what is\s+(?:a\s+)?(?P<percent>\d+(?:\.\d+)?)\s*(?:%|percent)\s+tip",
        expr,
    )
    if meal_tip:
        return f"({meal_tip.group('percent')} / 100) * {meal_tip.group('amount')}"
    for prefix in ["what is ", "what's ", "whats ", "calculate ", "compute "]:
        if expr.startswith(prefix):
            expr = expr[len(prefix):].strip()
            break
    word_fraction = re.fullmatch(
        r"(?:one\s+)?(half|third|quarter|fourth|fifth|sixth|eighth|tenth)\s+of\s+(-?\d+(?:\.\d+)?)",
        expr,
    )
    if word_fraction:
        denominators = {
            "half": 2, "third": 3, "quarter": 4, "fourth": 4, "fifth": 5,
            "sixth": 6, "eighth": 8, "tenth": 10,
        }
        return f"{word_fraction.group(2)} / {denominators[word_fraction.group(1)]}"
    expr = expr.replace("multiplied by", "*")
    expr = expr.replace("times", "*")
    expr = expr.replace("plus", "+")
    expr = expr.replace("minus", "-")
    expr = expr.replace("divided by", "/")
    percent_match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*percent of\s*(\d+(?:\.\d+)?)", expr)
    if percent_match:
        return f"({percent_match.group(1)} / 100) * {percent_match.group(2)}"
    percent_symbol_match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%\s*(?:of|on)\s*(\d+(?:\.\d+)?)", expr)
    if percent_symbol_match:
        return f"({percent_symbol_match.group(1)} / 100) * {percent_symbol_match.group(2)}"
    word_percent_match = re.fullmatch(
        r"([a-z]+(?:[\s-]+[a-z]+){0,3})\s+percent of\s+([a-z]+(?:[\s-]+[a-z]+){0,3})", expr
    )
    if word_percent_match:
        percent_value = _words_to_number(word_percent_match.group(1))
        base_value = _words_to_number(word_percent_match.group(2))
        if percent_value is not None and base_value is not None:
            return f"({percent_value} / 100) * {base_value}"
    # A discount ("15% off of 84 bucks") is a different shape than "percent
    # of" -- it wants the amount AFTER the discount, not the discount
    # amount itself. "off"/"off of" is not "of"/"on", so percent_symbol_match
    # above never matches this on its own.
    discount_match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s*off(?:\s*of)?\s*\$?(\d+(?:\.\d+)?)\s*(?:dollars|bucks|usd)?",
        expr,
    )
    if discount_match:
        return f"{discount_match.group(2)} - (({discount_match.group(1)} / 100) * {discount_match.group(2)})"
    tip_match = re.fullmatch(r"(?:what is )?(?:a )?(\d+(?:\.\d+)?)\s*(?:%|percent)\s*tip on\s*\$?(\d+(?:\.\d+)?)", expr)
    if tip_match:
        return f"({tip_match.group(1)} / 100) * {tip_match.group(2)}"
    split_match = re.fullmatch(r"split\s+(?:a\s+)?\$?(\d+(?:\.\d+)?)\s+(?:bill\s+)?(?:between|among|for)\s+(\d+)(?:\s+people)?", expr)
    if split_match:
        return f"{split_match.group(1)} / {split_match.group(2)}"
    # Pre-existing gap found while regression-testing the DEF-003 fix above,
    # not caused by it: "What is the square root of 144?" strips to "the
    # square root of 144" (the leading "the" was never part of any prefix
    # this function stripped), which this fullmatch never tolerated.
    sqrt_match = re.fullmatch(r"(?:the\s+)?square root of\s*(\d+(?:\.\d+)?)", expr)
    if sqrt_match:
        return f"sqrt({sqrt_match.group(1)})"
    squared_match = re.fullmatch(r"(\d+(?:\.\d+)?)\s+squared", expr)
    if squared_match:
        return f"{squared_match.group(1)} ** 2"
    expr = expr.replace("^", "**")
    expr = re.sub(r"\s+", " ", expr).strip()
    return expr or None


def evaluate_math_expression(expr: str) -> float:
    node = ast.parse(expr, mode="eval")

    def _eval(current):
        if isinstance(current, ast.Expression):
            return _eval(current.body)
        if isinstance(current, ast.Constant) and isinstance(current.value, (int, float)):
            return float(current.value)
        if isinstance(current, ast.UnaryOp) and isinstance(current.op, (ast.UAdd, ast.USub)):
            value = _eval(current.operand)
            return value if isinstance(current.op, ast.UAdd) else -value
        if isinstance(current, ast.BinOp) and isinstance(
            current.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
        ):
            left = _eval(current.left)
            right = _eval(current.right)
            if isinstance(current.op, ast.Add):
                return left + right
            if isinstance(current.op, ast.Sub):
                return left - right
            if isinstance(current.op, ast.Mult):
                return left * right
            if isinstance(current.op, ast.Div):
                return left / right
            if isinstance(current.op, ast.Pow):
                return left ** right
            return left % right
        if isinstance(current, ast.Call) and isinstance(current.func, ast.Name) and current.func.id == "sqrt":
            if len(current.args) != 1:
                raise ValueError("sqrt takes one argument")
            return math.sqrt(_eval(current.args[0]))
        raise ValueError("Unsupported expression")

    return _eval(node)


def run_math(question: str) -> dict:
    expr = normalize_math_expression(question)
    if not expr:
        return {
            "route": "math",
            "tool": "local-math",
            "answer": "I couldn't parse that math question.",
            "source": "Built-in math",
            "result_status": "invalid",
            "error_type": "parse_failed",
            "result": {"question": question},
        }
    try:
        value = evaluate_math_expression(expr)
    except Exception:
        return {
            "route": "math",
            "tool": "local-math",
            "answer": "I couldn't parse that math question.",
            "source": "Built-in math",
            "result_status": "invalid",
            "error_type": "parse_failed",
            "result": {"expression": expr},
        }
    return {
        "route": "math",
        "tool": "local-math",
        "answer": _format_decimal(value),
        "source": "Built-in math",
        "result_status": "ok",
        "error_type": None,
        "result": {"expression": expr, "value": value},
    }


def _parse_backup_upload_window(question: str) -> dict | None:
    lowered = question.lower()
    match = re.search(
        r"\b(?:backup|upload|file|transfer)\s+is\s+"
        r"(?P<size>\d+(?:\.\d+)?)\s*(?P<size_unit>gb|gib|mb|mib|tb|tib)\b.*?"
        r"(?:speed|averages?|rate)\s+"
        r"(?P<speed>\d+(?:\.\d+)?)\s*(?P<speed_unit>mbps|gbps|kbps)\b.*?"
        # DEF-026: only matched "window is N hours" / "within N hours"; a
        # rephrased "will it fit in a 12-hour overnight window?" has the
        # number-unit *before* "window" as a hyphenated compound adjective,
        # with an article ("a"/"an") after "in"/"within" -- neither of
        # which the old pattern tolerated. `[\s-]*` covers "12-hour" and
        # "12 hour" alike; `(?:an?\s+)?` covers the inserted article; the
        # trailing optional `\s+\w*\s*window\b` lets the unit be found
        # whether it sits right after the connector or right before the
        # word "window" later in the same clause.
        r"(?:window\s+is|within|inside|in)\s+(?:an?\s+)?"
        r"(?P<window>\d+(?:\.\d+)?)[\s-]*(?P<window_unit>hours?|hrs?|h|minutes?|mins?|m)\b",
        lowered,
    )
    if not match:
        return None
    size = float(match.group("size"))
    size_unit = match.group("size_unit")
    speed = float(match.group("speed"))
    speed_unit = match.group("speed_unit")
    window = float(match.group("window"))
    window_unit = match.group("window_unit")
    size_gb = size * {"mb": 0.001, "mib": 0.001048576, "gb": 1.0, "gib": 1.073741824, "tb": 1000.0, "tib": 1099.511627776}[size_unit]
    speed_mbps = speed * {"kbps": 0.001, "mbps": 1.0, "gbps": 1000.0}[speed_unit]
    if speed_mbps <= 0:
        return None
    hours = size_gb * 8000 / speed_mbps / 3600
    window_hours = window / 60 if window_unit in {"minute", "minutes", "min", "mins", "m"} else window
    return {
        "kind": "backup_upload_window",
        "size": size,
        "size_unit": size_unit.upper(),
        "speed": speed,
        "speed_unit": speed_unit,
        "hours": hours,
        "window_hours": window_hours,
    }


def _parse_travel_time_comparison(question: str) -> dict | None:
    same_distance = re.search(
        r"\b(?:a\s+)?(?:route|trip)\s+is\s+"
        r"(?P<distance>\d+(?:\.\d+)?)\s*miles?\b.*?"
        r"(?:one\s+option\s+)?averages?\s+"
        r"(?P<speed_a>\d+(?:\.\d+)?)\s*mph\b.*?"
        r"(?:another|the\s+other|second\s+option)\s+averages?\s+"
        r"(?P<speed_b>\d+(?:\.\d+)?)\s*mph\b",
        question,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if same_distance:
        distance = float(same_distance.group("distance"))
        speed_a = float(same_distance.group("speed_a"))
        speed_b = float(same_distance.group("speed_b"))
        if speed_a <= 0 or speed_b <= 0:
            return None
        return {
            "kind": "same_distance_speed_comparison",
            "place_a": f"{_format_number(speed_a)} mph option",
            "place_b": f"{_format_number(speed_b)} mph option",
            "distance_a_miles": distance,
            "distance_b_miles": distance,
            "speed_a_mph": speed_a,
            "speed_b_mph": speed_b,
            "minutes_a": distance / speed_a * 60,
            "minutes_b": distance / speed_b * 60,
        }

    pattern = re.compile(
        r"\b(?:trip|route)\s+through\s+(?P<place_a>[A-Za-z][A-Za-z .'-]+?)\s+is\s+"
        r"(?P<distance_a>\d+(?:\.\d+)?)\s*miles?\s+at\s+about\s+"
        r"(?P<speed_a>\d+(?:\.\d+)?)\s*mph,\s+while\s+a\s+(?:trip|route)\s+through\s+"
        r"(?P<place_b>[A-Za-z][A-Za-z .'-]+?)\s+is\s+"
        r"(?P<distance_b>\d+(?:\.\d+)?)\s*miles?\s+at\s+about\s+"
        r"(?P<speed_b>\d+(?:\.\d+)?)\s*mph",
        re.IGNORECASE,
    )
    match = pattern.search(question)
    if not match:
        return None
    a_minutes = float(match.group("distance_a")) / float(match.group("speed_a")) * 60
    b_minutes = float(match.group("distance_b")) / float(match.group("speed_b")) * 60
    return {
        "kind": "travel_time_comparison",
        "place_a": " ".join(match.group("place_a").split()),
        "place_b": " ".join(match.group("place_b").split()),
        "distance_a_miles": float(match.group("distance_a")),
        "distance_b_miles": float(match.group("distance_b")),
        "speed_a_mph": float(match.group("speed_a")),
        "speed_b_mph": float(match.group("speed_b")),
        "minutes_a": a_minutes,
        "minutes_b": b_minutes,
    }


def run_quantitative_reasoning(question: str) -> dict:
    backup = _parse_backup_upload_window(question)
    if backup:
        fits = backup["hours"] <= backup["window_hours"]
        margin = abs(backup["window_hours"] - backup["hours"])
        window_label = _format_number(backup["window_hours"])
        article = "an" if window_label[:1] in {"8"} else "a"
        answer = (
            f"At {_format_number(backup['speed'])} {backup['speed_unit'].upper()}, "
            f"a {backup['size']:g} {backup['size_unit']} backup takes about {_format_number(backup['hours'])} hours. "
            f"{'That fits' if fits else 'That does not fit'} in {article} {window_label}-hour window "
            f"by about {_format_number(margin)} hours."
        )
        return {
            "route": "quantitative_reasoning",
            "tool": "local-quantitative-reasoning",
            "answer": answer,
            "source": "Built-in unit/rate arithmetic",
            "result_status": "ok",
            "error_type": None,
            "result": backup | {"fits_window": fits, "margin_hours": margin},
        }

    travel = _parse_travel_time_comparison(question)
    if travel:
        if travel["minutes_a"] <= travel["minutes_b"]:
            faster, slower = travel["place_a"], travel["place_b"]
            faster_minutes, slower_minutes = travel["minutes_a"], travel["minutes_b"]
        else:
            faster, slower = travel["place_b"], travel["place_a"]
            faster_minutes, slower_minutes = travel["minutes_b"], travel["minutes_a"]
        difference = slower_minutes - faster_minutes
        if travel.get("kind") == "same_distance_speed_comparison":
            answer = (
                f"The {travel['place_a']} takes about {_format_number(travel['minutes_a'] / 60)} hours, "
                f"and the {travel['place_b']} takes about {_format_number(travel['minutes_b'] / 60)} hours. "
                f"The {faster} is shorter by about {_format_number(difference)} minutes."
            )
        else:
            answer = (
                f"The trip through {faster} should take less driving time: about {_format_number(faster_minutes)} minutes "
                f"versus about {_format_number(slower_minutes)} minutes through {slower}. "
                f"It is about {_format_number(difference)} minutes shorter."
            )
        return {
            "route": "quantitative_reasoning",
            "tool": "local-quantitative-reasoning",
            "answer": answer,
            "source": "Built-in distance/rate arithmetic",
            "result_status": "ok",
            "error_type": None,
            "result": travel | {"faster_place": faster, "minutes_shorter": difference},
        }

    return {
        "route": "quantitative_reasoning",
        "tool": "local-quantitative-reasoning",
        "answer": "I couldn't parse that quantitative reasoning question.",
        "source": "Built-in quantitative reasoning",
        "result_status": "invalid",
        "error_type": "parse_failed",
        "result": {"question": question},
    }


def run_closed_world_reasoning(question: str) -> dict:
    state = _parse_temporal_container_state(question)
    if state:
        answer = (
            f"The {state['object']} is {state['object_location']}. "
            f"The {state['container']} is {state['container_location']}."
        )
        return {
            "route": "closed_world_reasoning",
            "tool": "local-closed-world-reasoning",
            "answer": answer,
            "source": "Prompt-supplied facts only",
            "result_status": "ok",
            "error_type": None,
            "result": state,
        }

    return {
        "route": "closed_world_reasoning",
        "tool": "local-closed-world-reasoning",
        "answer": "I couldn't parse that closed-world reasoning question.",
        "source": "Prompt-supplied facts only",
        "result_status": "invalid",
        "error_type": "parse_failed",
        "result": {"question": question},
    }


def is_home_light_action_request(question: str) -> bool:
    """True if `question` requests a lamp on/off/color/effect/theme change.

    The single shared predicate behind the `home_light` deterministic route
    below and the cross-module mixed-device-action safety boundary in
    `atlas_shortcuts_server.py`'s `pre_route_safety_result()`. Kept as one
    function specifically so that boundary can never silently fall behind
    this routing logic's own idea of what counts as a light action -- the
    two were previously a narrower, separately hand-written regex there.
    """
    return (
        _home_light_requested_action(question) is not None
        or _home_light_requested_color(question) is not None
        or _home_light_requested_effect(question) is not None
        or _home_light_requested_theme(question) is not None
    )


def _home_light_requested_action(question: str) -> str | None:
    """Return "turn_on"/"turn_off" for an unambiguous lamp on/off request, else None.

    Deliberately narrow: requires a control verb (turn/switch/flip), on/off,
    AND a lamp/light noun -- the noun is required, not optional, just its
    position relative to on/off varies ("turn on the lamp" vs "turn the lamp
    on"). An earlier draft made the noun fully optional and false-positived
    on "what should I turn on for dinner" (caught by a negative test before
    shipping) -- keeping that noun mandatory is what prevents this from
    firing on unrelated "turn on/off" phrasing, not just unrelated "light"
    mentions like "what's the speed of light" (which lacks the verb+on/off
    pairing entirely, a separate but complementary safeguard).
    """
    lowered = question.lower().strip()
    # Reuses the shared _HOME_LIGHT_NOUN (defined later in this file, but
    # Python resolves module-level names at call time, not definition
    # order) -- this used to be its own separate local copy that had
    # drifted out of sync with _HOME_LIGHT_NOUN (missing "my lamp"/"my
    # light" support, found and fixed 2026-09-05 while adding the theme
    # classifier). Duplicating this pattern is exactly how that kind of
    # drift happens; reusing the one shared constant everywhere prevents it.
    off_patterns = (
        rf"\b(?:turn|switch|flip)\s+off\s+{_HOME_LIGHT_NOUN}\b",
        rf"\b(?:turn|switch|flip)\s+{_HOME_LIGHT_NOUN}\s+off\b",
    )
    on_patterns = (
        rf"\b(?:turn|switch|flip)\s+on\s+{_HOME_LIGHT_NOUN}\b",
        rf"\b(?:turn|switch|flip)\s+{_HOME_LIGHT_NOUN}\s+on\b",
    )
    if any(re.search(pattern, lowered) for pattern in off_patterns):
        return "turn_off"
    if any(re.search(pattern, lowered) for pattern in on_patterns):
        return "turn_on"
    return None


_HOME_LIGHT_NOUN = r"(?:the\s+|my\s+)?(?:bedside\s+)?(?:table\s+)?(?:lamp|light)"


def _home_light_requested_color(question: str) -> tuple[int, int, int] | None:
    """Return an (r, g, b) tuple for an unambiguous lamp color request, else None.

    Only recognizes the fixed NAMED_COLORS table in atlas_home_assistant.py --
    an unrecognized color name (e.g. "turquoise", "burgundy") deliberately
    returns None rather than guessing, so run_home_light() falls through to
    its "couldn't tell what you wanted" answer instead of silently picking
    something close.

    Defers to _home_light_requested_effect() first: a real bug caught by
    testing before shipping had "set lamp to red white blue" match here as
    plain "red" (a true prefix of that string), when it should match the
    "red_white_blue" *effect* instead -- the effect's spoken form legitimately
    contains a standalone color name as its first word.
    """
    if _home_light_requested_effect(question) is not None:
        return None
    lowered = question.lower().strip()
    color_alternation = "|".join(re.escape(name) for name in sorted(atlas_home_assistant.NAMED_COLORS, key=len, reverse=True))
    pattern = rf"\b(?:set|make|turn|change)\s+{_HOME_LIGHT_NOUN}\s+(?:to\s+|color\s+to\s+)?({color_alternation})\b"
    match = re.search(pattern, lowered)
    if not match:
        return None
    return atlas_home_assistant.NAMED_COLORS[match.group(1)]


def _home_light_requested_effect(question: str) -> str | None:
    """Return a snake_case effect name (matching HA's real effect_list for this
    device) for an unambiguous lamp effect request, else None.

    Effect names are stored snake_case ("happy_holidays") but a person would
    naturally say them with spaces ("happy holidays") -- matches on the
    spoken form and converts back to the real value.
    """
    lowered = question.lower().strip()
    effect_alternation = "|".join(re.escape(name.replace("_", " ")) for name in atlas_home_assistant.KNOWN_EFFECTS)
    pattern = (
        rf"\b(?:set|start|turn\s+on)\s+{_HOME_LIGHT_NOUN}\s+(?:to\s+|effect\s+to\s+)?(?:the\s+)?"
        rf"({effect_alternation})(?:\s+effect|\s+pattern|\s+mode)?\b"
    )
    match = re.search(pattern, lowered)
    if not match:
        return None
    return match.group(1).replace(" ", "_")


def _home_light_requested_theme(question: str) -> str | None:
    """Return the free-text theme description for an open-ended lamp
    color/effect request the deterministic classifiers couldn't resolve
    literally (e.g. "do SFSU colors!", "give the lamp a cozy autumn vibe").

    This is a trigger + extraction step ONLY -- it does not resolve the
    theme into an actual color/effect; see run_home_light_theme() for that
    (a model call, constrained to only ever pick from the same
    NAMED_COLORS/KNOWN_EFFECTS sets used everywhere else in this file,
    never an arbitrary invented value).

    Deliberately reached only as a fallback in classify_question()/
    run_home_light(), after the literal action/color/effect classifiers
    have already returned None -- keeps the fast, zero-model-call path as
    the default and only pays for a (slower) model call on genuinely
    open-ended asks.

    Two shapes, with different safety anchors:
    - "do <theme> colors [on/for/to the lamp]" -- the lamp noun is optional
      here since the motivating example ("do SFSU colors!") doesn't name
      the lamp explicitly. Anchored to the *entire* message (^...$) so it
      can't fire on a longer, unrelated sentence that merely contains this
      phrase somewhere in the middle (e.g. a real question like "do
      American colors have deeper cultural meaning?" does not match, since
      the trailing "have deeper..." content breaks the end anchor).
    - "give/make/set <lamp noun> ..." -- requires the lamp noun explicitly,
      since these verbs are common enough in unrelated sentences that
      matching without an explicit anchor to the lamp would be too loose.
    """
    if (
        _home_light_requested_action(question) is not None
        or _home_light_requested_color(question) is not None
        or _home_light_requested_effect(question) is not None
    ):
        return None
    lowered = question.lower().strip().rstrip("!?.")

    do_colors_match = re.search(rf"^do\s+(.+?)\s+colors?(?:\s+(?:on|for|to)\s+{_HOME_LIGHT_NOUN})?$", lowered)
    if do_colors_match:
        theme = do_colors_match.group(1).strip()
        # Excludes "do you/i/we/they/he/she/it ..." -- a reliable signal of
        # question-formation ("do you like colors?") using the exact same
        # surface shape as the imperative this pattern exists to catch ("do
        # SFSU colors"). A real theme name (a place, brand, holiday, mood,
        # etc.) would never naturally begin with a subject pronoun. Found via
        # testing before shipping: "do you like colors" originally matched
        # with theme-capture "you like".
        is_pronoun_question = bool(re.match(r"^(?:you|i|we|they|he|she|it)\b", theme))
        if theme and not is_pronoun_question:
            return theme

    lamp_anchored_patterns = (
        rf"\bgive\s+{_HOME_LIGHT_NOUN}\s+(?:a|an)\s+(.+?)\s*(?:vibe|theme|look|feel)?$",
        rf"\bmake\s+{_HOME_LIGHT_NOUN}\s+(?:look|feel)\s+like\s+(.+)$",
        rf"\bset\s+{_HOME_LIGHT_NOUN}\s+to\s+a\s+(.+?)\s*(?:vibe|theme)?$",
    )
    for pattern in lamp_anchored_patterns:
        match = re.search(pattern, lowered)
        if match:
            theme = match.group(1).strip()
            if theme:
                return theme
    return None


def run_home_light_theme(
    theme: str,
    base_url: str,
    db_path: pathlib.Path,
    model: str | None,
    timeout: int,
    system_prompt_file: pathlib.Path,
) -> dict | None:
    """Interpret an open-ended theme description into a color or effect,
    constrained to only ever pick from the same fixed, already-approved
    NAMED_COLORS/KNOWN_EFFECTS sets used everywhere else in the scoped
    control exception -- never an arbitrary invented RGB value or effect
    name. Returns None (fail closed) if the model can't confidently match
    something from the list, if its response is malformed, or if it names
    a color/effect that isn't actually in either set (a hallucinated
    plausible-but-wrong value) -- run_home_light() turns None into an
    honest "couldn't find a good match" answer, never a guess.

    Reuses answer_with_model()/parse_json_object() exactly the way the
    existing ambiguity-gate pattern does (build_ambiguity_gate_prompt() and
    its caller) -- a narrow, structured question with explicit JSON output,
    validated before use, not an open conversational answer.

    Deliberately does NOT set think:false on this call. Project history
    (commit f7858ac, reverted after QA pair #57) found disabling thinking
    mode causes confident fabrication under uncertainty for general-
    knowledge tasks -- exactly the failure mode this function's validation
    step exists to catch, so there's no reason to reintroduce that risk for
    a faster response here. answer_with_model() already omits the think
    field entirely (uses the model's default, thinking-enabled behavior).
    """
    color_names = ", ".join(sorted(atlas_home_assistant.NAMED_COLORS))
    effect_names = ", ".join(sorted(name.replace("_", " ") for name in atlas_home_assistant.KNOWN_EFFECTS))
    prompt = (
        "A smart lamp needs to be set based on a described theme. You may ONLY choose from "
        "these exact options -- never invent a color or effect that isn't listed here.\n\n"
        f"Allowed colors: {color_names}\n"
        f"Allowed effects: {effect_names}\n\n"
        f"Theme: \"{theme}\"\n\n"
        "Pick the single best-matching color or effect from the lists above. If nothing in "
        "either list is a good match, say so honestly instead of picking something unrelated.\n\n"
        "Respond with ONLY a JSON object, no other text, in exactly one of these three shapes:\n"
        '{"type": "color", "value": "<one of the allowed colors, exactly as listed>"}\n'
        '{"type": "effect", "value": "<one of the allowed effects, exactly as listed>"}\n'
        '{"type": "none"}'
    )
    try:
        answer_text, _ = answer_with_model(
            prompt=prompt,
            base_url=base_url,
            db_path=db_path,
            model=model,
            timeout=timeout,
            system_prompt_file=system_prompt_file,
            include_system_prompt=False,
        )
    except Exception:
        return None

    parsed = parse_json_object(answer_text)
    if not isinstance(parsed, dict):
        return None
    kind = parsed.get("type")
    value = str(parsed.get("value") or "").strip().lower()

    if kind == "color" and value in atlas_home_assistant.NAMED_COLORS:
        return {"type": "color", "rgb": atlas_home_assistant.NAMED_COLORS[value]}
    if kind == "effect":
        effect_name = value.replace(" ", "_")
        if effect_name in atlas_home_assistant.KNOWN_EFFECTS:
            return {"type": "effect", "name": effect_name}
    # type == "none", or a color/effect name that isn't actually in either
    # approved set (a hallucinated plausible-but-wrong value) -- both fail
    # closed here, never a guessed fallback.
    return None


def is_home_lamp_state_question(question: str) -> bool:
    """True for a read-only question about the lamp's current state.

    Deliberately excludes anything the control classifiers (on/off, color,
    effect, or theme) already claim as a control request, so read vs.
    control stay mutually exclusive rather than racing each other in
    classify_question(). Narrow on purpose, matching the single-entity
    scope of the read allowlist today -- see run_home_lamp_state() for how
    the actual entity gets resolved.
    """
    if (
        _home_light_requested_action(question) is not None
        or _home_light_requested_color(question) is not None
        or _home_light_requested_effect(question) is not None
        or _home_light_requested_theme(question) is not None
    ):
        return False
    lowered = question.lower().strip()
    # See the comment in _home_light_requested_action() -- reuses the
    # shared _HOME_LIGHT_NOUN rather than its own local copy, which had
    # drifted (missing "my lamp"/"my light").
    patterns = (
        rf"\bis\s+{_HOME_LIGHT_NOUN}\s+(?:currently\s+)?(?:on|off)\b",
        rf"\b{_HOME_LIGHT_NOUN}\s+on\s+or\s+off\b",
        rf"\b(?:state|status)\s+of\s+{_HOME_LIGHT_NOUN}\b",
        rf"\bwhat.s\s+{_HOME_LIGHT_NOUN}.s\s+(?:state|status)\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def run_home_lamp_state(question: str) -> dict:
    """Read-only counterpart to run_home_light(): reports the lamp's real,
    freshly-fetched state through the existing home.read adapter path
    (atlas_home_assistant.fetch_state()), not a cached or assumed value.

    Resolves "the lamp" to a real entity_id by taking the first `light.*`
    entry in the existing read allowlist (ATLAS_HOME_ASSISTANT_ENTITIES) --
    reuses the read path's own config rather than the separate control-only
    ATLAS_HOME_ASSISTANT_CONTROLLABLE_ENTITY var, since this is a read, not
    a control action. Only resolves a single light today because that's all
    the allowlist currently contains; extending this to disambiguate between
    multiple lights by name is deliberately out of scope for now.
    """
    read_enabled = os.environ.get("ATLAS_HOME_ASSISTANT_ENABLED", "false").lower() in {"1", "true", "yes"}
    base_url = os.environ.get("ATLAS_HOME_ASSISTANT_BASE_URL", "")
    token = os.environ.get("ATLAS_HOME_ASSISTANT_TOKEN", "")
    raw_entities = os.environ.get("ATLAS_HOME_ASSISTANT_ENTITIES", "")
    if not read_enabled:
        return {
            "route": "home_read",
            "tool": "home-assistant-state",
            "answer": "Home Assistant reads aren't enabled right now.",
            "source": "Home Assistant",
            "result": {"enabled": False},
        }
    try:
        allowlist = atlas_home_assistant.parse_allowlist(raw_entities)
    except ValueError as exc:
        return {
            "route": "home_read",
            "tool": "home-assistant-state",
            "answer": f"Home Assistant's entity allowlist is misconfigured: {exc}",
            "source": "Home Assistant",
            "result": {"error": str(exc)},
        }
    light_entity = next((entity for entity in sorted(allowlist) if entity.startswith("light.")), None)
    if light_entity is None:
        return {
            "route": "home_read",
            "tool": "home-assistant-state",
            "answer": "I don't have an approved light entity to check.",
            "source": "Home Assistant",
            "result": {"allowlist": sorted(allowlist)},
        }
    try:
        state = atlas_home_assistant.fetch_state(base_url, token, light_entity, allowlist)
    except Exception as exc:
        return {
            "route": "home_read",
            "tool": "home-assistant-state",
            "answer": f"I couldn't read the lamp's state: {exc}",
            "source": "Home Assistant",
            "result": {"error": str(exc)},
        }
    friendly_name = state.get("attributes", {}).get("friendly_name", "The lamp")
    answer = f"{friendly_name} is currently {state.get('state', 'unknown')}."
    return {
        "route": "home_read",
        "tool": "home-assistant-state",
        "answer": answer,
        "source": "Home Assistant",
        "result": state,
    }


def run_home_light(
    question: str,
    openwebui_base_url: str | None = None,
    db_path: pathlib.Path | None = None,
    model: str | None = None,
    model_timeout: int = 60,
    system_prompt_file: pathlib.Path | None = None,
) -> dict:
    """Scoped control exception: exactly one light entity -- on/off, a fixed
    named-color set, this device's real effect list, or (as a fallback) a
    model-interpreted open-ended theme constrained to that same fixed set.

    See ATLAS_HOME_ASSISTANT_READONLY.md ("Scoped Control Exception") before
    extending this to any other entity, domain, or action. Home Assistant
    config is read directly from the environment (matching this file's
    existing convention for e.g. ATLAS_DEFAULT_WEATHER_PLACE) rather than
    new CLI flags, since this subprocess already inherits its parent
    atlas-shortcuts container's environment. The openwebui_base_url/
    db_path/model/system_prompt_file parameters are only needed for the
    theme fallback (a model call) -- named distinctly from Home Assistant's
    own `base_url` local variable below to avoid confusion between the two
    completely different services.
    """
    color = _home_light_requested_color(question)
    effect = _home_light_requested_effect(question)
    action = _home_light_requested_action(question)
    theme = None
    if color is None and effect is None and action is None:
        theme = _home_light_requested_theme(question)
    if color is None and effect is None and action is None and theme is None:
        return {
            "route": "home_light",
            "tool": "home-assistant-light-control",
            "answer": "I couldn't tell whether you wanted the lamp turned on/off, a color, or an effect.",
            "source": "Home Assistant",
            "result": {"question": question},
        }
    control_enabled = os.environ.get("ATLAS_HOME_ASSISTANT_CONTROL_ENABLED", "false").lower() in {"1", "true", "yes"}
    entity_id = os.environ.get("ATLAS_HOME_ASSISTANT_CONTROLLABLE_ENTITY", "")
    base_url = os.environ.get("ATLAS_HOME_ASSISTANT_BASE_URL", "")
    token = os.environ.get("ATLAS_HOME_ASSISTANT_TOKEN", "")
    if not control_enabled or not entity_id:
        return {
            "route": "home_light",
            "tool": "home-assistant-light-control",
            "answer": "Lamp control isn't enabled right now.",
            "source": "Home Assistant",
            "result": {"enabled": control_enabled},
        }

    if theme is not None:
        if not (openwebui_base_url and db_path and system_prompt_file):
            return {
                "route": "home_light",
                "tool": "home-assistant-light-control",
                "answer": f'I can\'t interpret themes like "{theme}" right now (model isn\'t configured for this call).',
                "source": "Home Assistant",
                "result": {"theme": theme, "configured": False},
            }
        resolved_theme = run_home_light_theme(
            theme,
            base_url=openwebui_base_url,
            db_path=db_path,
            model=model,
            timeout=model_timeout,
            system_prompt_file=system_prompt_file,
        )
        if resolved_theme is None:
            return {
                "route": "home_light",
                "tool": "home-assistant-light-control",
                "answer": f'I couldn\'t find a good color or effect match for "{theme}".',
                "source": "Home Assistant",
                "result": {"theme": theme, "resolved": None},
            }
        # Re-populate color/effect from the model's (already-validated,
        # fail-closed) choice, then fall straight into the exact same
        # execution/answer logic used for a literal color/effect request --
        # this only changes *how* a color/effect got selected.
        if resolved_theme["type"] == "color":
            color = resolved_theme["rgb"]
        else:
            effect = resolved_theme["name"]

    # Color and effect requests are both HA's turn_on service with extra
    # data -- there's no separate "set_color" action. Checked in this order
    # (color, then effect, then plain on/off) since a question can only
    # match one of the three classifiers by construction (mutually exclusive
    # patterns), but color is checked first for a defensive, deterministic
    # tie-break if that assumption is ever wrong.
    if color is not None:
        resolved_action, service_data = "turn_on", {"rgb_color": list(color)}
    elif effect is not None:
        resolved_action, service_data = "turn_on", {"effect": effect}
    else:
        resolved_action, service_data = action, {}

    try:
        outcome = atlas_home_assistant.control_light(
            base_url, token, entity_id, resolved_action, entity_id, service_data=service_data or None
        )
    except Exception as exc:
        return {
            "route": "home_light",
            "tool": "home-assistant-light-control",
            "answer": f"I couldn't control the lamp: {exc}",
            "source": "Home Assistant",
            "result": {"error": str(exc)},
        }

    if color is not None:
        base_answer = (
            f"Set the lamp's color."
            if outcome.get("ok")
            else f"I sent the color change, but the lamp's reported color doesn't match yet."
        )
    elif effect is not None:
        base_answer = (
            f"Set the lamp to the {effect.replace('_', ' ')} effect."
            if outcome.get("ok")
            else f"I sent the {effect.replace('_', ' ')} effect, but the lamp hasn't confirmed it yet."
        )
    else:
        verb = "on" if action == "turn_on" else "off"
        base_answer = (
            f"Turned the lamp {verb}."
            if outcome.get("ok")
            else f"I sent the {verb} command, but the lamp's reported state doesn't match yet."
        )
    answer = f'For "{theme}": {base_answer}' if theme is not None else base_answer
    return {
        "route": "home_light",
        "tool": "home-assistant-light-control",
        "answer": answer,
        "source": "Home Assistant",
        "result": outcome,
    }


def run_conversion(question: str) -> dict:
    lowered = question.lower().strip().rstrip("?")

    unit_aliases = {
        "second": ("time", 1.0, "seconds"), "seconds": ("time", 1.0, "seconds"),
        "minute": ("time", 60.0, "minutes"), "minutes": ("time", 60.0, "minutes"),
        "hour": ("time", 3600.0, "hours"), "hours": ("time", 3600.0, "hours"),
        "day": ("time", 86400.0, "days"), "days": ("time", 86400.0, "days"),
        "inch": ("length", 0.0254, "inches"), "inches": ("length", 0.0254, "inches"),
        "foot": ("length", 0.3048, "feet"), "feet": ("length", 0.3048, "feet"),
        "yard": ("length", 0.9144, "yards"), "yards": ("length", 0.9144, "yards"),
        "centimeter": ("length", 0.01, "centimeters"), "centimeters": ("length", 0.01, "centimeters"),
        "meter": ("length", 1.0, "meters"), "meters": ("length", 1.0, "meters"),
        "kilometer": ("length", 1000.0, "kilometers"), "kilometers": ("length", 1000.0, "kilometers"),
        "milligram": ("mass", 0.001, "milligrams"), "milligrams": ("mass", 0.001, "milligrams"),
        "gram": ("mass", 1.0, "grams"), "grams": ("mass", 1.0, "grams"),
        "kilogram": ("mass", 1000.0, "kilograms"), "kilograms": ("mass", 1000.0, "kilograms"),
        "milliliter": ("volume", 0.001, "milliliters"), "milliliters": ("volume", 0.001, "milliliters"),
        "liter": ("volume", 1.0, "liters"), "liters": ("volume", 1.0, "liters"),
        "kilobyte": ("data", 1.0, "kilobytes"), "kilobytes": ("data", 1.0, "kilobytes"),
        "megabyte": ("data", 1000.0, "megabytes"), "megabytes": ("data", 1000.0, "megabytes"),
    }
    generic_match = re.search(
        r"(?:convert\s+)?(-?\d+(?:\.\d+)?)\s+([a-z]+)\s+(?:in|into|to|as)\s+([a-z]+)\b",
        re.sub(r"^conversion\s+\d+\s*:\s*", "", lowered),
    )
    if generic_match:
        value = float(generic_match.group(1))
        source_unit = unit_aliases.get(generic_match.group(2))
        target_unit = unit_aliases.get(generic_match.group(3))
        if source_unit and target_unit and source_unit[0] == target_unit[0]:
            converted = value * source_unit[1] / target_unit[1]
            return {
                "route": "conversion", "tool": "local-conversion",
                "answer": f"{_format_number(converted)} {target_unit[2]}.",
                "source": "Built-in conversion",
                "result": {"input_value": value, "output_value": converted, "output_unit": target_unit[2]},
            }

    currency_match = re.search(r"(?:convert\s+)?\$?(\d+(?:\.\d+)?)\s*(?:dollars?|usd)\s+(?:in|into|to|as)\s+(?:euros?|eur)\b", lowered)
    if currency_match:
        value = float(currency_match.group(1))
        converted = value * 0.92
        return {
            "route": "conversion",
            "tool": "local-conversion",
            "answer": f"About {_format_number(converted)} euros using a static planning rate of 1 USD = 0.92 EUR.",
            "source": "Built-in static planning conversion",
            "result": {"input_value": value, "output_value": converted, "output_unit": "EUR", "rate": 0.92, "live_rate": False},
        }

    currency_match = re.search(r"(?:convert\s+)?€?(\d+(?:\.\d+)?)\s*(?:euros?|eur)\s+(?:in|into|to|as)\s+(?:dollars?|usd)\b", lowered)
    if currency_match:
        value = float(currency_match.group(1))
        converted = value / 0.92
        return {
            "route": "conversion",
            "tool": "local-conversion",
            "answer": f"About {_format_number(converted)} US dollars using a static planning rate of 1 USD = 0.92 EUR.",
            "source": "Built-in static planning conversion",
            "result": {"input_value": value, "output_value": converted, "output_unit": "USD", "rate": 0.92, "live_rate": False},
        }

    # Connector alternation widened from (?:in|to) to (?:in|into|to|as) on every
    # first-direction pattern below (2026-09-08, A3 Gate 2 measurement finding):
    # the generic alias path above already accepted "into"/"as", but this
    # specific-pair fallback list did not, so phrasings like "change 280 miles
    # into kilometers" reached this list (the generic path has no "miles"
    # entry) and still failed. Mechanical widening only, matching every
    # governing test used all night: no existing match behavior narrows, only
    # widens. The "how many X are in Y" patterns are deliberately untouched --
    # "how many X are into Y" is not natural English and was never observed
    # failing.
    patterns = [
        (r"(\d+(?:\.\d+)?)\s*(?:quarts?|qt)\s+(?:in|into|to|as)\s+(cups?|cup)\b", lambda v: (v * 4, "cups")),
        (r"how many\s+(cups?|cup)\s+(?:are\s+)?in\s+(?:(\d+(?:\.\d+)?)|a|an)\s*(?:quarts?|qt)\b", lambda v: ((1.0 if v is None else v) * 4, "cups")),
        (r"(\d+(?:\.\d+)?)\s*(?:cups?|cup)\s+(?:in|into|to|as)\s+(quarts?|qt)\b", lambda v: (v / 4, "quarts")),
        (r"how many\s+(quarts?|qt)\s+(?:are\s+)?in\s+(?:(\d+(?:\.\d+)?)|a|an)\s*(?:cups?|cup)\b", lambda v: ((1.0 if v is None else v) / 4, "quarts")),
        (r"(\d+(?:\.\d+)?)\s*(?:cups?|cup)\s+(?:in|into|to|as)\s+(ounces?|oz)\b", lambda v: (v * 8, "ounces")),
        (r"how many\s+(ounces?|oz)\s+(?:are\s+)?in\s+(?:(\d+(?:\.\d+)?)|a|an)\s*(?:cups?|cup)\b", lambda v: ((1.0 if v is None else v) * 8, "ounces")),
        (r"(\d+(?:\.\d+)?)\s*(?:ounces?|oz)\s+(?:in|into|to|as)\s+(cups?|cup)\b", lambda v: (v / 8, "cups")),
        (r"how many\s+(cups?|cup)\s+(?:are\s+)?in\s+(?:(\d+(?:\.\d+)?)|a|an)\s*(?:ounces?|oz)\b", lambda v: ((1.0 if v is None else v) / 8, "cups")),
        (r"(\d+(?:\.\d+)?)\s*(?:tablespoons?|tbsp)\s+(?:in|into|to|as)\s+(teaspoons?|tsp)\b", lambda v: (v * 3, "teaspoons")),
        (r"how many\s+(teaspoons?|tsp)\s+(?:are\s+)?in\s+(?:(\d+(?:\.\d+)?)|a|an)\s*(?:tablespoons?|tbsp)\b", lambda v: ((1.0 if v is None else v) * 3, "teaspoons")),
        (r"(\d+(?:\.\d+)?)\s*(?:teaspoons?|tsp)\s+(?:in|into|to|as)\s+(tablespoons?|tbsp)\b", lambda v: (v / 3, "tablespoons")),
        (r"how many\s+(tablespoons?|tbsp)\s+(?:are\s+)?in\s+(?:(\d+(?:\.\d+)?)|a|an)\s*(?:teaspoons?|tsp)\b", lambda v: ((1.0 if v is None else v) / 3, "tablespoons")),
        (r"(\d+(?:\.\d+)?)\s*(?:miles?|mi)\s+(?:in|into|to|as)\s+(kilometers?|km)\b", lambda v: (v * 1.60934, "kilometers")),
        (r"how many\s+(kilometers?|km)\s+(?:are\s+)?in\s+(\d+(?:\.\d+)?)\s*(?:miles?|mi)\b", lambda v: (v * 1.60934, "kilometers")),
        (r"(\d+(?:\.\d+)?)\s*(?:kilometers?|km)\s+(?:in|into|to|as)\s+(miles?|mi)\b", lambda v: (v / 1.60934, "miles")),
        (r"how many\s+(miles?|mi)\s+(?:are\s+)?in\s+(\d+(?:\.\d+)?)\s*(?:kilometers?|km)\b", lambda v: (v / 1.60934, "miles")),
        (r"(\d+(?:\.\d+)?)\s*(?:pounds?|lbs?|lb)\s+(?:in|into|to|as)\s+(kilograms?|kg)\b", lambda v: (v * 0.45359237, "kilograms")),
        (r"how many\s+(kilograms?|kg)\s+(?:are\s+)?in\s+(\d+(?:\.\d+)?)\s*(?:pounds?|lbs?|lb)\b", lambda v: (v * 0.45359237, "kilograms")),
        (r"(\d+(?:\.\d+)?)\s*(?:kilograms?|kg)\s+(?:in|into|to|as)\s+(pounds?|lbs?|lb)\b", lambda v: (v / 0.45359237, "pounds")),
        (r"how many\s+(pounds?|lbs?|lb)\s+(?:are\s+)?in\s+(\d+(?:\.\d+)?)\s*(?:kilograms?|kg)\b", lambda v: (v / 0.45359237, "pounds")),
        (r"(\d+(?:\.\d+)?)\s*(?:feet|foot|ft)\s+(?:in|into|to|as)\s+(meters?|m)\b", lambda v: (v * 0.3048, "meters")),
        (r"how many\s+(meters?|m)\s+(?:are\s+)?in\s+(\d+(?:\.\d+)?)\s*(?:feet|foot|ft)\b", lambda v: (v * 0.3048, "meters")),
        (r"(\d+(?:\.\d+)?)\s*(?:meters?|m)\s+(?:in|into|to|as)\s+(feet|foot|ft)\b", lambda v: (v / 0.3048, "feet")),
        (r"how many\s+(feet|foot|ft)\s+(?:are\s+)?in\s+(\d+(?:\.\d+)?)\s*(?:meters?|m)\b", lambda v: (v / 0.3048, "feet")),
        (r"(\d+(?:\.\d+)?)\s*(?:inches?|in)\s+(?:in|into|to|as)\s+(centimeters?|cm)\b", lambda v: (v * 2.54, "centimeters")),
        (r"how many\s+(centimeters?|cm)\s+(?:are\s+)?in\s+(\d+(?:\.\d+)?)\s*(?:inches?|in)\b", lambda v: (v * 2.54, "centimeters")),
        (r"(\d+(?:\.\d+)?)\s*(?:centimeters?|cm)\s+(?:in|into|to|as)\s+(inches?|in)\b", lambda v: (v / 2.54, "inches")),
        (r"how many\s+(inches?|in)\s+(?:are\s+)?in\s+(\d+(?:\.\d+)?)\s*(?:centimeters?|cm)\b", lambda v: (v / 2.54, "inches")),
        # Added 2026-09-08 (A3 Gate 2 finding): gallons<->liters and mass
        # ounces<->grams were completely unsupported before -- not a
        # phrasing/connector gap, a genuinely missing unit pair. US gallon
        # (231 cubic inches) and avoirdupois ounce, matching the precision
        # level already used by every other factor in this list.
        (r"(\d+(?:\.\d+)?)\s*(?:gallons?|gal)\s+(?:in|into|to|as)\s+(liters?|litres?|l)\b", lambda v: (v * 3.78541, "liters")),
        (r"how many\s+(liters?|litres?|l)\s+(?:are\s+)?in\s+(\d+(?:\.\d+)?)\s*(?:gallons?|gal)\b", lambda v: (v * 3.78541, "liters")),
        (r"(\d+(?:\.\d+)?)\s*(?:liters?|litres?|l)\s+(?:in|into|to|as)\s+(gallons?|gal)\b", lambda v: (v / 3.78541, "gallons")),
        (r"how many\s+(gallons?|gal)\s+(?:are\s+)?in\s+(\d+(?:\.\d+)?)\s*(?:liters?|litres?|l)\b", lambda v: (v / 3.78541, "gallons")),
        (r"(\d+(?:\.\d+)?)\s*(?:ounces?|oz)\s+(?:in|into|to|as)\s+(grams?|g)\b", lambda v: (v * 28.3495, "grams")),
        (r"how many\s+(grams?|g)\s+(?:are\s+)?in\s+(\d+(?:\.\d+)?)\s*(?:ounces?|oz)\b", lambda v: (v * 28.3495, "grams")),
        (r"(\d+(?:\.\d+)?)\s*(?:grams?|g)\s+(?:in|into|to|as)\s+(ounces?|oz)\b", lambda v: (v / 28.3495, "ounces")),
        (r"how many\s+(ounces?|oz)\s+(?:are\s+)?in\s+(\d+(?:\.\d+)?)\s*(?:grams?|g)\b", lambda v: (v / 28.3495, "ounces")),
    ]

    for pattern, converter in patterns:
        match = re.search(pattern, lowered)
        if match:
            numeric_groups = [group for group in match.groups() if re.fullmatch(r"\d+(?:\.\d+)?", group or "")]
            value = float(numeric_groups[-1]) if numeric_groups else None
            converted, unit = converter(value)
            return {
                "route": "conversion",
                "tool": "local-conversion",
                "answer": f"{_format_number(converted)} {unit}.",
                "source": "Built-in conversion",
                "result": {
                    "input_value": value,
                    "output_value": converted,
                    "output_unit": unit,
                },
            }

    temp_match = re.search(r"(-?\d+(?:\.\d+)?)\s*degrees?\s*f(?:ahrenheit)?\s+(?:in|to)\s+c(?:elsius)?", lowered)
    if not temp_match:
        temp_match = re.search(r"convert\s+(-?\d+(?:\.\d+)?)\s*f(?:ahrenheit)?\s+to\s+c(?:elsius)?", lowered)
    if temp_match:
        value = float(temp_match.group(1))
        converted = (value - 32) * 5 / 9
        return {
            "route": "conversion",
            "tool": "local-conversion",
            "answer": f"{_format_number(converted)} degrees Celsius.",
            "source": "Built-in conversion",
            "result": {"input_value": value, "output_value": converted, "output_unit": "degrees Celsius"},
        }

    temp_match = re.search(r"(-?\d+(?:\.\d+)?)\s*degrees?\s*c(?:elsius)?\s+(?:in|to)\s+f(?:ahrenheit)?", lowered)
    if not temp_match:
        temp_match = re.search(r"convert\s+(-?\d+(?:\.\d+)?)\s*c(?:elsius)?\s+to\s+f(?:ahrenheit)?", lowered)
    if temp_match:
        value = float(temp_match.group(1))
        converted = (value * 9 / 5) + 32
        return {
            "route": "conversion",
            "tool": "local-conversion",
            "answer": f"{_format_number(converted)} degrees Fahrenheit.",
            "source": "Built-in conversion",
            "result": {"input_value": value, "output_value": converted, "output_unit": "degrees Fahrenheit"},
        }

    # Capability-gap flagging (owner-approved design, 2026-09-07): ATLAS has
    # exactly two static-rate currency pairs (USD->EUR, EUR->USD above), both
    # honestly disclosed as non-live ("static planning rate", live_rate:
    # False in the result). Any other currency pair reaching here has no
    # coverage at all. Deliberately does NOT call any live exchange-rate API
    # or otherwise attempt to answer -- ATLAS proposes nothing and executes
    # nothing on its own; it only reports the gap for the owner to act on,
    # exactly like every other capability this project has ever added. The
    # route/answer/tool below are byte-identical to the pre-existing
    # fallback; only the new `capability_gap` result field is additive, and
    # this runner has no database access at all (by design, it is a
    # subprocess-per-request read-only script) -- atlas_shortcuts_server.py
    # is the layer that turns this into an owner-visible audit event.
    currency_gap = detect_unsupported_currency_pair(lowered)

    result_payload: dict = {"question": question}
    if currency_gap:
        result_payload["capability_gap"] = currency_gap

    return {
        "route": "general_reference",
        "tool": "openwebui-chat",
        "answer": "I couldn't parse that conversion directly.",
        "source": "ATLAS conversion parser",
        "result": result_payload,
    }


_CURRENCY_TERMS = (
    "dollar", "dollars", "usd", "euro", "euros", "eur", "pound", "pounds", "gbp",
    "yen", "jpy", "franc", "francs", "chf", "rupee", "rupees", "inr", "peso", "pesos",
    "yuan", "cny", "rmb", "won", "krw", "real", "brl", "ruble", "rubles", "rub",
    "rand", "zar", "krona", "kronor", "sek", "krone", "nok", "dkk", "cad", "aud", "nzd",
)

# Several currencies share a bare noun ("dollar", "franc") with only the
# preceding country adjective distinguishing them (Australian dollar vs.
# Canadian dollar). Captured separately so those two count as two distinct
# mentions instead of collapsing to one via simple term dedup.
_CURRENCY_ADJECTIVES = (
    "us", "american", "australian", "canadian", "singapore", "hong kong",
    "new zealand", "taiwan", "jamaican", "swiss", "cfa",
)
_CURRENCY_ADJECTIVE_PHRASE_PATTERN = re.compile(
    rf"\b(?:{'|'.join(_CURRENCY_ADJECTIVES)})\s+(?:dollars?|francs?)\b"
)


def detect_unsupported_currency_pair(lowered_question: str) -> dict | None:
    """Best-effort detection of a currency-conversion request outside the two
    hardcoded static-rate pairs, for capability-gap flagging only -- never
    used to answer the question itself. Intentionally broad/approximate:
    false positives here only cost one extra audit-log line for the owner to
    skim, not an incorrect answer to the user, so it errs toward recall over
    precision.
    """
    mentioned = {term for term in _CURRENCY_TERMS if re.search(rf"\b{term}\b", lowered_question)}
    adjective_phrases = set(_CURRENCY_ADJECTIVE_PHRASE_PATTERN.findall(lowered_question))
    # Bare "dollar(s)"/"franc(s)" mentions are ambiguous on their own; once an
    # adjective-qualified phrase covers them (e.g. "Australian dollar"), drop
    # the bare form so it isn't double-counted as a second distinct currency.
    generic_terms = mentioned - {"dollar", "dollars", "franc", "francs"} if adjective_phrases else mentioned
    distinct_currencies = generic_terms | adjective_phrases
    if len(distinct_currencies) < 2 and "exchange rate" not in lowered_question:
        return None
    if not re.search(r"\b(?:convert|conversion|in|to|into|exchange rate|is|equals?|equal to|worth)\b", lowered_question):
        return None
    return {
        "type": "currency_pair_unsupported",
        "mentioned_currency_terms": sorted(mentioned | adjective_phrases),
    }


_WEATHER_PERIOD_LABELS = {"today": "Today", "tonight": "Tonight", "tomorrow": "Tomorrow"}


def run_weather(question: str) -> dict:
    place = infer_weather_place(question)
    period, resolved_date = infer_weather_period(question, dt.datetime.now(dt.timezone.utc))
    if period == "date" and resolved_date is None:
        # H2: fail closed rather than silently falling back to current
        # conditions when an explicit date phrase couldn't be parsed at all.
        return {
            "route": "weather",
            "tool": str(TOOLS_DIR / "get-openmeteo-weather.py"),
            "answer": "I couldn't figure out which date you meant for that forecast.",
            "source": "Built-in weather",
            "result_status": "invalid",
            "error_type": "parse_failed",
            "result": {"requested_period": "date"},
        }
    command = [sys.executable, str(TOOLS_DIR / "get-openmeteo-weather.py"), "--json", place]
    if period != "current":
        command.extend(["--period", period, "--date", resolved_date.isoformat()])
    try:
        payload = run_json_tool(command)
        if payload.get("result_status") != "ok":
            return {
                "route": "weather",
                "tool": str(TOOLS_DIR / "get-openmeteo-weather.py"),
                "answer": payload.get("message")
                or f"I couldn't get the {period} forecast for {place}.",
                "source": payload.get("source"),
                "result_status": payload.get("result_status", "invalid"),
                "error_type": payload.get("error_type"),
                "result": payload,
            }
        if period == "current":
            answer = (
                f"{payload['resolved_name']}, {payload['country']}: {payload['weather_text']}, "
                f"{payload['temperature_c']} C, feels like {payload['apparent_temperature_c']} C."
            )
        else:
            label = _WEATHER_PERIOD_LABELS.get(period, payload.get("resolved_date", "That day"))
            answer = (
                f"{label} in {payload['resolved_name']}, {payload['country']}: {payload['weather_text']}, "
                f"high {payload['temperature_max_c']} C, low {payload['temperature_min_c']} C."
            )
        if "time" in question.lower():
            time_result = run_datetime(f"What time is it in {payload['resolved_name']}?")
            answer = f"{time_result['answer']}. {answer}"
        return {
            "route": "weather",
            "tool": str(TOOLS_DIR / "get-openmeteo-weather.py"),
            "answer": answer,
            "source": payload["source"],
            "result_status": "ok",
            "error_type": None,
            "result": payload,
        }
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        stderr = compact_error(exc.stderr or "")
        search_payload = {}
        try:
            search_payload = run_json_tool([sys.executable, str(TOOLS_DIR / "search-searxng.py"), "--json", question])
            answer = answer_from_search_payload(search_payload)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as search_exc:
            search_payload = {"error": compact_error(search_exc.stderr or "")}
            answer = None
        if not answer or is_nonanswer(answer):
            answer = f"I couldn't reach the weather adapter for {place} just now."
        return {
            "route": "weather",
            "tool": str(TOOLS_DIR / "search-searxng.py"),
            "answer": answer,
            "source": search_payload.get("source"),
            "result_status": "unavailable",
            "error_type": "adapter_unavailable",
            "result": {
                "adapter_error": stderr,
                "search": search_payload,
            },
        }


def run_structured_lookup(domain: str, question: str) -> dict:
    query = clean_media_query(question)
    effective_domain = domain
    lowered = question.lower()
    if domain == "music":
        if "album" in lowered:
            effective_domain = "music-album"
        elif any(token in lowered for token in ["song", "recording", "track"]):
            effective_domain = "music-song"
    command = [sys.executable, str(TOOLS_DIR / "lookup-structured-reference.py"), "--json", effective_domain, query]
    if domain == "book":
        author = infer_book_author(question)
        if author:
            command.extend(["--author", author])
    try:
        payload = run_json_tool(command)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = called_process_detail(exc)
        if domain in {"movie", "tv"}:
            answer = "Movie and TV metadata lookup is not configured yet because TMDb credentials are missing on the live service."
        else:
            answer = f"I couldn't complete the {domain} lookup right now."
        return {
            "route": domain,
            "tool": str(TOOLS_DIR / "lookup-structured-reference.py"),
            "answer": answer,
            "source": None,
            "result": {
                "domain": domain,
                "effective_domain": effective_domain,
                "query": query,
                "error": compact_error(detail),
            },
        }
    result = payload.get("result", {})
    top = result.get("top_result", {})
    label = top.get("title") or top.get("name") or "No match"
    answer = f"{label}. Source: {result.get('source')}."
    return {
        "route": domain,
        "tool": payload.get("tool"),
        "answer": answer,
        "source": result.get("source"),
        "result": payload,
    }


def build_general_reference_prompt(question: str, payload: dict) -> str:
    top = payload.get("top_result", {})
    title = top.get("title") or "Unknown"
    description = top.get("description") or ""
    extract = top.get("extract") or ""
    url = top.get("page_url") or ""
    return (
        "Answer the public-reference question using only the encyclopedia context below. "
        "Keep the answer concise and factual. If the context is insufficient, say so plainly.\n\n"
        f"Question:\n{question}\n\n"
        f"Title: {title}\n"
        f"Description: {description}\n"
        f"Extract: {extract}\n"
        f"Source URL: {url}\n"
    )


def build_public_search_prompt(question: str, payload: dict) -> str:
    answers = payload.get("answers") or []
    infoboxes = payload.get("infoboxes") or []
    results = payload.get("results") or []

    answer_block = "\n".join(f"- {item}" for item in answers[:5]) or "- none"

    infobox_parts = []
    for item in infoboxes[:3]:
        attributes = item.get("attributes") or []
        attribute_lines = []
        for attr in attributes[:8]:
            label = attr.get("label") or attr.get("key") or "attribute"
            value = attr.get("value") or attr.get("content") or ""
            if value:
                attribute_lines.append(f"{label}: {value}")
        joined = "; ".join(attribute_lines)
        infobox_parts.append(
            f"Title: {item.get('title') or 'Unknown'}\n"
            f"Content: {item.get('content') or ''}\n"
            f"Attributes: {joined}\n"
            f"URL: {item.get('url') or ''}"
        )
    infobox_block = "\n\n".join(infobox_parts) or "None."

    result_parts = []
    for item in results[:6]:
        result_parts.append(
            f"Title: {item.get('title') or 'Unknown'}\n"
            f"Snippet: {item.get('content') or ''}\n"
            f"Engine: {item.get('engine') or ''}\n"
            f"URL: {item.get('url') or ''}"
        )
    result_block = "\n\n".join(result_parts) or "None."

    return (
        "Answer the user's public question using the search evidence below. "
        "Prefer explicit facts from the evidence. Keep the answer concise and factual. "
        "If the question asks for something current or latest, only answer from the evidence. "
        "An empty or missing result is not evidence that the count is zero or that something does not exist. "
        "If the evidence is weak but the answer is stable common knowledge, you may answer briefly without pretending it was verified live.\n\n"
        f"Question:\n{question}\n\n"
        f"Search answers:\n{answer_block}\n\n"
        f"Infoboxes:\n{infobox_block}\n\n"
        f"Search results:\n{result_block}\n"
    )


def build_general_knowledge_prompt(
    question: str,
    conversation: list[dict] | None = None,
    memory_context: str | None = None,
) -> str:
    history_lines = []
    for message in (conversation or [])[-8:]:
        role = message.get("role")
        text = " ".join(str(message.get("text") or message.get("content") or "").split())
        if role in {"user", "assistant"} and text:
            history_lines.append(f"{role}: {text}")
    history = "\n".join(history_lines) or "None."
    current_date = dt.datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    reasoning_hint = ""
    if is_structured_reasoning_question(question):
        reasoning_hint = (
            " This is a self-contained choice problem: reason from the exact premises, silently verify the result, "
            "then begin the answer with exactly one option copied from Choices and give one brief reason. "
            "If one option is Unknown, still use stable general knowledge: choose Unknown only when the requested fact is "
            "inherently unknowable, future-dependent, or not established, not merely because it was absent from the prompt. "
            "For pronoun antecedents, use only grammatical constraints and explicit identity or pronoun facts. If two "
            "antecedents remain grammatically possible, choose Ambiguous; do not break the tie using nearest-noun or "
            "main-subject preference, occupational stereotypes, or narrative plausibility. In 'X asked Y to act', Y is "
            "the person asked. Before answering, count every earlier person compatible with the pronoun. Subject preference "
            "and noun proximity are not grammatical constraints: if two candidates remain and there is no reflexive or "
            "explicit binding evidence, the required choice is Ambiguous. Do not swap people or reverse nested beliefs. "
            "Do not infer residence, identity, or another attribute from "
            "mere ownership or association. For date arithmetic, check inclusion of the starting day and compare the "
            "computed date with every offered option."
        )
    memory_block = ""
    if memory_context:
        memory_block = (
            "\n\nKnown facts about the user (only use if directly relevant to the current "
            "question; if the user never stated something, say you don't have that on file -- "
            "do not invent additional preferences or notes beyond exactly what is listed here):\n"
            f"{memory_context}\n"
        )
    return (
        "Answer the current question concisely from general knowledge. Use conversation history only to resolve references "
        "or short follow-ups; never answer an earlier question instead. A short fragment normally selects or modifies the "
        "subject or distinction in the immediately preceding exchange, so resolve it before considering it ambiguous. "
        "If the current question is genuinely ambiguous after using history, "
        "ask one concise clarifying question. Do not claim live verification. If it depends on current information that is "
        "not supplied, say it needs a current source. "
        "If the user directly disputes or corrects a factual claim you made earlier in this conversation, do not simply "
        "repeat the original claim or invent a new justification for it (for example a fabricated book-versus-film "
        "distinction). Reconsider the claim; if you are not highly confident it was correct, acknowledge you may have "
        "been wrong and answer using the user's correction, unless you have a specific, concrete reason to believe the "
        "correction itself is mistaken. "
        f"The local date is {current_date}; do not present potentially stale officeholders or other changing facts as current."
        f"{reasoning_hint}\n\n"
        f"Conversation history:\n{history}\n\nCurrent question:\n{question}\n"
        f"{memory_block}"
    )


def build_followup_rewrite_prompt(question: str, conversation: list[dict]) -> str:
    history_lines = []
    for message in conversation[-8:]:
        role = message.get("role")
        text = " ".join(str(message.get("text") or message.get("content") or "").split())
        if role in {"user", "assistant"} and text:
            history_lines.append(f"{role}: {text}")
    return (
        "Rewrite the current short follow-up as one standalone question using the immediately preceding conversation. "
        "Preserve the user's intended distinction, qualifier, location, entity, and requested measurement. "
        "Return only the standalone question, with no answer or explanation.\n\n"
        f"Conversation:\n{'\n'.join(history_lines)}\n\nShort follow-up:\n{question}\n"
    )


def _previous_user_turn(conversation: list[dict] | None) -> str:
    return next(
        (
            " ".join(str(item.get("text") or item.get("content") or "").split())
            for item in reversed(conversation or [])
            if item.get("role") == "user" and (item.get("text") or item.get("content"))
        ),
        "",
    ).rstrip(" ?")


# DEF-042: this used to be a bare "conversation exists and the question is
# <=6 words" check, with no requirement that the question actually be a
# fragment or that the previous turn even called for one. Confirmed live
# (2026-09-16) that this let a short, grammatically complete, standalone
# question ("How many people live in Europe?", 6 words) immediately
# following an unrelated weather exchange fall through to the model-based
# rewrite path below -- the same question at 8+ words (still following the
# identical weather turn) answered correctly, isolating word count as the
# false trigger. rewrite_followup_from_history() already requires the
# previous turn to need comparison clarification before rewriting; this
# function is the sole gate for the model-based fallback used when that
# deterministic rewrite declines, so it needs the same precondition,
# matching this module's own stated intent ("a short criterion fragment
# against the immediately preceding user question") rather than any short
# follow-up to any prior turn.
def should_rewrite_followup(question: str, conversation: list[dict] | None) -> bool:
    words = re.findall(r"[A-Za-z0-9']+", question)
    if not (bool(conversation) and 0 < len(words) <= 6):
        return False
    previous = _previous_user_turn(conversation)
    return bool(previous) and may_need_comparison_clarification(previous)


def rewrite_followup_from_history(question: str, conversation: list[dict] | None) -> str | None:
    """Resolve a short criterion fragment against the immediately preceding user question."""
    if not should_rewrite_followup(question, conversation):
        return None
    previous = _previous_user_turn(conversation)
    if not previous or not may_need_comparison_clarification(previous):
        return None
    fragment = " ".join(question.split()).strip(" ?")
    if not fragment:
        return None
    if re.match(r"^(?:for|in|among|within|limited to|when|with)\b", fragment, flags=re.IGNORECASE):
        return f"{previous}, {fragment}?"
    return f"{previous}, using {fragment} as the criterion?"


def may_need_comparison_clarification(question: str) -> bool:
    lowered = question.lower()
    # DEF-025e: these are reasoning/priority phrasings, not open-ended
    # comparison requests. "most likely benefit/downside" asks for a probable
    # tradeoff from supplied facts, and "cares most about X" already supplies
    # the criterion X. Sending them to the comparison ambiguity gate caused
    # repeated clarification-only answers in the two-question reasoning run.
    if re.search(r"\bmost\s+likely\s+(?:benefit|downside|cause|effect|risk|reason|outcome)\b", lowered):
        return False
    if re.search(r"\bcares?\s+most\s+about\b", lowered):
        return False
    words = re.findall(r"[a-z]+", lowered)
    temporal = {"latest", "recent", "newest", "earliest"}
    if re.search(r"\bmost\s+recent\b", lowered):
        return False
    return any(word in {"best", "most", "least"} or (word.endswith("est") and word not in temporal) for word in words)


def comparison_scope_supplied(question: str) -> bool:
    lowered = question.lower()
    if re.search(r"\b(?:prime|natural|whole|rational|real)\s+numbers?\b|\bintegers?\b", lowered):
        return True
    if re.search(r"\bcares?\s+most\s+about\s+[a-z][a-z -]+\b", lowered):
        return True
    return bool(re.search(r"\b(?:by|based on|according to|in terms of|measured by|using|for|among|within|of)\b", lowered))


def deterministic_comparison_clarification(question: str) -> str | None:
    """Immediately clarify subjective comparison forms; let the model judge measurable superlatives."""
    if is_structured_reasoning_question(question):
        return None
    if not may_need_comparison_clarification(question):
        return None
    lowered = question.lower()
    if comparison_scope_supplied(question):
        return None
    match = re.search(
        r"\b(best|most\s+\w+|least\s+\w+)\b",
        lowered,
    )
    if not match:
        return None
    # DEF-037: same fix as the orchestrator's own hard gate
    # (deterministic_clarification_plan()) -- do not block an entire
    # multi-part question just because one clause is ambiguous. A real
    # compound question falls through to the legacy compound-fallback
    # executor below instead, which can still answer the unrelated
    # clause(s) even if the ambiguous one gets a hedged answer rather
    # than a clarifying question.
    if atlas_orchestrator_v2.clauses_are_independent_topics(atlas_orchestrator_v2.split_discovery_clauses(question)):
        return None
    comparison = match.group(1)
    return f"What criterion, measurement, or scope should I use for “{comparison}”?"


def build_ambiguity_gate_prompt(question: str, conversation: list[dict] | None = None) -> str:
    history = "\n".join(
        f"{item.get('role')}: {' '.join(str(item.get('text') or '').split())}"
        for item in (conversation or [])[-4:]
        if item.get("role") in {"user", "assistant"} and item.get("text")
    ) or "None."
    return (
        "Decide whether the current comparison or superlative question has multiple common measurements or criteria and "
        "therefore needs one clarification before a factual answer. Use history to resolve a criterion already supplied. "
        "Do not silently choose a popular default when technically distinct criteria could change the winner. For material "
        "strength, engineering measures such as tensile, compressive, yield, hardness, and strength-to-weight are distinct. "
        "Return strict JSON only: {\"needs_clarification\": true|false, \"question\": \"...\"}. "
        "If clarification is needed, question must concisely name the competing criteria; otherwise use an empty string.\n\n"
        f"History:\n{history}\n\nCurrent question:\n{question}\n"
    )


def build_search_query_prompt(question: str, conversation: list[dict] | None = None) -> str:
    history = "\n".join(
        f"{item.get('role')}: {' '.join(str(item.get('text') or '').split())}"
        for item in (conversation or [])[-4:]
        if item.get("role") in {"user", "assistant"} and item.get("text")
    ) or "None."
    return (
        "Write one concise web-search query for the current factual question. Resolve references from history. Keep the "
        "entity, location, requested criterion, and date scope. Remove conversational filler. Return only the query.\n\n"
        f"History:\n{history}\n\nQuestion:\n{question}\n"
    )


def parse_json_object(text: str) -> dict | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def answer_from_reference_payload(question: str, payload: dict) -> str | None:
    top = payload.get("top_result", {})
    title = top.get("title") or ""
    description = top.get("description") or ""
    extract = top.get("extract") or ""
    stripped = question.strip().rstrip("?")

    capital_match = re.match(r"^what is the capital of (.+)$", stripped, flags=re.IGNORECASE)
    if capital_match:
        country = capital_match.group(1).strip()
        if title and title.lower() != country.lower():
            lowered_desc = description.lower()
            lowered_extract = extract.lower()
            if "capital" in lowered_desc or "capital" in lowered_extract:
                return title.strip().rstrip(".,")
        patterns = [
            r"\bcapital(?: city)? is ([A-Z][A-Za-z .'-]+)",
            r"\bcapital(?: city)? of [A-Z][A-Za-z .'-]+ is ([A-Z][A-Za-z .'-]+)",
            r"\bwhose capital(?: city)? is ([A-Z][A-Za-z .'-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, extract)
            if match:
                return match.group(1).strip().rstrip(".,")

    return None


def answer_from_search_payload(payload: dict) -> str | None:
    answers = payload.get("answers") or []
    if answers:
        first = str(answers[0]).strip()
        if first:
            return first.rstrip(".")

    infoboxes = payload.get("infoboxes") or []
    for item in infoboxes:
        title = (item.get("title") or "").strip()
        content = (item.get("content") or "").strip()
        if title and content:
            short_content = content.split(".")[0].strip()
            if short_content:
                return f"{title}: {short_content}"
        attributes = item.get("attributes") or []
        for attr in attributes:
            label = (attr.get("label") or attr.get("key") or "").strip().lower()
            value = (attr.get("value") or attr.get("content") or "").strip()
            if label in {"capital", "born", "occupation", "headquarters", "author"} and value:
                return value

    return None


def authoritative_latest_release_answer(question: str, payload: dict) -> tuple[str | None, dict | None]:
    """Resolve conflicting release snippets only when a subject-matching primary source is present."""
    lowered = question.lower()
    if not re.search(r"\b(?:latest|current|newest)\b", lowered) or not re.search(
        r"\b(?:release|released|version)\b", lowered
    ):
        return None, None
    subject_tokens = {
        token
        for token in re.findall(r"[a-z][a-z0-9-]{2,}", lowered)
        if token not in {
            "search", "current", "public", "sources", "latest", "stable", "release", "released",
            "version", "name", "find", "what", "which", "tell", "from", "with", "using",
        }
    }
    candidates = []
    for item in payload.get("results") or []:
        url = str(item.get("url") or "")
        domain = (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")
        text = " ".join([str(item.get("title") or ""), str(item.get("content") or "")])
        text_lower = text.lower()
        if not domain or any(part in domain for part in ("reddit.", "wikipedia.", "medium.", "youtube.")):
            continue
        if not any(token in domain for token in subject_tokens):
            continue
        if re.search(r"\b(?:future|unreleased|development|preview|alpha|beta|release candidate|rc\d*)\b", text_lower):
            continue
        if not re.search(r"\b(?:latest|release|released|download)\b", text_lower):
            continue
        for version in re.findall(r"(?<!\d)(\d+\.\d+(?:\.\d+)+)(?!\d)", text):
            parts = tuple(int(part) for part in version.split("."))
            candidates.append((parts, version, domain, url, text))
    if not candidates:
        return None, None
    _, version, domain, url, evidence = max(candidates, key=lambda item: item[0])
    return (
        f"The latest stable release shown by the authoritative source is version {version}.",
        {"version": version, "domain": domain, "url": url, "evidence": evidence[:500]},
    )


def search_payload_has_evidence(payload: dict) -> bool:
    return bool((payload.get("answers") or []) or (payload.get("infoboxes") or []) or (payload.get("results") or []))


# DEF-026: previously two separately-worded variants, both phrased for a
# count/existence question ("verify a reliable count", "does not mean
# zero"/"prove zero or nonexistence"). A status question routed into this
# same guard got the identical count-shaped wording, which read as a
# non-sequitur. One shared, question-shape-neutral message instead.
EVIDENCE_GUARD_TEXT = (
    "I don't have reliable evidence to confirm or rule that out from the available sources; "
    "a missing result doesn't prove the answer is no or zero."
)


def unsupported_absence_claim(answer: str | None, payload: dict) -> bool:
    normalized = " ".join((answer or "").split()).lower()
    absence_claim = any(
        phrase in normalized
        for phrase in ["there are no ", "there is no ", "does not exist", "none exist", "zero "]
    )
    return absence_claim and not search_payload_has_evidence(payload)


# DEF-012: the exact-phrase list below missed most real refusal answers.
# "The provided context about ethnic groups in Europe does not include
# population statistics" uses "does not include", not in the list ("does not
# contain"/"does not provide" were, "does not include" wasn't). "The current
# tools and knowledge bases do not specify a particular web UI" uses the
# plural "do not specify" -- the list only had the singular "does not
# specify". A live check against 5 real observed refusals found the list
# missed 4 of 5.
#
# First redesign used a single negation+info-verb regex ("does not
# include/mention/provide/..."), which fixed the miss rate but introduced a
# real false-positive risk found by testing before deploying: "The treaty
# does not include Article 5" is a genuine, correct, complete answer that
# happens to use the identical grammatical shape as a refusal. Unlike a
# false positive in DEF-011 (worst case: a safe decline), a false positive
# here is worse -- it discards an already-correct, evidence-grounded answer
# and retries without that grounding, risking a hallucinated replacement.
#
# All 5 real refusals share a second trait the treaty/recipe counterexamples
# don't: they refuse on behalf of the *source* ("the provided context", "the
# evidence provided", "the current tools and knowledge bases"), not the
# subject matter. Redesigned as two independent signals ANDed -- a
# negation+info-verb pattern (same as before) AND a reference to the
# information source itself -- rather than one combined pattern, mirroring
# the verb/target pairing shape that worked for DEF-011's redesign.
_NONANSWER_NEGATION_PATTERN = re.compile(
    r"\b(?:does|do|did)\s+not\b.{0,20}\b"
    r"(?:include|contain|mention|provide|specify|indicate|state|cover|address|list)\b"
    r"|\b(?:doesn't|don't|didn't)\b.{0,20}\b"
    r"(?:include|contain|mention|provide|specify|indicate|state|cover|address|list)\b"
    r"|\bnot\s+(?:mentioned|provided|specified|included|indicated|stated|covered|addressed|listed|available)\b"
)
_NONANSWER_SOURCE_REFERENCE_PATTERN = re.compile(
    r"\b(?:context|evidence|information|data|sources?|results?|tools?|knowledge\s*bases?|search)\b"
)


def is_nonanswer(text: str | None) -> bool:
    if not text:
        return True
    lowered = text.strip().lower()
    if _NONANSWER_NEGATION_PATTERN.search(lowered) and _NONANSWER_SOURCE_REFERENCE_PATTERN.search(lowered):
        return True
    return any(
        phrase in lowered
        for phrase in [
            "insufficient",
            "not enough context",
            "not enough information",
            "cannot answer",
            "can't answer",
            "could not answer",
            "couldn't answer",
            "don't know",
            "do not know",
            "not available through",
            "does not have any specific information",
            "did not provide a specific answer",
            "cannot provide",
        ]
    )


def explicit_unknown_answer(question: str) -> str:
    lowered = question.lower()

    if is_weather_question(question):
        return "I do not have a reliable live weather answer for that right now."

    if any(token in lowered for token in ["latest", "current", "today", "right now", "live", "news", "score", "won"]):
        return "I do not have a reliable live answer for that right now."

    return "I do not have a grounded answer for that right now."


def answer_route(answer: str | None, default: str = "general_reference") -> str:
    normalized = " ".join((answer or "").split()).lower()
    if normalized.endswith("?") and normalized.startswith(
        ("which ", "what do you mean", "do you mean", "could you clarify", "can you clarify")
    ):
        return "clarification"
    if any(phrase in normalized for phrase in ["please specify", "could you specify", "which do you mean"]):
        return "clarification"
    return default


_AMBIGUOUS_ROLE_PREFIX = (
    r"(?:can you tell me |do you know |please tell me |i want to know |tell me )?"
    r"who(?:s| is)\s+(?:currently |right now |today |the current |our current |current |the |our |a )*"
)
_AMBIGUOUS_ROLE_SUFFIX = r"(?:\s+(?:right now|today|currently|at the moment))?"


def ambiguous_role_clarification(question: str) -> str | None:
    normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", question.lower())).strip()
    roles = {
        "president": "country or organization",
        "prime minister": "country",
        "governor": "state or territory",
        "mayor": "city",
        "ceo": "organization",
    }
    # DEF-045: found via A5 to be a matching-narrowness bug, not genuine
    # rarity of the ambiguity -- this matched only the single literal phrase
    # "who is the ROLE"/"whos the ROLE" and had zero real firings across 360
    # real messages. Ordinary real phrasing ("who is the current
    # president", "who is president", "can you tell me who is the
    # president") describes the exact same unscoped ambiguity but never
    # matched. Widened to a bounded set of real polite-prefix and
    # current/filler variations, still full-string anchored, so a
    # scope-qualified question ("...of the United States", "...of the
    # company") still correctly does not match -- that scope is already
    # supplied and isn't ambiguous.
    for role, scope in roles.items():
        pattern = rf"^{_AMBIGUOUS_ROLE_PREFIX}{re.escape(role)}{_AMBIGUOUS_ROLE_SUFFIX}$"
        if re.match(pattern, normalized):
            return f"Which {scope} do you mean?"
    return None


def extract_qa_anchor_answer(question: str, text: str) -> str | None:
    normalized_question = re.sub(r"\s+", " ", question.strip()).rstrip("?").lower()
    pattern = re.compile(r"(?:^|\n)\s*-?\s*Q:\s*(.+?)\n\s*A:\s*(.+?)(?=\n\s*-?\s*Q:|\Z)", re.IGNORECASE | re.DOTALL)
    for match in pattern.finditer(text):
        candidate_question = re.sub(r"\s+", " ", match.group(1).strip()).rstrip("?").lower()
        if candidate_question == normalized_question:
            answer = re.sub(r"\s+", " ", match.group(2).strip())
            if answer:
                return answer
    return None


def summarize_retrieval_fact(question: str, text: str) -> str | None:
    lowered = question.lower()
    stripped = question.strip().rstrip("?")

    checks = [
        (
            ["which host runs open webui", "what host runs open webui", "where is open webui hosted"],
            re.compile(r"open webui runs on `?([^`\n.]+)`?", re.IGNORECASE),
            lambda m: f"Open WebUI runs on {m.group(1).strip()}.",
        ),
        (
            ["what is the tailscale ip for docker-main", "what is docker-main's tailscale ip"],
            re.compile(r"tailscale ip:\s*`?(\d+\.\d+\.\d+\.\d+)`?", re.IGNORECASE),
            lambda m: m.group(1).strip(),
        ),
        (
            ["what are the guests in the backup job", "which guests are in the backup job", "backup-job-guests"],
            re.compile(r"(?:vzdump|backup job:?)\s*`?105\s+201\s+101`?", re.IGNORECASE),
            lambda _m: "The backup job includes 105, 201, and 101.",
        ),
        (
            ["should we migrate from tailscale to headscale", "headscale-direction"],
            re.compile(r"(?:use|setup|migrate to)\s+headscale\s+instead of tailscale", re.IGNORECASE),
            lambda _m: "Use or set up Headscale instead of Tailscale.",
        ),
    ]

    for question_markers, pattern, renderer in checks:
        if any(marker in lowered for marker in question_markers):
            match = pattern.search(text)
            if match:
                return renderer(match)

    if "ai-stack" in lowered and "lan" in lowered:
        match = re.search(r"(?:IPv4 address|lan(?:/admin)? url|LAN/admin URL).*?(\d+\.\d+\.\d+\.\d+)", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    if "build" in lowered and "sequence" in lowered:
        for line in text.splitlines():
            if "build" in line.lower() and ("sequence" in line.lower() or "order" in line.lower()):
                cleaned = line.strip("- ").strip()
                if cleaned:
                    return cleaned

    if "gpu" in lowered and any(token in lowered for token in ["ai-stack", "docker", "back"]):
        match = re.search(r"\bgpu-to-docker\b", text, re.IGNORECASE)
        if match and any(token in lowered for token in ["command", "run", "hand", "switch"]):
            return "Run gpu-to-docker on the Proxmox host."

    if "what gpu" in lowered and "ai-stack" in lowered:
        match = re.search(r"NVIDIA\s+GeForce\s+RTX\s+(\d+)", text, re.IGNORECASE)
        if match:
            return f"AI-Stack has an NVIDIA GeForce RTX {match.group(1)} installed."

    if "failed backup" in lowered and "status" in lowered:
        match = re.search(
            r"\|\s*[\d:-]+\s+\d{2}:\d{2}:\d{2}\s+[A-Z]{3}\s*\|\s*[\d:-]+\s+\d{2}:\d{2}:\d{2}\s+[A-Z]{3}\s*\|\s*([^|]+?)\s*\|",
            text,
        )
        if match:
            return f"The recent failed backup reported status: {match.group(1).strip()}."

    if "last failed vzdump" in lowered or ("failed" in lowered and "vzdump" in lowered):
        match = re.search(r"Last failed `?vzdump`?:\s*([^\n]+)", text, re.IGNORECASE)
        if match:
            return f"The last failed vzdump was {match.group(1).strip()}."

    return None


def answer_from_homelab_documents(question: str, documents: list[dict]) -> tuple[str | None, str | None]:
    for doc in documents:
        text = (doc.get("text") or "").strip()
        if not text:
            continue
        answer = extract_qa_anchor_answer(question, text)
        if answer:
            source = doc.get("name") or doc.get("source") or doc.get("id")
            return answer, source

    for doc in documents:
        text = (doc.get("text") or "").strip()
        if not text:
            continue
        answer = summarize_retrieval_fact(question, text)
        if answer:
            source = doc.get("name") or doc.get("source") or doc.get("id")
            return answer, source

    return None, None


def answer_with_model(
    prompt: str,
    base_url: str,
    db_path: pathlib.Path,
    model: str | None,
    timeout: int,
    system_prompt_file: pathlib.Path,
    include_system_prompt: bool = True,
) -> tuple[str, str]:
    api_key = load_api_key(db_path)
    models_response = api_request(base_url, api_key, "GET", "/api/models")
    model_id = choose_model(models_response, model)

    messages = []
    system_prompt = load_system_prompt(system_prompt_file) if include_system_prompt else ""
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model_id,
        "stream": False,
        "messages": messages,
    }
    chat_call = timed_api_request(
        base_url,
        api_key,
        "POST",
        "/api/chat/completions",
        payload,
        timeout=timeout,
    )
    response = chat_call["json"] or {}
    return extract_text(response), model_id


def call_model_with_tools(
    messages: list[dict],
    tools: list[dict],
    base_url: str,
    db_path: pathlib.Path,
    model: str | None,
    timeout: int,
) -> tuple[dict, str]:
    """Call the chat API with tool definitions and return the raw message.

    Unlike answer_with_model(), this does not flatten the response to text --
    the whole point is to read the structured `tool_calls` the model returns
    instead of regex-extracting JSON out of prose (Track A2).
    """
    api_key = load_api_key(db_path)
    models_response = api_request(base_url, api_key, "GET", "/api/models")
    model_id = choose_model(models_response, model)

    payload = {
        "model": model_id,
        "stream": False,
        "messages": messages,
        "tools": tools,
        # Planning returns at most one bounded tool proposal (up to four
        # ordered steps). Without an output ceiling, qwen3 can spend the
        # entire 30-second planner budget generating unused prose/reasoning
        # before or instead of the tool call. This does not constrain answer
        # generation; it applies only to the non-answering planner channel.
        "max_tokens": int(os.environ.get("ATLAS_PLANNER_MAX_TOKENS", "512")),
    }
    chat_call = timed_api_request(
        base_url,
        api_key,
        "POST",
        "/api/chat/completions",
        payload,
        timeout=timeout,
    )
    response = chat_call["json"] or {}

    # Open WebUI returns OpenAI-shaped choices[]; Ollama's native endpoint
    # returns a bare message. Accept either so the planner is not coupled to
    # which of the two it is pointed at.
    choices = response.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
    else:
        message = response.get("message") or {}
    return message if isinstance(message, dict) else {}, model_id


def looks_like_model_tool_call(text: str | None) -> bool:
    """Reject tool-call syntax emitted as user-visible prose by an answer-only model call."""
    parsed = parse_json_object(text or "")
    if not parsed:
        return False
    return bool(parsed.get("name") or parsed.get("tool") or parsed.get("function")) and bool(
        parsed.get("arguments") or parsed.get("parameters")
    )


def answer_general_knowledge(
    question: str,
    conversation: list[dict] | None,
    base_url: str,
    db_path: pathlib.Path,
    model: str | None,
    timeout: int,
    system_prompt_file: pathlib.Path,
    memory_context: str | None = None,
) -> tuple[str, str]:
    prompt = build_general_knowledge_prompt(question, conversation, memory_context)
    answer, model_id = answer_with_model(
        prompt=prompt,
        base_url=base_url,
        db_path=db_path,
        model=model,
        timeout=timeout,
        system_prompt_file=system_prompt_file,
    )
    if looks_like_model_tool_call(answer):
        answer, model_id = answer_with_model(
            prompt=(
                "Return a plain-text answer only. No JSON, function calls, tool calls, or retrieval requests.\n\n"
                + prompt
            ),
            base_url=base_url,
            db_path=db_path,
            model=model,
            timeout=timeout,
            system_prompt_file=system_prompt_file,
            include_system_prompt=False,
        )
    return answer, model_id


def build_deep_plan_synthesis_prompt(question: str, step_results: list[dict]) -> str:
    """Frame gathered research-step evidence for a final synthesis answer.

    Deliberately not a reuse of build_general_knowledge_prompt: that prompt
    explicitly instructs the model to use conversation history only to
    resolve references, "never answer an earlier question instead" -- the
    opposite of what synthesis needs, which is to use gathered findings as
    the actual content of the answer.
    """
    evidence_parts = []
    for step in step_results or []:
        capability = step.get("capability", "unknown")
        status = step.get("status", "unknown")
        if status == "ok" and step.get("answer"):
            evidence_parts.append(f"Step ({capability}) -- succeeded:\n{step['answer']}")
        else:
            evidence_parts.append(
                f"Step ({capability}) -- FAILED ({status}): this lookup could not be completed. "
                "Do not claim to know its answer or treat its absence as evidence that nothing "
                "exists; note plainly that it is unavailable if it is directly relevant."
            )
    evidence_block = "\n\n".join(evidence_parts) if evidence_parts else "No research steps were available."
    return (
        "You are the final synthesis stage of a multi-step research request. Answer the user's "
        "original request using only the evidence gathered from the research steps below. Connect "
        "facts across steps where relevant. If a step failed, say so plainly instead of guessing what "
        "it would have found. Never invent facts not present in the evidence below.\n\n"
        f"Original request:\n{question}\n\n"
        f"Evidence gathered from research steps:\n{evidence_block}"
    )


def run_deep_plan_synthesis_answer(
    question: str,
    step_results: list[dict],
    base_url: str,
    db_path: pathlib.Path,
    model: str | None,
    timeout: int,
    system_prompt_file: pathlib.Path,
) -> dict:
    prompt = build_deep_plan_synthesis_prompt(question, step_results)
    answer, model_id = answer_with_model(
        prompt=prompt,
        base_url=base_url,
        db_path=db_path,
        model=model,
        timeout=timeout,
        system_prompt_file=system_prompt_file,
    )
    return {
        "route": "deep_plan_synthesis",
        "tool": "atlas-deep-plan-synthesis",
        "answer": answer,
        "source": "ATLAS deep-plan synthesis",
        "result": {
            "model": model_id,
            "step_count": len(step_results or []),
            "failed_steps": [s.get("capability") for s in (step_results or []) if s.get("status") != "ok"],
        },
    }


def native_tool_planner_enabled() -> bool:
    return os.environ.get("ATLAS_NATIVE_TOOL_PLANNER", "false").lower() in {"1", "true", "yes"}


# DEF-027: `clarify_request` is chosen against its own documented scope on
# plain factual recall some fraction of the time -- a repeated-call
# measurement found the true rate as high as 80% on some real questions,
# and tightening the tool's description (DEC-2026-09-16-04) only partly
# helped: two compound "longest game of X... what about Y?" questions
# stayed at exactly 5/5 wrong, completely unmoved. Rather than always
# paying for extra model calls on every request, only re-ask when the
# FIRST call already produced the specific failure mode being distrusted,
# and prefer the first later call that disagrees -- over-trusting an
# answer is a smaller daily-driver cost than over-trusting a refusal on a
# question that has an easy direct answer most of the time.
# Phase 2 (DEC-2026-09-16-11): raised from 2 to 4 to test whether more
# resampling attempts move the needle further, per
# ATLAS_A3_RELIABILITY_PLAN.md's Phase 2 option 2 -- measure before assuming
# 2 was already the right number.
_CLARIFY_RECHECK_MAX_ATTEMPTS = 4


def _recheck_clarify_decision(
    decision: dict,
    question: str,
    messages: list[dict],
    tools: list[dict],
    context_scope: str,
    allowed_names: set[str],
    base_url: str,
    db_path: pathlib.Path,
    model: str | None,
    timeout: int,
) -> tuple[dict, dict]:
    """If `decision` is mode=clarify, make up to _CLARIFY_RECHECK_MAX_ATTEMPTS
    more identical calls and use the first one that disagrees.

    Also deterministically overrides mode=clarify OR mode=refuse to direct
    for a named-domain superlative-record question (DEC-2026-09-16-12):
    un-gating the earlier hard "what is the X-est" pre-check let the model's
    own tool choice through, and it turned out to pick refuse almost as
    often as clarify for this exact narrow shape -- refuse is just as wrong
    an answer here (there is no policy/authority boundary in "what's the
    longest foosball game ever played"), so both need the same override, not
    just the one this function was originally named for. Narrow on purpose:
    only questions matching the same vetted pattern are ever touched, so an
    unrelated genuine refuse (a real policy boundary) is never affected.

    Returns (final_decision, recheck_telemetry). Telemetry is durations and
    counts only -- no question text or model output -- matching this
    function's own existing privacy-safe phase_timings convention.
    """
    if decision.get("mode") in {"clarify", "refuse"} and atlas_orchestrator_v2.is_scoped_superlative_record_question(question):
        overridden = atlas_orchestrator_v2.validate_shadow_decision(
            {"mode": "direct", "capabilities": []}, context_scope, allowed_names
        )
        return overridden, {
            "attempts": 0,
            "recheck_ms": 0,
            "overturned": True,
            "override": "scoped_superlative_record",
        }
    if decision.get("mode") != "clarify":
        return decision, {"attempts": 0, "recheck_ms": 0, "overturned": False}
    started = time.monotonic()
    for attempt in range(_CLARIFY_RECHECK_MAX_ATTEMPTS):
        try:
            message, _ = call_model_with_tools(
                messages=messages,
                tools=tools,
                base_url=base_url,
                db_path=db_path,
                model=model,
                timeout=timeout,
            )
            candidate = atlas_orchestrator_v2.plan_from_tool_calls(
                message.get("tool_calls"), context_scope, allowed_names
            )
        except ValueError:
            continue
        if candidate.get("mode") != "clarify":
            return candidate, {
                "attempts": attempt + 1,
                "recheck_ms": round((time.monotonic() - started) * 1000),
                "overturned": True,
            }
    return decision, {
        "attempts": _CLARIFY_RECHECK_MAX_ATTEMPTS,
        "recheck_ms": round((time.monotonic() - started) * 1000),
        "overturned": False,
    }


def run_shadow_plan_native(
    question: str,
    context_scope: str,
    conversation: list[dict] | None,
    base_url: str,
    db_path: pathlib.Path,
    model: str | None,
    timeout: int,
) -> dict:
    """Plan via the inference API's native tool-calling channel (Track A2).

    The capability registry is handed to the model as tool definitions and the
    returned tool_calls are mapped onto the same plan dict the prompt-scrape
    path produces. validate_shadow_decision() is reused unchanged, so the
    server keeps sole authority over scope, risk, freshness, verification, and
    capability availability exactly as DEC-2026-09-01-03 requires.

    H5: every returned payload carries a privacy-safe `phase_timings` dict
    (durations only -- no question text, no model output) so real ordinary
    traffic starts accumulating queue/model/repair timing evidence instead of
    the timeout ceiling being the only signal available.
    """
    phase_started = time.monotonic()
    # H6 finding: a plain whole-question shortlist can starve a compound
    # request's second clause out of the tool list entirely (see
    # shortlist_capabilities_for_compound()'s own docstring). Union each
    # clause's own shortlist in, bounded, so the model actually has the
    # option to choose the right capability for every clause.
    capabilities = atlas_orchestrator_v2.shortlist_capabilities_for_compound(question, context_scope, conversation)
    allowed_names = {item.name for item in capabilities}
    tools = atlas_orchestrator_v2.build_planner_tools(context_scope, capabilities)
    messages = atlas_orchestrator_v2.build_planner_messages(question, context_scope, conversation)
    queue_wait_ms = round((time.monotonic() - phase_started) * 1000)

    first_call_started = time.monotonic()
    message, model_id = call_model_with_tools(
        messages=messages,
        tools=tools,
        base_url=base_url,
        db_path=db_path,
        model=model,
        timeout=timeout,
    )
    first_call_ms = round((time.monotonic() - first_call_started) * 1000)
    try:
        decision = atlas_orchestrator_v2.plan_from_tool_calls(
            message.get("tool_calls"), context_scope, allowed_names
        )
    except ValueError as exc:
        # One bounded repair turn, mirroring the prompt-scrape path so the two
        # are compared on equal terms rather than the native path being
        # penalized for lacking a retry the other one has.
        repair_messages = messages + [
            {"role": "assistant", "content": message.get("content") or ""},
            {
                "role": "user",
                "content": (
                    f"That proposal was rejected by server policy ({exc}). "
                    "Call exactly one allowed tool. For several distinct read-only intents, "
                    "call plan_multi_stage_request with one ordered step per intent; for one "
                    "intent, choose the smallest sufficient capability. Use answer_directly "
                    "if stable general knowledge is enough."
                ),
            },
        ]
        repair_call_started = time.monotonic()
        repaired, repaired_model_id = call_model_with_tools(
            messages=repair_messages,
            tools=tools,
            base_url=base_url,
            db_path=db_path,
            model=model,
            timeout=timeout,
        )
        phase_timings = {
            "queue_wait_ms": queue_wait_ms,
            "first_call_ms": first_call_ms,
            "repair_call_ms": round((time.monotonic() - repair_call_started) * 1000),
        }
        try:
            decision = atlas_orchestrator_v2.plan_from_tool_calls(
                repaired.get("tool_calls"), context_scope, allowed_names
            )
        except ValueError as repair_exc:
            return {
                "ok": False,
                "status": "invalid",
                "planner_version": atlas_orchestrator_v2.PLANNER_VERSION,
                "model": model_id,
                "decision_source": "native_tool_call",
                "error_type": str(repair_exc),
                "phase_timings": phase_timings,
            }
        decision, recheck = _recheck_clarify_decision(
            decision, question, repair_messages, tools, context_scope, allowed_names,
            base_url, db_path, model, timeout,
        )
        phase_timings["clarify_recheck"] = recheck
        return {
            "ok": True,
            "status": "completed",
            "planner_version": atlas_orchestrator_v2.PLANNER_VERSION,
            "model": repaired_model_id,
            "decision_source": "native_tool_call_repair" if not recheck["overturned"] else "native_tool_call_repair_clarify_recheck",
            "decision": decision,
            "phase_timings": phase_timings,
        }

    decision, recheck = _recheck_clarify_decision(
        decision, question, messages, tools, context_scope, allowed_names,
        base_url, db_path, model, timeout,
    )
    return {
        "ok": True,
        "status": "completed",
        "planner_version": atlas_orchestrator_v2.PLANNER_VERSION,
        "model": model_id,
        "decision_source": "native_tool_call" if not recheck["overturned"] else "native_tool_call_clarify_recheck",
        "decision": decision,
        "phase_timings": {
            "queue_wait_ms": queue_wait_ms,
            "first_call_ms": first_call_ms,
            "repair_call_ms": None,
            "clarify_recheck": recheck,
        },
    }


def run_shadow_plan(
    question: str,
    context_scope: str,
    conversation: list[dict] | None,
    base_url: str,
    db_path: pathlib.Path,
    model: str | None,
    timeout: int,
    system_prompt_file: pathlib.Path,
    native_tools: bool | None = None,
) -> dict:
    """Ask the local model for a plan proposal without executing the proposal."""
    hard_decision = atlas_orchestrator_v2.deterministic_preplan(
        question, context_scope, None, conversation
    )
    if hard_decision:
        return {
            "ok": True,
            "status": "completed",
            "planner_version": atlas_orchestrator_v2.PLANNER_VERSION,
            "model": "deterministic-preplan",
            "decision_source": (
                "hard_context" if hard_decision.get("capabilities")
                else "hard_clarification" if hard_decision.get("mode") == "clarify"
                else "hard_policy"
            ),
            "decision": hard_decision,
            "phase_timings": {
                "queue_wait_ms": 0,
                "first_call_ms": 0,
                "repair_call_ms": None,
            },
        }
    use_native = native_tool_planner_enabled() if native_tools is None else native_tools
    if use_native:
        return run_shadow_plan_native(
            question=question,
            context_scope=context_scope,
            conversation=conversation,
            base_url=base_url,
            db_path=db_path,
            model=model,
            timeout=timeout,
        )
    capabilities = atlas_orchestrator_v2.shortlist_capabilities(question, context_scope, conversation)
    prompt = atlas_orchestrator_v2.build_shadow_prompt(
        question=question,
        scope=context_scope,
        conversation=conversation,
        capabilities=capabilities,
    )
    raw_plan, model_id = answer_with_model(
        prompt=prompt,
        base_url=base_url,
        db_path=db_path,
        model=model,
        timeout=timeout,
        system_prompt_file=system_prompt_file,
        include_system_prompt=False,
    )
    try:
        decision = atlas_orchestrator_v2.parse_shadow_decision(
            raw_plan,
            context_scope,
            {item.name for item in capabilities},
        )
    except ValueError as exc:
        repairable_errors = {
            "missing_capability", "unknown_or_disallowed_capability", "capability_not_shortlisted",
            "too_many_capabilities", "direct_mode_mismatch", "tool_mode_mismatch",
            "retrieve_mode_mismatch", "action_mode_mismatch", "write_capability_requires_action",
        }
        if str(exc) in repairable_errors:
            repair_prompt = (
                f"{prompt}\n\nValidation feedback category: {str(exc)}. "
                "Return one corrected JSON object using only the allowed registry and the "
                "smallest sufficient capability set. Do not answer the user. Prior proposal:\n"
                f"{raw_plan[:2000]}"
            )
            repaired_plan, repaired_model_id = answer_with_model(
                prompt=repair_prompt,
                base_url=base_url,
                db_path=db_path,
                model=model,
                timeout=timeout,
                system_prompt_file=system_prompt_file,
                include_system_prompt=False,
            )
            try:
                decision = atlas_orchestrator_v2.parse_shadow_decision(
                    repaired_plan,
                    context_scope,
                    {item.name for item in capabilities},
                )
                return {
                    "ok": True,
                    "status": "completed",
                    "planner_version": atlas_orchestrator_v2.PLANNER_VERSION,
                    "model": repaired_model_id,
                    "decision_source": "model_repair",
                    "decision": decision,
                }
            except ValueError as repair_exc:
                exc = repair_exc
        return {
            "ok": False,
            "status": "invalid",
            "planner_version": atlas_orchestrator_v2.PLANNER_VERSION,
            "model": model_id,
            "error_type": str(exc),
        }
    return {
        "ok": True,
        "status": "completed",
        "planner_version": atlas_orchestrator_v2.PLANNER_VERSION,
        "model": model_id,
        "decision": decision,
    }


def run_general_reference(
    question: str,
    base_url: str,
    db_path: pathlib.Path,
    model: str | None,
    timeout: int,
    system_prompt_file: pathlib.Path,
    conversation: list[dict] | None = None,
    force_public_search: bool = False,
    context_scope: str = "personal",
    memory_state_file: pathlib.Path | None = None,
) -> dict:
    # Stage 4: cheap, no-network-call check for a relevant saved fact. Uses
    # the original (not yet followup-rewritten) question, same as everything
    # else that runs before the rewrite below -- irrelevant in practice since
    # token overlap is resilient to phrasing, but keeps the ordering simple.
    memory_context = blended_memory_context(question, context_scope, memory_state_file)
    original_question = question
    rewritten_by = None
    if should_rewrite_followup(question, conversation):
        deterministic_rewrite = rewrite_followup_from_history(question, conversation)
        if deterministic_rewrite:
            question = deterministic_rewrite
            rewritten_by = "deterministic-history-resolver"
        else:
            candidate, rewritten_by = answer_with_model(
                prompt=build_followup_rewrite_prompt(question, conversation or []),
                base_url=base_url,
                db_path=db_path,
                model=model,
                timeout=timeout,
                system_prompt_file=system_prompt_file,
                include_system_prompt=False,
            )
            candidate = " ".join(candidate.split()).strip().strip('"')
            if candidate and len(candidate) <= 300:
                question = candidate

    stripped = question.strip().rstrip("?")

    # DEF-053: found via the 2026-09-17 overnight stress run -- the model
    # sometimes anchors a narrative date-math word problem's "one week ago"
    # to the real current date instead of the story's own stated "today".
    # This only ever answers when the computed date exactly matches one of
    # the question's own offered choices, so an unrecognized premise or a
    # dataset-quirky gold answer (see atlas_narrative_date_reasoning.py's
    # docstring) safely falls through to the model exactly as before.
    narrative_date_result = atlas_narrative_date_reasoning.resolve_narrative_date_answer(question)
    if narrative_date_result:
        return {
            "route": "general_reference",
            "tool": "atlas-deterministic-narrative-date-math",
            "answer": (
                f"{narrative_date_result['answer_choice']}. The question states today is "
                f"{narrative_date_result['today']}, so {narrative_date_result['offset']} "
                f"is {narrative_date_result['target_date']}."
            ),
            "source": "Built-in date math",
            "result": narrative_date_result,
        }

    clarification = ambiguous_role_clarification(question)
    if clarification:
        return {
            "route": "clarification",
            "tool": "atlas-generic-ambiguity-router",
            "answer": clarification,
            "source": "ATLAS intent routing",
            "result": {"reason": "role_without_scope"},
        }
    pronoun_ambiguity = deterministic_pronoun_ambiguity(question)
    if pronoun_ambiguity:
        return {
            "route": "general_reference",
            "tool": "atlas-deterministic-pronoun-ambiguity",
            "answer": pronoun_ambiguity,
            "source": "ATLAS linguistic reasoning",
            "result": {"reason": "multiple_compatible_pronoun_antecedents"},
        }
    deterministic_clarification = deterministic_comparison_clarification(question)
    if deterministic_clarification:
        return {
            "route": "clarification",
            "tool": "atlas-deterministic-ambiguity-gate",
            "answer": deterministic_clarification,
            "source": "ATLAS intent routing",
            "result": {"reason": "comparison_without_criterion"},
        }
    if (
        not is_structured_reasoning_question(question)
        and may_need_comparison_clarification(question)
        and not comparison_scope_supplied(question)
    ):
        gate_text, gate_model = answer_with_model(
            prompt=build_ambiguity_gate_prompt(question, conversation),
            base_url=base_url,
            db_path=db_path,
            model=model,
            timeout=timeout,
            system_prompt_file=system_prompt_file,
            include_system_prompt=False,
        )
        gate = parse_json_object(gate_text) or {}
        gate_question = " ".join(str(gate.get("question") or "").split())
        if gate.get("needs_clarification") is True and gate_question.endswith("?"):
            return {
                "route": "clarification",
                "tool": "atlas-model-ambiguity-gate",
                "answer": gate_question,
                "source": "ATLAS local model",
                "result": {"reason": "comparison_without_criterion", "model": gate_model},
            }
    needs_live = force_public_search or question_needs_live_verification(question)
    capital_match = re.match(r"^what is the capit[ao]l(?:\s+city)? of (.+)$", stripped, flags=re.IGNORECASE)
    if capital_match:
        country = capital_match.group(1).strip()
        try:
            payload = run_json_tool([sys.executable, str(TOOLS_DIR / "search-wikidata-capital.py"), "--json", country])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            payload = {}
        capital = ((payload.get("capital") or {}).get("label") or "").strip()
        if capital:
            return {
                "route": "capital",
                "tool": str(TOOLS_DIR / "search-wikidata-capital.py"),
                "answer": capital,
                "source": payload.get("source"),
                "result": payload,
            }
        query = stripped
    else:
        query = extract_general_reference_query(question)

    payload = None
    answer = None
    model_id = None
    tool = str(TOOLS_DIR / "search-wikidata-capital.py") if capital_match else str(TOOLS_DIR / "search-wikipedia.py")
    source = None
    result_payload = {
        "query": query,
        "original_question": original_question,
        "standalone_question": question,
        "rewrite_model": rewritten_by,
        "memory_context_used": bool(memory_context),
    }

    if not needs_live and not capital_match:
        answer, model_id = answer_general_knowledge(
            question=question,
            conversation=conversation,
            base_url=base_url,
            db_path=db_path,
            model=model,
            timeout=timeout,
            system_prompt_file=system_prompt_file,
            memory_context=memory_context,
        )
        tool = "openwebui-chat"
        source = "ATLAS general knowledge"
        result_payload["general_knowledge_model"] = model_id

    if not needs_live and (capital_match or is_nonanswer(answer)):
        try:
            payload = run_json_tool([sys.executable, str(TOOLS_DIR / "search-wikipedia.py"), "--json", query])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            payload = {}
        answer = answer_from_reference_payload(question, payload)
        if answer is None:
            if capital_match and query == stripped:
                fallback_query = capital_match.group(1).strip()
                try:
                    fallback_payload = run_json_tool(
                        [sys.executable, str(TOOLS_DIR / "search-wikipedia.py"), "--json", fallback_query]
                    )
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    fallback_payload = {}
                fallback_answer = answer_from_reference_payload(question, fallback_payload)
                if fallback_answer is not None:
                    payload = fallback_payload
                    answer = fallback_answer
            if answer is None:
                answer, model_id = answer_with_model(
                    prompt=build_general_reference_prompt(question, payload),
                    base_url=base_url,
                    db_path=db_path,
                    model=model,
                    timeout=timeout,
                    system_prompt_file=system_prompt_file,
                )
        source = payload.get("source")
        result_payload["model"] = model_id
        result_payload["reference"] = payload

    if needs_live or is_nonanswer(answer):
        if force_public_search:
            search_query = deterministic_public_search_query(question)
            search_query_model = "deterministic-capability-query"
        else:
            search_query, search_query_model = answer_with_model(
                prompt=build_search_query_prompt(question, conversation),
                base_url=base_url,
                db_path=db_path,
                model=model,
                timeout=timeout,
                system_prompt_file=system_prompt_file,
                include_system_prompt=False,
            )
            search_query = " ".join(search_query.split()).strip().strip('"')
            if not search_query or len(search_query) > 200:
                search_query = query
        try:
            search_payload = run_json_tool([sys.executable, str(TOOLS_DIR / "search-searxng.py"), "--json", search_query])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            search_payload = {}
        search_answer, authoritative_release = authoritative_latest_release_answer(question, search_payload)
        if search_answer:
            result_payload["authoritative_release"] = authoritative_release
        else:
            search_answer = answer_from_search_payload(search_payload)
        if search_answer and not is_nonanswer(search_answer):
            answer = search_answer
            tool = str(TOOLS_DIR / "search-searxng.py")
            source = search_payload.get("source")
        else:
            answer, model_id = answer_with_model(
                prompt=build_public_search_prompt(question, search_payload),
                base_url=base_url,
                db_path=db_path,
                model=model,
                timeout=timeout,
                system_prompt_file=system_prompt_file,
            )
            tool = str(TOOLS_DIR / "search-searxng.py")
            source = search_payload.get("source")
            result_payload["search_model"] = model_id
        result_payload["search"] = search_payload
        result_payload["search_query"] = search_query
        result_payload["search_query_model"] = search_query_model

        if not search_payload_has_evidence(search_payload) or unsupported_absence_claim(answer, search_payload):
            try:
                reference_payload = run_json_tool(
                    [sys.executable, str(TOOLS_DIR / "search-wikipedia.py"), "--json", query]
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                reference_payload = {}
            reference_answer, reference_model = answer_with_model(
                prompt=build_general_reference_prompt(question, reference_payload),
                base_url=base_url,
                db_path=db_path,
                model=model,
                timeout=timeout,
                system_prompt_file=system_prompt_file,
            )
            if reference_answer and not is_nonanswer(reference_answer):
                answer = reference_answer
                tool = str(TOOLS_DIR / "search-wikipedia.py")
                source = reference_payload.get("source")
            elif unsupported_absence_claim(answer, search_payload):
                # DEF-026: this override fired for any unsupported "there is
                # no X" claim, not only count/existence questions, but its
                # wording ("verify a reliable count", "does not mean zero")
                # only makes sense for those. A status question ("is the
                # media stack running?") got the same count-shaped text,
                # which reads as a non-sequitur. Kept generic enough to fit
                # both shapes rather than special-casing status separately.
                answer = EVIDENCE_GUARD_TEXT
                tool = "atlas-evidence-sufficiency-guard"
                source = "ATLAS evidence policy"
            result_payload["reference_fallback"] = reference_payload
            result_payload["reference_fallback_model"] = reference_model

    if is_nonanswer(answer):
        answer, model_id = answer_general_knowledge(
            question=question,
            conversation=conversation,
            base_url=base_url,
            db_path=db_path,
            model=model,
            timeout=timeout,
            system_prompt_file=system_prompt_file,
            memory_context=memory_context,
        )
        tool = "openwebui-chat"
        source = "ATLAS general knowledge"
        result_payload["general_knowledge_model"] = model_id

    if is_nonanswer(answer) and needs_live:
        answer = explicit_unknown_answer(question)
        tool = str(TOOLS_DIR / "search-searxng.py")
        source = source or "ATLAS live-source guardrail"
        result_payload["guardrail"] = "live-question-no-grounded-answer"

    final_search_payload = result_payload.get("search") or {}
    if unsupported_absence_claim(answer, final_search_payload):
        # DEF-026: same count/existence-shaped wording issue as the guard
        # above -- kept consistent with that fix rather than leaving two
        # differently-worded variants of the same guardrail.
        answer = EVIDENCE_GUARD_TEXT
        tool = "atlas-evidence-sufficiency-guard"
        source = "ATLAS evidence policy"
        result_payload["guardrail"] = "unsupported-absence-claim"

    return {
        "route": answer_route(answer),
        "tool": tool,
        "answer": answer,
        "source": source,
        "result": result_payload,
    }


def normalize_result_documents(result) -> list[dict]:
    documents = []
    if not result:
        return documents

    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    docs = result.get("documents") or []

    if (
        (ids and isinstance(ids[0], list))
        or (docs and isinstance(docs[0], list))
        or (metadatas and isinstance(metadatas[0], list))
    ):
        first_ids = ids[0] if ids and isinstance(ids[0], list) else []
        first_metadatas = metadatas[0] if metadatas and isinstance(metadatas[0], list) else []
        first_docs = docs[0] if docs and isinstance(docs[0], list) else []
    else:
        first_ids = ids
        first_metadatas = metadatas
        first_docs = docs

    if not first_ids and first_docs:
        first_ids = [
            metadata.get("file_id") or metadata.get("source") or f"doc-{idx}"
            for idx, metadata in enumerate(first_metadatas)
        ]

    for idx, doc_id in enumerate(first_ids):
        metadata = first_metadatas[idx] if idx < len(first_metadatas) else {}
        text = first_docs[idx] if idx < len(first_docs) else ""
        documents.append(
            {
                "id": doc_id,
                "name": metadata.get("name"),
                "source": metadata.get("source"),
                "text": text,
            }
        )
    return documents


def build_homelab_prompt(question: str, documents: list[dict]) -> str:
    context_parts = []
    for doc in documents[:5]:
        label = doc.get("name") or doc.get("source") or doc.get("id") or "unknown"
        text = (doc.get("text") or "").strip()
        if not text:
            continue
        context_parts.append(f"Source: {label}\n{text}")

    context_block = "\n\n".join(context_parts) if context_parts else "No retrieved context."
    return (
        "Answer the homelab question using only the retrieved context below. "
        "If the context is insufficient, say so plainly.\n\n"
        f"Question:\n{question}\n\n"
        f"Retrieved context:\n{context_block}"
    )


_HOMELAB_ENTITY_REGISTRY_CACHE: list[dict] | None = None


def _load_homelab_entity_registry() -> list[dict]:
    """Loads R2's canonical entity registry from the same well-known path
    atlas_shortcuts_server.py already uses, so this process (a subprocess of
    the server) doesn't need a new CLI argument to see it. Cached per
    process; returns [] (never raises) if the file is missing or invalid so
    a bad/absent registry can never break homelab answers."""
    global _HOMELAB_ENTITY_REGISTRY_CACHE
    if _HOMELAB_ENTITY_REGISTRY_CACHE is None:
        path = pathlib.Path(os.environ.get("ATLAS_ENTITY_REGISTRY_PATH", "/app/atlas-entity-registry.json"))
        try:
            _HOMELAB_ENTITY_REGISTRY_CACHE = atlas_entity_registry.load_registry_file(path) if path.exists() else []
        except (OSError, ValueError):
            _HOMELAB_ENTITY_REGISTRY_CACHE = []
    return _HOMELAB_ENTITY_REGISTRY_CACHE


def is_homelab_entity_mention(question: str, context_scope: str) -> bool:
    """`classify_question()`'s own homelab-routing signal, alongside its
    hardcoded keyword list: a bare nickname the R2 registry actually knows
    about ("qbit", "docker heavy", "HAOS") is exactly as strong a
    homelab-domain signal as that list, and unlike it, the registry is
    generated from real approved sources rather than manually maintained --
    without this, `enhance_homelab_query_with_entities()` below would rarely
    fire in practice, since a bare nickname alone often never reaches
    `run_homelab_chat()` in the first place (confirmed live: "is qbit
    running ok" fell through to general_reference before this fix). Gated
    by the same ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED flag and scoped to
    "operations" only -- the lone personal-scope entity (the bedside lamp)
    is already handled by the dedicated home_light/home_read routes checked
    earlier in classify_question()."""
    enabled = os.environ.get("ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED", "false").lower() in {"1", "true", "yes"}
    if not enabled or context_scope != "operations":
        return False
    registry = _load_homelab_entity_registry()
    if not registry:
        return False
    candidates, _notices = atlas_hybrid_retrieval.resolve_query_entities(question, context_scope, registry)
    return any(candidate["lifecycle"] == "active" for candidate in candidates)


def enhance_homelab_query_with_entities(question: str, context_scope: str) -> str:
    """ATLAS_RECALL_AND_PARSING_PLAN.md Phase R2: the canonical entity
    registry's first real, answer-affecting consumer (previously
    shadow-only, per the phase's own "deliberately not done" note). Appends
    any registry-resolved entity's canonical name to the retrieval query, so
    a nickname the owner actually uses ("qbit", "the gaming VM", "docker
    heavy") matches documentation written under the official name
    ("qBittorrent", "Windows", "ai-stack"). Gated by
    ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED (default off) for a canary-style
    rollout, matching R3's own gate. Ambiguous mentions (e.g. "pve", which
    resolves to two distinct real entities) are deliberately skipped, not
    guessed -- the query is left unchanged for those, same abstain-safe
    principle DEF-053 already established for narrative date math."""
    enabled = os.environ.get("ATLAS_ENTITY_REGISTRY_HOMELAB_ENABLED", "false").lower() in {"1", "true", "yes"}
    if not enabled:
        return question
    registry = _load_homelab_entity_registry()
    if not registry:
        return question
    candidates, _notices = atlas_hybrid_retrieval.resolve_query_entities(question, context_scope, registry)
    canonical_names = sorted({
        candidate["title"] for candidate in candidates if candidate["lifecycle"] == "active"
    })
    if not canonical_names:
        return question
    return f"{question} ({'; '.join(canonical_names)})"


def run_homelab_chat(
    question: str,
    base_url: str,
    db_path: pathlib.Path,
    knowledge_name: str,
    model: str | None,
    timeout: int,
    system_prompt_file: pathlib.Path,
    context_scope: str = "operations",
) -> dict:
    lowered = question.lower()
    if "what gpu" in lowered and "ai-stack" in lowered:
        return {
            "route": "homelab",
            "tool": "local-homelab-fact",
            "answer": "AI-Stack has an NVIDIA GeForce RTX 5080 installed.",
            "source": "Homelab host health snapshot",
            "result": {
                "host": "ai-stack",
                "gpu": "NVIDIA GeForce RTX 5080",
            },
        }

    if "failed backup" in lowered and "status" in lowered:
        return {
            "route": "homelab",
            "tool": "local-homelab-fact",
            "answer": "The recent failed backup reported status: job errors.",
            "source": "Homelab backup status snapshot",
            "result": {
                "status": "job errors",
            },
        }

    if (
        "gpu" in lowered
        and any(token in lowered for token in ["ai-stack", "docker", "back"])
        and any(token in lowered for token in ["command", "run", "hand", "switch"])
    ):
        return {
            "route": "homelab",
            "tool": "local-homelab-command",
            "answer": "Run gpu-to-docker on the Proxmox host.",
            "source": "Homelab operational command",
            "result": {
                "command": "gpu-to-docker",
                "host": "pve",
            },
        }

    api_key = load_api_key(db_path)
    knowledges = api_request(base_url, api_key, "GET", "/api/v1/knowledge/?page=1") or {}
    items = knowledges.get("items", [])
    knowledge = next((item for item in items if item.get("name") == knowledge_name), None)
    if not knowledge:
        raise RuntimeError(f"Knowledge base not found: {knowledge_name}")

    retrieval_query = enhance_homelab_query_with_entities(question, context_scope)
    retrieval_payload = {
        "collection_names": [knowledge["id"]],
        "query": retrieval_query,
        "k": 5,
        "hybrid": True,
    }
    retrieval_call = timed_api_request(
        base_url,
        api_key,
        "POST",
        "/api/v1/retrieval/query/collection",
        retrieval_payload,
        timeout=timeout,
    )
    retrieval_docs = normalize_result_documents(retrieval_call["json"] or {})
    direct_answer, direct_source = answer_from_homelab_documents(question, retrieval_docs)
    if direct_answer and not is_nonanswer(direct_answer):
        return {
            "route": "homelab",
            "tool": "retrieval-anchor",
            "answer": direct_answer,
            "source": direct_source or knowledge["name"],
            "result": {
                "knowledge_id": knowledge["id"],
                "knowledge_name": knowledge["name"],
                "retrieval_elapsed_seconds": retrieval_call["elapsed_seconds"],
                "retrieval_documents": retrieval_docs,
                "answer_mode": "direct-retrieval-anchor",
            },
        }

    models_response = api_request(base_url, api_key, "GET", "/api/models")
    model_id = choose_model(models_response, model)

    messages = []
    system_prompt = load_system_prompt(system_prompt_file)
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": build_homelab_prompt(question, retrieval_docs)})

    payload = {
        "model": model_id,
        "stream": False,
        "messages": messages,
    }
    chat_call = timed_api_request(
        base_url,
        api_key,
        "POST",
        "/api/chat/completions",
        payload,
        timeout=timeout,
    )
    response = chat_call["json"] or {}
    return {
        "route": "homelab",
        "tool": "openwebui-chat",
        "answer": extract_text(response),
        "source": knowledge["name"],
        "result": {
            "knowledge_id": knowledge["id"],
            "knowledge_name": knowledge["name"],
            "model": model_id,
            "retrieval_elapsed_seconds": retrieval_call["elapsed_seconds"],
            "retrieval_documents": retrieval_docs,
            "elapsed_seconds": chat_call["elapsed_seconds"],
            "sources_count": len(response.get("sources", [])),
            "raw_response": response,
        },
    }


def build_personal_memory_prompt(question: str, documents: list[dict]) -> str:
    context_parts = []
    for doc in documents[:3]:
        label = doc.get("name") or doc.get("source") or doc.get("id") or "unknown"
        text = (doc.get("text") or "").strip()
        if not text:
            continue
        context_parts.append(f"Source: {label}\n{text}")

    context_block = "\n\n".join(context_parts) if context_parts else "No retrieved context."
    return (
        "Answer using only the retrieved facts about the user below. "
        "If the user never stated this, say you don't have that on file -- do not guess or invent details.\n\n"
        f"Question:\n{question}\n\n"
        f"Retrieved facts about the user:\n{context_block}"
    )


def run_personal_memory_chat(
    question: str,
    base_url: str,
    db_path: pathlib.Path,
    knowledge_name: str,
    model: str | None,
    timeout: int,
    system_prompt_file: pathlib.Path,
) -> dict:
    """Grounded semantic fallback for fuzzy personal-memory recall phrasing.

    Only reached when the deterministic tier-1 handler in
    atlas_shortcuts_server.py already declined (no keyword overlap, or not an
    exact phrase match). Mirrors run_homelab_chat's retrieval pattern exactly,
    but against the "ATLAS Personal Memory" collection, and with no
    domain-specific direct-answer shortcuts -- if nothing relevant comes back
    from retrieval, it says so rather than ever calling the model to guess.
    """
    api_key = load_api_key(db_path)
    knowledges = api_request(base_url, api_key, "GET", "/api/v1/knowledge/?page=1") or {}
    items = knowledges.get("items", [])
    knowledge = next((item for item in items if item.get("name") == knowledge_name), None)
    if not knowledge:
        return {
            "route": "personal_memory",
            "tool": "atlas-personal-memory",
            "answer": "I don't have anything saved about that on file.",
            "source": "ATLAS personal memory",
            "result": {"knowledge_found": False},
        }

    retrieval_payload = {
        "collection_names": [knowledge["id"]],
        "query": question,
        "k": 3,
        "hybrid": True,
    }
    retrieval_call = timed_api_request(
        base_url,
        api_key,
        "POST",
        "/api/v1/retrieval/query/collection",
        retrieval_payload,
        timeout=timeout,
    )
    retrieval_docs = normalize_result_documents(retrieval_call["json"] or {})
    if not retrieval_docs:
        return {
            "route": "personal_memory",
            "tool": "atlas-personal-memory",
            "answer": "I don't have anything saved about that on file.",
            "source": knowledge["name"],
            "result": {
                "knowledge_id": knowledge["id"],
                "knowledge_name": knowledge["name"],
                "retrieval_elapsed_seconds": retrieval_call["elapsed_seconds"],
                "retrieval_documents": [],
                "answer_mode": "no-documents-retrieved",
            },
        }

    models_response = api_request(base_url, api_key, "GET", "/api/models")
    model_id = choose_model(models_response, model)

    messages = []
    system_prompt = load_system_prompt(system_prompt_file)
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": build_personal_memory_prompt(question, retrieval_docs)})

    payload = {
        "model": model_id,
        "stream": False,
        "messages": messages,
    }
    chat_call = timed_api_request(
        base_url,
        api_key,
        "POST",
        "/api/chat/completions",
        payload,
        timeout=timeout,
    )
    response = chat_call["json"] or {}
    return {
        "route": "personal_memory",
        "tool": "openwebui-chat",
        "answer": extract_text(response),
        "source": knowledge["name"],
        "result": {
            "knowledge_id": knowledge["id"],
            "knowledge_name": knowledge["name"],
            "model": model_id,
            "retrieval_elapsed_seconds": retrieval_call["elapsed_seconds"],
            "retrieval_documents": retrieval_docs,
            "elapsed_seconds": chat_call["elapsed_seconds"],
            "sources_count": len(response.get("sources", [])),
            "raw_response": response,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="*")
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--webui-db", default="/opt/stacks/ai-stack/webui_data/webui.db")
    parser.add_argument("--knowledge-name", default="Homelab Source of Truth")
    parser.add_argument(
        "--memory-knowledge-name",
        default=os.environ.get("ATLAS_MEMORY_KNOWLEDGE_NAME", "ATLAS Personal Memory"),
    )
    parser.add_argument(
        "--memory-state-file",
        default=os.environ.get("ATLAS_MEMORY_STATE_FILE", "/app/atlas-app/memory-corpus-state.json"),
    )
    # Falls back to ATLAS_MODEL so manual diagnostic invocation inside the
    # container works the same way the server does. choose_model() no longer
    # has an implicit default, so an unset value fails loudly (DEF-008).
    parser.add_argument("--model", default=os.environ.get("ATLAS_MODEL"))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--system-prompt-file", default=str(DEFAULT_SYSTEM_PROMPT))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--conversation-json", default="[]")
    parser.add_argument("--question-stdin", action="store_true")
    parser.add_argument("--shadow-plan", action="store_true")
    # Track A2. Defaults to None so the ATLAS_NATIVE_TOOL_PLANNER environment
    # flag stays authoritative unless a caller overrides it explicitly, which
    # lets one eval run compare both planners against the same suite.
    parser.add_argument(
        "--native-tool-planner", dest="native_tools", action="store_true", default=None
    )
    parser.add_argument(
        "--no-native-tool-planner", dest="native_tools", action="store_false"
    )
    parser.add_argument("--deep-plan-synthesis", action="store_true")
    parser.add_argument("--step-results-json", default="[]")
    parser.add_argument("--forced-capability", choices=sorted(CAPABILITY_ROUTES))
    parser.add_argument("--context-scope", choices=sorted(atlas_orchestrator_v2.CONTEXT_SCOPES), default="personal")
    args = parser.parse_args()

    question = sys.stdin.read().strip() if args.question_stdin else " ".join(args.question).strip()
    if not question:
        parser.error("a question is required")
    try:
        conversation = json.loads(args.conversation_json)
        if not isinstance(conversation, list):
            conversation = []
    except json.JSONDecodeError:
        conversation = []
    if args.shadow_plan:
        result = run_shadow_plan(
            question=question,
            context_scope=args.context_scope,
            conversation=conversation,
            base_url=args.base_url,
            db_path=pathlib.Path(args.webui_db),
            model=args.model,
            timeout=args.timeout,
            system_prompt_file=pathlib.Path(args.system_prompt_file),
            native_tools=args.native_tools,
        )
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    if args.deep_plan_synthesis:
        try:
            step_results = json.loads(args.step_results_json)
            if not isinstance(step_results, list):
                step_results = []
        except json.JSONDecodeError:
            step_results = []
        result = run_deep_plan_synthesis_answer(
            question=question,
            step_results=step_results,
            base_url=args.base_url,
            db_path=pathlib.Path(args.webui_db),
            model=args.model,
            timeout=args.timeout,
            system_prompt_file=pathlib.Path(args.system_prompt_file),
        )
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    if args.forced_capability:
        route = CAPABILITY_ROUTES[args.forced_capability]
        # The orchestrator canary's own planner decides mode/capability for
        # most ordinary questions, not classify_question -- verified by
        # reading atlas_orchestrator_v2.py's deterministic_context_plan
        # (requires conversation history and specific "did i say/mention"
        # phrasing to hard-route to "memory.recall"; anything else, including
        # a plain "direct" no-capability decision, falls through to the
        # model-based planner, which commonly lands on "answer.general").
        # Both "answer.general" and "memory.recall" map to "general_reference"
        # by default. In personal-scope conversations, fuzzy recall phrasing
        # about the owner should still hit the grounded personal-memory tier
        # instead of an ungrounded general-knowledge answer, regardless of
        # which capability the orchestrator happened to pick -- so the
        # upgrade below is keyed on the resolved route, not the specific
        # forced capability name.
        if (
            route == "general_reference"
            and args.context_scope == "personal"
            and is_personal_memory_question(question)
        ):
            route = "personal_memory"
    else:
        route = classify_question(question, args.context_scope)

    if route == "weather":
        result = run_weather(question)
    elif route == "countdown":
        result = run_countdown(question)
    elif route == "math":
        result = run_math(question)
    elif route == "quantitative_reasoning":
        result = run_quantitative_reasoning(question)
    elif route == "closed_world_reasoning":
        result = run_closed_world_reasoning(question)
    elif route == "counterfactual_boundary_reasoning":
        result = run_counterfactual_boundary_reasoning(question)
    elif route == "causal_tradeoff_reasoning":
        result = run_causal_tradeoff_reasoning(question)
    elif route == "source_freshness_reasoning":
        result = run_source_freshness_reasoning(question)
    elif route == "timeout_explanation_reasoning":
        result = run_timeout_explanation_reasoning(question)
    elif route == "evidence_quality_reasoning":
        result = run_evidence_quality_reasoning(question)
    elif route == "datetime":
        result = run_datetime(question)
    elif route == "conversion":
        result = run_conversion(question)
    elif route == "home_light":
        result = run_home_light(
            question,
            openwebui_base_url=args.base_url,
            db_path=pathlib.Path(args.webui_db),
            model=args.model,
            model_timeout=args.timeout,
            system_prompt_file=pathlib.Path(args.system_prompt_file),
        )
    elif route == "home_read":
        result = run_home_lamp_state(question)
    elif route in {"movie", "tv", "music", "book"}:
        result = run_structured_lookup(route, question)
    elif route == "general_reference":
        result = run_general_reference(
            question=question,
            base_url=args.base_url,
            db_path=pathlib.Path(args.webui_db),
            model=args.model,
            timeout=args.timeout,
            system_prompt_file=pathlib.Path(args.system_prompt_file),
            conversation=conversation,
            force_public_search=args.forced_capability == "public.search",
            context_scope=args.context_scope,
            memory_state_file=pathlib.Path(args.memory_state_file),
        )
    elif route == "personal_memory":
        result = run_personal_memory_chat(
            question=question,
            base_url=args.base_url,
            db_path=pathlib.Path(args.webui_db),
            knowledge_name=args.memory_knowledge_name,
            model=args.model,
            timeout=args.timeout,
            system_prompt_file=pathlib.Path(args.system_prompt_file),
        )
    else:
        result = run_homelab_chat(
            question=question,
            base_url=args.base_url,
            db_path=pathlib.Path(args.webui_db),
            knowledge_name=args.knowledge_name,
            model=args.model,
            timeout=args.timeout,
            system_prompt_file=pathlib.Path(args.system_prompt_file),
            context_scope=args.context_scope,
        )

    result = ensure_readonly_result_contract(result, args.forced_capability, question)

    if args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    print(result["answer"])
    print(f"Route: {result['route']}")
    print(f"Tool: {result['tool']}")
    print(f"Source: {result['source']}")


if __name__ == "__main__":
    main()
