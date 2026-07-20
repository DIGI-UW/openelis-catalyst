#!/usr/bin/env bash
# Bootstrap dependencies and start only the focused OpenELIS Catalyst MVP.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.mvp.yml"
ENV_FILE="${ROOT_DIR}/.env"
# shellcheck disable=SC1091
. "${ROOT_DIR}/scripts/mvp-model-config.sh"

cd "${ROOT_DIR}"

model_backend_override="${MVP_MODEL_BACKEND:-}"
external_router_url_override="${MVP_EXTERNAL_ROUTER_URL:-}"
local_router_url_override="${MVP_LOCAL_ROUTER_URL:-}"
fake_router_url_override="${MVP_FAKE_ROUTER_URL:-}"
external_model_override="${MVP_EXTERNAL_MODEL_ID:-}"
external_profile_override="${MVP_EXTERNAL_PROFILE_ID:-}"
external_role_models_override="${MVP_EXTERNAL_EXPECTED_ROLE_MODELS_JSON:-}"
bundled_model_override="${MVP_BUNDLED_MODEL_ID:-}"
bundled_profile_override="${MVP_BUNDLED_PROFILE_ID:-}"
bundled_role_models_override="${MVP_BUNDLED_EXPECTED_ROLE_MODELS_JSON:-}"
fake_model_override="${MVP_FAKE_MODEL_ID:-}"
fake_profile_override="${MVP_FAKE_PROFILE_ID:-}"
fake_role_models_override="${MVP_FAKE_EXPECTED_ROLE_MODELS_JSON:-}"
expected_model_override="${MVP_EXPECTED_MODEL_ID:-}"
profile_override="${MVP_PROFILE_ID:-}"
expected_role_models_override="${MVP_EXPECTED_ROLE_MODELS_JSON:-}"
hub_context_override="${MED_AGENT_HUB_CONTEXT:-}"
compose_override_override="${MVP_COMPOSE_OVERRIDE_FILE:-}"
gateway_port_override="${GATEWAY_PORT:-}"
ui_port_override="${CATALYST_UI_PORT:-}"
analytics_port_override="${ANALYTICS_DB_PORT:-}"
data_pipes_port_override="${DATA_PIPES_PORT:-}"
hub_port_override="${MED_AGENT_HUB_PORT:-}"
openelis_https_port_override="${OPENELIS_HTTPS_PORT:-}"
hapi_https_port_override="${HAPI_HTTPS_PORT:-}"

if [ ! -f "${ENV_FILE}" ]; then
  if [ "${MVP_RESOLVE_MODEL_CONFIG_ONLY:-false}" = "true" ]; then
    ENV_FILE="${ROOT_DIR}/env.recommended"
  else
    cp env.recommended .env
    echo "Created .env from env.recommended"
  fi
fi

set -a
# shellcheck disable=SC1091
. "${ENV_FILE}"
set +a

# The generic role map is an explicit invocation override. Persistent defaults
# are backend-specific so an external split profile cannot leak into local mode.
unset MVP_EXPECTED_ROLE_MODELS_JSON

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
if [ -n "${external_role_models_override}" ]; then
  export MVP_EXTERNAL_EXPECTED_ROLE_MODELS_JSON="${external_role_models_override}"
fi
if [ -n "${bundled_model_override}" ]; then
  export MVP_BUNDLED_MODEL_ID="${bundled_model_override}"
fi
if [ -n "${bundled_profile_override}" ]; then
  export MVP_BUNDLED_PROFILE_ID="${bundled_profile_override}"
fi
if [ -n "${bundled_role_models_override}" ]; then
  export MVP_BUNDLED_EXPECTED_ROLE_MODELS_JSON="${bundled_role_models_override}"
fi
if [ -n "${fake_model_override}" ]; then
  export MVP_FAKE_MODEL_ID="${fake_model_override}"
fi
if [ -n "${fake_profile_override}" ]; then
  export MVP_FAKE_PROFILE_ID="${fake_profile_override}"
fi
if [ -n "${fake_role_models_override}" ]; then
  export MVP_FAKE_EXPECTED_ROLE_MODELS_JSON="${fake_role_models_override}"
fi
if [ -n "${expected_model_override}" ]; then
  export MVP_EXPECTED_MODEL_ID="${expected_model_override}"
fi
if [ -n "${profile_override}" ]; then
  export MVP_PROFILE_ID="${profile_override}"
fi
if [ -n "${expected_role_models_override}" ]; then
  export MVP_EXPECTED_ROLE_MODELS_JSON="${expected_role_models_override}"
fi
if [ -n "${hub_context_override}" ]; then
  export MED_AGENT_HUB_CONTEXT="${hub_context_override}"
fi
if [ -n "${compose_override_override}" ]; then
  export MVP_COMPOSE_OVERRIDE_FILE="${compose_override_override}"
fi
if [ -n "${gateway_port_override}" ]; then
  export GATEWAY_PORT="${gateway_port_override}"
fi
if [ -n "${ui_port_override}" ]; then
  export CATALYST_UI_PORT="${ui_port_override}"
fi
if [ -n "${analytics_port_override}" ]; then
  export ANALYTICS_DB_PORT="${analytics_port_override}"
fi
if [ -n "${data_pipes_port_override}" ]; then
  export DATA_PIPES_PORT="${data_pipes_port_override}"
fi
if [ -n "${hub_port_override}" ]; then
  export MED_AGENT_HUB_PORT="${hub_port_override}"
fi
if [ -n "${openelis_https_port_override}" ]; then
  export OPENELIS_HTTPS_PORT="${openelis_https_port_override}"
fi
if [ -n "${hapi_https_port_override}" ]; then
  export HAPI_HTTPS_PORT="${hapi_https_port_override}"
fi

if [ "${MVP_RESOLVE_MODEL_CONFIG_ONLY:-false}" = "true" ]; then
  exec "${ROOT_DIR}/scripts/mvp-health.sh"
fi

mvp_resolve_model_config
model_backend="${MVP_RESOLVED_MODEL_BACKEND}"
router_url="${MVP_RESOLVED_ROUTER_URL}"
model_id="${MVP_RESOLVED_MODEL_ID}"

compose=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")
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

case "${model_backend}" in
  fake)
    compose+=(--profile fake)
    model_services+=(model-router-fake)
    stale_model_services+=(model-router)
    echo "Using deterministic fake model backend"
    ;;
  local)
    "${ROOT_DIR}/scripts/mvp-download-model.sh"
    model_services+=(model-router)
    stale_model_services+=(model-router-fake)
    echo "Using bundled ${model_id} llama.cpp backend"
    ;;
  external)
    stale_model_services+=(model-router model-router-fake)
    echo "Using external ${model_id} backend at ${router_url}"
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
echo "Seed and validate: MVP_MODEL_BACKEND=${model_backend} ./scripts/mvp-seed.sh"
echo "Catalyst UI: http://localhost:${CATALYST_UI_PORT:-3000}"
