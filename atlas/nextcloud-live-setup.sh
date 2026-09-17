#!/usr/bin/env bash
set -euo pipefail

docker_main_host="${1:?docker-main host required}"
ai_stack_host="${2:?ai-stack host required}"
reader_user="${3:?reader username required}"
owner_user="${4:?owner username required}"
share_root="${5:?share root required}"
nextcloud_base_url="${6:?nextcloud base url required}"

tmp_app_pass="$(mktemp)"
tmp_remote_script="$(mktemp)"

cleanup() {
  rm -f "$tmp_app_pass" "$tmp_remote_script"
}
trap cleanup EXIT

cat >"$tmp_remote_script" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

reader_user="$1"
owner_user="$2"
share_root="$3"
tmp_app_pass_path="$4"

reader_pass="$(openssl rand -base64 24 | tr -d '\n')"

docker exec nextcloud-aio-nextcloud sh -lc "mkdir -p /mnt/ncdata/${owner_user}/files/${share_root} && chown www-data:www-data /mnt/ncdata/${owner_user}/files/${share_root}"
docker exec nextcloud-aio-nextcloud php occ files:scan --path="/${owner_user}/files/${share_root}" --shallow --no-interaction --no-warnings >/dev/null

if ! docker exec nextcloud-aio-nextcloud php occ user:list | grep -q "  - ${reader_user}:"; then
  docker exec -e NC_PASS="$reader_pass" nextcloud-aio-nextcloud php occ user:add \
    --password-from-env \
    --display-name="ATLAS Reader" \
    --group=atlas_readers \
    --no-interaction \
    --no-warnings \
    "${reader_user}" >/dev/null
fi

docker exec -e OC_PASS="$reader_pass" nextcloud-aio-nextcloud php occ user:auth-tokens:add \
  --password-from-env \
  --no-interaction \
  --no-warnings \
  "${reader_user}" > "${tmp_app_pass_path}"

docker exec nextcloud-aio-nextcloud php occ app:enable calendar contacts --no-interaction --no-warnings >/dev/null

docker exec nextcloud-aio-nextcloud php -r '
require_once "/var/www/html/lib/base.php";
$root = \OC::$server->get(\OCP\Files\IRootFolder::class);
$folder = $root->getUserFolder("'"${owner_user}"'");
if (!$folder->nodeExists("'"${share_root}"'")) {
    $folder->newFolder("'"${share_root}"'");
}
$node = $folder->get("'"${share_root}"'");
$shareManager = \OC::$server->get(\OCP\Share\IManager::class);
$existing = $shareManager->getSharesBy("'"${owner_user}"'", \OCP\Share\IShare::TYPE_USER, $node, false, 1, 0);
foreach ($existing as $share) {
    if ($share->getSharedWith() === "'"${reader_user}"'") {
        exit(0);
    }
}
$share = $shareManager->newShare();
$share->setNode($node);
$share->setShareType(\OCP\Share\IShare::TYPE_USER);
$share->setSharedWith("'"${reader_user}"'");
$share->setSharedBy("'"${owner_user}"'");
$share->setShareOwner("'"${owner_user}"'");
$share->setPermissions(\OCP\Constants::PERMISSION_READ);
$shareManager->createShare($share);
' >/dev/null
EOF

chmod 700 "$tmp_remote_script"

scp "$tmp_remote_script" "${docker_main_host}:/tmp/nextcloud-live-setup.sh" >/dev/null
ssh "${docker_main_host}" "chmod 700 /tmp/nextcloud-live-setup.sh && /tmp/nextcloud-live-setup.sh '${reader_user}' '${owner_user}' '${share_root}' '/tmp/atlas-nextcloud-app-pass.txt'"
scp "${docker_main_host}:/tmp/atlas-nextcloud-app-pass.txt" "$tmp_app_pass" >/dev/null
ssh "${docker_main_host}" "rm -f /tmp/nextcloud-live-setup.sh /tmp/atlas-nextcloud-app-pass.txt"

app_pass="$(tail -n 1 "$tmp_app_pass" | tr -d '\r')"

ssh "${ai_stack_host}" "cp /opt/stacks/ai-stack/atlas-shortcuts/atlas-shortcuts.env /opt/stacks/ai-stack/atlas-shortcuts/atlas-shortcuts.env.bak.\$(date +%Y-%m-%d-%H%M%S) && python3 - <<'PY'
from pathlib import Path
path = Path('/opt/stacks/ai-stack/atlas-shortcuts/atlas-shortcuts.env')
lines = path.read_text().splitlines()
replacements = {
    'ATLAS_NEXTCLOUD_ENABLED': 'true',
    'ATLAS_NEXTCLOUD_BASE_URL': '${nextcloud_base_url}',
    'ATLAS_NEXTCLOUD_USERNAME': '${reader_user}',
    'ATLAS_NEXTCLOUD_APP_PASSWORD': '${app_pass}',
    'ATLAS_NEXTCLOUD_PATHS': '[\"${share_root}\"]',
}
out = []
seen = set()
for line in lines:
    key = line.split('=', 1)[0]
    if key in replacements:
        out.append(f'{key}={replacements[key]}')
        seen.add(key)
    else:
        out.append(line)
for key, value in replacements.items():
    if key not in seen:
        out.append(f'{key}={value}')
path.write_text('\\n'.join(out) + '\\n')
PY"

echo "nextcloud_live_setup_applied"
