#!/usr/bin/env python3

"""Sample approved external QA datasets into ATLAS eval-suite JSON.

This script intentionally keeps imported benchmark samples separate from
hand-authored ATLAS project examples. Generated files are eval-only and include
source/license metadata per case.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import sys
from datetime import datetime
from typing import Any


SCRIPT_PATH = pathlib.Path(__file__).resolve()
ROOT = SCRIPT_PATH.parents[2] if len(SCRIPT_PATH.parents) > 2 else pathlib.Path.cwd()
MANIFEST_PATH = ROOT / "ai-evals" / "external" / "atlas-external-general-qa-datasets-2026-08-30.json"
DEFAULT_OUTPUT_DIR = ROOT / "ai-evals" / "external" / "generated"


DATASET_ALIASES = {
    "bigbench": "BIG-bench JSON tasks",
    "big-bench": "BIG-bench JSON tasks",
    "google/big-bench": "BIG-bench JSON tasks",
    "truthfulqa": "TruthfulQA",
    "truthful_qa": "TruthfulQA",
    "truthfulqa/truthful_qa": "TruthfulQA",
    "arc": "ARC / AI2 Reasoning Challenge",
    "ai2_arc": "ARC / AI2 Reasoning Challenge",
    "allenai/ai2_arc": "ARC / AI2 Reasoning Challenge",
    "commonsenseqa": "CommonsenseQA",
    "commonsense_qa": "CommonsenseQA",
    "tau/commonsense_qa": "CommonsenseQA",
    "openbookqa": "OpenBookQA",
    "allenai/openbookqa": "OpenBookQA",
    "triviaqa": "TriviaQA",
    "trivia_qa": "TriviaQA",
    "mandarjoshi/trivia_qa": "TriviaQA",
}


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def load_manifest(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_dataset(manifest: dict[str, Any], requested: str) -> dict[str, Any]:
    canonical = DATASET_ALIASES.get(requested.lower(), requested)
    for dataset in manifest["datasets"]:
        names = [dataset["name"], *dataset.get("huggingface_id_candidates", [])]
        if canonical in names or requested in names:
            return dataset
    raise SystemExit(f"Unknown dataset: {requested}")


def require_import_allowed(dataset_meta: dict[str, Any]) -> None:
    if not dataset_meta.get("allowed_now"):
        raise SystemExit(
            f"{dataset_meta['name']} is manifest-only right now "
            f"({dataset_meta.get('license_status', 'license_status unknown')})."
        )


def load_hf_dataset(dataset_meta: dict[str, Any], subset: str | None, split: str):
    try:
        from datasets import load_dataset  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Python package 'datasets' is not installed. Install it intentionally "
            "before importing external eval samples, for example: "
            "python3 -m pip install datasets"
        ) from exc

    dataset_id = dataset_meta["huggingface_id_candidates"][0]
    if subset:
        return load_dataset(dataset_id, subset, split=split)
    return load_dataset(dataset_id, split=split)


def source_block(dataset_meta: dict[str, Any], split: str, seed: int, subset: str | None, source_id: str) -> dict[str, Any]:
    return {
        "dataset": dataset_meta["name"],
        "huggingface_id_candidates": dataset_meta.get("huggingface_id_candidates", []),
        "source_urls": dataset_meta.get("source_urls", []),
        "reported_license": dataset_meta.get("reported_license", "unknown"),
        "license_status": dataset_meta.get("license_status", "unknown"),
        "split": split,
        "subset": subset,
        "seed": seed,
        "source_id": source_id,
        "eval_only": True,
    }


def first_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value)


def convert_truthfulqa(row: dict[str, Any], dataset_meta: dict[str, Any], split: str, seed: int, index: int) -> dict[str, Any]:
    answer = first_text(row.get("best_answer") or row.get("correct_answers"))
    incorrect = row.get("incorrect_answers") or []
    if isinstance(incorrect, str):
        incorrect = [incorrect]
    return {
        "id": f"external:truthfulqa:{split}:{index}",
        "source": source_block(dataset_meta, split, seed, None, str(index)),
        "question": row["question"],
        "expect": [answer] if answer else [],
        "forbid": [str(item) for item in incorrect[:3] if item],
        "expected_route": "general_reference",
        "eval_only": True,
    }


def _choice_map(row: dict[str, Any]) -> dict[str, str]:
    choices = row.get("choices") or {}
    labels = choices.get("label") or []
    texts = choices.get("text") or []
    return {str(label): str(text) for label, text in zip(labels, texts)}


def convert_arc(row: dict[str, Any], dataset_meta: dict[str, Any], split: str, seed: int, subset: str, index: int) -> dict[str, Any]:
    choices = _choice_map(row)
    label = str(row.get("answerKey", ""))
    answer = choices.get(label, label)
    rendered_choices = "; ".join(f"{key}: {value}" for key, value in choices.items())
    return {
        "id": f"external:arc:{slug(subset)}:{split}:{index}",
        "source": source_block(dataset_meta, split, seed, subset, str(row.get("id") or index)),
        "question": f"{row['question']}\nChoices: {rendered_choices}\nAnswer with the choice and a short reason.",
        "expect": [answer],
        "expected_route": "general_reference",
        "eval_only": True,
        "answer_key": label,
    }


def convert_commonsenseqa(row: dict[str, Any], dataset_meta: dict[str, Any], split: str, seed: int, index: int) -> dict[str, Any]:
    choices = _choice_map(row)
    label = str(row.get("answerKey", ""))
    answer = choices.get(label, label)
    rendered_choices = "; ".join(f"{key}: {value}" for key, value in choices.items())
    return {
        "id": f"external:commonsenseqa:{split}:{index}",
        "source": source_block(dataset_meta, split, seed, None, str(row.get("id") or index)),
        "question": f"{row['question']}\nChoices: {rendered_choices}\nAnswer with the choice and a short reason.",
        "expect": [answer],
        "expected_route": "general_reference",
        "eval_only": True,
        "answer_key": label,
    }


def convert_bigbench(
    row: dict[str, Any],
    dataset_meta: dict[str, Any],
    split: str,
    seed: int,
    task_name: str,
    index: int,
) -> dict[str, Any]:
    question = first_text(row.get("input")).strip()
    if not question:
        raise SystemExit(f"BIG-bench task {task_name!r} example {index} has no input")

    expected: list[str] = []
    target_scores = row.get("target_scores")
    if isinstance(target_scores, dict):
        best_score = max((float(score) for score in target_scores.values()), default=0.0)
        expected = [str(choice) for choice, score in target_scores.items() if float(score) == best_score and best_score > 0]
        rendered_choices = "; ".join(str(choice) for choice in target_scores)
        question = f"{question}\nChoices: {rendered_choices}\nAnswer with the choice and a short reason."
    else:
        target = row.get("target")
        if isinstance(target, list):
            expected = [str(item) for item in target if str(item).strip()]
        elif target is not None and str(target).strip():
            expected = [str(target)]

    if not expected:
        raise SystemExit(f"BIG-bench task {task_name!r} example {index} has no positive target")

    # This broadens eval scoring only; it does not change live ATLAS behavior.
    if any(item.casefold() == "unknown" for item in expected):
        expected.extend(["could not verify", "cannot verify", "not known", "cannot know", "insufficient information"])

    source = source_block(dataset_meta, split, seed, task_name, str(index))
    source["task"] = task_name
    return {
        "id": f"external:bigbench:{slug(task_name)}:{index}",
        "source": source,
        "question": question,
        "expect_any": expected,
        "forbid": [],
        "expected_route": "general_reference",
        "eval_only": True,
    }


def load_bigbench_task_json(
    dataset_meta: dict[str, Any],
    task_path: pathlib.Path,
    count: int,
    seed: int,
) -> list[dict[str, Any]]:
    task = json.loads(task_path.read_text(encoding="utf-8"))
    examples = task.get("examples")
    if not isinstance(examples, list) or not examples:
        raise SystemExit(f"BIG-bench JSON task has no examples: {task_path}")
    task_name = str(task.get("name") or task_path.parent.name or task_path.stem)
    indices = sample_indices(len(examples), min(count, len(examples)), seed)
    cases = [convert_bigbench(dict(examples[index]), dataset_meta, "task_json", seed, task_name, index) for index in indices]
    for case in cases:
        case["source"]["canary"] = task.get("canary")
        case["source"]["local_task_file"] = str(task_path)
    return cases


def sample_indices(length: int, count: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    if count >= length:
        indices = list(range(length))
        rng.shuffle(indices)
        return indices
    return rng.sample(range(length), count)


def generate_cases(dataset_meta: dict[str, Any], count: int, seed: int, split: str | None, subset: str | None) -> list[dict[str, Any]]:
    name = dataset_meta["name"]
    default_sample = dataset_meta.get("initial_sample") or {}
    split = split or default_sample.get("split") or "validation"

    cases: list[dict[str, Any]] = []
    if name == "TruthfulQA":
        data = load_hf_dataset(dataset_meta, subset=None, split=split)
        for index in sample_indices(len(data), count, seed):
            cases.append(convert_truthfulqa(dict(data[index]), dataset_meta, split, seed, index))
        return cases

    if name == "ARC / AI2 Reasoning Challenge":
        subsets = [subset] if subset else default_sample.get("subsets") or ["ARC-Easy", "ARC-Challenge"]
        per_subset = max(1, count // len(subsets))
        remainder = max(0, count - (per_subset * len(subsets)))
        for subset_index, current_subset in enumerate(subsets):
            current_count = per_subset + (1 if subset_index < remainder else 0)
            data = load_hf_dataset(dataset_meta, subset=current_subset, split=split)
            for index in sample_indices(len(data), current_count, seed + subset_index):
                cases.append(convert_arc(dict(data[index]), dataset_meta, split, seed, current_subset, index))
        return cases[:count]

    if name == "CommonsenseQA":
        data = load_hf_dataset(dataset_meta, subset=None, split=split)
        for index in sample_indices(len(data), count, seed):
            cases.append(convert_commonsenseqa(dict(data[index]), dataset_meta, split, seed, index))
        return cases

    raise SystemExit(f"No importer implemented for {name}")


def generate_cases_from_fixture(
    dataset_meta: dict[str, Any],
    fixture_path: pathlib.Path,
    count: int,
    seed: int,
    split: str | None,
    subset: str | None,
) -> list[dict[str, Any]]:
    rows = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit(f"Fixture must be a JSON list: {fixture_path}")

    name = dataset_meta["name"]
    split = split or "fixture"
    indices = sample_indices(len(rows), min(count, len(rows)), seed)
    cases: list[dict[str, Any]] = []
    for index in indices:
        row = dict(rows[index])
        if name == "TruthfulQA":
            cases.append(convert_truthfulqa(row, dataset_meta, split, seed, index))
        elif name == "ARC / AI2 Reasoning Challenge":
            cases.append(convert_arc(row, dataset_meta, split, seed, subset or "fixture", index))
        elif name == "CommonsenseQA":
            cases.append(convert_commonsenseqa(row, dataset_meta, split, seed, index))
        else:
            raise SystemExit(f"No fixture converter implemented for {name}")
    return cases


def write_suite(cases: list[dict[str, Any]], dataset_meta: dict[str, Any], output_dir: pathlib.Path, seed: int) -> pathlib.Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    path = output_dir / f"atlas-external-{slug(dataset_meta['name'])}-{len(cases)}-{seed}-{timestamp}.json"
    path.write_text(json.dumps(cases, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", help="Dataset alias, e.g. truthfulqa, arc, commonsenseqa, bigbench")
    parser.add_argument("--manifest", default=str(MANIFEST_PATH))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--split", help="Override dataset split")
    parser.add_argument("--subset", help="Override dataset subset/config where applicable")
    parser.add_argument("--fixture-json", help="Use a local JSON fixture instead of loading from Hugging Face")
    parser.add_argument(
        "--bigbench-task-json",
        action="append",
        help="Import a reviewed local BIG-bench task.json file; repeat for a mixed suite",
    )
    parser.add_argument("--dry-run", action="store_true", help="Resolve metadata without loading the dataset")
    args = parser.parse_args()

    manifest = load_manifest(pathlib.Path(args.manifest))
    dataset_meta = resolve_dataset(manifest, args.dataset)
    require_import_allowed(dataset_meta)

    if args.dry_run:
        print(json.dumps(dataset_meta, indent=2))
        return 0

    if dataset_meta["name"] == "BIG-bench JSON tasks":
        if not args.bigbench_task_json:
            raise SystemExit("BIG-bench imports require at least one --bigbench-task-json with a reviewed local task.json file")
        task_paths = [pathlib.Path(item) for item in args.bigbench_task_json]
        per_task, remainder = divmod(args.count, len(task_paths))
        cases = []
        for task_index, task_path in enumerate(task_paths):
            task_count = per_task + (1 if task_index < remainder else 0)
            if task_count:
                cases.extend(load_bigbench_task_json(dataset_meta, task_path, task_count, args.seed + task_index))
    elif args.fixture_json:
        cases = generate_cases_from_fixture(
            dataset_meta,
            pathlib.Path(args.fixture_json),
            args.count,
            args.seed,
            args.split,
            args.subset,
        )
    else:
        cases = generate_cases(dataset_meta, args.count, args.seed, args.split, args.subset)
    output_path = write_suite(cases, dataset_meta, pathlib.Path(args.output_dir), args.seed)
    print(f"Wrote {len(cases)} eval cases to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
