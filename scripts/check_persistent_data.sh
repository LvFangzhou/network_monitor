#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/AI_python/network_monitor}"
cd "${PROJECT_DIR}"

DATA_ROOT="${DATA_ROOT:-}"
BACKUP_ROOT="${BACKUP_ROOT:-}"
if [ -f .env ]; then
  # shellcheck disable=SC1091
  set -a
  . ./.env
  set +a
fi

DATA_ROOT="${DATA_ROOT:-./data}"
BACKUP_ROOT="${BACKUP_ROOT:-./data/backups}"

required_dirs=(
  "${DATA_ROOT}"
  "${DATA_ROOT}/postgres"
  "${DATA_ROOT}/influxdb"
  "${DATA_ROOT}/influxdb/engine"
  "${DATA_ROOT}/influxdb/engine/data"
  "${DATA_ROOT}/influxdb/engine/wal"
  "${DATA_ROOT}/redis"
  "${DATA_ROOT}/rabbitmq"
  "${DATA_ROOT}/tacacs"
  "${BACKUP_ROOT}"
)

required_files=(
  "${DATA_ROOT}/tacacs/tac_plus.cfg"
)

failed=0
for dir in "${required_dirs[@]}"; do
  if [ ! -d "${dir}" ]; then
    echo "MISSING ${dir}"
    failed=1
  else
    echo "OK      ${dir}"
  fi
done

for file in "${required_files[@]}"; do
  if [ ! -f "${file}" ]; then
    echo "MISSING_FILE ${file}"
    failed=1
  else
    echo "OK      ${file}"
  fi
done

if [ "${failed}" -ne 0 ]; then
  echo "Persistent data preflight failed." >&2
  exit 1
fi

echo "Persistent data preflight passed."
