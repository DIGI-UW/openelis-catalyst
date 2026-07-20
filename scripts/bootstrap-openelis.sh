#!/bin/bash
# Fetch the OpenELIS Docker Compose deployment (configs + compose) into .openelis-docker/.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${ROOT_DIR}/.openelis-docker"
OPENELIS_DOCKER_REPO="${OPENELIS_DOCKER_REPO:-https://github.com/DIGI-UW/openelis-docker.git}"
OPENELIS_DOCKER_REF="${OPENELIS_DOCKER_REF:-f118d0ae778a30028c16be2af549843ec166f655}"

if [ -d "${TARGET_DIR}/.git" ]; then
  echo "OpenELIS docker checkout already exists at ${TARGET_DIR}"
else
  echo "Cloning ${OPENELIS_DOCKER_REPO} (${OPENELIS_DOCKER_REF}) into ${TARGET_DIR}"
  git clone --filter=blob:none --no-checkout "${OPENELIS_DOCKER_REPO}" "${TARGET_DIR}"
fi

git -C "${TARGET_DIR}" fetch --depth 1 origin "${OPENELIS_DOCKER_REF}"
git -C "${TARGET_DIR}" checkout --detach FETCH_HEAD

if [ "$(git -C "${TARGET_DIR}" rev-parse HEAD)" != "${OPENELIS_DOCKER_REF}" ]; then
  echo "ERROR: OpenELIS docker checkout does not match ${OPENELIS_DOCKER_REF}" >&2
  exit 1
fi

mkdir -p "${TARGET_DIR}/configs/database/data"

if [ ! -f "${TARGET_DIR}/.env" ]; then
  echo "ERROR: ${TARGET_DIR}/.env missing after clone" >&2
  exit 1
fi

echo "OpenELIS docker bootstrap complete: ${TARGET_DIR}"
