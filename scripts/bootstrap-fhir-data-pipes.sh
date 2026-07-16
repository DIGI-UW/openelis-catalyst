#!/usr/bin/env bash
# Fetch the exact FHIR Data Pipes revision used by the analytics contract.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${ROOT_DIR}/.fhir-data-pipes"
FHIR_DATA_PIPES_REPO="${FHIR_DATA_PIPES_REPO:-https://github.com/google/fhir-data-pipes.git}"
FHIR_DATA_PIPES_COMMIT="3ea890884d674e2f31257a2da421601f2d75b5e9"

if [ -e "${TARGET_DIR}" ] && [ ! -d "${TARGET_DIR}/.git" ]; then
  echo "ERROR: ${TARGET_DIR} exists but is not a Git checkout" >&2
  exit 1
fi

if [ ! -d "${TARGET_DIR}/.git" ]; then
  mkdir -p "${TARGET_DIR}"
  (
    cd "${TARGET_DIR}"
    git init
    git remote add origin "${FHIR_DATA_PIPES_REPO}"
  )
fi

(
  cd "${TARGET_DIR}"

  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: ${TARGET_DIR} has tracked local changes; refusing to replace them" >&2
    exit 1
  fi

  git fetch --depth 1 origin "${FHIR_DATA_PIPES_COMMIT}"
  git checkout --detach "${FHIR_DATA_PIPES_COMMIT}"

  actual_commit="$(git rev-parse HEAD)"
  if [ "${actual_commit}" != "${FHIR_DATA_PIPES_COMMIT}" ]; then
    echo "ERROR: expected ${FHIR_DATA_PIPES_COMMIT}, got ${actual_commit}" >&2
    exit 1
  fi
)

echo "FHIR Data Pipes ready at ${FHIR_DATA_PIPES_COMMIT}"
