#!/usr/bin/env python3
"""Generate a fresh R2 entity-resolution development suite from real seed data.

Not a sealed release: this is regenerable development material per
ATLAS_RECALL_AND_PARSING_PLAN.md's evaluation-program source separation.
Cases are derived mechanically from the real generated registry
(atlas-entity-registry-2026-09-11.json) plus deterministic case/spacing/
hyphenation paraphrases of its real aliases -- not hand-picked to flatter
the resolver, and not copied from any private conversation.
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path("/Users/valdemort/Projects/homelab")
sys.path.insert(0, str(ROOT / "tools/ai"))

from atlas_entity_registry import load_registry_file, normalize_alias  # noqa: E402

REGISTRY_PATH = ROOT / "atlas-entity-registry-2026-09-11.json"
OUT_DIR = ROOT / "ai-evals/recall"


def case_variants(alias: str) -> list[str]:
    variants = {alias.upper(), alias.lower(), alias.title()}
    if "-" in alias:
        variants.add(alias.replace("-", " "))
    if " " in alias:
        variants.add(alias.replace(" ", "-"))
        variants.add("  ".join(alias.split()))
    variants.discard(alias)
    return sorted(variants)


def collision_map(registry: list[dict]) -> dict[tuple[str, str], set[str]]:
    """normalized alias -> set of entity_ids that share it, within one scope."""
    by_scope_key: dict[tuple[str, str], set[str]] = {}
    for entity in registry:
        for alias in [entity["canonical_name"], *entity["aliases"]]:
            key = (entity["context_scope"], normalize_alias(alias))
            by_scope_key.setdefault(key, set()).add(entity["entity_id"])
    return by_scope_key


def build_cases(registry: list[dict]) -> list[dict]:
    collisions = collision_map(registry)
    cases = []
    case_id = 0

    def add(category: str, surface: str, scope: str, expected: dict) -> None:
        nonlocal case_id
        case_id += 1
        cases.append({
            "id": f"r2-{case_id:04d}",
            "category": category,
            "surface": surface,
            "context_scope": scope,
            "expected": expected,
        })

    for entity in registry:
        scope = entity["context_scope"]
        add("exact_id", entity["entity_id"], scope, {"status": "resolved", "entity_id": entity["entity_id"]})

        for alias in entity["aliases"]:
            key = (scope, normalize_alias(alias))
            colliders = collisions[key]
            expected = (
                {"status": "ambiguous"} if len(colliders) > 1
                else {"status": "resolved", "entity_id": entity["entity_id"]}
            )
            category = "colliding_alias" if len(colliders) > 1 else "exact_alias"
            add(category, alias, scope, expected)

            for variant in case_variants(alias):
                variant_key = (scope, normalize_alias(variant))
                variant_colliders = collisions[variant_key]
                variant_expected = (
                    {"status": "ambiguous"} if len(variant_colliders) > 1
                    else {"status": "resolved", "entity_id": entity["entity_id"]}
                )
                variant_category = "colliding_alias" if len(variant_colliders) > 1 else "paraphrased_alias"
                add(variant_category, variant, scope, variant_expected)

        # Cross-scope leakage probe: this entity's own canonical_name should
        # never resolve under any *other* scope.
        for other_scope in {"personal", "operations", "admin"} - {scope}:
            add("cross_scope_leakage", entity["canonical_name"], other_scope, {"status": "no_match"})

    # De-duplicate identical (surface, scope) pairs, keeping the first (an
    # exact_id/exact_alias case if one exists) so the same surface is never
    # scored twice under conflicting expectations.
    seen = set()
    deduped = []
    for case in cases:
        key = (case["surface"], case["context_scope"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(case)
    return deduped


def main() -> None:
    registry = load_registry_file(REGISTRY_PATH)
    cases = build_cases(registry)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_path = OUT_DIR / f"atlas-entity-alias-paraphrase-r2-development-{len(cases)}-seed-{date_tag}.json"
    out_path.write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")
    by_category: dict[str, int] = {}
    for case in cases:
        by_category[case["category"]] = by_category.get(case["category"], 0) + 1
    print(f"Wrote {len(cases)} cases to {out_path}")
    print(json.dumps(by_category, indent=2))


if __name__ == "__main__":
    main()
