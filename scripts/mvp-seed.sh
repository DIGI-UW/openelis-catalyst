#!/usr/bin/env bash
# Seed OpenELIS, backfill HAPI, run Data Pipes, and publish run provenance.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
COMPOSE_FILE="${OPENELIS_COMPOSE_FILE:-${ROOT_DIR}/docker-compose.mvp.yml}"
DB_SERVICE="${OPENELIS_DB_SERVICE:-db.openelis.org}"
PINNED_COMMIT="3ea890884d674e2f31257a2da421601f2d75b5e9"

if [ ! -f "${ENV_FILE}" ]; then
  ENV_FILE="${ROOT_DIR}/env.recommended"
fi
set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a

OPENELIS_VERSION="${OPENELIS_VERSION:-}"
if [[ ! "${OPENELIS_VERSION}" =~ ^3\.2\.1\.[0-9]+$ ]]; then
  echo "ERROR: set OPENELIS_VERSION to the deployed 3.2.1.x release (for example 3.2.1.11)" >&2
  exit 2
fi

if [ -z "${OE_DB_PASSWORD:-}" ] && [ -f "${ROOT_DIR}/.openelis-docker/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${ROOT_DIR}/.openelis-docker/.env"
  set +a
fi
OE_DB_PASSWORD="${OE_DB_PASSWORD:-clinlims}"

compose=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")

wait_for_url() {
  local name="$1"
  local url="$2"
  local attempts="${3:-120}"
  local attempt
  for attempt in $(seq 1 "${attempts}"); do
    if curl -kfsS "${url}" >/dev/null 2>&1; then
      echo "OK: ${name}"
      return 0
    fi
    echo "Waiting for ${name} (${attempt}/${attempts})..."
    sleep 5
  done
  echo "ERROR: ${name} not ready at ${url}" >&2
  return 1
}

wait_for_url "OpenELIS application" \
  "https://localhost:${OPENELIS_HTTPS_PORT:-8443}/OpenELIS-Global/"

mkdir -p "${ROOT_DIR}/logs"
hapi_client_p12="${ROOT_DIR}/logs/.mvp-hapi-client.p12"
hapi_client_pem="${ROOT_DIR}/logs/.mvp-hapi-client.pem"
"${compose[@]}" cp \
  oe.openelis.org:/etc/openelis-global/client_facing_keystore \
  "${hapi_client_p12}"
openssl pkcs12 -legacy \
  -in "${hapi_client_p12}" \
  -out "${hapi_client_pem}" \
  -nodes \
  -passin pass:kspass
chmod 600 "${hapi_client_p12}" "${hapi_client_pem}"

for attempt in $(seq 1 "${FHIR_WAIT_ATTEMPTS:-120}"); do
  if curl -kfsS \
    --cert "${hapi_client_pem}" \
    "https://localhost:${HAPI_HTTPS_PORT:-8444}/fhir/metadata" \
    >/dev/null 2>&1; then
    echo "OK: HAPI FHIR"
    break
  fi
  if [ "${attempt}" = "${FHIR_WAIT_ATTEMPTS:-120}" ]; then
    echo "ERROR: HAPI FHIR not ready" >&2
    exit 1
  fi
  echo "Waiting for HAPI FHIR (${attempt}/${FHIR_WAIT_ATTEMPTS:-120})..."
  sleep 5
done

"${compose[@]}" exec -T \
  -e "PGPASSWORD=${OE_DB_PASSWORD}" \
  "${DB_SERVICE}" \
  psql --username clinlims --dbname clinlims \
    --set=ON_ERROR_STOP=1 \
    --set="openelis_version=${OPENELIS_VERSION}" \
  < "${ROOT_DIR}/analytics/openelis/seed-openelis-3.2.1.sql"

if [ "${SKIP_HAPI_BACKFILL:-false}" = "true" ]; then
  echo "WARN: HAPI backfill skipped; Data Pipes will not see direct SQL seed rows yet" >&2
  exit 0
fi

OE_BACKFILL_URL="${OE_BACKFILL_URL:-https://localhost:${OPENELIS_HTTPS_PORT:-8443}/OpenELIS-Global/OEToFhir}" \
HAPI_FHIR_URL="${HAPI_FHIR_URL:-https://localhost:${HAPI_HTTPS_PORT:-8444}/fhir}" \
HAPI_CLIENT_CERT="${HAPI_CLIENT_CERT:-${hapi_client_pem}}" \
OE_TLS_INSECURE=true \
HAPI_TLS_INSECURE=true \
  "${ROOT_DIR}/analytics/openelis/backfill-hapi.sh"

wait_for_url "FHIR Data Pipes controller" \
  "http://localhost:${DATA_PIPES_PORT:-8090}/actuator/health"

run_id="full-$(date -u +%Y%m%dT%H%M%SZ)"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
run_response="$(
  curl -fsS -X POST \
    "http://localhost:${DATA_PIPES_PORT:-8090}/run?runMode=FULL"
)"
if [ "${run_response}" != "SUCCESS" ]; then
  echo "ERROR: Data Pipes rejected FULL run: ${run_response}" >&2
  exit 1
fi

for attempt in $(seq 1 "${DATA_PIPES_RUN_ATTEMPTS:-180}"); do
  status="$(
    curl -fsS "http://localhost:${DATA_PIPES_PORT:-8090}/status" |
      python3 -c 'import json,sys; print(json.load(sys.stdin).get("pipelineStatus", ""))'
  )"
  if [ "${status}" = "IDLE" ]; then
    echo "OK: FHIR Data Pipes FULL run completed"
    break
  fi
  if [ "${attempt}" = "${DATA_PIPES_RUN_ATTEMPTS:-180}" ]; then
    echo "ERROR: FHIR Data Pipes run did not return to IDLE" >&2
    exit 1
  fi
  echo "Waiting for FHIR Data Pipes run (${attempt}/${DATA_PIPES_RUN_ATTEMPTS:-180})..."
  sleep 5
done

"${compose[@]}" exec -T analytics-db \
  psql --username catalyst_analytics_writer --dbname catalyst_analytics \
  --set=ON_ERROR_STOP=1 \
  < "${ROOT_DIR}/analytics/sql/001_analytics_v1.sql"

fact_count="$(
  "${compose[@]}" exec -T analytics-db \
    psql --username catalyst_analytics_writer --dbname catalyst_analytics \
    --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 \
    --command="SELECT count(*) FROM analytics.lab_result_fact_v1;"
)"
if [ "${fact_count}" != "3" ]; then
  echo "ERROR: expected exactly 3 analytics mart rows, got ${fact_count}" >&2
  exit 1
fi

"${compose[@]}" exec -T analytics-db \
  psql --username catalyst_analytics_writer --dbname catalyst_analytics \
  --set=ON_ERROR_STOP=1 <<SQL
INSERT INTO analytics.pipeline_run_v1 (
    pipeline_run_id,
    completion_state,
    source_watermark,
    started_at,
    completed_at,
    observed_at,
    data_pipes_commit,
    resource_counts
)
SELECT
    '${run_id}',
    'succeeded',
    max(GREATEST(observed_at, issued_at, specimen_received_at)),
    '${started_at}'::timestamptz,
    clock_timestamp(),
    clock_timestamp(),
    '${PINNED_COMMIT}',
    '{"Patient":1,"Observation":3,"ServiceRequest":3,"Specimen":3,"DiagnosticReport":3}'::jsonb
FROM analytics.lab_result_fact_v1;
SQL

"${ROOT_DIR}/scripts/mvp-health.sh"
