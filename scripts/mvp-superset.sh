#!/usr/bin/env bash
# Explicit local operator boundary for Catalyst-owned Superset state.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
if [ ! -f "${ENV_FILE}" ]; then
  ENV_FILE="${ROOT_DIR}/env.recommended"
fi

compose=(docker compose --env-file "${ENV_FILE}" -f "${ROOT_DIR}/docker-compose.mvp.yml")
compose_override_file="${MVP_COMPOSE_OVERRIDE_FILE:-}"
if [ -n "${compose_override_file}" ]; then
  if [ ! -f "${compose_override_file}" ]; then
    echo "ERROR: compose override file does not exist: ${compose_override_file}" >&2
    exit 1
  fi
  compose+=(-f "${compose_override_file}")
fi
compose+=(--profile superset-import)

# Import receipts are durable evidence. Record the exact Catalyst source that
# produced them rather than an ambiguous worktree label.
catalyst_revision="$(git -C "${ROOT_DIR}" rev-parse --verify HEAD)"
if ! [[ "${catalyst_revision}" =~ ^[a-f0-9]{40}$ ]]; then
  echo "ERROR: unable to resolve an exact Catalyst revision for Superset import" >&2
  exit 1
fi
export CATALYST_IMPORTER_REVISION="${catalyst_revision}"

usage() {
  echo "Usage: scripts/mvp-superset.sh {status|import|reset}" >&2
}

case "${1:-}" in
  status)
    "${compose[@]}" ps superset superset-metadata-db
    "${compose[@]}" run --rm superset-importer status
    ;;
  import)
    "${compose[@]}" run --rm superset-importer import
    ;;
  reset)
    echo "ERROR: reset requires the validated last-verified importer implementation" >&2
    exit 3
    ;;
  *)
    usage
    exit 2
    ;;
esac
