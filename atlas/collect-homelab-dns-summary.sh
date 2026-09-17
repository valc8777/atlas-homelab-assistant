#!/bin/zsh

set -euo pipefail

ROOT="/Users/valdemort/Projects/homelab"
OUT_MD="$ROOT/homelab-dns-summary-2026-08-06.md"
OUT_JSON="$ROOT/homelab-dns-summary-2026-08-06.json"
STAMP="$(date '+%Y-%m-%d %H:%M:%S %Z')"
DOCKER_MAIN_SSH="${DOCKER_MAIN_SSH:-root@100.64.1.20}"
AI_STACK_SSH="${AI_STACK_SSH:-cerebro@100.64.1.10}"
PVE_SSH="${PVE_SSH:-root@100.70.171.62}"

docker_main="$(ssh "$DOCKER_MAIN_SSH" 'printf "Resolver configuration\n"; cat /etc/resolv.conf; printf "\nMagicDNS lookups\n"; for h in docker-main.example-tailnet.ts.net zola-cerebro.example-tailnet.ts.net ai-stack.example-tailnet.ts.net vaultwarden.example-tailnet.ts.net jellyfin.example-tailnet.ts.net nextcloud.example-tailnet.ts.net; do timeout 3 getent hosts "$h" 2>/dev/null || printf "UNRESOLVED\t%s\n" "$h"; done')"
ai_stack="$(ssh "$AI_STACK_SSH" 'printf "Resolver configuration\n"; cat /etc/resolv.conf; printf "\nMagicDNS lookups\n"; for h in docker-main.example-tailnet.ts.net zola-cerebro.example-tailnet.ts.net ai-stack.example-tailnet.ts.net vaultwarden.example-tailnet.ts.net jellyfin.example-tailnet.ts.net nextcloud.example-tailnet.ts.net; do timeout 3 getent hosts "$h" 2>/dev/null || printf "UNRESOLVED\t%s\n" "$h"; done')"
pve="$(ssh "$PVE_SSH" 'printf "Resolver configuration\n"; cat /etc/resolv.conf; printf "\nMagicDNS lookups\n"; for h in docker-main.example-tailnet.ts.net zola-cerebro.example-tailnet.ts.net ai-stack.example-tailnet.ts.net vaultwarden.example-tailnet.ts.net jellyfin.example-tailnet.ts.net nextcloud.example-tailnet.ts.net; do timeout 3 getent hosts "$h" 2>/dev/null || printf "UNRESOLVED\t%s\n" "$h"; done')"

cat > "$OUT_MD" <<EOF
# Homelab DNS Summary

Generated: $STAMP

Purpose: provide a current DNS and MagicDNS resolution snapshot for the main homelab hosts so the AI can answer name-resolution questions without guessing.

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

## Answering Rules

- Use this file for resolver configuration and name-resolution questions only.
- Prefer this file over older static notes when the question is about whether a hostname resolves right now.
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
