#!/usr/bin/env bash
# Seed OpenELIS, backfill HAPI, run Data Pipes, and publish run provenance.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
COMPOSE_FILE="${OPENELIS_COMPOSE_FILE:-${ROOT_DIR}/docker-compose.mvp.yml}"
DB_SERVICE="${OPENELIS_DB_SERVICE:-db.openelis.org}"
PINNED_COMMIT="3ea890884d674e2f31257a2da421601f2d75b5e9"
CURL_CONNECT_TIMEOUT_SECONDS="${MVP_CURL_CONNECT_TIMEOUT_SECONDS:-5}"
CURL_MAX_TIME_SECONDS="${MVP_CURL_MAX_TIME_SECONDS:-15}"
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

if [ "${MVP_RESOLVE_MODEL_CONFIG_ONLY:-false}" = "true" ]; then
  exec "${ROOT_DIR}/scripts/mvp-health.sh"
fi

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
compose_override_file="${MVP_COMPOSE_OVERRIDE_FILE:-}"
if [ -n "${compose_override_file}" ]; then
  if [ ! -f "${compose_override_file}" ]; then
    echo "ERROR: compose override file does not exist: ${compose_override_file}" >&2
    exit 1
  fi
  compose+=(-f "${compose_override_file}")
fi

wait_for_url() {
  local name="$1"
  local url="$2"
  local attempts="${3:-120}"
  local attempt
  for attempt in $(seq 1 "${attempts}"); do
    if curl -kfsS \
      --connect-timeout "${CURL_CONNECT_TIMEOUT_SECONDS}" \
      --max-time "${CURL_MAX_TIME_SECONDS}" \
      "${url}" >/dev/null 2>&1; then
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
    --connect-timeout "${CURL_CONNECT_TIMEOUT_SECONDS}" \
    --max-time "${CURL_MAX_TIME_SECONDS}" \
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

"${compose[@]}" exec -T \
  -e "PGPASSWORD=${OE_DB_PASSWORD}" \
  "${DB_SERVICE}" \
  psql --username clinlims --dbname clinlims \
    --set=ON_ERROR_STOP=1 \
  < "${ROOT_DIR}/analytics/openelis/seed-catalyst-cohort-v1.sql"

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

"${compose[@]}" exec -T \
  -e "PGPASSWORD=${OE_DB_PASSWORD}" \
  "${DB_SERVICE}" \
  psql --username clinlims --dbname clinlims --no-psqlrc \
    --tuples-only --no-align --field-separator=$'\t' \
    --command="
      SELECT sample.accession_number,
             round(EXTRACT(EPOCH FROM (
               analysis.completed_date - sample.received_date
             )) / 60)::integer
      FROM clinlims.sample AS sample
      JOIN clinlims.sample_item AS sample_item ON sample_item.samp_id = sample.id
      JOIN clinlims.analysis AS analysis ON analysis.sampitem_id = sample_item.id
      WHERE sample.accession_number LIKE 'CAT%'
      ORDER BY sample.accession_number;
    " |
  python3 "${ROOT_DIR}/analytics/openelis/normalize-catalyst-specimen-times.py" \
    --fhir-url "${HAPI_FHIR_URL:-https://localhost:${HAPI_HTTPS_PORT:-8444}/fhir}" \
    --client-cert "${HAPI_CLIENT_CERT:-${hapi_client_pem}}" \
    --turnaround-map - \
    --expected 1152 \
    --insecure

wait_for_url "FHIR Data Pipes controller" \
  "http://localhost:${DATA_PIPES_PORT:-8090}/actuator/health"

run_id="full-$(date -u +%Y%m%dT%H%M%SZ)"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
run_response="$(
  curl -fsS -X POST \
    --connect-timeout "${CURL_CONNECT_TIMEOUT_SECONDS}" \
    --max-time "${MVP_DATA_PIPES_REQUEST_TIMEOUT_SECONDS:-30}" \
    "http://localhost:${DATA_PIPES_PORT:-8090}/run?runMode=FULL"
)"
if [ "${run_response}" != "SUCCESS" ]; then
  echo "ERROR: Data Pipes rejected FULL run: ${run_response}" >&2
  exit 1
fi

for attempt in $(seq 1 "${DATA_PIPES_RUN_ATTEMPTS:-180}"); do
  status="$(
    curl -fsS \
      --connect-timeout "${CURL_CONNECT_TIMEOUT_SECONDS}" \
      --max-time "${CURL_MAX_TIME_SECONDS}" \
      "http://localhost:${DATA_PIPES_PORT:-8090}/status" |
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

# The controller writes Parquet and registers its tables and views into the
# thriftserver. Reading back through Spark is what proves the warehouse
# materialized -- the controller's own success message does not.
registered_views="$(
  "${compose[@]}" exec -T spark-thriftserver \
    beeline -u 'jdbc:hive2://localhost:10000' \
    --silent=true --outputformat=tsv2 -e 'SHOW VIEWS;' 2>/dev/null | tail -n +2 | wc -l | tr -d '[:space:]'
)"
if [ "${registered_views}" -lt 1 ]; then
  echo "ERROR: the pipeline registered no views into the Spark thriftserver." >&2
  echo "Check that the controller and the thriftserver mount the warehouse" >&2
  echo "volume at the same path." >&2
  exit 1
fi
echo "Spark registered ${registered_views} view(s) from the warehouse."

"${ROOT_DIR}/scripts/mvp-health.sh"
