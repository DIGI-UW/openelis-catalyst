#!/usr/bin/env bash
# Gate every MVP dependency and emit live, non-secret assembly provenance.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
PINNED_COMMIT="3ea890884d674e2f31257a2da421601f2d75b5e9"
PINNED_OPENELIS_DOCKER_COMMIT="f118d0ae778a30028c16be2af549843ec166f655"
CURL_CONNECT_TIMEOUT_SECONDS="${MVP_CURL_CONNECT_TIMEOUT_SECONDS:-5}"
CURL_MAX_TIME_SECONDS="${MVP_CURL_MAX_TIME_SECONDS:-15}"
# shellcheck disable=SC1091
. "${ROOT_DIR}/scripts/mvp-model-config.sh"
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
superset_port_override="${SUPERSET_PORT:-}"

if [ ! -f "${ENV_FILE}" ]; then
  ENV_FILE="${ROOT_DIR}/env.recommended"
fi
set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a
unset MVP_EXPECTED_ROLE_MODELS_JSON
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
if [ -n "${superset_port_override}" ]; then
  export SUPERSET_PORT="${superset_port_override}"
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
compose+=(--profile fake)

mvp_resolve_model_config
model_backend="${MVP_RESOLVED_MODEL_BACKEND}"
router_mode="${model_backend}"
router_url="${MVP_RESOLVED_ROUTER_URL}"
model_id="${MVP_RESOLVED_MODEL_ID}"
profile_id="${MVP_RESOLVED_PROFILE_ID}"
role_models_json="${MVP_RESOLVED_ROLE_MODELS_JSON}"
role_models_json="$(
  EXPECTED_ROLE_MODELS_JSON="${role_models_json}" python3 - <<'PY'
import json
import os

role_models = json.loads(os.environ["EXPECTED_ROLE_MODELS_JSON"])
allowed_role_sets = (
    {"query_generate"},
    {"query_generate", "query_review"},
)
if not isinstance(role_models, dict) or set(role_models) not in allowed_role_sets:
    raise SystemExit(
        "MVP_EXPECTED_ROLE_MODELS_JSON must map query_generate and may map query_review"
    )
if any(not isinstance(model, str) or not model.strip() for model in role_models.values()):
    raise SystemExit("MVP_EXPECTED_ROLE_MODELS_JSON model IDs must be non-empty strings")
print(json.dumps(role_models, sort_keys=True, separators=(",", ":")))
PY
)"

if [ "${MVP_RESOLVE_MODEL_CONFIG_ONLY:-false}" = "true" ]; then
  MODEL_BACKEND="${model_backend}" \
  ROUTER_URL="${router_url}" \
  MODEL_ID="${model_id}" \
  PROFILE_ID="${profile_id}" \
  ROLE_MODELS_JSON="${role_models_json}" \
  python3 - <<'PY'
import json
import os

print(json.dumps({
    "backend": os.environ["MODEL_BACKEND"],
    "routerUrl": os.environ["ROUTER_URL"],
    "modelId": os.environ["MODEL_ID"],
    "profileId": os.environ["PROFILE_ID"],
    "roleModels": json.loads(os.environ["ROLE_MODELS_JSON"]),
}, sort_keys=True))
PY
  exit 0
fi

wait_for() {
  local name="$1"
  shift
  local attempt
  for attempt in $(seq 1 "${MVP_HEALTH_ATTEMPTS:-120}"); do
    if "$@" >/dev/null 2>&1; then
      echo "OK: ${name}"
      return 0
    fi
    echo "Waiting for ${name} (${attempt}/${MVP_HEALTH_ATTEMPTS:-120})..."
    sleep "${MVP_HEALTH_DELAY_SECONDS:-5}"
  done
  echo "ERROR: ${name} not ready" >&2
  return 1
}

analytics_psql() {
  "${compose[@]}" exec -T analytics-db \
    psql --username catalyst_analytics_writer --dbname catalyst_analytics \
    --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 "$@"
}

