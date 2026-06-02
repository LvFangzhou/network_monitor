#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-172.18.16.92}"
REMOTE_USER="${REMOTE_USER:-ubuntu}"
REMOTE_DIR="${REMOTE_DIR:-/opt/AI_python/network_monitor}"

cd "$(dirname "$0")/.."

rsync -az \
  --exclude-from .deployignore \
  ./ "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"

ssh "${REMOTE_USER}@${REMOTE_HOST}" \
  "cd '${REMOTE_DIR}' && chmod +x scripts/*.sh && if [ -x scripts/check_persistent_data.sh ]; then scripts/check_persistent_data.sh; fi"

echo "Synced code to ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"
echo "Persistent data directories were excluded by .deployignore."
