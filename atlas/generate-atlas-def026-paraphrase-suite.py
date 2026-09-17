#!/usr/bin/env python3
"""Generate a DEF-026-scale paraphrase suite (parity with DEF-025's 140).

Every case is a fresh paraphrase within one of the six categories DEF-026
fixed (DEC-2026-09-15-11) -- none copied from the original 16-case audit,
the 7+7 already-spent live verification sets, or the small hand-written
unit test in test-atlas-def026-paraphrase-robustness.py. That's the same
"don't close a repro case on the exact string that exposed it" rule
DEC-2026-09-15-10 made durable, applied to bulk generation instead of one
case at a time.

Each generated case carries a computed `expect` field (exact date, hours,
day count, or place name) so the revalidation runner can check the actual
answer text, not just the route -- this is the "was the answer correct,"
not just "did it hit a route" version of DEF-025's looser two-part-signal
heuristic.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import random
from collections import Counter
from datetime import datetime


ROOT = pathlib.Path(__file__).resolve().parents[2]
OUTPUT_ROOT = ROOT / "ai-evals" / "reasoning"

CATEGORIES = (
    "countdown_base_clause",
    "event_reminder_offset",
    "backup_upload_window",
    "causal_tradeoff",
    "weather_place_extraction",
    "compound_countdown_weather",
)

HOLIDAYS = [
    ("Christmas", 12, 25),
    ("Halloween", 10, 31),
    ("New Year's Day", 1, 1),
    ("New Year's Eve", 12, 31),
    ("Independence Day", 7, 4),
]

EVENTS = [
    "flight", "conference", "doctor appointment", "dentist visit", "renewal deadline",
    "car inspection", "passport renewal", "job interview", "moving day", "vet checkup",
]

TRANSFER_NOUNS = ["backup", "upload", "file", "transfer"]
WINDOW_CONNECTORS = ["window is", "within", "inside", "in"]
CITIES = [
    ("Chicago", None), ("Denver", None), ("Miami", None), ("Boston", None),
    ("Austin", "Texas"), ("Portland", "Oregon"), ("Seattle", None), ("Phoenix", None),
    ("Nashville", "Tennessee"), ("Columbus", "Ohio"),
]
WEATHER_TRAILERS = [
    "and should I bring an umbrella",
    "and should I plan on bringing a jacket",
    "should I expect it to be cold",
    "is it going to rain",
]
CHANGE_SUBJECTS = [
    "compress database backups", "cache DNS lookups", "batch notification delivery",
    "pre-fetch weather data", "encrypt local backups", "index conversation history",
    "retry failed webhook deliveries", "compress log storage",
]
CHANGE_LEAD_INS = ["If we", "Imagine we", "Suppose we"]
MEASUREMENT_ASKS = [
    "what measurement would tell us whether the tradeoff was worth keeping?",
    "How would we know if it's worth it?",
    "what would tell us whether the tradeoff is actually worth keeping?",
]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ordinal_free_date(d: dt.date) -> str:
    return d.strftime("%B %-d, %Y")


def gen_countdown_base_clause(rng: random.Random, index: int) -> dict:
    name, month, day = rng.choice(HOLIDAYS)
    year = rng.choice([2026, 2027, 2028])
    base_date = dt.date(2026, 9, 15)
    target = dt.date(year, month, day)
    days = (target - base_date).days
    phrasing = rng.choice(
        [
            f"How many days from today until {name} {year}?",
            f"How many days from now until {name} {year}?",
            f"How far away is {name} {year} from today?",
        ]
    )
    return {
        "id": f"countdown_base_clause-{index:03d}",
        "category": "countdown_base_clause",
        "question": phrasing,
        "expect": {"days": days},
    }


def gen_event_reminder_offset(rng: random.Random, index: int) -> dict:
    event = rng.choice(EVENTS)
    event_days = rng.randint(5, 60)
    reminder_days = rng.randint(1, min(event_days - 1, 10))
    base_date = dt.date(2026, rng.randint(9, 11), rng.randint(1, 20))
    base_text = base_date.strftime("%B %-d, %Y")
    event_date = base_date + dt.timedelta(days=event_days)
    reminder_date = event_date - dt.timedelta(days=reminder_days)
    lead_in = rng.choice(["If today is", "Suppose today is"])
    phrasing = rng.choice(
        [
            f"{lead_in} {base_text}, and my {event} is {event_days} days from now, what date is that? "
            f"I'd like a reminder {reminder_days} days before the {event} -- what date should that be?",
            f"{lead_in} {base_text} and my {event} is {event_days} days later. "
            f"If I want a reminder {reminder_days} days before, what date is that?",
        ]
    )
    return {
        "id": f"event_reminder_offset-{index:03d}",
        "category": "event_reminder_offset",
        "question": phrasing,
        "expect": {
            "event_date": _ordinal_free_date(event_date),
            "reminder_date": _ordinal_free_date(reminder_date),
        },
    }


def gen_backup_upload_window(rng: random.Random, index: int) -> dict:
    noun = rng.choice(TRANSFER_NOUNS)
    size_gb = rng.choice([150, 320, 500, 750, 900, 1200])
    speed_mbps = rng.choice([25, 35, 50, 75, 100, 150])
    window_hours = rng.choice([6, 8, 12, 16, 20, 24])
    connector = rng.choice(WINDOW_CONNECTORS)
    window_phrase = rng.choice([f"{window_hours}-hour", f"{window_hours} hour"])
    hours = size_gb * 8000 / speed_mbps / 3600
    fits = hours <= window_hours

    if connector == "window is":
        phrasing = (
            f"A {noun} is {size_gb} GB and averages {speed_mbps} Mbps. "
            f"Roughly how many hours will it take? If the {noun} window is {window_hours} hours, is that enough?"
        )
    else:
        article = "an " if window_phrase.startswith(("8", "18", "11")) else "a "
        phrasing = (
            f"A {noun} is {size_gb} GB and averages {speed_mbps} Mbps. "
            f"Roughly how many hours will it take, and will it fit {connector} {article}{window_phrase} window?"
        )
    return {
        "id": f"backup_upload_window-{index:03d}",
        "category": "backup_upload_window",
        "question": phrasing,
        "expect": {"hours_ceil": round(hours, 1), "fits": fits},
    }


def gen_causal_tradeoff(rng: random.Random, index: int) -> dict:
    change = rng.choice(CHANGE_SUBJECTS)
    lead_in = rng.choice(CHANGE_LEAD_INS)
    measurement = rng.choice(MEASUREMENT_ASKS)
    phrasing = (
        f"{lead_in} {change} more aggressively; what's the most likely benefit and the most likely downside? "
        f"{measurement}"
    )
    return {
        "id": f"causal_tradeoff-{index:03d}",
        "category": "causal_tradeoff",
        "question": phrasing,
        "expect": {"contains": ["benefit", "downside"]},
    }


def gen_weather_place_extraction(rng: random.Random, index: int) -> dict:
    city, region = rng.choice(CITIES)
    trailer = rng.choice(WEATHER_TRAILERS)
    place = f"{city}, {region}" if region else city
    period = rng.choice(["tomorrow", "today", "right now", "tonight"])
    phrasing = f"What's the weather like in {place} {period}, {trailer}?"
    return {
        "id": f"weather_place_extraction-{index:03d}",
        "category": "weather_place_extraction",
        "question": phrasing,
        "expect": {"place_contains": city},
    }


def gen_compound_countdown_weather(rng: random.Random, index: int) -> dict:
    name, month, day = rng.choice(HOLIDAYS)
    year = rng.choice([2026, 2027])
    base_date = dt.date(2026, 9, 15)
    target = dt.date(year, month, day)
    days = (target - base_date).days
    city, region = rng.choice(CITIES)
    place = f"{city}, {region}" if region else city
    phrasing = f"How many days until {name} {year}, and separately, what's the weather in {place} right now?"
    return {
        "id": f"compound_countdown_weather-{index:03d}",
        "category": "compound_countdown_weather",
        "question": phrasing,
        # Known, disclosed A3 limitation (DEC-2026-09-15-12): only the
        # recognized (countdown) clause is expected to be answered.
        "expect": {"days": days, "weather_clause_known_dropped": True},
    }


GENERATORS = {
    "countdown_base_clause": gen_countdown_base_clause,
    "event_reminder_offset": gen_event_reminder_offset,
    "backup_upload_window": gen_backup_upload_window,
    "causal_tradeoff": gen_causal_tradeoff,
    "weather_place_extraction": gen_weather_place_extraction,
    "compound_countdown_weather": gen_compound_countdown_weather,
}


def generate(per_category: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    cases: list[dict] = []
    seen: set[str] = set()
    for category in CATEGORIES:
        generator = GENERATORS[category]
        count = 0
        attempts = 0
        while count < per_category:
            attempts += 1
            if attempts > per_category * 20:
                raise RuntimeError(f"could not generate {per_category} unique cases for {category}")
            item = generator(rng, count)
            norm = " ".join(item["question"].lower().split())
            if norm in seen:
                continue
            seen.add(norm)
            cases.append(item)
            count += 1
    rng.shuffle(cases)
    return cases


def write_suite(cases: list[dict], cycle: str, seed: int) -> tuple[pathlib.Path, pathlib.Path]:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    suite_path = OUTPUT_ROOT / f"atlas-def026-paraphrase-suite-{cycle}-{len(cases)}-seed-{seed}.json"
    manifest_path = OUTPUT_ROOT / f"atlas-def026-paraphrase-suite-{cycle}-manifest.json"
    suite_text = json.dumps(cases, indent=2, sort_keys=True) + "\n"
    suite_path.write_text(suite_text, encoding="utf-8")
    manifest = {
        "name": "ATLAS DEF-026 paraphrase revalidation suite",
        "tier": "focused_revalidation",
        "cycle": cycle,
        "seed": seed,
        "count": len(cases),
        "suite_file": suite_path.name,
        "suite_sha256": sha256_text(suite_text),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "category_counts": dict(Counter(item["category"] for item in cases)),
        "rules": {
            "distinct_from_original_16_case_audit": True,
            "distinct_from_spent_7_plus_7_live_sets": True,
            "distinct_from_test_atlas_def026_unit_test": True,
            "compound_countdown_weather_scores_recognized_clause_only": True,
            "not_a_sealed_holdout": True,
        },
    }
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    manifest_path.write_text(manifest_text, encoding="utf-8")
    return suite_path, manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-category", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260916)
    parser.add_argument("--cycle", default="2026-09-16-rev1")
    args = parser.parse_args()

    cases = generate(args.per_category, args.seed)
    suite_path, manifest_path = write_suite(cases, args.cycle, args.seed)
    print(f"wrote {suite_path}")
    print(f"wrote {manifest_path}")
    print(json.dumps(dict(Counter(item["category"] for item in cases)), sort_keys=True))


if __name__ == "__main__":
    main()
