#!/usr/bin/env bash
# Load the deterministic Catalyst demo fixture, then backfill it to HAPI.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${OPENELIS_COMPOSE_FILE:-${ROOT_DIR}/docker-compose.full-stack.yml}"
DB_SERVICE="${OPENELIS_DB_SERVICE:-db.openelis.org}"
OPENELIS_VERSION="${OPENELIS_VERSION:-}"

if [[ ! "${OPENELIS_VERSION}" =~ ^3\.2\.1\.[0-9]+$ ]]; then
  echo "ERROR: set OPENELIS_VERSION to the deployed 3.2.1.x release (for example 3.2.1.11)" >&2
  exit 2
fi

if [ -f "${ROOT_DIR}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${ROOT_DIR}/.env"
  set +a
fi

if [ -z "${OE_DB_PASSWORD:-}" ] && [ -f "${ROOT_DIR}/.openelis-docker/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${ROOT_DIR}/.openelis-docker/.env"
  set +a
fi

OE_DB_PASSWORD="${OE_DB_PASSWORD:-clinlims}"

docker compose -f "${COMPOSE_FILE}" exec -T \
  -e "PGPASSWORD=${OE_DB_PASSWORD}" \
  "${DB_SERVICE}" \
  psql --username clinlims --dbname clinlims \
    --set="openelis_version=${OPENELIS_VERSION}" \
  < "${ROOT_DIR}/analytics/openelis/seed-openelis-3.2.1.sql"

if [ "${SKIP_HAPI_BACKFILL:-false}" = "true" ]; then
  echo "WARN: HAPI backfill skipped; Data Pipes will not see direct SQL seed rows yet" >&2
  exit 0
fi

"${ROOT_DIR}/analytics/openelis/backfill-hapi.sh"
