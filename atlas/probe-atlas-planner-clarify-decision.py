#!/usr/bin/env python3
"""Repeated-call probe for DEF-027: measure clarify_request's real
per-question failure rate before/after a tool-description change.

Run this INSIDE the atlas-shortcuts container (it imports
run-atlas-readonly.py's run_shadow_plan() directly, in-process, to avoid
per-call subprocess/docker-exec overhead across many repeated calls).

Per DEC-2026-09-16-03: a single call proves nothing about a sampled model
decision. This calls each question --repeats times and reports the mode
distribution, not a single pass/fail.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys
from collections import Counter


MODULE_PATH = pathlib.Path("/app/run-atlas-readonly.py")
spec = importlib.util.spec_from_file_location("atlas_readonly", MODULE_PATH)
atlas_readonly = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.path.insert(0, str(MODULE_PATH.parent))
spec.loader.exec_module(atlas_readonly)


# Standalone factual questions with no reasonable ambiguity -- should
# NEVER trigger clarify_request. Pulled from the DEF-027 historical replay,
# filtered to exclude conversational follow-ups that legitimately need
# prior-turn context the replay didn't preserve (e.g. "US", "Try again",
# "But what is that" -- those are a separate, real methodology issue, not
# a clarify-tool miscalibration case).
SHOULD_NOT_CLARIFY = [
    "What year did the civil war start?",
    "What happened in Nanking",
    "What is the time 4:20 popular for?",
    "How many KJ of energy would be exerted if the sun exploded",
    "What is the longest game of foosball ever played? What about football?",
    "What is the longest game of soccer ever played? How much difference was it between that and football's longest match?",
]

# Borderline superlative-shaped questions -- arguably could go either way,
# but a direct answer with a caveat is more useful than a hard block.
BORDERLINE = [
    "What is the most sold brand of vodka",
    "What is the largest landmass? How much larger is it than the smallest land mass?",
]

# Genuine negative controls: SHOULD still trigger clarify_request. If
# tightening the tool description drops these to zero, the fix broke
# real ambiguity detection, not just the false positives.
SHOULD_CLARIFY = [
    "What's the best show?",
    "Item with the most sales",
    "Highest player count",
]


def probe(question: str, repeats: int, context_scope: str, base_url: str, db_path: pathlib.Path, model: str, timeout: int, system_prompt_file: pathlib.Path) -> dict:
    modes = []
    for _ in range(repeats):
        try:
            result = atlas_readonly.run_shadow_plan(
                question=question,
                context_scope=context_scope,
                conversation=None,
                base_url=base_url,
                db_path=db_path,
                model=model,
                timeout=timeout,
                system_prompt_file=system_prompt_file,
            )
            decision = result.get("decision") or {}
            modes.append(decision.get("mode") or f"error:{result.get('error_type')}")
        except Exception as exc:  # noqa: BLE001
            modes.append(f"exception:{type(exc).__name__}")
    return {"question": question, "modes": modes, "mode_counts": dict(Counter(modes))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--context-scope", default="personal")
    parser.add_argument("--base-url", default=os.environ.get("ATLAS_BASE_URL", "http://open-webui:8080"))
    parser.add_argument("--webui-db", type=pathlib.Path, default=pathlib.Path(os.environ.get("ATLAS_WEBUI_DB", "/app/webui_data/webui.db")))
    parser.add_argument("--model", default=os.environ.get("ATLAS_MODEL", "qwen3:8b"))
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--system-prompt-file", type=pathlib.Path, default=pathlib.Path(os.environ.get("ATLAS_SYSTEM_PROMPT_FILE", "/app/atlas-readonly-system.md")))
    parser.add_argument("--label", default="run")
    parser.add_argument("--output-json", type=pathlib.Path, required=True)
    args = parser.parse_args()

    all_cases = [("should_not_clarify", q) for q in SHOULD_NOT_CLARIFY]
    all_cases += [("borderline", q) for q in BORDERLINE]
    all_cases += [("should_clarify", q) for q in SHOULD_CLARIFY]

    results = []
    for category, question in all_cases:
        row = probe(question, args.repeats, args.context_scope, args.base_url, args.webui_db, args.model, args.timeout, args.system_prompt_file)
        row["category"] = category
        results.append(row)
        print(f"[{category}] {question[:60]!r} -> {row['mode_counts']}", flush=True)

    summary = {
        "label": args.label,
        "repeats": args.repeats,
        "results": results,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
