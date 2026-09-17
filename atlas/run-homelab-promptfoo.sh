#!/bin/zsh

set -euo pipefail

ROOT="/Users/valdemort/Projects/homelab"
USER_HOME="/Users/valdemort"
NODE_BIN="/Users/valdemort/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
PNPM_BIN="/Users/valdemort/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/pnpm"
PROMPTFOO_CONFIG_DIR="$ROOT/.promptfoo"
PROMPTFOO_CACHE_PATH="$PROMPTFOO_CONFIG_DIR/cache"
DEFAULT_CONFIG="$ROOT/ai-evals/promptfooconfig.yaml"
DEFAULT_OUTPUT="/private/tmp/homelab-promptfoo-eval.json"

mkdir -p "$PROMPTFOO_CONFIG_DIR" "$PROMPTFOO_CACHE_PATH"

export HOME="$ROOT"
export PROMPTFOO_CONFIG_DIR
export PROMPTFOO_CACHE_PATH

find_promptfoo_entrypoint() {
  find -L "$USER_HOME/Library/Caches/pnpm/dlx" -path '*/node_modules/promptfoo/dist/src/entrypoint.js' 2>/dev/null | tail -n 1
}

PROMPTFOO_ENTRYPOINT="$(find_promptfoo_entrypoint)"

if [[ -z "$PROMPTFOO_ENTRYPOINT" ]]; then
  PATH="$(dirname "$NODE_BIN"):$PATH" "$PNPM_BIN" dlx promptfoo@latest --version >/dev/null
  PROMPTFOO_ENTRYPOINT="$(find_promptfoo_entrypoint)"
fi

if [[ -z "$PROMPTFOO_ENTRYPOINT" || ! -f "$PROMPTFOO_ENTRYPOINT" ]]; then
  echo "Failed to locate promptfoo entrypoint after install." >&2
  exit 1
fi

SUBCOMMAND="${1:-validate}"
shift || true

case "$SUBCOMMAND" in
  validate)
    "$NODE_BIN" "$PROMPTFOO_ENTRYPOINT" validate -c "$DEFAULT_CONFIG" "$@"
    ;;
  eval)
    "$NODE_BIN" "$PROMPTFOO_ENTRYPOINT" eval -c "$DEFAULT_CONFIG" --output "$DEFAULT_OUTPUT" "$@"
    ;;
  view)
    "$NODE_BIN" "$PROMPTFOO_ENTRYPOINT" view "$@"
    ;;
  *)
    "$NODE_BIN" "$PROMPTFOO_ENTRYPOINT" "$SUBCOMMAND" "$@"
    ;;
esac
