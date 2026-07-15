#!/bin/bash
# Bootstrap OpenELIS docker configs and start OpenELIS + Catalyst on a shared network.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.full-stack.yml"

cd "${ROOT_DIR}"

if [ ! -f "${ROOT_DIR}/.env" ]; then
  cp env.recommended .env
  echo "Created .env from env.recommended"
fi

"${ROOT_DIR}/scripts/bootstrap-deps.sh"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed or not on PATH" >&2
  exit 1
fi

docker compose -f "${COMPOSE_FILE}" up -d --build "$@"

echo ""
echo "Full stack starting. Run ./scripts/full-stack-health.sh to check readiness."
echo "  OpenELIS UI:      https://localhost/  (admin / adminADMIN!)"
echo "  Catalyst Gateway: http://localhost:${GATEWAY_PORT:-8000}/health"
echo "  med-agent-hub:    http://localhost:${MED_AGENT_HUB_PORT:-8080}/health"
