#!/usr/bin/env python3
"""Generate disjoint A3/A4 hardening development and sealed-holdout suites."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import re
from collections import Counter


CATEGORIES = (
    "temporal_mismatch",
    "semantic_step_failure",
    "compound_capability_omission",
    "direct_tool_bypass",
    "partial_fallback",
    "planner_timeout",
)

CITIES = [
    "Alameda", "Berkeley", "Burlingame", "Campbell", "Concord",
    "Cupertino", "Davis", "El Cerrito", "Fairfield", "Fremont",
    "Gilroy", "Hayward", "Livermore", "Los Gatos", "Martinez",
    "Millbrae", "Napa", "Novato", "Pacifica", "Petaluma",
    "Pleasanton", "Redwood City", "San Leandro", "San Mateo", "Sunnyvale",
]
COUNTRIES = [
    "Albania", "Armenia", "Belize", "Bhutan", "Bolivia",
    "Brunei", "Cambodia", "Cyprus", "Ecuador", "Georgia",
    "Guyana", "Jordan", "Laos", "Latvia", "Malawi",
    "Malta", "Moldova", "Nepal", "Oman", "Paraguay",
    "Rwanda", "Suriname", "Togo", "Vanuatu", "Zambia",
]
SYSTEMS = [
    "ATLAS scheduler", "backup CT", "Caddy proxy", "comic request worker", "Docker Main",
    "GPU handoff", "Home Assistant VM", "Jellyfin service", "memory corpus", "Nextcloud reader",
    "notification worker", "ntfy service", "Ollama runtime", "Open WebUI", "PBS datastore",
    "Piper service", "Proxmox host", "school bridge", "SearXNG service", "signal refresher",
    "Suwayomi server", "task store", "Windows gaming guest", "mobile PWA", "weather adapter",
]
TOPICS = [
    "container signing", "encrypted backups", "GPU scheduling", "local inference", "passkey recovery",
    "private notifications", "service monitoring", "software provenance", "zero trust networking", "backup testing",
    "local speech synthesis", "model evaluation", "disaster recovery", "secret rotation", "filesystem snapshots",
    "privacy safe telemetry", "container isolation", "dependency pinning", "network segmentation", "restore drills",
    "certificate renewal", "audit retention", "local first sync", "hardware monitoring", "update rollbacks",
]


def normalized(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def case(category: str, index: int, question: str, scope: str, expected: dict) -> dict:
    return {
        "id": f"a3-a4-h0-{category}-{index + 1:02d}",
        "category": category,
        "question": question,
        "context_scope": scope,
        "conversation": [],
        "expected": expected,
    }


def temporal_cases() -> list[dict]:
    periods = ("tomorrow morning", "tomorrow afternoon", "tomorrow evening", "tonight", "tomorrow")
    advice = ("need a jacket", "need an umbrella", "be comfortable outside", "expect rain", "expect fog")
    return [
        case(
            "temporal_mismatch", i,
            f"Will I {advice[i % len(advice)]} in {city} {periods[i % len(periods)]}?",
            "personal",
            {
                "required_capabilities": ["weather.forecast"],
                "forbidden_capabilities": ["answer.general", "datetime.resolve"],
                "required_period": periods[i % len(periods)],
                "resolved_place": city,
                "semantic_status": "ok",
            },
        )
        for i, city in enumerate(CITIES)
    ]


def semantic_failure_cases() -> list[dict]:
    return [
        case(
            "semantic_step_failure", i,
            f"Check tomorrow's weather for Atlas Test Location {4100 + i}, and calculate {31 + i} times {7 + (i % 9)}.",
            "personal",
            {
                "required_capabilities": ["weather.forecast", "math.calculate"],
                "required_mode": "deep",
                "required_step_statuses": ["unavailable", "ok"],
                "require_explicit_partial": True,
                "forbid_all_steps_ok": True,
            },
        )
        for i in range(25)
    ]


def compound_omission_cases() -> list[dict]:
    patterns = (
        lambda i: (
            f"Find current public guidance about {TOPICS[i]}, and tell me the documented role of the {SYSTEMS[i]}.",
            "operations", ["public.search", "homelab.knowledge"],
        ),
        lambda i: (
            f"Convert {41 + i} inches to centimeters, then name the capital of {COUNTRIES[i]}.",
            "personal", ["units.convert", "public.reference"],
        ),
        lambda i: (
            f"Give me tomorrow's forecast for {CITIES[i]}, calculate {17 + i} times {13 + (i % 7)}, and name the capital of {COUNTRIES[i]}.",
            "personal", ["weather.forecast", "math.calculate", "public.reference"],
        ),
    )
    rows = []
    for i in range(25):
        question, scope, capabilities = patterns[i % len(patterns)](i)
        rows.append(case(
            "compound_capability_omission", i, question, scope,
            {
                "required_capabilities": capabilities,
                "required_mode": "deep",
                "require_complete_coverage": True,
                "forbidden_capabilities": ["answer.general"],
            },
        ))
    return rows


def direct_bypass_cases() -> list[dict]:
    patterns = (
        lambda i: (f"What is {14 + i}.5 percent of {220 + i * 3}?", "personal", "math.calculate"),
        lambda i: (f"Convert {63 + i} kilometers into miles.", "personal", "units.convert"),
        lambda i: (f"What calendar date is {12 + i} days after November {1 + (i % 20)}, 2026?", "personal", "datetime.resolve"),
        lambda i: (f"What is the latest public guidance about {TOPICS[i]}?", "personal", "public.search"),
        lambda i: (f"Which host or VM runs the documented {SYSTEMS[i]}?", "operations", "homelab.knowledge"),
    )
    rows = []
    for i in range(25):
        question, scope, capability = patterns[i % len(patterns)](i)
        rows.append(case(
            "direct_tool_bypass", i, question, scope,
            {
                "required_capabilities": [capability],
                "forbidden_capabilities": ["answer.general"],
                "forbid_direct_mode": True,
            },
        ))
    return rows


def partial_fallback_cases() -> list[dict]:
    unsupported = (
        "find the fastest live-traffic route", "read a camera image", "transcribe ambient room audio",
        "look up a live airline seat map", "inspect an unsupported smart thermostat",
    )
    supported = (
        lambda i: (f"calculate {43 + i} times {9 + (i % 8)}", "math.calculate"),
        lambda i: (f"convert {71 + i} miles to kilometers", "units.convert"),
        lambda i: (f"name the capital of {COUNTRIES[i]}", "public.reference"),
    )
    rows = []
    for i in range(25):
        supported_text, capability = supported[i % len(supported)](i)
        question = f"Please {unsupported[i % len(unsupported)]} for me, and also {supported_text}."
        rows.append(case(
            "partial_fallback", i, question, "personal",
            {
                "required_capabilities": [capability],
                "require_explicit_partial": True,
                "forbid_silent_omission": True,
                "forbid_physical_action": True,
            },
        ))
    return rows


def timeout_cases() -> list[dict]:
    rows = []
    for i in range(25):
        question = (
            f"Give me tomorrow's forecast for {CITIES[i]}, convert {82 + i} kilometers to miles, "
            f"and name the capital of {COUNTRIES[i]}."
        )
        rows.append(case(
            "planner_timeout", i, question, "personal",
            {
                "required_capabilities": ["weather.forecast", "units.convert", "public.reference"],
                "required_mode": "deep",
                "planner_latency_ms_max": 30000,
                "forbid_timeout": True,
            },
        ))
    return rows


def collect_prior_questions(root: pathlib.Path, excluded: set[pathlib.Path]) -> set[str]:
    found: set[str] = set()
    for path in root.rglob("*.json"):
        if path.resolve() in excluded:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stack = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                question = item.get("question")
                if isinstance(question, str):
                    found.add(normalized(question))
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    return found


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=pathlib.Path, default=pathlib.Path("ai-evals/orchestration"))
    parser.add_argument("--prior-root", type=pathlib.Path, default=pathlib.Path("ai-evals"))
    parser.add_argument("--seed", type=int, default=2026091105)
    args = parser.parse_args()

    output_dir = args.output_dir
    dev_path = output_dir / f"atlas-a3-a4-hardening-h0-development-120-seed-{args.seed}.json"
    holdout_path = output_dir / f"atlas-a3-a4-hardening-h0-holdout-30-seed-{args.seed}.json"
    manifest_path = output_dir / "atlas-a3-a4-hardening-h0-manifest.json"
    previous_manifest = {}
    if manifest_path.exists():
        try:
            previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous_manifest = {}
    excluded = {dev_path.resolve(), holdout_path.resolve(), manifest_path.resolve()}
    prior = collect_prior_questions(args.prior_root, excluded)

    by_category = {
        "temporal_mismatch": temporal_cases(),
        "semantic_step_failure": semantic_failure_cases(),
        "compound_capability_omission": compound_omission_cases(),
        "direct_tool_bypass": direct_bypass_cases(),
        "partial_fallback": partial_fallback_cases(),
        "planner_timeout": timeout_cases(),
    }
    rng = random.Random(args.seed)
    development, holdout = [], []
    for category in CATEGORIES:
        candidates = by_category[category]
        rng.shuffle(candidates)
        development.extend(candidates[:20])
        holdout.extend(candidates[20:25])
    rng.shuffle(development)
    rng.shuffle(holdout)

    dev_questions = {normalized(item["question"]) for item in development}
    holdout_questions = {normalized(item["question"]) for item in holdout}
    if len(dev_questions) != 120 or len(holdout_questions) != 30:
        raise SystemExit("duplicate generated questions")
    if dev_questions & holdout_questions:
        raise SystemExit("development/holdout overlap")
    overlap = (dev_questions | holdout_questions) & prior
    if overlap:
        raise SystemExit(f"prior-suite overlap: {len(overlap)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    dev_path.write_text(json.dumps(development, indent=2) + "\n", encoding="utf-8")
    holdout_path.write_text(json.dumps(holdout, indent=2) + "\n", encoding="utf-8")
    holdout_sha256 = sha256(holdout_path)
    previous_holdout = previous_manifest.get("holdout") or {}
    preserve_run_state = (
        previous_manifest.get("suite") == "atlas-a3-a4-hardening-h0"
        and previous_manifest.get("seed") == args.seed
        and previous_holdout.get("sha256") == holdout_sha256
    )
    opened = bool(previous_holdout.get("opened")) if preserve_run_state else False
    run_count = int(previous_holdout.get("run_count") or 0) if preserve_run_state else 0
    manifest = {
        "suite": "atlas-a3-a4-hardening-h0",
        "seed": args.seed,
        "status": (
            "development_and_holdout_complete"
            if opened and run_count >= 1
            else "development_ready_holdout_sealed_unrun"
        ),
        "categories": list(CATEGORIES),
        "development": {
            "path": str(dev_path),
            "count": len(development),
            "category_counts": dict(sorted(Counter(item["category"] for item in development).items())),
            "sha256": sha256(dev_path),
        },
        "holdout": {
            "path": str(holdout_path),
            "count": len(holdout),
            "category_counts": dict(sorted(Counter(item["category"] for item in holdout).items())),
            "sha256": holdout_sha256,
            "opened": opened,
            "run_count": run_count,
        },
        "disjointness": {
            "development_holdout_exact_overlap": 0,
            "prior_eval_exact_overlap": 0,
        },
        "safety": {
            "contains_physical_actions": False,
            "contains_raw_production_conversations": False,
            "audit_policy": "privacy_minimized_metadata_only",
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote development={len(development)} holdout={len(holdout)} manifest={manifest_path}")
    print(f"development_sha256={manifest['development']['sha256']}")
    print(f"holdout_sha256={manifest['holdout']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
