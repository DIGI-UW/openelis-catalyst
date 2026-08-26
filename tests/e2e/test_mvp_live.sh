#!/usr/bin/env bash
# Exercise the deployed MVP through the real local model and seeded analytics.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"
QUESTION="${PLAYWRIGHT_QUERY:-Show viral load results since 2026-01-01 with value, unit, release date, and receipt-to-release time}"
PROFILE_ID="${MVP_PROFILE_ID:-${MVP_EXTERNAL_PROFILE_ID:-catalyst-query-gemma-e4b}}"
LOG_DIR="${ROOT_DIR}/logs"

mkdir -p "${LOG_DIR}"

question_payload="$(
  QUESTION="${QUESTION}" PROFILE_ID="${PROFILE_ID}" python3 - <<'PY'
import json
import os

print(json.dumps({
    "contractVersion": "catalyst.question.request.v1",
    "deploymentMode": "demo",
    "question": os.environ["QUESTION"],
    "profileId": os.environ["PROFILE_ID"],
}))
PY
)"

runtime_catalog="$(
  curl -fsS --max-time 30 \
    "${GATEWAY_URL}/v1/catalyst/workbench/catalog"
)"
printf '%s\n' "${runtime_catalog}" > "${LOG_DIR}/mvp-live-catalog.json"

preview="$(
  curl -fsS --max-time 200 \
    "${GATEWAY_URL}/v1/catalyst/queries" \
    -H 'Content-Type: application/json' \
    --data "${question_payload}"
)"
printf '%s\n' "${preview}" > "${LOG_DIR}/mvp-live-preview.json"

mapfile -t preview_meta < <(
  PREVIEW_JSON="${preview}" \
  RUNTIME_CATALOG_JSON="${runtime_catalog}" \
  python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["PREVIEW_JSON"])
runtime_catalog = json.loads(os.environ["RUNTIME_CATALOG_JSON"])
readable_relations = sorted(
    view["qualifiedName"]
    for schema in runtime_catalog["schemas"]
    for view in schema["views"]
)
assert payload["contractVersion"] == "catalyst.preview.v1", payload
assert payload["deploymentMode"] == "demo", payload
assert sorted(payload["target"]["approvedViews"]) == readable_relations, payload
assert any(
    relation != "analytics.lab_result_fact_v1"
    for relation in readable_relations
), runtime_catalog
assert payload["parameters"] == [{
    "name": "date_1",
    "type": "date",
    "source": "question",
    "value": "2026-01-01",
}], payload
assert ":date_1" in payload["sql"], payload
print(payload["previewId"])
print(payload["queryDigest"])
PY
)

preview_id="${preview_meta[0]}"
query_digest="${preview_meta[1]}"
idempotency_key="mvp-live-$(date +%s%N)"

execute_payload="$(
  PREVIEW_ID="${preview_id}" \
  QUERY_DIGEST="${query_digest}" \
  IDEMPOTENCY_KEY="${idempotency_key}" \
  python3 - <<'PY'
import json
import os

print(json.dumps({
    "contractVersion": "catalyst.execute.request.v1",
    "previewId": os.environ["PREVIEW_ID"],
    "queryDigest": os.environ["QUERY_DIGEST"],
    "accept": True,
    "idempotencyKey": os.environ["IDEMPOTENCY_KEY"],
}))
PY
)"

table="$(
  curl -fsS --max-time 60 \
    "${GATEWAY_URL}/v1/catalyst/previews/${preview_id}/execute" \
    -H 'Content-Type: application/json' \
    --data "${execute_payload}"
)"
printf '%s\n' "${table}" > "${LOG_DIR}/mvp-live-table.json"

TABLE_JSON="${table}" EXPECTED_PROFILE_ID="${PROFILE_ID}" python3 - <<'PY'
import json
import os
from decimal import Decimal

payload = json.loads(os.environ["TABLE_JSON"])
assert payload["contractVersion"] == "catalyst.table.v1", payload
assert payload["deploymentMode"] == "demo", payload
assert payload["table"]["rowCount"]["returned"] == 3, payload

columns = [column["name"] for column in payload["table"]["columns"]]
value_index = columns.index("result_value")
unit_index = columns.index("result_unit")
tat_index = columns.index("receipt_to_release_minutes")
values = sorted(
    Decimal(row[value_index]["value"])
    for row in payload["table"]["rows"]
)
assert values == [Decimal("80"), Decimal("450"), Decimal("1200")], values
assert {
    row[unit_index]["value"] for row in payload["table"]["rows"]
} == {"copies/ml"}
assert {
    Decimal(row[tat_index]["value"]) for row in payload["table"]["rows"]
} == {Decimal("60")}
assert payload["source"]["freshness"]["completionState"] == "complete", payload
assert payload["provenance"]["profileId"] == os.environ["EXPECTED_PROFILE_ID"], payload
assert payload["provenance"]["catalystTraceId"], payload
assert payload["provenance"]["hubTraceId"], payload
print(
    "PASS: live local LLM returned 3 seeded viral-load rows "
    "through Catalyst query-to-table"
)
PY
