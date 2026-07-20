#!/usr/bin/env bash
# Bootstrap dependencies and start only the focused OpenELIS Catalyst MVP.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.mvp.yml"

cd "${ROOT_DIR}"

model_backend_override="${MVP_MODEL_BACKEND:-}"
external_router_url_override="${MVP_EXTERNAL_ROUTER_URL:-}"
local_router_url_override="${MVP_LOCAL_ROUTER_URL:-}"
fake_router_url_override="${MVP_FAKE_ROUTER_URL:-}"
external_model_override="${MVP_EXTERNAL_MODEL_ID:-}"
external_profile_override="${MVP_EXTERNAL_PROFILE_ID:-}"
hub_context_override="${MED_AGENT_HUB_CONTEXT:-}"
compose_override_override="${MVP_COMPOSE_OVERRIDE_FILE:-}"

if [ ! -f .env ]; then
  cp env.recommended .env
  echo "Created .env from env.recommended"
fi

set -a
# shellcheck disable=SC1091
. "${ROOT_DIR}/.env"
set +a

# Explicit invocation settings take precedence over values copied into .env.
if [ -n "${model_backend_override}" ]; then
  export MVP_MODEL_BACKEND="${model_backend_override}"
fi
if [ -n "${external_router_url_override}" ]; then
  export MVP_EXTERNAL_ROUTER_URL="${external_router_url_override}"
fi
if [ -n "${local_router_url_override}" ]; then
  export MVP_LOCAL_ROUTER_URL="${local_router_url_override}"
fi
if [ -n "${fake_router_url_override}" ]; then
  export MVP_FAKE_ROUTER_URL="${fake_router_url_override}"
fi
if [ -n "${external_model_override}" ]; then
  export MVP_EXTERNAL_MODEL_ID="${external_model_override}"
fi
if [ -n "${external_profile_override}" ]; then
  export MVP_EXTERNAL_PROFILE_ID="${external_profile_override}"
fi
if [ -n "${hub_context_override}" ]; then
  export MED_AGENT_HUB_CONTEXT="${hub_context_override}"
fi
if [ -n "${compose_override_override}" ]; then
  export MVP_COMPOSE_OVERRIDE_FILE="${compose_override_override}"
fi

compose=(docker compose --env-file "${ROOT_DIR}/.env" -f "${COMPOSE_FILE}")
compose_override_file="${MVP_COMPOSE_OVERRIDE_FILE:-}"
if [ -n "${compose_override_file}" ]; then
  if [ ! -f "${compose_override_file}" ]; then
    echo "ERROR: compose override file does not exist: ${compose_override_file}" >&2
    exit 1
  fi
  compose+=(-f "${compose_override_file}")
fi

"${ROOT_DIR}/scripts/bootstrap-openelis.sh"
"${ROOT_DIR}/scripts/bootstrap-fhir-data-pipes.sh"
if [ -n "${MED_AGENT_HUB_CONTEXT:-}" ]; then
  if [ ! -f "${MED_AGENT_HUB_CONTEXT}/Dockerfile" ]; then
    echo "ERROR: MED_AGENT_HUB_CONTEXT does not contain a Hub Dockerfile: ${MED_AGENT_HUB_CONTEXT}" >&2
    exit 1
  fi
  echo "Using med-agent-hub source at ${MED_AGENT_HUB_CONTEXT}"
else
  "${ROOT_DIR}/scripts/bootstrap-med-agent-hub.sh"
fi

compose_all_profiles=("${compose[@]}" --profile fake)
model_services=()
stale_model_services=()
model_backend="${MVP_MODEL_BACKEND:-external}"
external_router_url="${MVP_EXTERNAL_ROUTER_URL:-http://host.docker.internal:8077}"
local_router_url="${MVP_LOCAL_ROUTER_URL:-http://model-router:8077}"
fake_router_url="${MVP_FAKE_ROUTER_URL:-http://model-router-fake:8077}"

case "${model_backend}" in
  fake)
    compose+=(--profile fake)
    model_services+=(model-router-fake)
    stale_model_services+=(model-router)
    router_url="${fake_router_url}"
    echo "Using deterministic fake model backend"
    ;;
  local)
    "${ROOT_DIR}/scripts/mvp-download-model.sh"
    model_services+=(model-router)
    stale_model_services+=(model-router-fake)
    router_url="${local_router_url}"
    echo "Using bundled ${MVP_BUNDLED_MODEL_ID:-qwen2.5-coder-1.5b-instruct-q4_k_m} llama.cpp backend"
    ;;
  external)
    stale_model_services+=(model-router model-router-fake)
    router_url="${external_router_url}"
    echo "Using external ${MVP_EXTERNAL_MODEL_ID:-gemma-e4b} backend at ${router_url}"
    ;;
  *)
    echo "ERROR: MVP_MODEL_BACKEND must be local, external, or fake." >&2
    exit 2
    ;;
esac

export MVP_SELECTED_ROUTER_URL="${router_url}"
"${compose_all_profiles[@]}" stop "${stale_model_services[@]}"

"${compose[@]}" up -d --build \
  certs \
  db.openelis.org \
  oe.openelis.org \
  fhir.openelis.org \
  analytics-db \
  fhir-data-pipes \
  "${model_services[@]}" \
  med-agent-hub \
  catalyst-gateway \
  catalyst-ui

echo "MVP services started."
echo "Seed and validate: ./scripts/mvp-seed.sh"
echo "Catalyst UI: http://localhost:${CATALYST_UI_PORT:-3000}"
