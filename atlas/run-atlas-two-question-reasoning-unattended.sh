#!/usr/bin/env zsh
set -euo pipefail

cd "$(dirname "$0")/../.."

token="$(ssh cerebro@100.64.1.10 "sed -n 's/^ATLAS_SHORTCUTS_TOKEN=//p' /opt/stacks/ai-stack/atlas-shortcuts/atlas-shortcuts.env")"
export ATLAS_SHORTCUTS_TOKEN="$token"
export PYTHONUNBUFFERED=1

python3 tools/ai/run-atlas-two-question-reasoning-eval.py \
  --suite ai-evals/reasoning/atlas-two-question-reasoning-2026-09-def025-a3-reasoning-1000-seed-20260913.json \
  --base-url http://100.64.1.10:8787 \
  --client atlas-twoq-reasoning-dev-20260913-unattended \
  --concurrency 2 \
  --timeout 180 \
  --output-json ai-evals/reasoning/runs/atlas-two-question-reasoning-2026-09-def025-a3-reasoning-1000-20260913-unattended.json \
  --output-md ai-evals/reasoning/runs/atlas-two-question-reasoning-2026-09-def025-a3-reasoning-1000-20260913-unattended.md
