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
external_profile_override="${MVP_EXTERNAL_PROFILE_ID:-}"
profile_override="${MVP_PROFILE_ID:-}"
hub_context_override="${MED_AGENT_HUB_CONTEXT:-}"
compose_override_override="${MVP_COMPOSE_OVERRIDE_FILE:-}"
gateway_port_override="${GATEWAY_PORT:-}"
ui_port_override="${CATALYST_UI_PORT:-}"
spark_thrift_port_override="${SPARK_THRIFT_PORT:-}"
data_pipes_port_override="${DATA_PIPES_PORT:-}"
hub_port_override="${MED_AGENT_HUB_PORT:-}"
openelis_https_port_override="${OPENELIS_HTTPS_PORT:-}"
hapi_https_port_override="${HAPI_HTTPS_PORT:-}"
superset_port_override="${SUPERSET_PORT:-}"

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

# Explicit invocation settings take precedence over values copied into .env.
if [ -n "${model_backend_override}" ]; then
  export MVP_MODEL_BACKEND="${model_backend_override}"
fi
if [ -n "${external_router_url_override}" ]; then
  export MVP_EXTERNAL_ROUTER_URL="${external_router_url_override}"
fi
if [ -n "${external_profile_override}" ]; then
  export MVP_EXTERNAL_PROFILE_ID="${external_profile_override}"
fi
if [ -n "${profile_override}" ]; then
  export MVP_PROFILE_ID="${profile_override}"
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
if [ -n "${spark_thrift_port_override}" ]; then
  export SPARK_THRIFT_PORT="${spark_thrift_port_override}"
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
if [ -n "${superset_port_override}" ]; then
  export SUPERSET_PORT="${superset_port_override}"
fi

if [ "${MVP_RESOLVE_MODEL_CONFIG_ONLY:-false}" = "true" ]; then
  exec "${ROOT_DIR}/scripts/mvp-health.sh"
fi

mvp_resolve_model_config
model_backend="${MVP_RESOLVED_MODEL_BACKEND}"
router_url="${MVP_RESOLVED_ROUTER_URL}"
profile_id="${MVP_RESOLVED_PROFILE_ID}"

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
hub_context="${MED_AGENT_HUB_CONTEXT:-${ROOT_DIR}/.med-agent-hub}"
hub_build_revision="$(git -C "${hub_context}" rev-parse HEAD)"
if [[ ! "${hub_build_revision}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: unable to resolve a 40-character Hub commit from ${hub_context}" >&2
  exit 1
fi
export HUB_BUILD_REVISION="${hub_build_revision}"

echo "Using external Hub router at ${router_url} with query profile ${profile_id}"

export MVP_SELECTED_ROUTER_URL="${router_url}"
mkdir -p \
  "${ROOT_DIR}/runtime/superset/outbox" \
  "${ROOT_DIR}/runtime/superset/receipts/attempts" \
  "${ROOT_DIR}/runtime/superset/receipts/latest" \
  "${ROOT_DIR}/runtime/superset/receipts/last-verified"
"${compose[@]}" up -d --build \
  certs \
  db.openelis.org \
  oe.openelis.org \
  fhir.openelis.org \
  spark-thriftserver \
  superset-metadata-db \
  superset-init \
  superset \
  fhir-data-pipes \
  med-agent-hub \
  catalyst-gateway \
  catalyst-ui

for attempt in $(seq 1 "${MVP_PROFILE_PREFLIGHT_ATTEMPTS:-30}"); do
  if MED_AGENT_HUB_PORT="${MED_AGENT_HUB_PORT:-8082}" \
    PROFILE_ID="${profile_id}" python3 - <<'PY'
import json
import os
import urllib.request

url = f"http://localhost:{os.environ['MED_AGENT_HUB_PORT']}/v1/hub/query-profiles"
with urllib.request.urlopen(url, timeout=5) as response:
    profiles = json.load(response).get("data", [])
profile_id = os.environ["PROFILE_ID"]
profile = next((item for item in profiles if item.get("id") == profile_id), None)
if profile is None:
    raise SystemExit(f"configured Hub query profile is not defined: {profile_id}")
if profile.get("available") is not True:
    raise SystemExit(
        f"configured Hub query profile is unavailable: {profile_id}; "
        f"{profile.get('unavailable_reasons', [])}"
    )
PY
  then
    echo "OK: real Hub query profile ${profile_id}"
    break
  fi
  if [ "${attempt}" = "${MVP_PROFILE_PREFLIGHT_ATTEMPTS:-30}" ]; then
    echo "ERROR: real Hub query profile ${profile_id} did not become available." >&2
    exit 1
  fi
  echo "Waiting for real Hub query profile (${attempt}/${MVP_PROFILE_PREFLIGHT_ATTEMPTS:-30})..."
  sleep 2
done

echo "MVP services started with a real externally routed Hub profile."
echo "Seed and validate: MVP_MODEL_BACKEND=${model_backend} ./scripts/mvp-seed.sh"
echo "Catalyst UI: http://localhost:${CATALYST_UI_PORT:-3000}"
echo "Superset: http://localhost:${SUPERSET_PORT:-8088}"
