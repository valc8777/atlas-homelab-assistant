#!/usr/bin/env bash
set -euo pipefail

host="${ATLAS_REMOTE_HOST:-cerebro@100.64.1.10}"
state_dir="${ATLAS_WATCHDOG_STATE_DIR:-/home/cerebro/.local/state/atlas-watchdog}"
progress_glob="${ATLAS_EVAL_PROGRESS_GLOB:-/opt/stacks/ai-stack/atlas-evals/*.progress.json}"

ssh "$host" "set -e
echo '== atlas desired state =='
if [ -f '$state_dir/desired-state' ]; then cat '$state_dir/desired-state'; else echo unknown; fi
echo '== atlas containers =='
cd /opt/stacks/ai-stack
docker compose ps atlas-shortcuts atlas-scheduler caddy ollama
echo '== atlas health =='
curl -fsS --max-time 8 http://127.0.0.1:8787/health || true
echo
echo '== watchdog recent events =='
if [ -f '$state_dir/events.log' ]; then tail -20 '$state_dir/events.log'; else echo none; fi
echo '== eval runner processes =='
pgrep -af '[r]un-atlas-two-question-reasoning-eval.py' || true
echo '== eval progress files =='
ls -1t $progress_glob 2>/dev/null | head -5 | while read -r f; do echo \"--- \$f\"; cat \"\$f\"; done
"
