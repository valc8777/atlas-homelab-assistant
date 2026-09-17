#!/bin/zsh

set -euo pipefail

ROOT="/Users/valdemort/Projects/homelab"
OUT_MD="$ROOT/homelab-host-health-2026-08-06.md"
OUT_JSON="$ROOT/homelab-host-health-2026-08-06.json"
STAMP="$(date '+%Y-%m-%d %H:%M:%S %Z')"

docker_main="$(ssh root@100.64.1.20 "hostname && printf '\n'; uptime; printf '\n'; free -h; printf '\n'; df -h / /opt /mnt 2>/dev/null || true")"
ai_stack="$(ssh cerebro@100.64.1.10 "hostname && printf '\n'; uptime; printf '\n'; free -h; printf '\n'; df -h / /opt /mnt 2>/dev/null || true; printf '\n'; nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null || true")"
pve="$(ssh root@100.70.171.62 "hostname && printf '\n'; uptime; printf '\n'; free -h; printf '\n'; df -h / /var/lib/vz /mnt 2>/dev/null || true")"

cat > "$OUT_MD" <<EOF
# Homelab Host Health

Generated: $STAMP

Purpose: provide a current host-level health snapshot for CPU/load, memory, storage, and GPU summary.

## Docker-Main

\`\`\`text
$docker_main
\`\`\`

## AI-Stack

\`\`\`text
$ai_stack
\`\`\`

## Proxmox Host

\`\`\`text
$pve
\`\`\`
EOF

python3 - <<'PY' "$OUT_JSON" "$STAMP" "$docker_main" "$ai_stack" "$pve"
import json
import sys

out_json, stamp, docker_main, ai_stack, pve = sys.argv[1:]

payload = {
    "generated_at": stamp,
    "docker_main": docker_main,
    "ai_stack": ai_stack,
    "pve": pve,
}

with open(out_json, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)
    fh.write("\n")
PY
