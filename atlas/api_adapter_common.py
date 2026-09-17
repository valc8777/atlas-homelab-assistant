#!/usr/bin/env python3

import hashlib
import json
import os
import pathlib
import time
import urllib.parse
import urllib.request
from typing import Any


MODULE_DIR = pathlib.Path(__file__).resolve().parent


def default_cache_dir() -> pathlib.Path:
    env_path = os.environ.get("HOMELAB_AI_CACHE_DIR")
    if env_path:
        return pathlib.Path(env_path)

    repo_candidate = MODULE_DIR.parents[1] / ".cache" / "ai-api" if len(MODULE_DIR.parents) >= 2 else None
    if repo_candidate and repo_candidate.parent.parent.name:
        return repo_candidate

    return MODULE_DIR / ".cache" / "ai-api"


DEFAULT_CACHE_DIR = default_cache_dir()


def cache_dir() -> pathlib.Path:
    path = pathlib.Path(os.environ.get("HOMELAB_AI_CACHE_DIR", str(DEFAULT_CACHE_DIR)))
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_identity(url: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": url,
        "params": params,
    }


def build_cache_key(namespace: str, identity: dict[str, Any]) -> str:
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{namespace}-{digest}.json"


def load_cache(namespace: str, url: str, params: dict[str, Any], ttl_seconds: int) -> dict[str, Any] | None:
    path = cache_dir() / build_cache_key(namespace, cache_identity(url, params))
    if not path.exists():
        return None
    age_seconds = time.time() - path.stat().st_mtime
    if age_seconds > ttl_seconds:
        return None
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    payload["_cache"] = {
        "hit": True,
        "path": str(path),
        "age_seconds": round(age_seconds, 1),
        "ttl_seconds": ttl_seconds,
    }
    return payload


def save_cache(
    namespace: str,
    url: str,
    params: dict[str, Any],
    payload: dict[str, Any],
    ttl_seconds: int,
) -> dict[str, Any]:
    path = cache_dir() / build_cache_key(namespace, cache_identity(url, params))
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    payload["_cache"] = {
        "hit": False,
        "path": str(path),
        "age_seconds": 0,
        "ttl_seconds": ttl_seconds,
    }
    return payload


def fetch_json(url: str, params: dict[str, Any], headers: dict[str, str], timeout: int = 30) -> dict[str, Any]:
    request_url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    request = urllib.request.Request(request_url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_json_cached(
    namespace: str,
    url: str,
    params: dict[str, Any],
    headers: dict[str, str],
    ttl_seconds: int,
    timeout: int = 30,
) -> dict[str, Any]:
    cached = load_cache(namespace, url, params, ttl_seconds)
    if cached is not None:
        return cached
    payload = fetch_json(url, params, headers=headers, timeout=timeout)
    return save_cache(namespace, url, params, payload, ttl_seconds)


def cache_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return payload.get("_cache") or {
        "hit": False,
        "path": None,
        "age_seconds": 0,
        "ttl_seconds": None,
    }
