#!/usr/bin/env python3

import json
import pathlib
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request


def load_api_key(db_path: pathlib.Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        row = cur.execute(
            """
            select ak.key
            from api_key ak
            join user u on u.id = ak.user_id
            where ak.key like 'sk-%' and u.role = 'admin'
            order by ak.created_at asc
            limit 1
            """
        ).fetchone()
        if not row or not row[0]:
            raise RuntimeError(f"No Open WebUI API key found in {db_path}")
        return row[0]
    finally:
        conn.close()


def api_request(base_url: str, api_key: str, method: str, path: str, payload=None):
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    headers = {"Authorization": f"Bearer {api_key}"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def timed_api_request(base_url: str, api_key: str, method: str, path: str, payload=None, timeout: int = 300):
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    headers = {"Authorization": f"Bearer {api_key}"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    started = time.monotonic()
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            elapsed = time.monotonic() - started
            return {
                "status": resp.status,
                "elapsed_seconds": round(elapsed, 3),
                "json": json.loads(raw.decode("utf-8")) if raw else None,
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        elapsed = time.monotonic() - started
        raise RuntimeError(
            f"{method} {path} failed after {elapsed:.3f}s: {exc.code} {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        elapsed = time.monotonic() - started
        raise RuntimeError(f"{method} {path} failed after {elapsed:.3f}s: {exc}") from exc


def choose_model(models_response: dict, preferred_model: str | None) -> str:
    """Resolve the model to use. The caller must name it explicitly.

    There is deliberately no implicit fallback. This function previously
    defaulted, in order, to `huihui_ai/qwen2.5-abliterate:latest` and then
    `roblox-coder:latest`, and finally to whatever model happened to be listed
    first. The first of those is an uncensored model with its safety training
    stripped out, which meant a single unset `ATLAS_MODEL` would silently route
    owner-facing answers through it -- the opposite of the privacy-first,
    owner-only posture in `DEC-2026-08-14-01`. Selecting a model is a policy
    decision, so it fails loudly instead of guessing (`DEF-008`).
    """
    models = models_response.get("data", [])
    if not preferred_model:
        raise RuntimeError(
            "No model specified. Pass --model explicitly or set ATLAS_MODEL; "
            "implicit model selection was removed because it defaulted to a "
            "safety-training-stripped model (see DEF-008 in "
            "ATLAS_CAPABILITY_ROADMAP.md)."
        )

    for model in models:
        if model.get("id") == preferred_model:
            return preferred_model

    available = sorted(
        str(model.get("id")) for model in models if model.get("id")
    )
    raise RuntimeError(
        f"Preferred model not found: {preferred_model}. "
        f"Available in Open WebUI: {', '.join(available) or '(none)'}. "
        "Note that Open WebUI registers newly pulled Ollama models with "
        "is_active=0, which hides them from /api/models until enabled."
    )


def extract_text(response: dict) -> str:
    choices = response.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "") or ""
    if "message" in response:
        return response.get("message", {}).get("content", "") or ""
    return ""
