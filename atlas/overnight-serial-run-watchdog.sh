#!/usr/bin/env bash
# Watchdog for the overnight serial (concurrency=1) stress probe + historical
# replay run. Meant to run via cron every few minutes, independent of any
# Claude session -- restarts the run if it died or was never started, resumes
# from whichever phase didn't finish (checked by output-file existence, since
# neither script has incremental checkpointing within a phase), and removes
# its own cron entry once both phases are confirmed done.
set -euo pipefail

LOG=/tmp/overnight-serial-run.log
STRESS_JSON=/tmp/overnight-stress-probe-100x3-serial.json
REPLAY_JSON=/tmp/atlas-a3-historical-replay-full-history-serial.json
CRON_MARKER="overnight-serial-run-watchdog.sh"

# Already fully done -- remove our own cron entry and stop.
if grep -q "\[serial-run\] all done" "$LOG" 2>/dev/null; then
  crontab -l 2>/dev/null | grep -v "$CRON_MARKER" | crontab - || true
  exit 0
fi

# Still alive -- nothing to do. Matches either phase's driving script.
if pgrep -f "run-atlas-overnight-stress-probe.py|run-atlas-a3-historical-replay.py" > /dev/null 2>&1; then
  exit 0
fi

echo "[watchdog] restarting at $(date), not alive and not complete" >> "$LOG"

# Resume point: phase 1's own output file only gets written on successful
# completion, so its presence means phase 1 is done and only phase 2 is
# needed. Neither script has partial/incremental checkpointing, so a phase
# that was interrupted mid-way restarts from its own beginning, not from
# wherever it got to -- an accepted limitation, not a silent gap.
nohup bash -c "
TOKEN=\$(sed -n 's/^ATLAS_SHORTCUTS_TOKEN=//p' /opt/stacks/ai-stack/atlas-shortcuts/atlas-shortcuts.env)
export ATLAS_SHORTCUTS_TOKEN=\$TOKEN
if [ ! -f '$STRESS_JSON' ]; then
  echo \"[watchdog] (re)starting stress probe at \$(date)\" >> '$LOG'
  python3 -u /tmp/run-atlas-overnight-stress-probe.py --base-url http://127.0.0.1:8787 --concurrency 1 --timeout 60 --repeats 3 --output-json '$STRESS_JSON' --output-md /tmp/overnight-stress-probe-100x3-serial.md >> '$LOG' 2>&1
fi
echo \"[watchdog] (re)starting historical replay at \$(date)\" >> '$LOG'
python3 -u /tmp/run-atlas-a3-historical-replay.py --since 2026-08-01T00:00:00+00:00 --base-url http://127.0.0.1:8787 --concurrency 1 --timeout 60 --output-json '$REPLAY_JSON' --output-md /tmp/atlas-a3-historical-replay-full-history-serial.md >> '$LOG' 2>&1
echo \"[serial-run] all done at \$(date)\" >> '$LOG'
" >> "$LOG" 2>&1 < /dev/null &
disown

echo "[watchdog] relaunched, pid $!" >> "$LOG"
