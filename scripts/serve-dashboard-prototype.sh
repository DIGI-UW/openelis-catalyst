#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
prototype_dir="${repo_root}/docs/prototypes/dashboard-builder-mvp"
port="${CATALYST_DASHBOARD_PROTOTYPE_PORT:-18443}"

if [[ ! -d "${prototype_dir}" ]]; then
  printf 'Dashboard prototype directory not found: %s\n' "${prototype_dir}" >&2
  exit 1
fi

printf 'Serving Dashboard Builder prototypes at http://127.0.0.1:%s/\n' "${port}"
exec python3 -m http.server "${port}" --bind 127.0.0.1 --directory "${prototype_dir}"