mkdir -p "${ROOT_DIR}/logs"
hapi_client_p12="${ROOT_DIR}/logs/.mvp-hapi-client.p12"
hapi_client_pem="${ROOT_DIR}/logs/.mvp-hapi-client.pem"
"${compose[@]}" cp \
  oe.openelis.org:/etc/openelis-global/client_facing_keystore \
  "${hapi_client_p12}" >/dev/null
openssl pkcs12 -legacy \
  -in "${hapi_client_p12}" \
  -out "${hapi_client_pem}" \
  -nodes \
  -passin pass:kspass
chmod 600 "${hapi_client_p12}" "${hapi_client_pem}"

check_openelis_db() {
  "${compose[@]}" exec -T db.openelis.org \
    pg_isready -q -d clinlims -U clinlims
}

check_openelis_deployment_pin() {
  test "$(git -C "${ROOT_DIR}/.openelis-docker" rev-parse HEAD)" = \
    "${OPENELIS_DOCKER_REF:-${PINNED_OPENELIS_DOCKER_COMMIT}}"
}

check_openelis_app() {
  curl -kfsS \
    --connect-timeout "${CURL_CONNECT_TIMEOUT_SECONDS}" \
    --max-time "${CURL_MAX_TIME_SECONDS}" \
    "https://localhost:${OPENELIS_HTTPS_PORT:-8443}/OpenELIS-Global/" \
    >/dev/null
}

check_hapi_seed() {
  local base="https://localhost:${HAPI_HTTPS_PORT:-8444}/fhir"
  local resource expected ids response total
  while IFS='|' read -r resource expected ids; do
    response="$(
      curl -kfsS \
        --connect-timeout "${CURL_CONNECT_TIMEOUT_SECONDS}" \
        --max-time "${CURL_MAX_TIME_SECONDS}" \
        --cert "${hapi_client_pem}" \
        "${base}/${resource}?_summary=count&_id=${ids}"
    )"
    total="$(
      FHIR_RESPONSE="${response}" python3 -c \
        'import json,os; print(json.loads(os.environ["FHIR_RESPONSE"]).get("total", 0))'
    )"
    [ "${total}" = "${expected}" ] || return 1
  done <<'EOF'
Patient|1|11111111-1111-4111-8111-111111111111
Observation|3|51111111-1111-4111-8111-111111111111,52222222-2222-4222-8222-222222222222,53333333-3333-4333-8333-333333333333
ServiceRequest|3|41111111-1111-4111-8111-111111111111,42222222-2222-4222-8222-222222222222,43333333-3333-4333-8333-333333333333
Specimen|3|31111111-1111-4111-8111-111111111111,32222222-2222-4222-8222-222222222222,33333333-3333-4333-8333-333333333333
DiagnosticReport|3|41111111-1111-4111-8111-111111111111,42222222-2222-4222-8222-222222222222,43333333-3333-4333-8333-333333333333
EOF
}

check_data_pipes() {
  test "$(
    git -C "${ROOT_DIR}/.fhir-data-pipes" rev-parse HEAD
  )" = "${PINNED_COMMIT}" &&
    curl -fsS \
      --connect-timeout "${CURL_CONNECT_TIMEOUT_SECONDS}" \
      --max-time "${CURL_MAX_TIME_SECONDS}" \
      "http://localhost:${DATA_PIPES_PORT:-8090}/actuator/health" >/dev/null &&
    test "$(
      curl -fsS \
        --connect-timeout "${CURL_CONNECT_TIMEOUT_SECONDS}" \
        --max-time "${CURL_MAX_TIME_SECONDS}" \
        "http://localhost:${DATA_PIPES_PORT:-8090}/status" |
        python3 -c 'import json,sys; print(json.load(sys.stdin).get("pipelineStatus", ""))'
    )" = "IDLE"
}

