#!/usr/bin/env bash
# Stop the focused MVP while retaining database and model volumes.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [ ! -f "${ENV_FILE}" ]; then
  ENV_FILE="${ROOT_DIR}/env.recommended"
fi

docker compose \
  --env-file "${ENV_FILE}" \
  -f "${ROOT_DIR}/docker-compose.mvp.yml" \
  --profile fake \
  down --remove-orphans "$@"
