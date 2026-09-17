#!/usr/bin/env python3

import argparse
import json
import openwebui_client
import os
import pathlib
import re
import sys
import time


TOOLS_DIR = pathlib.Path(__file__).resolve().parent


def default_system_prompt_file() -> pathlib.Path:
    repo_candidate = TOOLS_DIR.parents[1] / "ai-evals" / "prompts" / "atlas-readonly-system.md"
    if repo_candidate.exists():
        return repo_candidate
    return TOOLS_DIR / "atlas-readonly-system.md"


DEFAULT_SYSTEM_PROMPT = default_system_prompt_file()


def reconfigure_stdio():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)


def normalize_check_text(value: str) -> str:
    # Remove simple markdown emphasis/code markers so exact facts still match.
    value = re.sub(r"[`*_#]", "", value)
    return value.lower()


def normalize_result_documents(result) -> list[dict]:
    documents = []
    if not result:
        return documents

    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    distances = result.get("distances") or result.get("scores") or []
    docs = result.get("documents") or []

    if (
        (ids and isinstance(ids[0], list))
        or (docs and isinstance(docs[0], list))
        or (metadatas and isinstance(metadatas[0], list))
        or (distances and isinstance(distances[0], list))
    ):
        first_ids = ids[0] if ids and isinstance(ids[0], list) else []
        first_metadatas = metadatas[0] if metadatas and isinstance(metadatas[0], list) else []
        first_distances = distances[0] if distances and isinstance(distances[0], list) else []
        first_docs = docs[0] if docs and isinstance(docs[0], list) else []
    else:
        first_ids = ids
        first_metadatas = metadatas
        first_distances = distances
        first_docs = docs

    if not first_ids and first_docs:
        first_ids = [metadata.get("file_id") or metadata.get("source") or f"doc-{idx}" for idx, metadata in enumerate(first_metadatas)]

    for idx, doc_id in enumerate(first_ids):
        metadata = first_metadatas[idx] if idx < len(first_metadatas) else {}
        text = first_docs[idx] if idx < len(first_docs) else ""
        score = first_distances[idx] if idx < len(first_distances) else None
        documents.append(
            {
                "id": doc_id,
                "score": score,
                "name": metadata.get("name"),
                "source": metadata.get("source"),
                "file_id": metadata.get("file_id"),
                "text_preview": text[:240],
            }
        )
    return documents


def run_chat_probe(args, base_url: str, api_key: str, knowledge: dict, model_id: str) -> tuple[dict, list[str]]:
    attempts = []
    failure_messages = []

    for attempt in range(1, args.chat_attempts + 1):
        payload = {
            "model": model_id,
            "stream": False,
            "messages": build_chat_messages(args.question, args.system_prompt_file),
            "files": [{"type": "collection", "id": knowledge["id"], "name": knowledge["name"]}],
        }
        if args.function_calling:
            payload["params"] = {"function_calling": args.function_calling}

        chat_call = openwebui_client.timed_api_request(
            base_url,
            api_key,
            "POST",
            "/api/chat/completions",
            payload,
            timeout=args.timeout,
        )
        response = chat_call["json"] or {}
        answer = openwebui_client.extract_text(response)
        normalized = normalize_check_text(answer)
        checks = [
            {"expect": needle, "matched": normalize_check_text(needle) in normalized}
            for needle in args.expect
        ]
        missing = [check["expect"] for check in checks if not check["matched"]]
        attempt_ok = bool(answer.strip()) and not missing
        attempts.append(
            {
                "attempt": attempt,
                "elapsed_seconds": chat_call["elapsed_seconds"],
                "answer": answer,
                "checks": checks,
                "sources_count": len(response.get("sources", [])),
                "ok": attempt_ok,
            }
        )
        if attempt_ok:
            return (
                {
                    "elapsed_seconds": chat_call["elapsed_seconds"],
                    "answer": answer,
                    "checks": checks,
                    "sources_count": len(response.get("sources", [])),
                    "attempts": attempts,
                    "attempts_used": attempt,
                },
                [],
            )

        failure_messages = []
        if not answer.strip():
            failure_messages.append("chat probe returned an empty answer")
        if missing:
            failure_messages.append(f"chat probe missing expected substrings: {', '.join(missing)}")

        if attempt < args.chat_attempts and args.retry_delay > 0:
            time.sleep(args.retry_delay)

    return (
        {
            "elapsed_seconds": attempts[-1]["elapsed_seconds"] if attempts else None,
            "answer": attempts[-1]["answer"] if attempts else "",
            "checks": attempts[-1]["checks"] if attempts else [],
            "sources_count": attempts[-1]["sources_count"] if attempts else 0,
            "attempts": attempts,
            "attempts_used": len(attempts),
        },
        failure_messages,
    )


