#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUITE="$ROOT/ai-evals/atlas-general-knowledge-probe-2026-08-30.json"
BASE_URL="${ATLAS_BASE_URL:-https://ai-stack.example-tailnet.ts.net/atlas}"

if [[ -z "${ATLAS_TOKEN:-}" ]]; then
  echo "Set ATLAS_TOKEN first, then rerun." >&2
  echo "Example:" >&2
  echo "  export ATLAS_TOKEN='<token from tools/ai/show-atlas-mobile-token.sh>'" >&2
  exit 2
fi

python3 "$ROOT/tools/ai/run-atlas-app-regressions.py" \
  --base-url "$BASE_URL" \
  --token "$ATLAS_TOKEN" \
  --suite "$SUITE" \
  --client "atlas-general-knowledge-probe"
