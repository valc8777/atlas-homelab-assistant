#!/usr/bin/env python3

import json
from datetime import datetime

ROOT = "/Users/valdemort/Projects/homelab"
OUT_MD = f"{ROOT}/homelab-service-location-index-2026-08-06.md"
OUT_JSON = f"{ROOT}/homelab-service-location-index-2026-08-06.json"

SERVICES = [
    {
        "service": "Homepage",
        "aliases": ["homepage"],
        "host": "docker-main",
        "guest": "VM 105",
        "stack_path": "/opt/stacks/managers",
        "config_path": "/opt/stacks/homepage/config",
        "urls": ["http://100.64.1.20:3000"],
    },
    {
        "service": "Nextcloud AIO",
        "aliases": ["nextcloud", "nextcloud aio"],
        "host": "docker-main",
        "guest": "VM 105",
        "stack_path": "/opt/stacks/nextcloud",
        "config_path": "/opt/stacks/nextcloud",
        "urls": ["https://docker-main.example-tailnet.ts.net/nextcloud/apps/dashboard/"],
    },
    {
        "service": "Vaultwarden",
        "aliases": ["vaultwarden"],
        "host": "docker-main",
        "guest": "VM 105",
        "stack_path": "/opt/stacks/security",
        "config_path": "/opt/stacks/security/vw-data",
        "urls": ["https://vaultwarden.example-tailnet.ts.net"],
    },
    {
        "service": "Navidrome",
        "aliases": ["navidrome"],
        "host": "docker-main",
        "guest": "VM 105",
        "stack_path": "/opt/stacks/navidrome",
        "config_path": "/opt/stacks/navidrome/config",
        "urls": ["http://100.64.1.20:4533"],
    },
    {
        "service": "Music Hub",
        "aliases": ["music hub", "music-hub"],
        "host": "docker-main",
        "guest": "VM 105",
        "stack_path": "/opt/stacks/music-hub",
        "config_path": "/opt/stacks/music-hub",
        "urls": ["http://100.64.1.20:4540"],
    },
    {
        "service": "Kavita",
        "aliases": ["kavita"],
        "host": "docker-main",
        "guest": "VM 105",
        "stack_path": "/opt/stacks/kavita",
        "config_path": "/opt/stacks/kavita/config",
        "urls": ["http://100.64.1.20:5000"],
    },
    {
        "service": "Audiobookshelf",
        "aliases": ["audiobookshelf"],
        "host": "docker-main",
        "guest": "VM 105",
        "stack_path": "/opt/stacks/audiobookshelf",
        "config_path": "/opt/stacks/audiobookshelf/config",
        "urls": ["http://100.64.1.20:13378"],
    },
    {
        "service": "qBittorrent",
        "aliases": ["qbittorrent", "qbit"],
        "host": "docker-main",
        "guest": "VM 105",
        "stack_path": "/opt/stacks/servarr",
        "config_path": "/opt/stacks/qbittorrent/config",
        "urls": ["http://100.64.1.20:8090"],
    },
    {
        "service": "Prowlarr",
        "aliases": ["prowlarr"],
        "host": "docker-main",
        "guest": "VM 105",
        "stack_path": "/opt/stacks/servarr",
        "config_path": "/opt/stacks/servarr/config/prowlarr",
        "urls": ["http://100.64.1.20:9696"],
    },
    {
        "service": "Sonarr",
        "aliases": ["sonarr"],
        "host": "docker-main",
        "guest": "VM 105",
        "stack_path": "/opt/stacks/servarr",
        "config_path": "/opt/stacks/servarr/config/sonarr",
        "urls": ["http://100.64.1.20:8989"],
    },
    {
        "service": "Radarr",
        "aliases": ["radarr"],
        "host": "docker-main",
        "guest": "VM 105",
        "stack_path": "/opt/stacks/servarr",
        "config_path": "/opt/stacks/servarr/config/radarr",
        "urls": ["http://100.64.1.20:7878"],
    },
    {
        "service": "Lidarr",
        "aliases": ["lidarr"],
        "host": "docker-main",
        "guest": "VM 105",
        "stack_path": "/opt/stacks/servarr",
        "config_path": "/opt/stacks/lidarr/config",
        "urls": ["http://100.64.1.20:8686"],
    },
    {
        "service": "Mylar3",
        "aliases": ["mylar", "mylar3"],
        "host": "docker-main",
        "guest": "VM 105",
        "stack_path": "/opt/stacks/servarr",
        "config_path": "/opt/stacks/mylar3/config",
        "urls": ["http://100.64.1.20:8092"],
    },
    {
        "service": "Home Assistant",
        "aliases": ["home assistant", "homeassistant", "ha"],
        "host": "zola-cerebro (forwarder) -> VM 205",
        "guest": "VM 205",
        "stack_path": "n/a (HAOS)",
        "config_path": "n/a (HAOS)",
        "urls": [
            "http://zola-cerebro.example-tailnet.ts.net:18124 (Tailscale, via socat forwarder on zola-cerebro)"
        ],
    },
    {
        "service": "Jellyseerr (docker-main)",
        "aliases": ["jellyseerr docker-main", "jellyseerr on docker-main"],
        "host": "docker-main",
        "guest": "VM 105",
        "stack_path": "/opt/stacks/servarr",
        "config_path": "/opt/stacks/jellyseerr/config",
        "urls": ["https://docker-main.example-tailnet.ts.net:5055"],
    },
    {
        "service": "Open WebUI",
        "aliases": ["open webui", "open-webui"],
        "host": "ai-stack",
        "guest": "VM 201",
        "stack_path": "/opt/stacks/ai-stack",
        "config_path": "/opt/stacks/ai-stack/webui_data",
        "urls": ["http://100.64.1.10:3000"],
    },
    {
        "service": "ATLAS",
        "aliases": ["atlas", "atlas mobile", "atlas pwa"],
        "host": "ai-stack",
        "guest": "VM 201",
        "stack_path": "/opt/stacks/ai-stack",
        "config_path": "/opt/stacks/ai-stack/atlas-shortcuts",
        "urls": ["http://ai-stack.example-tailnet.ts.net:8787/mobile/"],
    },
    {
        "service": "Ollama",
        "aliases": ["ollama"],
        "host": "ai-stack",
        "guest": "VM 201",
        "stack_path": "/opt/stacks/ai-stack",
        "config_path": "/opt/stacks/ai-stack/ollama_data",
        "urls": [],
    },
    {
        "service": "SearXNG",
        "aliases": ["searxng", "searx"],
        "host": "ai-stack",
        "guest": "VM 201",
        "stack_path": "/opt/stacks/ai-stack",
        "config_path": "/opt/stacks/ai-stack/SearXNG",
        "urls": ["http://100.64.1.10:8080"],
    },
    {
        "service": "Piper TTS",
        "aliases": ["piper", "piper tts", "piper-tts"],
        "host": "ai-stack",
        "guest": "VM 201",
        "stack_path": "/opt/stacks/ai-stack",
        "config_path": "/opt/stacks/ai-stack/piper_config",
        "urls": ["http://100.64.1.10:8000"],
    },
    {
        "service": "Jellyfin",
        "aliases": ["jellyfin"],
        "host": "ai-stack",
        "guest": "VM 201",
        "stack_path": "/opt/stacks/ai-stack",
        "config_path": "/opt/stacks/ai-stack/jellyfin_config",
        "urls": ["https://jellyfin.example-tailnet.ts.net/web", "http://100.64.1.10:8096"],
    },
    {
        "service": "Jellyseerr (ai-stack)",
        "aliases": ["jellyseerr ai-stack", "jellyseerr on ai-stack"],
        "host": "ai-stack",
        "guest": "VM 201",
        "stack_path": "/opt/stacks/ai-stack",
        "config_path": "/opt/stacks/ai-stack/config",
        "urls": ["http://100.64.1.10:5055"],
    },
    {
        "service": "Caddy",
        "aliases": ["caddy"],
        "host": "ai-stack",
        "guest": "VM 201",
        "stack_path": "/opt/stacks/ai-stack",
        "config_path": "/opt/stacks/ai-stack/Caddyfile",
        "urls": ["https://100.64.1.10"],
    },
    {
        "service": "Proxmox VE",
        "aliases": ["proxmox", "proxmox ve", "pve"],
        "host": "pve",
        "guest": "host",
        "stack_path": "/etc/pve",
        "config_path": "/etc/pve",
        "urls": ["https://zola-cerebro.example-tailnet.ts.net:8006"],
    },
    {
        "service": "Proxmox Backup Server",
        "aliases": ["pbs", "proxmox backup server"],
        "host": "Backup",
        "guest": "CT 102",
        "stack_path": "/",
        "config_path": "/etc/proxmox-backup",
        "urls": ["https://100.64.1.40:8007"],
    },
    {
        "service": "cync-lan",
        "aliases": ["cync-lan", "cync lan", "ge cync local control"],
        "host": "ai-stack",
        "guest": "VM 201",
        "stack_path": "/opt/stacks/cync-lan",
        "config_path": "/opt/stacks/cync-lan/config",
        "urls": ["http://192.168.1.80:23778"],
    },
    {
        "service": "Mosquitto (cync-lan broker)",
        "aliases": ["mosquitto", "cync-mosquitto", "mqtt broker"],
        "host": "ai-stack",
        "guest": "VM 201",
        "stack_path": "/opt/stacks/cync-lan",
        "config_path": "/opt/stacks/cync-lan/mosquitto",
        "urls": [],
    },
]


def main():
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": stamp, "services": SERVICES}, fh, indent=2)
        fh.write("\n")

    lines = [
        "# Homelab Service Location Index",
        "",
        f"Generated: {stamp}",
        "",
        "Purpose: provide one compact placement index so the AI can answer 'where does this service live?' questions from a stable, explicit source.",
        "",
        "| Service | Aliases | Host | Guest | Stack Path | Config Path | URLs |",
        "|---|---|---|---|---|---|---|",
    ]

    for item in SERVICES:
        aliases = ", ".join(f"`{alias}`" for alias in item["aliases"])
        urls = "<br>".join(f"`{url}`" for url in item["urls"]) if item["urls"] else "-"
        lines.append(
            f"| {item['service']} | {aliases} | `{item['host']}` | `{item['guest']}` | "
            f"`{item['stack_path']}` | `{item['config_path']}` | {urls} |"
        )

    lines.extend(
        [
            "",
            "## Answering Rules",
            "",
            "- Use this file first for placement, host, stack-path, and config-path questions.",
            "- Use runtime-status and reachability sources separately for current up/down state.",
        ]
    )

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
