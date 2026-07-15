#!/bin/bash
# Bootstrap external compose dependencies (OpenELIS docker + med-agent-hub).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"${ROOT_DIR}/scripts/bootstrap-openelis.sh"
"${ROOT_DIR}/scripts/bootstrap-med-agent-hub.sh"
