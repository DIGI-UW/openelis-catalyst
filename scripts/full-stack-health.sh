#!/bin/bash
# Wait for OpenELIS DB, Catalyst gateway, and med-agent-hub health endpoints.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
GATEWAY_PORT=8000
HUB_PORT=8080
OE_URL="${OE_URL:-https://localhost/}"

if [ -f "${ENV_FILE}" ]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi

GATEWAY_PORT="${GATEWAY_PORT:-8000}"
HUB_PORT="${MED_AGENT_HUB_PORT:-8080}"

wait_for() {
  local name="$1"
  local cmd="$2"
  local attempts="${3:-60}"
  local delay="${4:-5}"
  for i in $(seq 1 "${attempts}"); do
    if eval "${cmd}"; then
      echo "OK: ${name}"
      return 0
    fi
    echo "Waiting for ${name} (${i}/${attempts})..."
    sleep "${delay}"
  done
  echo "ERROR: ${name} not ready" >&2
  return 1
}

wait_for "OpenELIS database" \
  "docker compose -f ${ROOT_DIR}/docker-compose.full-stack.yml exec -T db.openelis.org pg_isready -q -d clinlims -U clinlims"

wait_for "Catalyst gateway" \
  "curl -sf http://localhost:${GATEWAY_PORT}/health"

wait_for "med-agent-hub" \
  "curl -sf http://localhost:${HUB_PORT}/health"

if curl -kfs "${OE_URL}" >/dev/null 2>&1; then
  echo "OK: OpenELIS web UI (${OE_URL})"
else
  echo "WARN: OpenELIS web UI not reachable yet at ${OE_URL} (OE webapp may still be starting)"
fi

echo "Full-stack health checks passed (or web UI still warming up)."
