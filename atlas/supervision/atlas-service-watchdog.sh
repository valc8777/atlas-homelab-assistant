#!/usr/bin/env bash
set -euo pipefail

stack_dir="${ATLAS_STACK_DIR:-/opt/stacks/ai-stack}"
state_dir="${ATLAS_WATCHDOG_STATE_DIR:-$HOME/.local/state/atlas-watchdog}"
desired_state_file="${ATLAS_WATCHDOG_DESIRED_STATE_FILE:-$state_dir/desired-state}"
log_file="${ATLAS_WATCHDOG_LOG:-$state_dir/events.log}"
service_list="${ATLAS_WATCHDOG_SERVICES:-atlas-shortcuts atlas-scheduler caddy}"
health_url="${ATLAS_WATCHDOG_HEALTH_URL:-http://127.0.0.1:8787/health}"
curl_timeout="${ATLAS_WATCHDOG_CURL_TIMEOUT:-8}"
health_failures_file="$state_dir/health-failures"
health_failure_threshold="${ATLAS_WATCHDOG_HEALTH_FAILURE_THRESHOLD:-2}"

mkdir -p "$state_dir"
touch "$log_file"

if [ ! -f "$desired_state_file" ]; then
  printf '%s\n' "running" > "$desired_state_file"
fi

timestamp() {
  date --iso-8601=seconds
}

log_event() {
  printf '%s %s\n' "$(timestamp)" "$*" >> "$log_file"
}

desired_state="$(tr '[:upper:]' '[:lower:]' < "$desired_state_file" | tr -d '[:space:]')"

if [ "$desired_state" != "running" ]; then
  log_event "desired_state=$desired_state action=skip"
  exit 0
fi

if [ ! -d "$stack_dir" ]; then
  log_event "status=error reason=missing_stack_dir stack_dir=$stack_dir"
  exit 2
fi

cd "$stack_dir"

unhealthy=""
container_unhealthy=""
for service in $service_list; do
  container_id="$(docker compose ps -q "$service" 2>/dev/null || true)"
  if [ -z "$container_id" ]; then
    container_unhealthy="$container_unhealthy missing:$service"
    continue
  fi
  running="$(docker inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null || true)"
  if [ "$running" != "true" ]; then
    container_unhealthy="$container_unhealthy stopped:$service"
  fi
done

unhealthy="$container_unhealthy"
if ! curl -fsS --max-time "$curl_timeout" "$health_url" >/dev/null 2>&1; then
  failures=0
  if [ -f "$health_failures_file" ]; then
    failures="$(cat "$health_failures_file" 2>/dev/null || printf '0')"
  fi
  failures=$((failures + 1))
  printf '%s\n' "$failures" > "$health_failures_file"
  if [ -n "$container_unhealthy" ] || [ "$failures" -ge "$health_failure_threshold" ]; then
    unhealthy="$unhealthy healthcheck:atlas-shortcuts(${failures})"
  else
    log_event "status=healthcheck_failed count=$failures action=wait threshold=$health_failure_threshold"
    exit 0
  fi
else
  rm -f "$health_failures_file"
fi

if [ -z "$unhealthy" ]; then
  exit 0
fi

log_event "status=unhealthy reason=\"$unhealthy\" action=restart services=\"$service_list\""
docker compose up -d $service_list >/tmp/atlas-watchdog-compose.out 2>&1 || {
  rc=$?
  log_event "status=restart_failed rc=$rc output=$(tr '\n' ' ' </tmp/atlas-watchdog-compose.out | cut -c1-800)"
  exit "$rc"
}
log_event "status=restart_requested output=$(tr '\n' ' ' </tmp/atlas-watchdog-compose.out | cut -c1-800)"
