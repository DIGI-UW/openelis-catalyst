#!/usr/bin/env bash
# Prove an OpenELIS result update reaches the analytics mart incrementally.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.mvp.yml"
OBSERVATION_ID="53333333-3333-4333-8333-333333333333"
ORIGINAL_VALUE="80"
UPDATED_VALUE="81"

if [ ! -f "${ENV_FILE}" ]; then
  ENV_FILE="${ROOT_DIR}/env.recommended"
fi
set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a

compose=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")
hapi_client_p12="${ROOT_DIR}/logs/.mvp-hapi-client.p12"
hapi_client_pem="${ROOT_DIR}/logs/.mvp-hapi-client.pem"

if [ ! -f "${hapi_client_pem}" ]; then
  mkdir -p "${ROOT_DIR}/logs"
  "${compose[@]}" cp \
    oe.openelis.org:/etc/openelis-global/client_facing_keystore \
    "${hapi_client_p12}" >/dev/null
  openssl pkcs12 -legacy \
    -in "${hapi_client_p12}" \
    -out "${hapi_client_pem}" \
    -nodes \
    -passin pass:kspass
  chmod 600 "${hapi_client_p12}" "${hapi_client_pem}"
fi

openelis_psql() {
  "${compose[@]}" exec -T db.openelis.org \
    psql --username clinlims --dbname clinlims \
    --no-psqlrc --set=ON_ERROR_STOP=1 "$@"
}

analytics_value() {
  "${compose[@]}" exec -T analytics-db \
    psql --username catalyst_analytics_writer --dbname catalyst_analytics \
    --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 \
    --command="
      SELECT result_value::numeric::text
      FROM analytics.lab_result_fact_v1
      WHERE observation_id = '${OBSERVATION_ID}';
    "
}

set_openelis_value() {
  local value="$1"
  openelis_psql --command="
    UPDATE clinlims.result
    SET value = '${value}', lastupdated = clock_timestamp()
    WHERE fhir_uuid = '${OBSERVATION_ID}'::uuid;
  " >/dev/null
}

backfill_hapi() {
  OE_BACKFILL_URL="https://localhost:${OPENELIS_HTTPS_PORT:-8443}/OpenELIS-Global/OEToFhir" \
  HAPI_FHIR_URL="https://localhost:${HAPI_HTTPS_PORT:-8444}/fhir" \
  HAPI_CLIENT_CERT="${hapi_client_pem}" \
  OE_TLS_INSECURE=true \
  HAPI_TLS_INSECURE=true \
    "${ROOT_DIR}/analytics/openelis/backfill-hapi.sh" >/dev/null
}

wait_for_hapi_value() {
  local expected="$1"
  local attempt actual
  for attempt in $(seq 1 120); do
    actual="$(
      curl -kfsS --cert "${hapi_client_pem}" \
        "https://localhost:${HAPI_HTTPS_PORT:-8444}/fhir/Observation/${OBSERVATION_ID}" |
        python3 -c '
import json
import sys
payload = json.load(sys.stdin)
print(payload.get("valueQuantity", {}).get("value", ""))
'
    )"
    if [ "${actual}" = "${expected}" ]; then
      return 0
    fi
    sleep 2
  done
  echo "ERROR: HAPI Observation value did not become ${expected}" >&2
  return 1
}

run_incremental() {
  local response status attempt
  response="$(
    curl -fsS -X POST \
      "http://localhost:${DATA_PIPES_PORT:-8090}/run?runMode=INCREMENTAL"
  )"
  if [ "${response}" != "SUCCESS" ]; then
    echo "ERROR: Data Pipes rejected INCREMENTAL run: ${response}" >&2
    return 1
  fi
  for attempt in $(seq 1 180); do
    status="$(
      curl -fsS "http://localhost:${DATA_PIPES_PORT:-8090}/status" |
        python3 -c 'import json,sys; print(json.load(sys.stdin).get("pipelineStatus", ""))'
    )"
    if [ "${status}" = "IDLE" ]; then
      return 0
    fi
    sleep 2
  done
  echo "ERROR: Data Pipes incremental run did not return to IDLE" >&2
  return 1
}

restore_original() {
  set_openelis_value "${ORIGINAL_VALUE}"
  backfill_hapi
  wait_for_hapi_value "${ORIGINAL_VALUE}"
  run_incremental
}
trap restore_original EXIT

set_openelis_value "${UPDATED_VALUE}"
backfill_hapi
wait_for_hapi_value "${UPDATED_VALUE}"
run_incremental

if [ "$(analytics_value)" != "${UPDATED_VALUE}" ]; then
  echo "ERROR: incremental analytics value was not ${UPDATED_VALUE}" >&2
  exit 1
fi
if [ "$(
  "${compose[@]}" exec -T analytics-db \
    psql --username catalyst_analytics_writer --dbname catalyst_analytics \
    --no-psqlrc --tuples-only --no-align \
    --command="SELECT count(*) FROM analytics.lab_result_fact_v1;"
)" != "3" ]; then
  echo "ERROR: incremental run duplicated analytics rows" >&2
  exit 1
fi

echo "PASS: Data Pipes incremental update changed 80 to 81 without duplicates"
