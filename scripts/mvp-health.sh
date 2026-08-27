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
  ENV_FILE="${ROOT_DIR}/env.recommended"
fi
set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a
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
mvp_resolve_model_config
model_backend="${MVP_RESOLVED_MODEL_BACKEND}"
router_mode="${model_backend}"
router_url="${MVP_RESOLVED_ROUTER_URL}"
profile_id="${MVP_RESOLVED_PROFILE_ID}"

if [ "${MVP_RESOLVE_MODEL_CONFIG_ONLY:-false}" = "true" ]; then
  MODEL_BACKEND="${model_backend}" \
  ROUTER_URL="${router_url}" \
  PROFILE_ID="${profile_id}" \
  python3 - <<'PY'
import json
import os

print(json.dumps({
    "backend": os.environ["MODEL_BACKEND"],
    "routerUrl": os.environ["ROUTER_URL"],
    "profileId": os.environ["PROFILE_ID"],
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

spark_sql() {
  "${compose[@]}" exec -T spark-thriftserver \
    beeline -u 'jdbc:hive2://localhost:10000' \
    --silent=true --outputformat=tsv2 -e "$1" 2>/dev/null | tail -n +2
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

check_warehouse() {
  # Reading through Spark, not the filesystem, is what proves the registered
  # views resolve to the Parquet the controller wrote.
  local views rows
  views="$(spark_sql 'SHOW VIEWS;' | wc -l | tr -d '[:space:]')"
  test "${views}" -ge 1 || return 1
  rows="$(spark_sql 'SELECT COUNT(*) FROM patient_flat;' | tr -d '[:space:]')"
  test -n "${rows}" && test "${rows}" -gt 0
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

check_hub_profile() {
  HUB_URL="http://localhost:${MED_AGENT_HUB_PORT:-8082}" \
  PROFILE_ID="${profile_id}" \
  python3 - <<'PY'
import json
import os
import urllib.request

url = os.environ["HUB_URL"] + "/v1/hub/query-profiles"
with urllib.request.urlopen(url, timeout=5) as response:
    payload = json.load(response)
profiles = payload.get("profiles", [])
if not profiles:
    profiles = payload.get("data", [])
profile_id = os.environ["PROFILE_ID"]
profile = next((item for item in profiles if item.get("id") == profile_id), None)
if profile is None:
    raise SystemExit(
        f"{profile_id} is not defined by Hub; Hub advertises "
        f"{[item.get('id') for item in profiles]}"
    )
if profile.get("available") is not True:
    raise SystemExit(
        f"{profile_id} is unavailable: {profile.get('unavailableReasons', [])}"
    )
role_models = profile.get("role_models", {})
# A writer is required; a reviewer is optional. Hub advertises writer-only and
# reviewed profiles alike, so pinning an exact role set here would reject valid
# configured profiles rather than check the stack.
if "query_generate" not in role_models:
    raise SystemExit(f"{profile_id} must define a writer role: {role_models!r}")
if not set(role_models) <= {"query_generate", "query_review"}:
    raise SystemExit(f"{profile_id} defines unknown roles: {role_models!r}")
evidence = profile.get("profileEvidence")
if not isinstance(evidence, dict) or evidence.get("profileId") != profile_id:
    raise SystemExit(f"{profile_id} does not expose matching Hub profile evidence")
if not str(evidence.get("profileDigest", "")):
    raise SystemExit("Hub profile evidence must have profileDigest")
print(json.dumps(role_models, sort_keys=True, separators=(",", ":")))
PY
}

check_gateway_profile() {
  GATEWAY_URL="http://localhost:${GATEWAY_PORT:-8000}" \
  PROFILE_ID="${profile_id}" \
  EXPECTED_ROLE_MODELS_JSON="${role_models_json}" \
  python3 - <<'PY'
import json
import os
import urllib.request

with urllib.request.urlopen(
    os.environ["GATEWAY_URL"] + "/v1/catalyst/query-options", timeout=5
) as response:
    profiles = json.load(response).get("profiles", [])
profile = next((item for item in profiles if item.get("id") == os.environ["PROFILE_ID"]), None)
if profile is None:
    raise SystemExit("Gateway did not expose the selected available Hub profile")
expected = json.loads(os.environ["EXPECTED_ROLE_MODELS_JSON"])
if profile.get("roleModels") != expected:
    raise SystemExit("Gateway profile role models differ from Hub discovery")
if profile.get("available") is not True:
    raise SystemExit("Gateway exposed an unavailable Hub profile")
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
    body = response.read().decode("utf-8").strip()
try:
    health = json.loads(body)
except json.JSONDecodeError:
    if body != "OK":
        raise SystemExit(body)
else:
    if health.get("status") != "OK":
        raise SystemExit(json.dumps(health))
PY
}

wait_for "OpenELIS deployment pin" check_openelis_deployment_pin
wait_for "OpenELIS database" check_openelis_db
wait_for "OpenELIS application" check_openelis_app
wait_for "HAPI seed resources" check_hapi_seed
wait_for "FHIR Data Pipes controller" check_data_pipes
wait_for "Spark warehouse readable" check_warehouse
wait_for "hub router configuration" check_hub_router_config
wait_for "hub query profile" check_hub_profile
role_models_json="$(check_hub_profile)"
wait_for "gateway view of Hub query profile" check_gateway_profile
wait_for "Catalyst gateway" check_gateway
wait_for "Catalyst UI" check_ui
wait_for "Superset renderer" check_superset

# The retired PostgreSQL path kept a hand-registered pipeline_run row. Nothing
# writes one on the warehouse path, so provenance records what the warehouse
# itself reports rather than a second source of truth.
pipeline_json="$(
  printf '{"registeredViews": %s}' \
    "$(spark_sql 'SHOW VIEWS;' | wc -l | tr -d '[:space:]')"
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
SUPERSET_PLATFORM="${SUPERSET_PLATFORM:-linux/arm64}" \
SUPERSET_DRIVER_REVISION="${SUPERSET_DRIVER_REVISION:-psycopg2-binary==2.9.9}" \
python3 - <<'PY'
import datetime
import json
import os
from pathlib import Path

role_models = json.loads(os.environ["ROLE_MODELS_JSON"])
model_router = {
    "mode": os.environ["ROUTER_MODE"],
    "baseUrl": os.environ["ROUTER_URL"],
    "roleModels": role_models,
    "profileId": os.environ["PROFILE_ID"],
}
model_router["modelIds"] = sorted(set(role_models.values()))

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
        "catalogVersion": "live",
    },
    "superset": {
        "version": "6.1.0",
        "image": os.environ["SUPERSET_IMAGE"],
        "platform": os.environ["SUPERSET_PLATFORM"],
        "driverRevision": os.environ["SUPERSET_DRIVER_REVISION"],
        "metadataStore": "superset-metadata-db",
    },
    "pipelineRun": json.loads(os.environ["PIPELINE_JSON"]),
}
path = Path(os.environ["PROVENANCE_PATH"])
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2))
PY

echo "MVP health gates passed; provenance: logs/mvp-provenance.json"
