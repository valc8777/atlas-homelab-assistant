#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REMOTE_HOST="${REMOTE_HOST:-cerebro@100.64.1.10}"
REMOTE_STACK_DIR="${REMOTE_STACK_DIR:-/opt/stacks/ai-stack}"
REMOTE_APP_DIR="$REMOTE_STACK_DIR/atlas-shortcuts"
REMOTE_ENV_FILE="$REMOTE_APP_DIR/atlas-shortcuts.env"
REMOTE_MOBILE_DIR="$REMOTE_APP_DIR/atlas_mobile"

FILES=(
  "$ROOT/tools/ai/api_adapter_common.py"
  "$ROOT/tools/ai/atlas_app_store.py"
  "$ROOT/tools/ai/atlas_home_assistant.py"
  "$ROOT/tools/ai/atlas_memory_corpus.py"
  "$ROOT/tools/ai/atlas_nextcloud.py"
  "$ROOT/tools/ai/atlas_orchestrator_v2.py"
  "$ROOT/tools/ai/atlas_scheduler.py"
  "$ROOT/tools/ai/atlas_school_ical.py"
  "$ROOT/tools/ai/atlas_sfsu_graph.py"
  "$ROOT/tools/ai/atlas_sfsu_oauth.py"
  "$ROOT/tools/ai/atlas_sfsu_store.py"
  "$ROOT/tools/ai/atlas_shortcuts_server.py"
  "$ROOT/tools/ai/export-atlas-improvement-log.py"
  "$ROOT/tools/ai/get-openmeteo-weather.py"
  "$ROOT/tools/ai/import_openwebui_corpus.py"
  "$ROOT/tools/ai/lookup-structured-reference.py"
  "$ROOT/tools/ai/openwebui_client.py"
  "$ROOT/tools/ai/report-atlas-orchestrator-shadow.py"
  "$ROOT/tools/ai/run-atlas-orchestration-eval.py"
  "$ROOT/tools/ai/run-atlas-readonly.py"
  "$ROOT/tools/ai/search-musicbrainz.py"
  "$ROOT/tools/ai/search-openlibrary.py"
  "$ROOT/tools/ai/search-searxng.py"
  "$ROOT/tools/ai/search-tmdb.py"
  "$ROOT/tools/ai/search-wikidata-capital.py"
  "$ROOT/tools/ai/search-wikipedia.py"
  "$ROOT/ai-evals/prompts/atlas-readonly-system.md"
  "$ROOT/ai-evals/prompts/atlas-general-assistant-supplement.md"
)

ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_APP_DIR'"
scp "${FILES[@]}" "$REMOTE_HOST:$REMOTE_APP_DIR/"
ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_MOBILE_DIR'"
scp "$ROOT"/tools/ai/atlas_mobile/* "$REMOTE_HOST:$REMOTE_MOBILE_DIR/"

ssh "$REMOTE_HOST" "test -f '$REMOTE_ENV_FILE' || python3 -c 'import secrets; print(\"ATLAS_SHORTCUTS_TOKEN=\" + secrets.token_urlsafe(32))' > '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_SCHOOL_BRIDGE_ENABLED=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_SCHOOL_BRIDGE_ENABLED=false' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_SCHOOL_BRIDGE_OWNER_ID=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_SCHOOL_BRIDGE_OWNER_ID=owner' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_SCHOOL_BRIDGE_TOKEN=' '$REMOTE_ENV_FILE' || python3 -c 'import secrets; print(\"ATLAS_SCHOOL_BRIDGE_TOKEN=\" + secrets.token_urlsafe(32))' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_HOME_ASSISTANT_ENABLED=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_HOME_ASSISTANT_ENABLED=false' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_HOME_ASSISTANT_BASE_URL=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_HOME_ASSISTANT_BASE_URL=' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_HOME_ASSISTANT_TOKEN=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_HOME_ASSISTANT_TOKEN=' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_HOME_ASSISTANT_ENTITIES=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_HOME_ASSISTANT_ENTITIES=[]' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_NEXTCLOUD_ENABLED=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_NEXTCLOUD_ENABLED=false' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_NEXTCLOUD_BASE_URL=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_NEXTCLOUD_BASE_URL=' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_NEXTCLOUD_USERNAME=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_NEXTCLOUD_USERNAME=' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_NEXTCLOUD_APP_PASSWORD=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_NEXTCLOUD_APP_PASSWORD=' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_NEXTCLOUD_PATHS=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_NEXTCLOUD_PATHS=[]' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_ORCHESTRATOR_SHADOW_ENABLED=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_ORCHESTRATOR_SHADOW_ENABLED=true' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_ORCHESTRATOR_SHADOW_TIMEOUT_SECONDS=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_ORCHESTRATOR_SHADOW_TIMEOUT_SECONDS=90' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_ORCHESTRATOR_SHADOW_QUEUE_SIZE=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_ORCHESTRATOR_SHADOW_QUEUE_SIZE=64' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_ORCHESTRATOR_READONLY_CANARY_ENABLED=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_ORCHESTRATOR_READONLY_CANARY_ENABLED=false' >> '$REMOTE_ENV_FILE'"
ssh "$REMOTE_HOST" "grep -q '^ATLAS_ORCHESTRATOR_READONLY_CANARY_TIMEOUT_SECONDS=' '$REMOTE_ENV_FILE' || printf '%s\n' 'ATLAS_ORCHESTRATOR_READONLY_CANARY_TIMEOUT_SECONDS=10' >> '$REMOTE_ENV_FILE'"

ssh "$REMOTE_HOST" "cp '$REMOTE_STACK_DIR/docker-compose.yaml' '$REMOTE_STACK_DIR/docker-compose.yaml.bak.$(date +%Y-%m-%d-%H%M%S)'"
ssh "$REMOTE_HOST" "cp '$REMOTE_STACK_DIR/Caddyfile' '$REMOTE_STACK_DIR/Caddyfile.bak.$(date +%Y-%m-%d-%H%M%S)'"

ssh "$REMOTE_HOST" "python3 - <<'PY'
from pathlib import Path

compose_path = Path('/opt/stacks/ai-stack/docker-compose.yaml')
text = compose_path.read_text()
text = text.replace('jarvis-shortcuts', 'atlas-shortcuts')
text = text.replace('jarvis_shortcuts_server.py', 'atlas_shortcuts_server.py')
text = text.replace('run-jarvis-readonly.py', 'run-atlas-readonly.py')
text = text.replace('jarvis-readonly-system.md', 'atlas-readonly-system.md')
text = text.replace('JARVIS_RUNNER_PATH', 'ATLAS_RUNNER_PATH')
text = text.replace('JARVIS_BASE_URL', 'ATLAS_BASE_URL')
text = text.replace('JARVIS_WEBUI_DB', 'ATLAS_WEBUI_DB')
text = text.replace('JARVIS_KNOWLEDGE_NAME', 'ATLAS_KNOWLEDGE_NAME')
text = text.replace('JARVIS_SYSTEM_PROMPT_FILE', 'ATLAS_SYSTEM_PROMPT_FILE')
text = text.replace('JARVIS_BIND_HOST', 'ATLAS_BIND_HOST')
text = text.replace('JARVIS_BIND_PORT', 'ATLAS_BIND_PORT')
if 'atlas-shortcuts:' not in text:
    block = '''
  ntfy:
    image: binwiederhier/ntfy:latest
    container_name: ntfy
    restart: unless-stopped
    command:
      - serve
    environment:
      - NTFY_CACHE_FILE=/var/lib/ntfy/cache.db
      - NTFY_BEHIND_PROXY=true
      - TZ=America/Los_Angeles
    volumes:
      - ./ntfy:/var/lib/ntfy

  atlas-shortcuts:
    image: python:3.12-slim
    container_name: atlas-shortcuts
    restart: unless-stopped
    working_dir: /app
    command:
      - python3
      - /app/atlas_shortcuts_server.py
    env_file:
      - ./atlas-shortcuts/atlas-shortcuts.env
    environment:
      - ATLAS_RUNNER_PATH=/app/run-atlas-readonly.py
      - ATLAS_BASE_URL=http://open-webui:8080
      - ATLAS_WEBUI_DB=/app/webui_data/webui.db
      - ATLAS_KNOWLEDGE_NAME=Homelab Source of Truth
      - ATLAS_SYSTEM_PROMPT_FILE=/app/atlas-readonly-system.md
      - ATLAS_APP_DB=/app/atlas-app/atlas-app.db
      - ATLAS_PIPER_BASE_URL=http://piper-tts:8000
      - ATLAS_NTFY_BASE_URL=http://ntfy:80
      - ATLAS_NTFY_TOPIC=atlas
      - ATLAS_NTFY_TOKEN=${ATLAS_NTFY_TOKEN:-}
      - ATLAS_PUBLIC_BASE_URL=https://ai-stack.example-tailnet.ts.net/atlas
      - ATLAS_SFSU_ENABLED=${ATLAS_SFSU_ENABLED:-false}
      - ATLAS_SFSU_CLIENT_ID=${ATLAS_SFSU_CLIENT_ID:-}
      - ATLAS_SFSU_OWNER_TENANT_ID=${ATLAS_SFSU_OWNER_TENANT_ID:-}
      - ATLAS_BIND_HOST=0.0.0.0
      - ATLAS_BIND_PORT=8787
      - PYTHONUNBUFFERED=1
    ports:
      - 8787:8787
    volumes:
      - ./atlas-shortcuts:/app
      - ./atlas-app:/app/atlas-app
      - ./webui_data:/app/webui_data:ro
    depends_on:
      - open-webui
      - piper-tts
      - ntfy

  atlas-scheduler:
    image: python:3.12-slim
    container_name: atlas-scheduler
    restart: unless-stopped
    working_dir: /app
    command:
      - python3
      - /app/atlas_scheduler.py
      - --base-url
      - http://atlas-shortcuts:8787
      - --interval-seconds
      - "60"
    env_file:
      - ./atlas-shortcuts/atlas-shortcuts.env
    environment:
      - PYTHONUNBUFFERED=1
    volumes:
      - ./atlas-shortcuts:/app
      - ./atlas-app:/app/atlas-app
    depends_on:
      - atlas-shortcuts
'''
    if '\nnetworks:\n' not in text:
        raise SystemExit('compose file missing networks block')
    text = text.replace('\nnetworks:\n', f'{block}\nnetworks:\n', 1)

if 'atlas-shortcuts:' in text and 'ATLAS_APP_DB=/app/atlas-app/atlas-app.db' not in text:
    old = '      - ATLAS_SYSTEM_PROMPT_FILE=/app/atlas-readonly-system.md\n'
    new = '      - ATLAS_SYSTEM_PROMPT_FILE=/app/atlas-readonly-system.md\n      - ATLAS_APP_DB=/app/atlas-app/atlas-app.db\n'
    if old not in text:
        raise SystemExit('compose file missing expected atlas system prompt environment line')
    text = text.replace(old, new, 1)

if 'atlas-shortcuts:' in text and 'ATLAS_PIPER_BASE_URL=http://piper-tts:8000' not in text:
    old = '      - ATLAS_APP_DB=/app/atlas-app/atlas-app.db\n'
    new = '      - ATLAS_APP_DB=/app/atlas-app/atlas-app.db\n      - ATLAS_PIPER_BASE_URL=http://piper-tts:8000\n'
    if old not in text:
        raise SystemExit('compose file missing expected atlas app db environment line')
    text = text.replace(old, new, 1)

if 'atlas-shortcuts:' in text and 'ATLAS_NTFY_BASE_URL=http://ntfy:80' not in text:
    old = '      - ATLAS_PIPER_BASE_URL=http://piper-tts:8000\n'
    new = (
        '      - ATLAS_PIPER_BASE_URL=http://piper-tts:8000\n'
        '      - ATLAS_NTFY_BASE_URL=http://ntfy:80\n'
        '      - ATLAS_NTFY_TOPIC=atlas\n'
        '      - ATLAS_NTFY_TOKEN=${ATLAS_NTFY_TOKEN:-}\n'
        '      - ATLAS_PUBLIC_BASE_URL=https://ai-stack.example-tailnet.ts.net/atlas\n'
    )
    if old not in text:
        raise SystemExit('compose file missing expected atlas piper environment line')
    text = text.replace(old, new, 1)

if 'atlas-shortcuts:' in text and './atlas-app:/app/atlas-app' not in text:
    old = '      - ./atlas-shortcuts:/app\n'
    new = '      - ./atlas-shortcuts:/app\n      - ./atlas-app:/app/atlas-app\n'
    if old not in text:
        raise SystemExit('compose file missing expected atlas app volume line')
    text = text.replace(old, new, 1)

if 'atlas-shortcuts:' in text and '      - piper-tts\n' not in text:
    old = '    depends_on:\n      - open-webui\n'
    new = '    depends_on:\n      - open-webui\n      - piper-tts\n'
    if old not in text:
        raise SystemExit('compose file missing expected atlas depends_on block')
    text = text.replace(old, new, 1)

if '  ntfy:' not in text:
    block = '''
  ntfy:
    image: binwiederhier/ntfy:latest
    container_name: ntfy
    restart: unless-stopped
    command:
      - serve
    environment:
      - NTFY_CACHE_FILE=/var/lib/ntfy/cache.db
      - NTFY_BEHIND_PROXY=true
      - TZ=America/Los_Angeles
    volumes:
      - ./ntfy:/var/lib/ntfy
'''
    if '\n  atlas-shortcuts:\n' not in text:
        raise SystemExit('compose file missing atlas-shortcuts block')
    text = text.replace('\n  atlas-shortcuts:\n', f'{block}\n  atlas-shortcuts:\n', 1)

if '  atlas-scheduler:' not in text:
    block = '''
  atlas-scheduler:
    image: python:3.12-slim
    container_name: atlas-scheduler
    restart: unless-stopped
    working_dir: /app
    command:
      - python3
      - /app/atlas_scheduler.py
      - --base-url
      - http://atlas-shortcuts:8787
      - --interval-seconds
      - "60"
    env_file:
      - ./atlas-shortcuts/atlas-shortcuts.env
    environment:
      - PYTHONUNBUFFERED=1
    volumes:
      - ./atlas-shortcuts:/app
      - ./atlas-app:/app/atlas-app
    depends_on:
      - atlas-shortcuts
'''
    if '\nnetworks:\n' not in text:
        raise SystemExit('compose file missing networks block')
    text = text.replace('\nnetworks:\n', f'{block}\nnetworks:\n', 1)

if 'atlas-scheduler:' in text and 'atlas-scheduler:' in text.split('networks:', 1)[0] and 'atlas-scheduler:' in text and '      - ./atlas-shortcuts:/app\n      - ./atlas-app:/app/atlas-app\n    depends_on:\n      - atlas-shortcuts\n' not in text:
    old = '    volumes:\n      - ./atlas-shortcuts:/app\n    depends_on:\n      - atlas-shortcuts\n'
    new = '    volumes:\n      - ./atlas-shortcuts:/app\n      - ./atlas-app:/app/atlas-app\n    depends_on:\n      - atlas-shortcuts\n'
    if old not in text:
        raise SystemExit('compose file missing expected atlas-scheduler volume block')
    text = text.replace(old, new, 1)

if 'atlas-shortcuts:' in text and '      - ntfy\n' not in text:
    old = '    depends_on:\n      - open-webui\n      - piper-tts\n'
    new = '    depends_on:\n      - open-webui\n      - piper-tts\n      - ntfy\n'
    if old not in text:
        raise SystemExit('compose file missing expected atlas depends_on block with piper')
    text = text.replace(old, new, 1)

if 'atlas-shortcuts:' in text and 'ATLAS_NTFY_BASE_URL=http://ntfy:80' not in text:
    old = '      - ATLAS_PIPER_BASE_URL=http://piper-tts:8000\n'
    new = '      - ATLAS_PIPER_BASE_URL=http://piper-tts:8000\n      - ATLAS_NTFY_BASE_URL=http://ntfy:80\n      - ATLAS_NTFY_TOPIC=atlas\n'
    if old not in text:
        raise SystemExit('compose file missing expected atlas piper environment line')
    text = text.replace(old, new, 1)

if 'atlas-shortcuts:' in text and 'ATLAS_PUBLIC_BASE_URL=https://ai-stack.example-tailnet.ts.net/atlas' not in text:
    old = '      - ATLAS_NTFY_TOPIC=atlas\n'
    new = '      - ATLAS_NTFY_TOPIC=atlas\n      - ATLAS_PUBLIC_BASE_URL=https://ai-stack.example-tailnet.ts.net/atlas\n'
    if old not in text:
        raise SystemExit('compose file missing expected atlas ntfy topic environment line')
    text = text.replace(old, new, 1)

if '/var/run/tailscale/tailscaled.sock:/var/run/tailscale/tailscaled.sock' not in text:
    old = '''    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - ./:/certs
'''
    new = '''    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - ./:/certs
      - /var/run/tailscale/tailscaled.sock:/var/run/tailscale/tailscaled.sock
'''
    if old not in text:
        raise SystemExit('compose file missing expected caddy volume block')
    text = text.replace(old, new, 1)

if 'atlas-shortcuts:' in text and '8787:8787' not in text:
    old = '''    environment:
      - ATLAS_RUNNER_PATH=/app/run-atlas-readonly.py
      - ATLAS_BASE_URL=http://open-webui:8080
      - ATLAS_WEBUI_DB=/app/webui_data/webui.db
      - ATLAS_KNOWLEDGE_NAME=Homelab Source of Truth
      - ATLAS_SYSTEM_PROMPT_FILE=/app/atlas-readonly-system.md
      - ATLAS_APP_DB=/app/atlas-app/atlas-app.db
      - ATLAS_PIPER_BASE_URL=http://piper-tts:8000
      - ATLAS_NTFY_BASE_URL=http://ntfy:80
      - ATLAS_NTFY_TOPIC=atlas
      - ATLAS_NTFY_TOKEN=${ATLAS_NTFY_TOKEN:-}
      - ATLAS_PUBLIC_BASE_URL=https://ai-stack.example-tailnet.ts.net/atlas
      - ATLAS_BIND_HOST=0.0.0.0
      - ATLAS_BIND_PORT=8787
      - PYTHONUNBUFFERED=1
    volumes:
'''
    new = '''    environment:
      - ATLAS_RUNNER_PATH=/app/run-atlas-readonly.py
      - ATLAS_BASE_URL=http://open-webui:8080
      - ATLAS_WEBUI_DB=/app/webui_data/webui.db
      - ATLAS_KNOWLEDGE_NAME=Homelab Source of Truth
      - ATLAS_SYSTEM_PROMPT_FILE=/app/atlas-readonly-system.md
      - ATLAS_APP_DB=/app/atlas-app/atlas-app.db
      - ATLAS_PIPER_BASE_URL=http://piper-tts:8000
      - ATLAS_BIND_HOST=0.0.0.0
      - ATLAS_BIND_PORT=8787
      - PYTHONUNBUFFERED=1
    ports:
      - 8787:8787
    volumes:
'''
    if old not in text:
        raise SystemExit('compose file missing expected atlas environment block')
    text = text.replace(old, new, 1)

compose_path.write_text(text)
PY"

ssh "$REMOTE_HOST" "python3 - <<'PY'
from pathlib import Path

caddy_path = Path('/opt/stacks/ai-stack/Caddyfile')
text = caddy_path.read_text()
text = text.replace('/jarvis*', '/atlas*')
text = text.replace('jarvis-shortcuts:8787', 'atlas-shortcuts:8787')
needle = '\thandle_path /atlas* {\n\t\treverse_proxy atlas-shortcuts:8787\n\t}\n\n'
if 'atlas-shortcuts:8787' not in text:
    anchor = '\t# Jellyfin (The fix)\n'
    if anchor not in text:
        raise SystemExit('Caddyfile missing expected anchor')
    text = text.replace(anchor, needle + anchor, 1)
    caddy_path.write_text(text)

ntfy_needle = '\thandle_path /ntfy* {\n\t\treverse_proxy ntfy:80\n\t}\n\n'
if 'reverse_proxy ntfy:80' not in text:
    anchor = '\t# Jellyfin (The fix)\n'
    if anchor not in text:
        raise SystemExit('Caddyfile missing expected anchor')
    text = text.replace(anchor, ntfy_needle + anchor, 1)
    caddy_path.write_text(text)
PY"

ssh "$REMOTE_HOST" "docker rm -f jarvis-shortcuts >/dev/null 2>&1 || true; docker rm -f atlas-shortcuts atlas-scheduler >/dev/null 2>&1 || true; cd '$REMOTE_STACK_DIR' && docker compose up -d ntfy atlas-shortcuts atlas-scheduler caddy --remove-orphans"
ssh "$REMOTE_HOST" "docker restart caddy >/dev/null"
ssh "$REMOTE_HOST" "docker ps --format '{{.Names}} {{.Status}} {{.Ports}}' | grep -E '^(ntfy|atlas-shortcuts|atlas-scheduler|caddy) '"
ssh "$REMOTE_HOST" "TOKEN=\$(sed -n 's/^ATLAS_SHORTCUTS_TOKEN=//p' '$REMOTE_ENV_FILE'); curl -fsS http://127.0.0.1:8787/health; echo; curl -fsS -H \"Authorization: Bearer \$TOKEN\" -H 'Content-Type: application/json' -d '{\"question\":\"Which host runs Open WebUI?\",\"context_scope\":\"operations\"}' http://127.0.0.1:8787/ask; echo"
