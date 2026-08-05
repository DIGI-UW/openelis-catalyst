#!/usr/bin/env bash
# Idempotently initialize the pinned local Superset metadata database.
set -euo pipefail

: "${SUPERSET_ADMIN_USERNAME:?SUPERSET_ADMIN_USERNAME is required}"
: "${SUPERSET_ADMIN_PASSWORD:?SUPERSET_ADMIN_PASSWORD is required}"
: "${SUPERSET_ADMIN_EMAIL:?SUPERSET_ADMIN_EMAIL is required}"

superset db upgrade

if ! superset fab create-admin \
  --username "${SUPERSET_ADMIN_USERNAME}" \
  --firstname Catalyst \
  --lastname Admin \
  --email "${SUPERSET_ADMIN_EMAIL}" \
  --password "${SUPERSET_ADMIN_PASSWORD}"; then
  superset fab reset-password \
    --username "${SUPERSET_ADMIN_USERNAME}" \
    --password "${SUPERSET_ADMIN_PASSWORD}"
fi

superset init
