#!/bin/bash
# Fetch the pinned, unmodified med-agent-hub fallback for standalone Catalyst.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${ROOT_DIR}/.med-agent-hub"
HUB_REPO="${MED_AGENT_HUB_REPO:-https://github.com/pmanko/med-agent-hub.git}"
HUB_REF="${MED_AGENT_HUB_REF:-50ab0fd40dc5f6d66c66004dcab92024ef41c41d}"

if [ -d "${TARGET_DIR}/.git" ]; then
  echo "med-agent-hub checkout already exists at ${TARGET_DIR}"
else
  echo "Cloning ${HUB_REPO} into ${TARGET_DIR}"
  git clone --filter=blob:none --no-checkout "${HUB_REPO}" "${TARGET_DIR}"
fi

(
  cd "${TARGET_DIR}"
  if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: ${TARGET_DIR} has local changes; refusing to use a modified Hub fallback." >&2
    echo "Remove the disposable checkout and run this script again." >&2
    exit 1
  fi
  git fetch --depth 1 origin "${HUB_REF}"
  git checkout --detach FETCH_HEAD
)

echo "Unmodified med-agent-hub fallback ready at ${TARGET_DIR} (${HUB_REF})"
