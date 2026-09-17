#!/usr/bin/env python3
"""Combine disjoint general and A4 suites into one sealed release artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import re
from datetime import datetime


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(value: str) -> str:
    value = re.sub(r"^for request label [a-z]+ [a-z]+ \d+,\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^(?:request|policy scenario|general concept) \d+:\s*", "", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip().casefold()


def case_signature(item: dict) -> str:
    parts = [normalized(item["question"])]
    for turn in item.get("conversation") or []:
        parts.append(f"{turn.get('role')}:{normalized(str(turn.get('text') or turn.get('content') or ''))}")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--general", required=True, type=pathlib.Path)
    parser.add_argument("--compound", required=True, type=pathlib.Path)
    parser.add_argument("--exclude-suite", action="append", default=[])
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    general = json.loads(args.general.read_text(encoding="utf-8"))
    compound = json.loads(args.compound.read_text(encoding="utf-8"))
    cases = general + compound
    if len(general) != 336 or len(compound) != 64 or len(cases) != 400:
        raise SystemExit("release must contain 336 general and 64 A4 cases")
    signatures = [case_signature(item) for item in cases]
    if len(signatures) != len(set(signatures)):
        raise SystemExit("duplicate signatures inside release")
    excluded = set()
    for value in args.exclude_suite:
        excluded.update(case_signature(item) for item in json.loads(pathlib.Path(value).read_text(encoding="utf-8")))
    overlap = sorted(set(signatures) & excluded)
    if overlap:
        raise SystemExit(f"release overlaps {len(overlap)} excluded signatures")
    random.Random(args.seed).shuffle(cases)
    args.output.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for item in cases:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "sealed_unrun",
        "seed": args.seed,
        "count": len(cases),
        "suite_file": args.output.name,
        "suite_sha256": digest(args.output),
        "category_counts": dict(sorted(counts.items())),
        "compound_cases": len(compound),
        "excluded_suite_overlap": 0,
        "policy": "Run once. Never tune against or rerun individual release cases.",
    }
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
