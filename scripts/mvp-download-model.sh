#!/usr/bin/env bash
# Download and verify the small local coder used by the live MVP demo.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_REPO="${MVP_MODEL_REPO:-bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF}"
MODEL_FILE="${MVP_MODEL_FILE:-Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf}"
MODEL_SHA256="${MVP_MODEL_SHA256:-f530705d447660a4336c329981af164b471b60b974b1d808d57e8ec9fe23b239}"
MODEL_DIR="${MVP_MODEL_DIR:-${ROOT_DIR}/.models}"
MODEL_PATH="${MODEL_DIR}/${MODEL_FILE}"
MODEL_URL="${MVP_MODEL_URL:-https://huggingface.co/${MODEL_REPO}/resolve/main/${MODEL_FILE}}"

mkdir -p "${MODEL_DIR}"

verify_model() {
  local actual_sha256
  actual_sha256="$(sha256sum "${MODEL_PATH}" | awk '{print $1}')"
  [ "${actual_sha256}" = "${MODEL_SHA256}" ]
}

if [ -f "${MODEL_PATH}" ] && verify_model; then
  echo "Local model already verified: ${MODEL_PATH}"
  exit 0
fi

partial="${MODEL_PATH}.partial"
rm -f "${partial}"
echo "Downloading ${MODEL_REPO}/${MODEL_FILE} ..."
curl --fail --location --retry 4 --retry-all-errors \
  --output "${partial}" "${MODEL_URL}"
mv "${partial}" "${MODEL_PATH}"

if ! verify_model; then
  rm -f "${MODEL_PATH}"
  echo "ERROR: local model checksum mismatch" >&2
  exit 1
fi

echo "Local model verified: ${MODEL_PATH}"
