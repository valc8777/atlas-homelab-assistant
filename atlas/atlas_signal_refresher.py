#!/usr/bin/env python3
"""Separate-cadence collector for stale-prone ATLAS Phase 2b signals."""

from __future__ import annotations

import argparse
import os
import pathlib
import json
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import atlas_app_store


LOCAL_TZ = ZoneInfo("America/Los_Angeles")
DEFAULT_DB = pathlib.Path(os.environ.get("ATLAS_APP_DB", "/app/atlas-app/atlas-app.db"))
SSH_KEY = os.environ.get("ATLAS_PHASE2B_SSH_KEY", "/run/atlas-monitor/atlas_phase2b_ed25519")
SSH_KNOWN_HOSTS = os.environ.get("ATLAS_PHASE2B_KNOWN_HOSTS", "/run/atlas-monitor/known_hosts")
SERVICES = (
    ("Homepage", "http://100.64.1.20:3000"),
    ("Nextcloud", "https://docker-main.example-tailnet.ts.net/nextcloud/apps/dashboard/"),
    ("Jellyseerr (docker-main)", "https://docker-main.example-tailnet.ts.net:5055"),
    ("Navidrome", "http://100.64.1.20:4533"),
    ("Kavita", "http://100.64.1.20:5000"),
    ("Open WebUI", "http://100.64.1.10:3000"),
    ("Jellyseerr (ai-stack)", "http://100.64.1.10:5055"),
    ("Piper TTS", "http://100.64.1.10:8000"),
    ("PBS", "https://100.123.180.115:8007"),
    ("Proxmox VE", "https://100.70.171.62:8006"),
)
HOSTS = (("docker_main", "root", "100.64.1.20"), ("ai_stack", "cerebro", "100.64.1.10"), ("proxmox", "root", "100.70.171.62"))


def probe(name: str, url: str, timeout: int = 10, opener=None) -> dict:
    opener = opener or urllib.request.urlopen
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "atlas-signal-refresher/1.0"})
    try:
        with opener(request, timeout=timeout, context=context) as response:
            return {"name": name, "ok": True, "status": response.status}
    except urllib.error.HTTPError as exc:
        return {"name": name, "ok": True, "status": exc.code}
    except Exception as exc:
        return {"name": name, "ok": False, "status": None, "error": type(exc).__name__}


def refresh_service_reachability(db_path: pathlib.Path, services=SERVICES) -> dict:
    with ThreadPoolExecutor(max_workers=min(10, max(1, len(services)))) as executor:
        results = list(executor.map(lambda item: probe(*item), services))
    value = {
        "total": len(results),
        "unreachable_count": sum(not item["ok"] for item in results),
        "services": results,
    }
    return atlas_app_store.put_signal_observation(
        db_path, "service_reachability", value, observed_at=datetime.now(LOCAL_TZ).isoformat(timespec="seconds")
    )


def run_remote(user: str, host: str, command: str) -> dict:
    raw = subprocess.check_output(
        [
            "ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", f"IdentityFile={SSH_KEY}", "-o", f"UserKnownHostsFile={SSH_KNOWN_HOSTS}",
            "-o", "ConnectTimeout=10", f"{user}@{host}", command,
        ],
        text=True,
        timeout=45,
    )
    return json.loads(raw)


def assess_host_health(metrics: dict) -> tuple[bool, list[str]]:
    reasons = []
    total = int(metrics.get("memory_total") or 0)
    available = int(metrics.get("memory_available") or 0)
    if total and available / total < 0.10:
        reasons.append("memory_available_below_10_percent")
    cpu_count = max(1, int(metrics.get("cpu_count") or 1))
    if float(metrics.get("load_1m") or 0) > cpu_count * 2:
        reasons.append("load_1m_above_2x_cpu_count")
    for mount in metrics.get("mounts") or []:
        disk_total = int(mount.get("total") or 0)
        if disk_total and int(mount.get("used") or 0) / disk_total >= 0.90:
            reasons.append(f"disk_{mount.get('mount')}_at_or_above_90_percent")
    return bool(reasons), reasons


def refresh_host_health(db_path: pathlib.Path, hosts=HOSTS) -> dict:
    def collect(item):
        name, user, host = item
        metrics = run_remote(user, host, "host-health")
        unhealthy, reasons = assess_host_health(metrics)
        return {"name": name, "unhealthy": unhealthy, "reasons": reasons, "metrics": metrics}

    with ThreadPoolExecutor(max_workers=len(hosts)) as executor:
        results = list(executor.map(collect, hosts))
    value = {"total": len(results), "unhealthy_count": sum(item["unhealthy"] for item in results), "hosts": results}
    return atlas_app_store.put_signal_observation(
        db_path, "host_health", value, observed_at=datetime.now(LOCAL_TZ).isoformat(timespec="seconds")
    )


def refresh_backup_status(db_path: pathlib.Path) -> dict:
    raw = run_remote("root", "100.70.171.62", "backup-status")
    now_epoch = int(datetime.now(LOCAL_TZ).timestamp())
    last_ok = raw.get("last_ok")
    last_ok_epoch = int(last_ok.get("starttime") or 0) if isinstance(last_ok, dict) else 0
    value = {
        "latest_ok": bool(raw.get("latest") and raw["latest"].get("status") == "OK"),
        "last_success_age_hours": round((now_epoch - last_ok_epoch) / 3600, 2) if last_ok_epoch else None,
        "task_count": raw.get("task_count", 0),
        "last_failure": raw.get("last_failure"),
    }
    return atlas_app_store.put_signal_observation(
        db_path, "backup_status", value, observed_at=datetime.now(LOCAL_TZ).isoformat(timespec="seconds")
    )


def refresh_all(db_path: pathlib.Path) -> list[dict]:
    results = []
    for signal_name, callback in (
        ("service_reachability", refresh_service_reachability),
        ("host_health", refresh_host_health),
        ("backup_status", refresh_backup_status),
    ):
        try:
            results.append(callback(db_path))
        except Exception as exc:
            results.append(atlas_app_store.put_signal_observation(
                db_path, signal_name, None, status="error",
                observed_at=datetime.now(LOCAL_TZ).isoformat(timespec="seconds"), error=type(exc).__name__,
            ))
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    atlas_app_store.init_db(args.db)
    while True:
        results = refresh_all(args.db)
        print([{"signal_name": item["signal_name"], "status": item["status"], "observed_at": item["observed_at"]} for item in results], flush=True)
        if args.once:
            return
        time.sleep(max(60, args.interval_seconds))


if __name__ == "__main__":
    main()
