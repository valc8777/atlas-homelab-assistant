#!/usr/bin/env bash
set -euo pipefail

ssh cerebro@100.64.1.10 "sed -n 's/^ATLAS_SHORTCUTS_TOKEN=//p' /opt/stacks/ai-stack/atlas-shortcuts/atlas-shortcuts.env"
