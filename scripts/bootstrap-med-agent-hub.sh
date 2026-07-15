#!/bin/bash
# Fetch med-agent-hub for report-generation workflows (staged SSE / profiles).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${ROOT_DIR}/.med-agent-hub"
HUB_REPO="${MED_AGENT_HUB_REPO:-https://github.com/pmanko/med-agent-hub.git}"
HUB_REF="${MED_AGENT_HUB_REF:-main}"

if [ -d "${TARGET_DIR}/.git" ]; then
  echo "med-agent-hub checkout already exists at ${TARGET_DIR}"
  (
    cd "${TARGET_DIR}"
    git fetch --depth 1 origin "${HUB_REF}"
    git checkout "${HUB_REF}"
    git pull --ff-only origin "${HUB_REF}" 2>/dev/null || true
  )
else
  echo "Cloning ${HUB_REPO} (${HUB_REF}) into ${TARGET_DIR}"
  git clone --depth 1 --branch "${HUB_REF}" "${HUB_REPO}" "${TARGET_DIR}"
fi

echo "med-agent-hub bootstrap complete: ${TARGET_DIR}"
