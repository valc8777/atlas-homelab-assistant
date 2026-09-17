#!/usr/bin/env python3

"""Adapter for triggering the comic-bot Playwright container on docker-main.

Mirrors atlas_home_assistant.py's validated-HTTP-call style: a single
narrow, allowlisted-shape request to a private-network endpoint, bounded
timeout, no arbitrary URL/host acceptance. See DEC-2026-09-10-04.

Unlike atlas_home_assistant.py this is a write action with a real external
side effect (driving a browser against a third-party site, writing a file
outside ATLAS's own storage) -- it is only ever called from
atlas_app_store.execute_confirmed_action() after an owner has approved the
corresponding pending confirmation, never directly from chat or a shortlist.
"""

from __future__ import annotations

import ipaddress
import json
import urllib.parse
import urllib.request


def validate_trigger_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("comic-bot trigger URL must use http or https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("comic-bot trigger URL must not contain credentials, query, or fragment")
    host = parsed.hostname.lower()
    private_host = host in {"localhost", "docker-main"} or host.endswith(".ts.net")
    try:
        private_host = private_host or ipaddress.ip_address(host).is_private
    except ValueError:
        pass
    if not private_host:
        raise ValueError("comic-bot trigger URL must use a private-network endpoint")
    return value.rstrip("/")


def trigger_comic_request(
    base_url: str,
    token: str,
    value: str,
    result_index: int | None = None,
    timeout: int = 120,
    opener=urllib.request.urlopen,
) -> dict:
    """POST the owner-approved value to docker-main's trigger listener.

    `result_index` optionally overrides which search result comic-bot picks
    (default 0, the top result) -- for the rare case a series' search
    results rank an unwanted match (e.g. a TPB collection) ahead of the
    intended single issue. See DEC-2026-09-10-07.

    Returns the listener's JSON body on HTTP 200. Raises on missing
    configuration, a non-2xx response, or a network/timeout error -- the
    caller (atlas_app_store's executor) is responsible for turning that into
    a failed, retriable confirmation rather than a silent no-op.
    """
    if not token:
        raise ValueError("comic-bot trigger token is not configured")
    if not value or not value.strip():
        raise ValueError("comic-bot request value must not be empty")
    url = f"{validate_trigger_url(base_url)}/run-comic-bot"
    body = {"comic_x_value": value}
    if result_index is not None:
        body["result_index"] = result_index
    request = urllib.request.Request(
        url,
        method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with opener(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("comic-bot trigger returned a non-object response")
    return payload
