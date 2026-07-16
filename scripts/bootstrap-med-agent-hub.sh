#!/bin/bash
# Fetch pinned med-agent-hub and apply the Catalyst query-profile patch.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${ROOT_DIR}/.med-agent-hub"
HUB_REPO="${MED_AGENT_HUB_REPO:-https://github.com/pmanko/med-agent-hub.git}"
HUB_REF="${MED_AGENT_HUB_REF:-7869c629cccfa45731ce580d5c5f44d541279920}"
HUB_PATCH="${ROOT_DIR}/patches/med-agent-hub/catalyst-query-profile.patch"

if [ -d "${TARGET_DIR}/.git" ]; then
  echo "med-agent-hub checkout already exists at ${TARGET_DIR}"
else
  echo "Cloning ${HUB_REPO} into ${TARGET_DIR}"
  git clone --filter=blob:none --no-checkout "${HUB_REPO}" "${TARGET_DIR}"
fi

(
  cd "${TARGET_DIR}"
  git fetch --depth 1 origin "${HUB_REF}"
  git checkout --detach FETCH_HEAD

  if git apply --reverse --check "${HUB_PATCH}" >/dev/null 2>&1; then
    echo "Catalyst query-profile patch already applied"
  else
    git apply --check "${HUB_PATCH}"
    git apply "${HUB_PATCH}"
    echo "Applied Catalyst query-profile patch"
  fi
)

echo "med-agent-hub bootstrap complete: ${TARGET_DIR}"
