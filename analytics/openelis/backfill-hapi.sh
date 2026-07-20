#!/usr/bin/env bash
# Trigger the OpenELIS 3.2.1.x legacy-data transform and wait for fixed seed IDs.
set -euo pipefail

OE_BACKFILL_URL="${OE_BACKFILL_URL:-https://localhost:8443/OpenELIS-Global/OEToFhir}"
HAPI_FHIR_URL="${HAPI_FHIR_URL:-https://localhost:8444/fhir}"
OE_USERNAME="${OE_USERNAME:-admin}"
OE_PASSWORD="${OE_PASSWORD:-adminADMIN!}"
FHIR_WAIT_ATTEMPTS="${FHIR_WAIT_ATTEMPTS:-60}"
FHIR_WAIT_SECONDS="${FHIR_WAIT_SECONDS:-2}"
CURL_CONNECT_TIMEOUT_SECONDS="${MVP_CURL_CONNECT_TIMEOUT_SECONDS:-5}"

oe_curl=(
  curl -fsS
  --connect-timeout "${CURL_CONNECT_TIMEOUT_SECONDS}"
  --max-time "${OE_BACKFILL_TIMEOUT_SECONDS:-600}"
)
hapi_curl=(
  curl -fsS
  --connect-timeout "${CURL_CONNECT_TIMEOUT_SECONDS}"
  --max-time "${MVP_CURL_MAX_TIME_SECONDS:-15}"
)

if [ "${OE_TLS_INSECURE:-true}" = "true" ]; then
  oe_curl+=(-k)
fi
if [ -n "${OE_USERNAME}" ]; then
  oe_curl+=(-u "${OE_USERNAME}:${OE_PASSWORD}")
fi
if [ -n "${HAPI_CLIENT_CERT:-}" ]; then
  hapi_curl+=(--cert "${HAPI_CLIENT_CERT}")
fi
if [ -n "${HAPI_CLIENT_KEY:-}" ]; then
  hapi_curl+=(--key "${HAPI_CLIENT_KEY}")
fi
if [ -n "${HAPI_CLIENT_P12:-}" ]; then
  hapi_curl+=(
    --cert-type P12
    --cert "${HAPI_CLIENT_P12}:${HAPI_CLIENT_P12_PASSWORD:-kspass}"
  )
fi
if [ "${HAPI_TLS_INSECURE:-true}" = "true" ]; then
  hapi_curl+=(-k)
fi

backfill_response="$(
  "${oe_curl[@]}" \
    "${OE_BACKFILL_URL}?checkAll=true&batchSize=10&threads=1&waitForResults=true"
)"

BACKFILL_RESPONSE="${backfill_response}" python3 - <<'PY'
import json
import os

raw = os.environ["BACKFILL_RESPONSE"].strip()
if not raw:
    print("OpenELIS backfill returned an empty success response; checking HAPI state")
    raise SystemExit(0)
payload = json.loads(raw)
if payload.get("running") is not False:
    raise SystemExit(f"OpenELIS backfill did not finish: {payload}")
if payload.get("phase") != "Finished":
    raise SystemExit(f"OpenELIS backfill ended in an unexpected phase: {payload}")
if payload.get("batchFailure", 0) != 0:
    raise SystemExit(f"OpenELIS backfill reported failed batches: {payload}")
PY

wait_for_resource() {
  local resource="$1"
  local expected="$2"
  local ids="$3"
  local attempt response total

  for attempt in $(seq 1 "${FHIR_WAIT_ATTEMPTS}"); do
    if response="$(
      "${hapi_curl[@]}" \
        "${HAPI_FHIR_URL}/${resource}?_summary=count&_id=${ids}" 2>/dev/null
    )"; then
      if total="$(
        FHIR_RESPONSE="${response}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["FHIR_RESPONSE"])
print(payload.get("total", 0))
PY
      )" && [ "${total}" -ge "${expected}" ]; then
        echo "OK: ${resource} seed resources visible (${total}/${expected})"
        return 0
      fi
    fi
    echo "Waiting for ${resource} seed resources (${attempt}/${FHIR_WAIT_ATTEMPTS})..."
    sleep "${FHIR_WAIT_SECONDS}"
  done

  echo "ERROR: ${resource} seed resources did not become visible in HAPI" >&2
  return 1
}

wait_for_resource "Patient" 1 \
  "11111111-1111-4111-8111-111111111111"
wait_for_resource "Observation" 3 \
  "51111111-1111-4111-8111-111111111111,52222222-2222-4222-8222-222222222222,53333333-3333-4333-8333-333333333333"
wait_for_resource "ServiceRequest" 3 \
  "41111111-1111-4111-8111-111111111111,42222222-2222-4222-8222-222222222222,43333333-3333-4333-8333-333333333333"
wait_for_resource "Specimen" 3 \
  "31111111-1111-4111-8111-111111111111,32222222-2222-4222-8222-222222222222,33333333-3333-4333-8333-333333333333"
wait_for_resource "DiagnosticReport" 3 \
  "41111111-1111-4111-8111-111111111111,42222222-2222-4222-8222-222222222222,43333333-3333-4333-8333-333333333333"

echo "OpenELIS-to-HAPI seed backfill complete"