check_mart() {
  test "$(
    analytics_psql --command="
      SELECT
        count(*)::text || '|' ||
        count(DISTINCT patient_id)::text || '|' ||
        count(DISTINCT test_name)::text || '|' ||
        count(*) FILTER (WHERE test_name = 'Viral Load')::text || '|' ||
        count(*) FILTER (
          WHERE test_code_system = 'http://loinc.org'
            AND NULLIF(test_code, '') IS NOT NULL
        )::text || '|' ||
        count(DISTINCT test_code)::text || '|' ||
        min(observed_at)::date::text || '|' ||
        max(observed_at)::date::text
      FROM analytics.lab_result_fact_v1;
    "
  )" = "1152|96|9|384|1152|9|2025-07-15|2026-04-27" &&
    test "$(
      analytics_psql --command="
        SELECT
          count(*)::text || '|' ||
          count(*) FILTER (
            WHERE test_code_system = 'http://loinc.org'
              AND NULLIF(test_code, '') IS NOT NULL
              AND NULLIF(test_name, '') IS NOT NULL
          )::text || '|' ||
          count(DISTINCT test_code)::text
        FROM public.service_request_flat_v1;
      "
    )" = "1152|1152|9" &&
    test "$(
      analytics_psql --command="
        SELECT count(*)
        FROM analytics.pipeline_run_v1
        WHERE completion_state = 'succeeded'
          AND data_pipes_commit = '${PINNED_COMMIT}'
          AND source_watermark IS NOT NULL;
      "
    )" -ge 1
}

check_router() {
  "${compose[@]}" exec -T \
    -e "ROUTER_URL=${router_url}" \
    -e "EXPECTED_ROLE_MODELS_JSON=${role_models_json}" \
    med-agent-hub python - <<'PY'
import json
import os
import urllib.request

with urllib.request.urlopen(
    os.environ["ROUTER_URL"] + "/v1/models", timeout=5
) as response:
    models = json.load(response).get("data", [])
expected = json.loads(os.environ["EXPECTED_ROLE_MODELS_JSON"])
served = {item.get("id") for item in models}
missing = sorted(set(expected.values()) - served)
if missing:
    raise SystemExit(f"expected profile models are not served: {missing!r}")
PY
}

check_hub_router_config() {
  "${compose[@]}" exec -T \
    -e "EXPECTED_ROUTER_URL=${router_url}" \
    med-agent-hub python - <<'PY'
import os

configured = os.environ.get("LLM_BASE_URL", "").rstrip("/")
expected = os.environ["EXPECTED_ROUTER_URL"].rstrip("/")
if configured != expected:
    raise SystemExit(
        f"Hub LLM_BASE_URL {configured!r} does not match selected router {expected!r}"
    )
PY
}

# The governed-query profiles moved out of the hub when the gateway took over
# orchestration: the hub is now a generic model executor and advertises no
# catalyst-query-* ids at all. Probe the gateway, which owns them.
check_hub_profile() {
  GATEWAY_URL="http://localhost:${GATEWAY_PORT:-8000}" \
  PROFILE_ID="${profile_id}" \
  EXPECTED_ROLE_MODELS_JSON="${role_models_json}" \
  python3 - <<'PY'
import json
import os
import urllib.request

url = os.environ["GATEWAY_URL"] + "/v1/catalyst/query-options"
with urllib.request.urlopen(url, timeout=5) as response:
    payload = json.load(response)
profiles = payload.get("profiles", [])
profile_id = os.environ["PROFILE_ID"]
expected_role_models = json.loads(os.environ["EXPECTED_ROLE_MODELS_JSON"])

profile = next((item for item in profiles if item.get("id") == profile_id), None)
if profile is None:
    raise SystemExit(
        f"{profile_id} is not offered; gateway advertises "
        f"{[item.get('id') for item in profiles]}"
    )
if profile.get("available") is not True:
    raise SystemExit(
        f"{profile_id} is unavailable: {profile.get('unavailableReasons', [])}"
    )

role_models = profile.get("roleModels", {})
if role_models != expected_role_models:
    raise SystemExit(
        f"{profile_id} role models {role_models!r} do not match "
        f"{expected_role_models!r}"
    )

provenance = profile.get("provenance")
if not isinstance(provenance, dict):
    raise SystemExit(f"{profile_id} does not expose provenance")
if provenance.get("profileId") != profile_id:
    raise SystemExit("provenance.profileId does not match the selected profile")
if provenance.get("profileLabel") != profile.get("label"):
    raise SystemExit("provenance.profileLabel does not match the profile label")
if not str(provenance.get("profileConfigurationDigest", "")):
    raise SystemExit("provenance.profileConfigurationDigest is required")

# A reviewed profile has to actually run a review stage and be able to revise.
if "query_review" in expected_role_models:
    if profile.get("revisionCapable") is not True:
        raise SystemExit(f"{profile_id} is not advertised as revision capable")
    if "query_review" not in profile.get("stages", []):
        raise SystemExit(f"{profile_id} does not run a query_review stage")
PY
}

