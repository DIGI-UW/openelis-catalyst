#!/usr/bin/env bash
# Verify the pinned pipeline, HAPI seed, semantic fact, and freshness contract.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PINNED_COMMIT="3ea890884d674e2f31257a2da421601f2d75b5e9"
DATA_PIPES_CONTROLLER_URL="${DATA_PIPES_CONTROLLER_URL:-http://localhost:8090}"
HAPI_FHIR_URL="${HAPI_FHIR_URL:-http://localhost:8081/fhir}"
ANALYTICS_DATABASE_URL="${ANALYTICS_DATABASE_URL:-}"

if [ -z "${ANALYTICS_DATABASE_URL}" ]; then
  echo "ERROR: ANALYTICS_DATABASE_URL is required" >&2
  exit 2
fi

if [ ! -d "${ROOT_DIR}/.fhir-data-pipes/.git" ]; then
  echo "ERROR: run scripts/bootstrap-fhir-data-pipes.sh first" >&2
  exit 1
fi

actual_commit="$(git -C "${ROOT_DIR}/.fhir-data-pipes" rev-parse HEAD)"
if [ "${actual_commit}" != "${PINNED_COMMIT}" ]; then
  echo "ERROR: FHIR Data Pipes is ${actual_commit}, expected ${PINNED_COMMIT}" >&2
  exit 1
fi
echo "OK: FHIR Data Pipes commit ${PINNED_COMMIT}"

curl -fsS "${DATA_PIPES_CONTROLLER_URL}/actuator/health" >/dev/null
echo "OK: FHIR Data Pipes controller health"

hapi_response="$(
  curl -fsS \
    "${HAPI_FHIR_URL}/Observation?_summary=count&_id=51111111-1111-4111-8111-111111111111,52222222-2222-4222-8222-222222222222,53333333-3333-4333-8333-333333333333"
)"
HAPI_RESPONSE="${hapi_response}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["HAPI_RESPONSE"])
if payload.get("total") != 3:
    raise SystemExit(f"Expected 3 fixed seed Observations, got {payload.get('total')}")
PY
echo "OK: three fixed HAPI Observations"

fact_summary="$(
  psql "${ANALYTICS_DATABASE_URL}" --no-psqlrc --tuples-only --no-align \
    --set=ON_ERROR_STOP=1 <<'SQL'
SELECT
    count(*)::text || '|' ||
    count(DISTINCT patient_id)::text || '|' ||
    string_agg(result_value::numeric::text, ',' ORDER BY result_value::numeric)
FROM analytics.lab_result_fact_v1
WHERE observation_id IN (
    '51111111-1111-4111-8111-111111111111',
    '52222222-2222-4222-8222-222222222222',
    '53333333-3333-4333-8333-333333333333'
);
SQL
)"
if [ "${fact_summary}" != "3|1|80,450,1200" ]; then
  echo "ERROR: unexpected analytics seed summary: ${fact_summary}" >&2
  exit 1
fi
echo "OK: semantic fact has one patient and fixed values 80,450,1200"

freshness_ok="$(
  psql "${ANALYTICS_DATABASE_URL}" --no-psqlrc --tuples-only --no-align \
    --set=ON_ERROR_STOP=1 <<SQL
SELECT (
    completion_state = 'succeeded'
    AND source_watermark IS NOT NULL
    AND completed_at IS NOT NULL
    AND data_pipes_commit = '${PINNED_COMMIT}'
)::text
FROM analytics.pipeline_run_v1
ORDER BY completed_at DESC NULLS LAST
LIMIT 1;
SQL
)"
if [ "${freshness_ok}" != "t" ]; then
  echo "ERROR: no current succeeded pipeline-run metadata for the pinned commit" >&2
  exit 1
fi
echo "OK: structured freshness/run metadata"
