#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-}"
REMOTE_USER="${REMOTE_USER:-ubuntu}"
REMOTE_DIR="${REMOTE_DIR:-/opt/AI_python/network_monitor}"

if [[ -z "${REMOTE_HOST}" ]]; then
  echo "REMOTE_HOST is required. Example: REMOTE_HOST=<server-address> scripts/safe_sync_to_remote.sh" >&2
  exit 2
fi

cd "$(dirname "$0")/.."

rsync -az \
  --exclude-from .deployignore \
  ./ "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"

ssh "${REMOTE_USER}@${REMOTE_HOST}" \
  "cd '${REMOTE_DIR}' && chmod +x scripts/*.sh && if [ -x scripts/check_persistent_data.sh ]; then scripts/check_persistent_data.sh; fi"

echo "Synced code to ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"
echo "Persistent data directories were excluded by .deployignore."
