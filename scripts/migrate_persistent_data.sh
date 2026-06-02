#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/AI_python/network_monitor}"
DATA_ROOT="${DATA_ROOT:-/opt/network_monitor_data}"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/network_monitor_backups}"

cd "${PROJECT_DIR}"

if docker ps >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker compose"
elif sudo docker ps >/dev/null 2>&1 && sudo docker compose version >/dev/null 2>&1; then
  DOCKER_COMPOSE="sudo docker compose"
else
  echo "docker compose is not available" >&2
  exit 1
fi

if mkdir -p "${DATA_ROOT}" "${BACKUP_ROOT}" >/dev/null 2>&1; then
  SUDO=""
else
  SUDO="sudo"
  ${SUDO} mkdir -p "${DATA_ROOT}" "${BACKUP_ROOT}"
fi

if sudo -n true >/dev/null 2>&1 || sudo true >/dev/null 2>&1; then
  SUDO_RSYNC="sudo"
else
  SUDO_RSYNC=""
fi

if [ ! -d data ]; then
  echo "No local data directory found at ${PROJECT_DIR}/data" >&2
  exit 1
fi

echo "Stopping services before copying persistent data..."
${DOCKER_COMPOSE} stop

echo "Copying data to ${DATA_ROOT}..."
${SUDO_RSYNC} rsync -a data/ "${DATA_ROOT}/"

if [ -d "${DATA_ROOT}/backups" ]; then
  echo "Moving existing backups to ${BACKUP_ROOT}..."
  ${SUDO_RSYNC} rsync -a "${DATA_ROOT}/backups/" "${BACKUP_ROOT}/"
fi

cat > .env <<EOF
DATA_ROOT=${DATA_ROOT}
BACKUP_ROOT=${BACKUP_ROOT}
EOF

echo "Starting services with external persistent data root..."
${DOCKER_COMPOSE} up -d

echo "Done. Old ${PROJECT_DIR}/data is intentionally retained as a rollback copy."
echo "DATA_ROOT=${DATA_ROOT}"
echo "BACKUP_ROOT=${BACKUP_ROOT}"
