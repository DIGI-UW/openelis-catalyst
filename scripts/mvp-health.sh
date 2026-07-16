#!/usr/bin/env bash
# Gate every MVP dependency and emit live, non-secret assembly provenance.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
PINNED_COMMIT="3ea890884d674e2f31257a2da421601f2d75b5e9"

if [ ! -f "${ENV_FILE}" ]; then
  ENV_FILE="${ROOT_DIR}/env.recommended"
fi
set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a

compose=(
  docker compose
  --env-file "${ENV_FILE}"
  -f "${ROOT_DIR}/docker-compose.mvp.yml"
  --profile fake
)

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

check_openelis_app() {
  curl -kfsS \
    "https://localhost:${OPENELIS_HTTPS_PORT:-8443}/OpenELIS-Global/" \
    >/dev/null
}

check_hapi_seed() {
  local base="https://localhost:${HAPI_HTTPS_PORT:-8444}/fhir"
  local resource expected ids response total
  while IFS='|' read -r resource expected ids; do
    response="$(
      curl -kfsS \
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
      "http://localhost:${DATA_PIPES_PORT:-8090}/actuator/health" >/dev/null &&
    test "$(
      curl -fsS "http://localhost:${DATA_PIPES_PORT:-8090}/status" |
        python3 -c 'import json,sys; print(json.load(sys.stdin).get("pipelineStatus", ""))'
    )" = "IDLE"
}

check_mart() {
  test "$(
    analytics_psql --command="
      SELECT
        count(*)::text || '|' ||
        string_agg(result_value::numeric::text, ',' ORDER BY result_value) || '|' ||
        string_agg(round(receipt_to_release_minutes)::text, ',' ORDER BY observed_at)
      FROM analytics.lab_result_fact_v1;
    "
  )" = "3|80,450,1200|60,60,60" &&
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

running_services="$("${compose[@]}" ps --services --status running)"
router_host="model-router"
router_mode="live"
if printf '%s\n' "${running_services}" | awk '$0 == "model-router-fake" { found=1 } END { exit !found }'; then
  router_host="model-router-fake"
  router_mode="fake"
fi

check_router() {
  "${compose[@]}" exec -T \
    -e "ROUTER_URL=http://${router_host}:8077" \
    med-agent-hub python - <<'PY'
import json
import os
import urllib.request

with urllib.request.urlopen(
    os.environ["ROUTER_URL"] + "/v1/models", timeout=5
) as response:
    models = json.load(response).get("data", [])
if "qwen2.5-coder-14b" not in {item.get("id") for item in models}:
    raise SystemExit("qwen2.5-coder-14b alias is not served")
PY
}

check_hub_profile() {
  HUB_URL="http://localhost:${MED_AGENT_HUB_PORT:-8082}" python3 - <<'PY'
import json
import os
import urllib.request

with urllib.request.urlopen(os.environ["HUB_URL"] + "/v1/models", timeout=5) as response:
    models = json.load(response).get("data", [])
profile = next((item for item in models if item.get("id") == "catalyst-query-checked"), None)
if not profile or profile.get("available") is not True:
    raise SystemExit("catalyst-query-checked is unavailable")
if "catalyst.query.v1" not in profile.get("outputContracts", []):
    raise SystemExit("catalyst.query.v1 is not advertised")
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
  curl -fsS "http://localhost:${CATALYST_UI_PORT:-3000}/health" >/dev/null
}

wait_for "OpenELIS database" check_openelis_db
wait_for "OpenELIS application" check_openelis_app
wait_for "HAPI seed resources" check_hapi_seed
wait_for "FHIR Data Pipes controller" check_data_pipes
wait_for "analytics mart exact rows" check_mart
wait_for "model router" check_router
wait_for "hub query profile" check_hub_profile
wait_for "Catalyst gateway" check_gateway
wait_for "Catalyst UI" check_ui

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
hub_commit="$(git -C "${ROOT_DIR}/.med-agent-hub" rev-parse HEAD)"
mkdir -p "${ROOT_DIR}/logs"
PIPELINE_JSON="${pipeline_json}" \
PROVENANCE_PATH="${ROOT_DIR}/logs/mvp-provenance.json" \
OPENELIS_VERSION="${OPENELIS_VERSION:-unknown}" \
DATA_PIPES_COMMIT="${PINNED_COMMIT}" \
HUB_COMMIT="${hub_commit}" \
ROUTER_MODE="${router_mode}" \
MODEL_REPO="${MVP_MODEL_REPO:-bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF}" \
MODEL_FILE="${MVP_MODEL_FILE:-Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf}" \
python3 - <<'PY'
import datetime
import json
import os
from pathlib import Path

payload = {
    "contractVersion": "catalyst.mvp.provenance.v1",
    "generatedAt": datetime.datetime.now(datetime.timezone.utc)
    .isoformat()
    .replace("+00:00", "Z"),
    "openelisVersion": os.environ["OPENELIS_VERSION"],
    "fhirDataPipes": {"commit": os.environ["DATA_PIPES_COMMIT"], "spark": False},
    "medAgentHub": {
        "commit": os.environ["HUB_COMMIT"],
        "patch": "patches/med-agent-hub/catalyst-query-profile.patch",
    },
    "modelRouter": {
        "mode": os.environ["ROUTER_MODE"],
        "alias": "qwen2.5-coder-14b",
        "repository": os.environ["MODEL_REPO"],
        "file": os.environ["MODEL_FILE"],
    },
    "catalog": {
        "contractVersion": "catalyst.analytics.catalog.v1",
        "catalogVersion": "analytics-catalog-v1",
    },
    "pipelineRun": json.loads(os.environ["PIPELINE_JSON"]),
}
path = Path(os.environ["PROVENANCE_PATH"])
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2))
PY

echo "MVP health gates passed; provenance: logs/mvp-provenance.json"
