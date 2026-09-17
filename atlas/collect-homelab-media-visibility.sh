#!/bin/zsh

set -euo pipefail

ROOT="/Users/valdemort/Projects/homelab"
OUT_MD="$ROOT/homelab-media-visibility-2026-08-06.md"
OUT_JSON="$ROOT/homelab-media-visibility-2026-08-06.json"
STAMP="$(date '+%Y-%m-%d %H:%M:%S %Z')"
DOCKER_MAIN_SSH="${DOCKER_MAIN_SSH:-root@100.64.1.20}"
AI_STACK_SSH="${AI_STACK_SSH:-cerebro@100.64.1.10}"

docker_main="$(ssh "$DOCKER_MAIN_SSH" 'for p in /opt/stacks/media/media/movies /opt/stacks/media/media/tv /opt/stacks/media/media/music /opt/stacks/media/media/books /opt/stacks/media/media/comics /opt/stacks/media/media/manga /opt/stacks/media/media/audiobooks /opt/stacks/media/imports/music; do if [ -d "$p" ]; then c=$(find "$p" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d " "); s=$(du -sh "$p" 2>/dev/null | cut -f1); printf "%s\tpresent\t%s\t%s\n" "$p" "$c" "$s"; else printf "%s\tmissing\t0\t-\n" "$p"; fi; done')"

ai_stack="$(ssh "$AI_STACK_SSH" 'docker exec jellyfin sh -lc '"'"'for p in /media/movies /media/tv /media/music /media/books /media/comics /media/manga /media/audiobooks; do if [ -d "$p" ]; then c=$(find "$p" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d " "); s=$(du -sh "$p" 2>/dev/null | cut -f1); printf "%s\tpresent\t%s\t%s\n" "$p" "$c" "$s"; else printf "%s\tmissing\t0\t-\n" "$p"; fi; done'"'"'')"

cat > "$OUT_MD" <<EOF
# Homelab Media Visibility

Generated: $STAMP

Purpose: provide a privacy-safe summary of whether the shared media libraries are mounted and populated, without exposing filenames or titles.

## Docker-Main Shared Media Paths

\`\`\`text
$docker_main
\`\`\`

## AI-Stack Jellyfin Media Mount Visibility

\`\`\`text
$ai_stack
\`\`\`

## Answering Rules

- Use this file for media-path presence, mount visibility, and high-level library population only.
- Do not infer specific titles from this file.
EOF

python3 - <<'PY' "$OUT_JSON" "$STAMP" "$docker_main" "$ai_stack"
import json
import sys

out_json, stamp, docker_main, ai_stack = sys.argv[1:]

payload = {
    "generated_at": stamp,
    "docker_main": docker_main,
    "ai_stack_jellyfin": ai_stack,
}

with open(out_json, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)
    fh.write("\n")
PY
