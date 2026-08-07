#!/usr/bin/env bash
# Resolve the one supported manual MVP model path: the external model router.

mvp_resolve_model_config() {
  local backend="${MVP_MODEL_BACKEND:-external}"
  if [ "${backend}" != "external" ]; then
    echo "ERROR: MVP_MODEL_BACKEND must be external." >&2
    return 2
  fi

  export MVP_RESOLVED_MODEL_BACKEND="external"
  export MVP_RESOLVED_ROUTER_URL="${MVP_EXTERNAL_ROUTER_URL:-http://host.docker.internal:1234}"
  export MVP_RESOLVED_PROFILE_ID="${MVP_PROFILE_ID:-${MVP_EXTERNAL_PROFILE_ID:-catalyst-query-e4b-qwen14b}}"
}
