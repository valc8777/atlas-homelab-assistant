#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUITE="${1:-}"
SEED="${ATLAS_EVAL_SEED:-20260830}"

if [[ -z "${SUITE}" ]]; then
  echo "Usage: $0 /path/to/generated-external-suite.json" >&2
  exit 2
fi

if [[ -z "${ATLAS_TOKEN:-}" ]]; then
  echo "ATLAS_TOKEN is required. Get it from tools/ai/show-atlas-mobile-token.sh." >&2
  exit 2
fi

python3 "${ROOT}/tools/ai/run-atlas-app-regressions.py" \
  --base-url "${ATLAS_BASE_URL:-https://ai-stack.example-tailnet.ts.net/atlas}" \
  --token "${ATLAS_TOKEN}" \
  --suite "${SUITE}" \
  --client "atlas-external-eval-runner" \
  --seed "${SEED}" \
  --output-dir "${ROOT}/ai-evals/external/runs"
