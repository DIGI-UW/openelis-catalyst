#!/usr/bin/env bash
# Remove disposable MVP state, including the OpenELIS bind-mounted database.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
OE_DB_DATA_DIR="${ROOT_DIR}/.openelis-docker/configs/database/data"

if [ ! -f "${ENV_FILE}" ]; then
  ENV_FILE="${ROOT_DIR}/env.recommended"
fi

compose=(
  docker compose
  --env-file "${ENV_FILE}"
  -f "${ROOT_DIR}/docker-compose.mvp.yml"
)
compose_override_file="${MVP_COMPOSE_OVERRIDE_FILE:-}"
if [ -n "${compose_override_file}" ]; then
  if [ ! -f "${compose_override_file}" ]; then
    echo "ERROR: compose override file does not exist: ${compose_override_file}" >&2
    exit 1
  fi
  compose+=(-f "${compose_override_file}")
fi

"${compose[@]}" down --volumes --remove-orphans

if [ -d "${OE_DB_DATA_DIR}" ]; then
  docker run --rm \
    -v "${OE_DB_DATA_DIR}:/data" \
    alpine:3.22 \
    sh -c 'rm -rf /data/* /data/.[!.]* /data/..?*'
fi

rm -f \
  "${ROOT_DIR}/logs/mvp-provenance.json" \
  "${ROOT_DIR}/logs/.mvp-hapi-client.p12" \
  "${ROOT_DIR}/logs/.mvp-hapi-client.pem"
echo "Disposable MVP databases and run provenance reset; model cache retained."
