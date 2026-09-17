#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 EVALUATOR_PID OUTPUT_DIR RELEASE_LABEL" >&2
  exit 2
fi

evaluator_pid="$1"
output_dir="$2"
release_label="$3"

while kill -0 "$evaluator_pid" 2>/dev/null; do
  sleep 20
done

if find "$output_dir" -maxdepth 1 -type f -name '*.json' -print -quit | grep -q . \
  && find "$output_dir" -maxdepth 1 -type f -name '*.md' -print -quit | grep -q .; then
  notice_title="ATLAS test complete"
  notice_body="$release_label finished and produced its JSON and Markdown reports. Results are ready for review."
else
  notice_title="ATLAS test needs attention"
  notice_body="$release_label stopped without both final reports. Check the evaluator log before taking further action."
fi

docker exec \
  -e ATLAS_RELEASE_NOTICE_TITLE="$notice_title" \
  -e ATLAS_RELEASE_NOTICE_BODY="$notice_body" \
  atlas-shortcuts python3 -c \
  'import os; import atlas_shortcuts_server as s; r=s.deliver_ntfy({"title":os.environ["ATLAS_RELEASE_NOTICE_TITLE"],"body":os.environ["ATLAS_RELEASE_NOTICE_BODY"]}); print("ntfy_status="+str(r["status"]))'
