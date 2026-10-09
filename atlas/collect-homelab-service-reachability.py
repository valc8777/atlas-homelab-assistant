#!/usr/bin/env python3

import json
import ssl
import urllib.error
import urllib.request
from datetime import datetime

SERVICES = [
    ("Homepage", "http://100.64.1.20:3000"),
    ("Nextcloud", "https://docker-main.example-tailnet.ts.net/nextcloud/apps/dashboard/"),
    ("Jellyseerr (docker-main)", "https://docker-main.example-tailnet.ts.net:5055"),
    ("Navidrome", "http://100.64.1.20:4533"),
    ("Kavita", "http://100.64.1.20:5000"),
    ("Open WebUI", "http://100.64.1.10:3000"),
    ("Jellyseerr (ai-stack)", "http://100.64.1.10:5055"),
    ("Piper TTS", "http://100.64.1.10:8000"),
    ("PBS", "https://100.64.1.40:8007"),
    ("Proxmox VE", "https://100.64.1.30:8006"),
]

ROOT = "/Users/valdemort/Projects/homelab"
OUT_MD = f"{ROOT}/homelab-service-reachability-2026-08-06.md"
OUT_JSON = f"{ROOT}/homelab-service-reachability-2026-08-06.json"


def probe(name: str, url: str) -> dict:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "homelab-ai-reachability/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            return {
                "name": name,
                "url": url,
                "ok": True,
                "status": resp.status,
                "reason": getattr(resp, "reason", ""),
            }
    except urllib.error.HTTPError as exc:
        return {
            "name": name,
            "url": url,
            "ok": True,
            "status": exc.code,
            "reason": str(exc.reason),
        }
    except Exception as exc:
        return {
            "name": name,
            "url": url,
            "ok": False,
            "status": None,
            "reason": str(exc),
        }


def main():
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    results = [probe(name, url) for name, url in SERVICES]

    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": stamp, "services": results}, fh, indent=2)
        fh.write("\n")

    lines = [
        "# Homelab Service Reachability",
        "",
        f"Generated: {stamp}",
        "",
        "Purpose: provide a current browser/API reachability snapshot for major homelab entry points.",
        "",
        "| Service | URL | Result |",
        "|---|---|---|",
    ]
    for item in results:
        result = f"{item['status']} {item['reason']}".strip() if item["ok"] else f"FAIL {item['reason']}"
        lines.append(f"| {item['name']} | `{item['url']}` | {result} |")

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