check_gateway() {
  GATEWAY_URL="http://localhost:${GATEWAY_PORT:-8000}" python3 - <<'PY'
import json
import os
import urllib.request

with urllib.request.urlopen(os.environ["GATEWAY_URL"] + "/health", timeout=5) as response:
    health = json.load(response)
if health.get("status") != "ready":
    raise SystemExit(json.dumps(health))
PY
}

check_ui() {
  curl -fsS \
    --connect-timeout "${CURL_CONNECT_TIMEOUT_SECONDS}" \
    --max-time "${CURL_MAX_TIME_SECONDS}" \
    "http://localhost:${CATALYST_UI_PORT:-3000}/health" >/dev/null
}

check_superset() {
  SUPERSET_URL="http://localhost:${SUPERSET_PORT:-8088}" python3 - <<'PY'
import json
import os
import urllib.request

with urllib.request.urlopen(os.environ["SUPERSET_URL"] + "/health", timeout=5) as response:
    health = json.load(response)
if health.get("status") != "OK":
    raise SystemExit(json.dumps(health))
PY
}

wait_for "OpenELIS deployment pin" check_openelis_deployment_pin
wait_for "OpenELIS database" check_openelis_db
wait_for "OpenELIS application" check_openelis_app
wait_for "HAPI seed resources" check_hapi_seed
wait_for "FHIR Data Pipes controller" check_data_pipes
wait_for "analytics mart exact rows" check_mart
wait_for "model router" check_router
wait_for "hub router configuration" check_hub_router_config
wait_for "hub query profile" check_hub_profile
wait_for "Catalyst gateway" check_gateway
wait_for "Catalyst UI" check_ui
wait_for "Superset renderer" check_superset

pipeline_json="$(
  analytics_psql --command="
    SELECT json_build_object(
      'pipelineRunId', pipeline_run_id,
      'completionState', completion_state,
      'sourceWatermark', source_watermark,
      'startedAt', started_at,
      'completedAt', completed_at,
      'observedAt', observed_at,
      'observedLagSeconds', observed_lag_seconds,
      'resourceCounts', resource_counts
    )
    FROM analytics.pipeline_freshness_v1
    WHERE completion_state = 'succeeded'
    ORDER BY completed_at DESC
    LIMIT 1;
  "
)"
hub_context="${MED_AGENT_HUB_CONTEXT:-${ROOT_DIR}/.med-agent-hub}"
if ! git -C "${hub_context}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: med-agent-hub source is unavailable at ${hub_context}." >&2
  exit 1
fi
hub_commit="$(git -C "${hub_context}" rev-parse HEAD)"
hub_source="standalone-fallback"
if [ -n "${MED_AGENT_HUB_CONTEXT:-}" ]; then
  hub_source="harness-sibling"
fi
hub_dirty=false
if [ -n "$(git -C "${hub_context}" status --porcelain)" ]; then
  hub_dirty=true