def build_chat_messages(question: str, system_prompt_file: str | None) -> list[dict]:
    messages = []
    if system_prompt_file:
        system_prompt = pathlib.Path(system_prompt_file).read_text(encoding="utf-8").strip()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": question})
    return messages


def main():
    reconfigure_stdio()

    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--webui-db", default="/opt/stacks/ai-stack/webui_data/webui.db")
    parser.add_argument("--knowledge-name", default="Homelab Source of Truth")
    # See run-atlas-readonly.py: choose_model() has no implicit default any
    # more, so name the model here or via ATLAS_MODEL (DEF-008).
    parser.add_argument("--model", default=os.environ.get("ATLAS_MODEL"))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--mode", choices=["chat", "retrieval", "both"], default="both")
    parser.add_argument(
        "--function-calling",
        choices=["default", "native"],
        help="Pass through Open WebUI function-calling mode for chat probes.",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--chat-attempts",
        type=int,
        default=1,
        help="How many times to retry the chat probe before failing.",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=1.5,
        help="Seconds to wait between chat retries.",
    )
    parser.add_argument(
        "--question",
        default="Which host runs Open WebUI? Answer in one sentence and mention the host name.",
    )
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        help="Case-insensitive substring that must appear in the answer. Repeat for multiple checks.",
    )
    parser.add_argument("--system-prompt-file", default=str(DEFAULT_SYSTEM_PROMPT))
    args = parser.parse_args()
    if not args.expect:
        args.expect = ["ai-stack"]

    db_path = pathlib.Path(args.webui_db)
    if not db_path.is_file():
        raise SystemExit(f"Open WebUI DB not found: {db_path}")

    api_key = openwebui_client.load_api_key(db_path)
    knowledges = openwebui_client.api_request(args.base_url, api_key, "GET", "/api/v1/knowledge/?page=1") or {}
    items = knowledges.get("items", [])
    knowledge = next((item for item in items if item.get("name") == args.knowledge_name), None)
    if not knowledge:
        raise SystemExit(f"Knowledge base not found: {args.knowledge_name}")

    models_response = openwebui_client.api_request(args.base_url, api_key, "GET", "/api/models")
    model_id = openwebui_client.choose_model(models_response, args.model)

    result = {
        "knowledge_id": knowledge["id"],
        "knowledge_name": knowledge["name"],
        "model": model_id,
        "question": args.question,
        "mode": args.mode,
    }

    failure_messages = []

    if args.mode in ("retrieval", "both"):
        retrieval_payload = {
            "collection_names": [knowledge["id"]],
            "query": args.question,
            "k": args.top_k,
            "hybrid": True,
        }
        retrieval_call = openwebui_client.timed_api_request(
            args.base_url,
            api_key,
            "POST",
            "/api/v1/retrieval/query/collection",
            retrieval_payload,
            timeout=args.timeout,
        )
        retrieval_json = retrieval_call["json"] or {}
        retrieval_docs = normalize_result_documents(retrieval_json)
        retrieval_parts = []
        for doc in retrieval_docs:
            retrieval_parts.extend(
                [
                    doc.get("name", ""),
                    doc.get("source", ""),
                    doc.get("text_preview", ""),
                ]
            )
        retrieval_text = " ".join(filter(None, retrieval_parts)).lower()
        retrieval_checks = [
            {"expect": needle, "matched": needle.lower() in retrieval_text} for needle in args.expect
        ]
        if not any(check["matched"] for check in retrieval_checks):
            failure_messages.append("retrieval probe did not surface expected evidence")
        result["retrieval"] = {
            "elapsed_seconds": retrieval_call["elapsed_seconds"],
            "top_documents": retrieval_docs,
            "checks": retrieval_checks,
        }

    if args.mode in ("chat", "both"):
        chat_result, chat_failures = run_chat_probe(args, args.base_url, api_key, knowledge, model_id)
        result["chat"] = chat_result
        failure_messages.extend(chat_failures)

    print(json.dumps(result, indent=2), flush=True)

    if failure_messages:
        raise SystemExit("; ".join(failure_messages))


if __name__ == "__main__":
    main()
