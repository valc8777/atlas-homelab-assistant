#!/usr/bin/env python3
"""Forced-command endpoint for the ATLAS read-only monitoring key."""

import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time


def meminfo():
    values = {}
    for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
        key, raw = line.split(":", 1)
        values[key] = int(raw.strip().split()[0]) * 1024
    return values


def host_health():
    memory = meminfo()
    mounts = []
    for mount in ("/", "/opt", "/mnt", "/var/lib/vz"):
        if not pathlib.Path(mount).exists():
            continue
        usage = shutil.disk_usage(mount)
        mounts.append({"mount": mount, "total": usage.total, "used": usage.used, "free": usage.free})
    load = os.getloadavg()
    return {
        "hostname": socket.gethostname(),
        "observed_epoch": int(time.time()),
        "uptime_seconds": int(float(pathlib.Path("/proc/uptime").read_text().split()[0])),
        "cpu_count": os.cpu_count() or 1,
        "load_1m": load[0],
        "load_5m": load[1],
        "load_15m": load[2],
        "memory_total": memory.get("MemTotal", 0),
        "memory_available": memory.get("MemAvailable", 0),
        "mounts": mounts,
    }


def backup_status():
    raw = subprocess.check_output([
        "pvesh", "get", "/nodes/pve/tasks", "--typefilter", "vzdump",
        "--limit", "500", "--output-format", "json",
    ], text=True, timeout=30)
    tasks = sorted(
        [item for item in json.loads(raw) if item.get("type") == "vzdump"],
        key=lambda item: item.get("starttime", 0), reverse=True,
    )
    last_ok = next((item for item in tasks if item.get("status") == "OK"), None)
    last_failure = next((item for item in tasks if item.get("status") != "OK"), None)
    return {
        "observed_epoch": int(time.time()),
        "latest": tasks[0] if tasks else None,
        "last_ok": last_ok,
        "last_failure": last_failure,
        "task_count": len(tasks),
    }


command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
if command == "host-health":
    print(json.dumps(host_health(), sort_keys=True))
elif command == "backup-status" and shutil.which("pvesh"):
    print(json.dumps(backup_status(), sort_keys=True))
else:
    print("command not permitted", file=sys.stderr)
    raise SystemExit(126)