fi
mkdir -p "${ROOT_DIR}/logs"
PIPELINE_JSON="${pipeline_json}" \
PROVENANCE_PATH="${ROOT_DIR}/logs/mvp-provenance.json" \
OPENELIS_VERSION="${OPENELIS_VERSION:-unknown}" \
OPENELIS_DOCKER_COMMIT="${OPENELIS_DOCKER_REF:-${PINNED_OPENELIS_DOCKER_COMMIT}}" \
DATA_PIPES_COMMIT="${PINNED_COMMIT}" \
HUB_COMMIT="${hub_commit}" \
HUB_SOURCE="${hub_source}" \
HUB_DIRTY="${hub_dirty}" \
ROUTER_MODE="${router_mode}" \
ROUTER_URL="${router_url}" \
ROLE_MODELS_JSON="${role_models_json}" \
PROFILE_ID="${profile_id}" \
MODEL_REPO="${MVP_MODEL_REPO:-bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF}" \
MODEL_FILE="${MVP_MODEL_FILE:-Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf}" \
SUPERSET_IMAGE="apache/superset:6.1.0-dev@sha256:5822dff49c41fd745ce33e38af502f9c64df30d133aeba148c5d89b35a1004ef" \
python3 - <<'PY'
import datetime
import json
import os
from pathlib import Path

role_models = json.loads(os.environ["ROLE_MODELS_JSON"])
model_ids = sorted(set(role_models.values()))
model_router = {
    "mode": os.environ["ROUTER_MODE"],
    "baseUrl": os.environ["ROUTER_URL"],
    "modelIds": model_ids,
    "roleModels": role_models,
    "profileId": os.environ["PROFILE_ID"],
}
if len(model_ids) == 1:
    model_router["modelId"] = model_ids[0]
if os.environ["ROUTER_MODE"] == "local":
    model_router["artifact"] = {
        "repository": os.environ["MODEL_REPO"],
        "file": os.environ["MODEL_FILE"],
    }
elif os.environ["ROUTER_MODE"] == "fake":
    model_router["simulated"] = True

payload = {
    "contractVersion": "catalyst.mvp.provenance.v1",
    "generatedAt": datetime.datetime.now(datetime.timezone.utc)
    .isoformat()
    .replace("+00:00", "Z"),
    "openelisVersion": os.environ["OPENELIS_VERSION"],
    "openelisDocker": {
        "commit": os.environ["OPENELIS_DOCKER_COMMIT"],
        "images": {
            "certs": "itechuw/certgen@sha256:e27a8194300ba73309e835a4070e9ce531687eb3ee604895de781f3061791635",
            "database": "itechuw/openelis-global-2-database@sha256:e801c93a8bedc41c2e502722e38585979fbbaf0e92ee4c248cdde72d9c33ec1e",
            "application": "itechuw/openelis-global-2@sha256:2217d76104051589d99eb808cef22ae692f6ad2d12a0fadc70ecc549162df36f",
            "fhir": "itechuw/openelis-global-2-fhir@sha256:667680632b8fe491bb1955f3935751562e60933d3aea91d79256ccd4eac857c3",
        },
    },
    "fhirDataPipes": {"commit": os.environ["DATA_PIPES_COMMIT"], "spark": False},
    "medAgentHub": {
        "commit": os.environ["HUB_COMMIT"],
        "source": os.environ["HUB_SOURCE"],
        "workingTreeDirty": os.environ["HUB_DIRTY"] == "true",
    },
    "modelRouter": model_router,
    "catalog": {
        "contractVersion": "catalyst.analytics.catalog.v1",
        "catalogVersion": "analytics-catalog-v1",
    },
    "superset": {
        "version": "6.1.0",
        "image": os.environ["SUPERSET_IMAGE"],
        "metadataStore": "superset-metadata-db",
    },
    "pipelineRun": json.loads(os.environ["PIPELINE_JSON"]),
}
path = Path(os.environ["PROVENANCE_PATH"])
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2))
PY

echo "MVP health gates passed; provenance: logs/mvp-provenance.json"
