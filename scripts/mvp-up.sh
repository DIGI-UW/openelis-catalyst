#!/usr/bin/env bash
# Bootstrap dependencies and start only the focused OpenELIS Catalyst MVP.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.mvp.yml"

cd "${ROOT_DIR}"

if [ ! -f .env ]; then
  cp env.recommended .env
  echo "Created .env from env.recommended"
fi

set -a
# shellcheck disable=SC1091
. "${ROOT_DIR}/.env"
set +a

"${ROOT_DIR}/scripts/bootstrap-openelis.sh"
"${ROOT_DIR}/scripts/bootstrap-fhir-data-pipes.sh"
"${ROOT_DIR}/scripts/bootstrap-med-agent-hub.sh"

compose=(docker compose --env-file "${ROOT_DIR}/.env" -f "${COMPOSE_FILE}")
router_service="model-router"
if [ "${MVP_FAKE_BACKEND:-false}" = "true" ]; then
  compose+=(--profile fake)
  router_service="model-router-fake"
  export MVP_HUB_LLM_BASE_URL="http://model-router-fake:8077"
  echo "Using deterministic fake model backend"
else
  export MVP_HUB_LLM_BASE_URL="${MVP_HUB_LLM_BASE_URL:-http://model-router:8077}"
  echo "Using live qwen2.5-coder-14b llama.cpp backend"
fi

"${compose[@]}" up -d --build \
  certs \
  db.openelis.org \
  oe.openelis.org \
  fhir.openelis.org \
  analytics-db \
  fhir-data-pipes \
  "${router_service}" \
  med-agent-hub \
  catalyst-gateway \
  catalyst-ui

echo "MVP services started."
echo "Seed and validate: ./scripts/mvp-seed.sh"
echo "Catalyst UI: http://localhost:${CATALYST_UI_PORT:-3000}"
