#!/usr/bin/env bash
# Resolve the selected MVP model backend to one profile, router, and role map.

mvp_resolve_model_config() {
  local backend="${MVP_MODEL_BACKEND:-external}"
  local router_url
  local model_id
  local profile_id
  local mode_role_models_json

  case "${backend}" in
    fake)
      router_url="${MVP_FAKE_ROUTER_URL:-http://model-router-fake:8077}"
      model_id="${MVP_EXPECTED_MODEL_ID:-${MVP_FAKE_MODEL_ID:-gemma-4-12b}}"
      profile_id="${MVP_PROFILE_ID:-${MVP_FAKE_PROFILE_ID:-catalyst-query-gemma-4-12b}}"
      mode_role_models_json="${MVP_FAKE_EXPECTED_ROLE_MODELS_JSON:-}"
      if [ -z "${mode_role_models_json}" ]; then
        mode_role_models_json='{"query_generate":"gemma-4-12b","query_review":"qwen2.5-14b"}'
      fi
      ;;
    local)
      router_url="${MVP_LOCAL_ROUTER_URL:-http://model-router:8077}"
      model_id="${MVP_EXPECTED_MODEL_ID:-${MVP_BUNDLED_MODEL_ID:-qwen2.5-coder-1.5b-instruct-q4_k_m}}"
      profile_id="${MVP_PROFILE_ID:-${MVP_BUNDLED_PROFILE_ID:-catalyst-query-qwen-coder-1.5b}}"
      mode_role_models_json="${MVP_BUNDLED_EXPECTED_ROLE_MODELS_JSON:-}"
      ;;
    external)
      router_url="${MVP_EXTERNAL_ROUTER_URL:-http://host.docker.internal:8077}"
      model_id="${MVP_EXPECTED_MODEL_ID:-${MVP_EXTERNAL_MODEL_ID:-gemma-4-12b}}"
      profile_id="${MVP_PROFILE_ID:-${MVP_EXTERNAL_PROFILE_ID:-catalyst-query-gemma-4-12b}}"
      mode_role_models_json="${MVP_EXTERNAL_EXPECTED_ROLE_MODELS_JSON:-}"
      if [ -z "${mode_role_models_json}" ]; then
        mode_role_models_json='{"query_generate":"gemma-4-12b","query_review":"qwen2.5-14b"}'
      fi
      ;;
    *)
      echo "ERROR: MVP_MODEL_BACKEND must be local, external, or fake." >&2
      return 2
      ;;
  esac

  export MVP_RESOLVED_MODEL_BACKEND="${backend}"
  export MVP_RESOLVED_ROUTER_URL="${router_url}"
  export MVP_RESOLVED_MODEL_ID="${model_id}"
  export MVP_RESOLVED_PROFILE_ID="${profile_id}"
  export MVP_RESOLVED_ROLE_MODELS_JSON="${MVP_EXPECTED_ROLE_MODELS_JSON:-${mode_role_models_json}}"

  if [ -z "${MVP_RESOLVED_ROLE_MODELS_JSON}" ]; then
    export MVP_RESOLVED_ROLE_MODELS_JSON="$(
      MODEL_ID="${model_id}" python3 - <<'PY'
import json
import os

model_id = os.environ["MODEL_ID"]
print(json.dumps({"query_generate": model_id, "query_review": model_id}))
PY
    )"
  fi
}
