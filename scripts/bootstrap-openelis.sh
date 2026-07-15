#!/bin/bash
# Fetch the OpenELIS Docker Compose deployment (configs + compose) into .openelis-docker/.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${ROOT_DIR}/.openelis-docker"
OPENELIS_DOCKER_REPO="${OPENELIS_DOCKER_REPO:-https://github.com/DIGI-UW/openelis-docker.git}"
OPENELIS_DOCKER_REF="${OPENELIS_DOCKER_REF:-main}"

if [ -d "${TARGET_DIR}/.git" ]; then
  echo "OpenELIS docker checkout already exists at ${TARGET_DIR}"
  (
    cd "${TARGET_DIR}"
    git fetch --depth 1 origin "${OPENELIS_DOCKER_REF}"
    git checkout "${OPENELIS_DOCKER_REF}"
    git pull --ff-only origin "${OPENELIS_DOCKER_REF}" 2>/dev/null || true
  )
else
  echo "Cloning ${OPENELIS_DOCKER_REPO} (${OPENELIS_DOCKER_REF}) into ${TARGET_DIR}"
  git clone --depth 1 --branch "${OPENELIS_DOCKER_REF}" "${OPENELIS_DOCKER_REPO}" "${TARGET_DIR}"
fi

mkdir -p "${TARGET_DIR}/configs/database/data"

if [ ! -f "${TARGET_DIR}/.env" ]; then
  echo "ERROR: ${TARGET_DIR}/.env missing after clone" >&2
  exit 1
fi

echo "OpenELIS docker bootstrap complete: ${TARGET_DIR}"
