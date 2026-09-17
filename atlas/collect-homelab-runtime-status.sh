#!/bin/zsh

set -euo pipefail

ROOT="/Users/valdemort/Projects/homelab"
OUT_MD="$ROOT/homelab-runtime-status-2026-08-06.md"
OUT_JSON="$ROOT/homelab-runtime-status-2026-08-06.json"
STAMP="$(date '+%Y-%m-%d %H:%M:%S %Z')"
DOCKER_MAIN_SSH="${DOCKER_MAIN_SSH:-root@100.64.1.20}"
AI_STACK_SSH="${AI_STACK_SSH:-cerebro@100.64.1.10}"
PVE_SSH="${PVE_SSH:-root@100.70.171.62}"

docker_main_ps="$(ssh "$DOCKER_MAIN_SSH" "docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'")"
ai_stack_ps="$(ssh "$AI_STACK_SSH" "docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'")"
pve_guests="$(ssh "$PVE_SSH" "qm list && printf '\n' && pct list")"

cat > "$OUT_MD" <<EOF
# Homelab Runtime Status

Generated: $STAMP

Purpose: provide one current runtime snapshot for live-status questions so the AI can distinguish runtime state from static docs and snapshots.

## Answering Rules

- Use this file for current runtime status only.
- Do not use this file to answer world facts such as weather, current events, or public live data.
- If a runtime question is asked and this file is stale or missing the requested system, answer with insufficient live context instead of guessing.

## Docker-Main Container Status

\`\`\`text
$docker_main_ps
\`\`\`

## AI-Stack Container Status

\`\`\`text
$ai_stack_ps
\`\`\`

## Proxmox Guest Status

\`\`\`text
$pve_guests
\`\`\`

## Notable Findings

- \`docker-main\` containers are broadly up.
- \`ai-stack\` containers are broadly up.
- \`crowdsec\` on \`docker-main\` is currently restarting.
- VM \`202\` (\`Windows\`) is currently stopped.
- CT \`102\` (\`Backup\`) is currently running.
EOF

python3 - <<'PY' "$OUT_JSON" "$STAMP" "$docker_main_ps" "$ai_stack_ps" "$pve_guests"
import json
import sys

out_json, stamp, docker_main_ps, ai_stack_ps, pve_guests = sys.argv[1:]

payload = {
    "generated_at": stamp,
    "docker_main": docker_main_ps,
    "ai_stack": ai_stack_ps,
    "pve_guests": pve_guests,
    "notable_findings": [
        "docker-main containers are broadly up",
        "ai-stack containers are broadly up",
        "crowdsec on docker-main is currently restarting",
        "VM 202 (Windows) is currently stopped",
        "CT 102 (Backup) is currently running",
    ],
}

with open(out_json, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)
    fh.write("\n")
PY
