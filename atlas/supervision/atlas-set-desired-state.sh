#!/usr/bin/env bash
set -euo pipefail

host="${ATLAS_REMOTE_HOST:-cerebro@100.64.1.10}"
state="${1:-}"

case "$state" in
  running|stopped) ;;
  *)
    printf 'Usage: %s running|stopped\n' "$0" >&2
    exit 2
    ;;
esac

ssh "$host" "mkdir -p /home/cerebro/.local/state/atlas-watchdog && printf '%s\n' '$state' > /home/cerebro/.local/state/atlas-watchdog/desired-state && printf '%s desired_state=%s\n' \"\$(date --iso-8601=seconds)\" '$state' >> /home/cerebro/.local/state/atlas-watchdog/events.log && cat /home/cerebro/.local/state/atlas-watchdog/desired-state"
