#!/bin/zsh

set -euo pipefail

ROOT="/Users/valdemort/Projects/homelab"
REMOTE_HOST="cerebro@100.64.1.10"
REMOTE_BASE_DIR="/home/cerebro/homelab-corpus-import"
REMOTE_RELEASE="$(date +%Y%m%d-%H%M%S)"
REMOTE_STAGING_DIR="$REMOTE_BASE_DIR/releases/$REMOTE_RELEASE/files"
REMOTE_CORPUS_DIR="$REMOTE_BASE_DIR/current/files"
REMOTE_IMPORTER="/home/cerebro/bin/import_openwebui_corpus.py"

"$ROOT/tools/ai/collect-homelab-runtime-status.sh"
"$ROOT/tools/ai/collect-homelab-host-health.sh"
"$ROOT/tools/ai/collect-homelab-dns-summary.sh"
"$ROOT/tools/ai/collect-homelab-media-visibility.sh"
python3 "$ROOT/tools/ai/collect-homelab-service-reachability.py"
python3 "$ROOT/tools/ai/collect-homelab-backup-status.py"
python3 "$ROOT/tools/ai/collect-homelab-service-location-index.py"
python3 "$ROOT/tools/ai/collect-homelab-daily-briefing.py"
python3 "$ROOT/tools/ai/collect-homelab-daily-reminders.py"
"$ROOT/tools/ai/build-homelab-corpus.sh"

ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_STAGING_DIR' '$REMOTE_BASE_DIR/releases' '/home/cerebro/bin'"
scp "$ROOT/tools/ai/import_openwebui_corpus.py" "$REMOTE_HOST:$REMOTE_IMPORTER"
scp -r "$ROOT/ai-corpus/build/current/files/." "$REMOTE_HOST:$REMOTE_STAGING_DIR/"
ssh "$REMOTE_HOST" "rm -rf '$REMOTE_BASE_DIR/current' && mkdir -p '$REMOTE_BASE_DIR' && mv '$REMOTE_BASE_DIR/releases/$REMOTE_RELEASE' '$REMOTE_BASE_DIR/current' && chmod +x '$REMOTE_IMPORTER' && python3 '$REMOTE_IMPORTER' --corpus-dir '$REMOTE_CORPUS_DIR'"
