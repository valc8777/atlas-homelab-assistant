#!/usr/bin/env bash
set -euo pipefail

state="${1:-}"

case "$state" in
  running|stopped) ;;
  *)
    printf 'Usage: %s running|stopped\n' "$0" >&2
    exit 2
    ;;
esac

state_dir="${ATLAS_WATCHDOG_STATE_DIR:-$HOME/.local/state/atlas-watchdog}"
mkdir -p "$state_dir"
printf '%s\n' "$state" > "$state_dir/desired-state"
printf '%s desired_state=%s\n' "$(date --iso-8601=seconds)" "$state" >> "$state_dir/events.log"
cat "$state_dir/desired-state"
