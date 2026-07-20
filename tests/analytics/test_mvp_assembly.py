import json
import os
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.mvp.yml"


class MvpComposeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = COMPOSE.read_text()
        cls.env = (ROOT / "env.recommended").read_text()
        cls.up_script = (ROOT / "scripts/mvp-up.sh").read_text()
        cls.health_script = (ROOT / "scripts/mvp-health.sh").read_text()
        cls.model_config_script = (ROOT / "scripts/mvp-model-config.sh").read_text()
        cls.hub_bootstrap = (ROOT / "scripts/bootstrap-med-agent-hub.sh").read_text()

    def test_compose_assembles_only_the_required_mvp_services(self):
        self.assertIn(".openelis-docker/docker-compose.yml", self.compose)
        for service in (
            "analytics-db",
            "hapi-mtls-proxy",
            "fhir-data-pipes",
            "model-router",
            "model-router-fake",
            "med-agent-hub",
            "catalyst-gateway",
            "catalyst-ui",
        ):
            self.assertRegex(self.compose, rf"(?m)^  {re.escape(service)}:")
        self.assertNotRegex(self.compose, r"(?m)^  spark:")

    def test_openelis_checkout_and_runtime_images_are_immutable(self):
        self.assertIn(
            "OPENELIS_DOCKER_REF=f118d0ae778a30028c16be2af549843ec166f655",
            self.env,
        )
        for digest in (
            "e27a8194300ba73309e835a4070e9ce531687eb3ee604895de781f3061791635",
            "e801c93a8bedc41c2e502722e38585979fbbaf0e92ee4c248cdde72d9c33ec1e",
            "2217d76104051589d99eb808cef22ae692f6ad2d12a0fadc70ecc549162df36f",
            "667680632b8fe491bb1955f3935751562e60933d3aea91d79256ccd4eac857c3",
        ):
            self.assertIn(f"@sha256:{digest}", self.compose)

    def test_hub_source_is_injectable_and_standalone_fallback_is_unmodified(self):
        expected_context = 'build: "${MED_AGENT_HUB_CONTEXT:-./.med-agent-hub}"'
        self.assertIn(expected_context, self.compose)
        self.assertIn(
            expected_context,
            (ROOT / "docker-compose.full-stack.yml").read_text(),
        )
        self.assertIn("MED_AGENT_HUB_CONTEXT", self.up_script)
        self.assertIn("harness-sibling", self.health_script)
        self.assertNotIn('"patch"', self.health_script)
        self.assertNotIn("git apply", self.hub_bootstrap)
        self.assertIn(
            "099d23395c785de34ed89cf192d196def713b216",
            self.hub_bootstrap,
        )
        self.assertFalse(
            (ROOT / "patches/med-agent-hub/catalyst-query-profile.patch").exists()
        )

    def test_lifecycle_scripts_accept_one_optional_compose_override(self):
        for script_name in (
            "mvp-up.sh",
            "mvp-seed.sh",
            "mvp-health.sh",
            "mvp-down.sh",
            "mvp-reset.sh",
        ):
            with self.subTest(script=script_name):
                script = (ROOT / "scripts" / script_name).read_text()
                self.assertIn("MVP_COMPOSE_OVERRIDE_FILE", script)
                self.assertIn(
                    'if [ -n "${compose_override_file}" ]; then',
                    script,
                )
                self.assertIn(
                    'compose+=(-f "${compose_override_file}")',
                    script,
                )
                self.assertIn(
                    "compose override file does not exist",
                    script,
                )

    def test_invocation_port_overrides_survive_env_file_loading(self):
        expected = {
            "mvp-up.sh": (
                "GATEWAY_PORT",
                "CATALYST_UI_PORT",
                "ANALYTICS_DB_PORT",
                "DATA_PIPES_PORT",
                "MED_AGENT_HUB_PORT",
                "OPENELIS_HTTPS_PORT",
                "HAPI_HTTPS_PORT",
            ),
            "mvp-seed.sh": (
                "GATEWAY_PORT",
                "CATALYST_UI_PORT",
                "ANALYTICS_DB_PORT",
                "DATA_PIPES_PORT",
                "MED_AGENT_HUB_PORT",
                "OPENELIS_HTTPS_PORT",
                "HAPI_HTTPS_PORT",
            ),
            "mvp-health.sh": (
                "GATEWAY_PORT",
                "CATALYST_UI_PORT",
                "ANALYTICS_DB_PORT",
                "DATA_PIPES_PORT",
                "MED_AGENT_HUB_PORT",
                "OPENELIS_HTTPS_PORT",
                "HAPI_HTTPS_PORT",
            ),
        }
        for script_name, variables in expected.items():
            script = (ROOT / "scripts" / script_name).read_text()
            for variable in variables:
                with self.subTest(script=script_name, variable=variable):
                    self.assertIn(f"export {variable}=", script)

    def test_data_pipes_is_built_from_the_pinned_checkout_without_spark(self):
        self.assertIn("context: ./.fhir-data-pipes", self.compose)
        self.assertIn("./analytics/config:/app/config:ro", self.compose)
        self.assertTrue((ROOT / "analytics/config/flink-conf.yaml").is_file())
        self.assertIn('FHIRDATA_GENERATEPARQUETFILES: "false"', self.compose)
        self.assertIn('FHIRDATA_CREATEHIVERESOURCETABLES: "false"', self.compose)
        self.assertIn('FHIRDATA_CREATEPARQUETVIEWS: "false"', self.compose)
        self.assertIn("javax.net.ssl.keyStore", self.compose)
        self.assertIn("key_trust-store-volume:/etc/openelis-global:ro", self.compose)
        self.assertIn(
            "./analytics/config/hapi-mtls-proxy.conf:/etc/nginx/nginx.conf:ro",
            self.compose,
        )
        proxy_config = (ROOT / "analytics/config/hapi-mtls-proxy.conf").read_text()
        self.assertIn("proxy_ssl_certificate", proxy_config)
        self.assertIn("sub_filter_once off", proxy_config)
        self.assertIn(
            "http://hapi-mtls-proxy:8080/fhir",
            proxy_config,
        )

    def test_router_identity_hub_and_ui_ports_do_not_collide(self):
        self.assertIn("bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF", self.env)
        self.assertIn("Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf", self.compose)
        self.assertIn("qwen2.5-coder-1.5b-instruct-q4_k_m", self.compose)
        self.assertNotIn("qwen2.5-coder-14b", self.compose)
        self.assertIn(
            "ghcr.io/ggml-org/llama.cpp@sha256:"
            "6bc9134e3278a0ecab23d7ef2f6a46b4595740014fe9bc2f67e8ba7dca8395b4",
            self.compose,
        )
        self.assertIn("./.models:/models:ro", self.compose)
        self.assertIn("--model", self.compose)
        self.assertIn("${MED_AGENT_HUB_PORT:-8082}:8080", self.compose)
        self.assertIn("${CATALYST_UI_PORT:-3000}:8080", self.compose)
        for port_mapping in (
            "127.0.0.1:${ANALYTICS_DB_PORT:-15433}:5432",
            "127.0.0.1:${DATA_PIPES_PORT:-8090}:8080",
            "127.0.0.1:${MED_AGENT_HUB_PORT:-8082}:8080",
            "127.0.0.1:${GATEWAY_PORT:-8000}:8000",
            "127.0.0.1:${CATALYST_UI_PORT:-3000}:8080",
        ):
            self.assertIn(port_mapping, self.compose)
        self.assertIn("./docs/contracts:/docs/contracts:ro", self.compose)
        self.assertIn(
            'CATALYST_HUB_TIMEOUT_SECONDS: "${CATALYST_HUB_TIMEOUT_SECONDS:-360}"',
            self.compose,
        )
        self.assertIn(
            "proxy_read_timeout 420s",
            (ROOT / "catalyst-ui/nginx.conf").read_text(),
        )

    def test_external_gemma_router_is_the_recommended_manual_backend(self):
        self.assertIn("MVP_MODEL_BACKEND=external", self.env)
        self.assertIn(
            "MVP_EXTERNAL_ROUTER_URL=http://host.docker.internal:8077", self.env
        )
        self.assertIn("MVP_EXTERNAL_MODEL_ID=gemma-4-12b", self.env)
        self.assertIn("MVP_EXTERNAL_PROFILE_ID=catalyst-query-gemma-4-12b", self.env)
        self.assertIn(
            "MVP_EXTERNAL_EXPECTED_ROLE_MODELS_JSON='"
            '{"query_generate":"gemma-4-12b","query_review":"qwen2.5-14b"}'
            "'",
            self.env,
        )
        self.assertIn(
            'LLM_BASE_URL: "${MVP_SELECTED_ROUTER_URL:-http://host.docker.internal:8077}"',
            self.compose,
        )
        self.assertIn("HUB_LLM_PROVIDER=llama.cpp", self.env)
        self.assertIn('LLM_PROVIDER: "${HUB_LLM_PROVIDER:-llama.cpp}"', self.compose)
        self.assertIn("host.docker.internal:host-gateway", self.compose)
        self.assertIn("mvp_resolve_model_config", self.up_script)
        self.assertIn(
            "gemma-e4b,gemma-4-12b,qwen2.5-14b,qwen2.5-coder-1.5b-instruct-q4_k_m",
            self.compose,
        )

    def test_router_urls_are_mode_specific_and_stale_generic_url_is_ignored(self):
        self.assertIn(
            'router_url="${MVP_EXTERNAL_ROUTER_URL:-http://host.docker.internal:8077}"',
            self.model_config_script,
        )
        self.assertIn(
            'router_url="${MVP_LOCAL_ROUTER_URL:-http://model-router:8077}"',
            self.model_config_script,
        )
        self.assertIn(
            'router_url="${MVP_FAKE_ROUTER_URL:-http://model-router-fake:8077}"',
            self.model_config_script,
        )
        for script in (self.up_script, self.health_script, self.model_config_script):
            self.assertNotIn("MVP_HUB_LLM_BASE_URL", script)
        self.assertNotIn("MVP_HUB_LLM_BASE_URL", self.env)
        self.assertNotIn("MVP_HUB_LLM_BASE_URL", self.compose)
        self.assertIn('export MVP_SELECTED_ROUTER_URL="${router_url}"', self.up_script)

    def test_up_stops_routers_not_selected_by_the_configured_mode(self):
        self.assertIn("stale_model_services", self.up_script)
        self.assertIn("stale_model_services+=(model-router)", self.up_script)
        self.assertIn("stale_model_services+=(model-router-fake)", self.up_script)
        self.assertIn('stop "${stale_model_services[@]}"', self.up_script)

    def test_health_never_infers_mode_from_leftover_router_containers(self):
        self.assertNotIn("running_services", self.health_script)
        self.assertNotIn('awk \'$0 == "model-router-fake"', self.health_script)
        self.assertIn("check_hub_router_config", self.health_script)
        self.assertIn('os.environ.get("LLM_BASE_URL", "")', self.health_script)
        self.assertIn('-e "EXPECTED_ROUTER_URL=${router_url}"', self.health_script)

    def test_health_and_provenance_use_the_selected_router_identity(self):
        self.assertIn("MVP_EXTERNAL_MODEL_ID", self.model_config_script)
        self.assertIn("MVP_BUNDLED_MODEL_ID", self.model_config_script)
        self.assertIn('model_router["modelId"] = model_ids[0]', self.health_script)
        self.assertIn('"baseUrl": os.environ["ROUTER_URL"]', self.health_script)
        self.assertNotIn("qwen2.5-coder-14b", self.health_script)
        self.assertNotIn("qwen2.5-coder-14b", self.up_script)

    def test_health_gates_and_records_the_openelis_deployment_pin(self):
        self.assertIn("check_openelis_deployment_pin", self.health_script)
        self.assertIn('"openelisDocker": {', self.health_script)
        self.assertIn(
            '"commit": os.environ["OPENELIS_DOCKER_COMMIT"]',
            self.health_script,
        )
        self.assertIn("itechuw/openelis-global-2@sha256:", self.health_script)

    def test_health_validates_and_records_the_exact_profile_role_model_map(self):
        self.assertIn("MVP_EXPECTED_ROLE_MODELS_JSON", self.health_script)
        self.assertIn(
            "EXPECTED_ROLE_MODELS_JSON=${role_models_json}", self.health_script
        )
        self.assertIn(
            "missing = sorted(set(expected.values()) - served)",
            self.health_script,
        )
        self.assertIn(
            "if role_models != expected_role_models:",
            self.health_script,
        )
        self.assertIn(
            'if profile.get("revisionCapable") is not True:',
            self.health_script,
        )
        self.assertIn(
            'profile_evidence = profile.get("profileEvidence")',
            self.health_script,
        )
        self.assertIn(
            'if profile_evidence.get("profileId") != profile_id:',
            self.health_script,
        )
        self.assertIn(
            'if role_evidence.get("modelId") != expected_model:',
            self.health_script,
        )
        self.assertIn(
            '{"query_generate": model_id, "query_review": model_id}',
            self.model_config_script,
        )
        self.assertIn("if len(model_ids) == 1:", self.health_script)
        self.assertIn('"roleModels": role_models', self.health_script)
        self.assertIn('"modelIds": model_ids', self.health_script)

    def test_gateway_image_contains_runtime_contracts_and_catalog(self):
        dockerfile = (ROOT / "catalyst-gateway/Dockerfile").read_text()
        self.assertIn("COPY docs/contracts /docs/contracts", dockerfile)
        self.assertIn(
            "COPY analytics/catalog /app/config",
            dockerfile,
        )
        self.assertIn("context: .", self.compose)
        self.assertIn("dockerfile: catalyst-gateway/Dockerfile", self.compose)
        legacy_compose = (ROOT / "catalyst-dev.docker-compose.yml").read_text()
        self.assertIn("context: .", legacy_compose)
        self.assertIn("dockerfile: catalyst-gateway/Dockerfile", legacy_compose)
        dockerignore = (ROOT / ".dockerignore").read_text()
        self.assertIn(".env", dockerignore)
        self.assertIn(".models", dockerignore)
        self.assertIn("!docs/contracts/**", dockerignore)
        self.assertIn("!analytics/catalog/**", dockerignore)


class MvpScriptContractTests(unittest.TestCase):
    _MODEL_OVERRIDE_KEYS = (
        "MVP_MODEL_BACKEND",
        "MVP_EXTERNAL_ROUTER_URL",
        "MVP_LOCAL_ROUTER_URL",
        "MVP_FAKE_ROUTER_URL",
        "MVP_EXTERNAL_MODEL_ID",
        "MVP_EXTERNAL_PROFILE_ID",
        "MVP_EXTERNAL_EXPECTED_ROLE_MODELS_JSON",
        "MVP_BUNDLED_MODEL_ID",
        "MVP_BUNDLED_PROFILE_ID",
        "MVP_BUNDLED_EXPECTED_ROLE_MODELS_JSON",
        "MVP_FAKE_MODEL_ID",
        "MVP_FAKE_PROFILE_ID",
        "MVP_FAKE_EXPECTED_ROLE_MODELS_JSON",
        "MVP_EXPECTED_MODEL_ID",
        "MVP_PROFILE_ID",
        "MVP_EXPECTED_ROLE_MODELS_JSON",
    )

    def _resolved_model_config(self, script_name, backend, **overrides):
        environment = os.environ.copy()
        for name in self._MODEL_OVERRIDE_KEYS:
            environment.pop(name, None)
        environment.update(
            {
                "MVP_MODEL_BACKEND": backend,
                "MVP_RESOLVE_MODEL_CONFIG_ONLY": "true",
                **overrides,
            }
        )
        completed = subprocess.run(
            [ROOT / "scripts" / script_name],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_mvp_lifecycle_scripts_parse_and_are_executable(self):
        for name in (
            "mvp-reset.sh",
            "mvp-up.sh",
            "mvp-download-model.sh",
            "mvp-seed.sh",
            "mvp-health.sh",
            "mvp-down.sh",
            "../tests/e2e/test_data_pipes_incremental.sh",
        ):
            with self.subTest(name=name):
                script = ROOT / "scripts" / name
                self.assertTrue(os.access(script, os.X_OK))
                subprocess.run(["bash", "-n", script], check=True)
        subprocess.run(["bash", "-n", ROOT / "scripts/mvp-model-config.sh"], check=True)

    def test_backend_resolution_executes_consistently_through_every_lifecycle_script(
        self,
    ):
        expected = {
            "external": {
                "modelId": "gemma-4-12b",
                "profileId": "catalyst-query-gemma-4-12b",
                "routerUrl": "http://host.docker.internal:8077",
                "roleModels": {
                    "query_generate": "gemma-4-12b",
                    "query_review": "qwen2.5-14b",
                },
            },
            "fake": {
                "modelId": "gemma-4-12b",
                "profileId": "catalyst-query-gemma-4-12b",
                "routerUrl": "http://model-router-fake:8077",
                "roleModels": {
                    "query_generate": "gemma-4-12b",
                    "query_review": "qwen2.5-14b",
                },
            },
            "local": {
                "modelId": "qwen2.5-coder-1.5b-instruct-q4_k_m",
                "profileId": "catalyst-query-qwen-coder-1.5b",
                "routerUrl": "http://model-router:8077",
                "roleModels": {
                    "query_generate": "qwen2.5-coder-1.5b-instruct-q4_k_m",
                    "query_review": "qwen2.5-coder-1.5b-instruct-q4_k_m",
                },
            },
        }
        for script_name in ("mvp-up.sh", "mvp-seed.sh", "mvp-health.sh"):
            for backend, backend_expected in expected.items():
                with self.subTest(script=script_name, backend=backend):
                    resolved = self._resolved_model_config(script_name, backend)
                    self.assertEqual(resolved["backend"], backend)
                    for key, value in backend_expected.items():
                        self.assertEqual(resolved[key], value)

    def test_explicit_model_invocation_overrides_survive_every_env_file_load(self):
        role_models = {
            "query_generate": "custom-writer",
            "query_review": "custom-reviewer",
        }
        overrides = {
            "MVP_LOCAL_ROUTER_URL": "http://custom-local-router:9000",
            "MVP_EXPECTED_MODEL_ID": "custom-writer",
            "MVP_PROFILE_ID": "custom-local-profile",
            "MVP_EXPECTED_ROLE_MODELS_JSON": json.dumps(role_models),
        }
        for script_name in ("mvp-up.sh", "mvp-seed.sh", "mvp-health.sh"):
            with self.subTest(script=script_name):
                resolved = self._resolved_model_config(
                    script_name,
                    "local",
                    **overrides,
                )
                self.assertEqual(
                    resolved["routerUrl"], overrides["MVP_LOCAL_ROUTER_URL"]
                )
                self.assertEqual(resolved["modelId"], "custom-writer")
                self.assertEqual(resolved["profileId"], "custom-local-profile")
                self.assertEqual(resolved["roleModels"], role_models)

    def test_up_omits_openelis_frontend_and_proxy(self):
        script = (ROOT / "scripts/mvp-up.sh").read_text()
        self.assertIn("db.openelis.org", script)
        self.assertIn("oe.openelis.org", script)
        self.assertIn("fhir.openelis.org", script)
        self.assertNotIn("frontend.openelis.org", script)
        self.assertNotRegex(script, r'[" ]proxy[" )]')

    def test_seed_psql_stops_on_the_first_error(self):
        script = (ROOT / "scripts/mvp-seed.sh").read_text()
        self.assertIn("--set=ON_ERROR_STOP=1", script)

    def test_seed_gates_the_semantic_cohort_not_only_its_row_count(self):
        script = (ROOT / "scripts/mvp-seed.sh").read_text()
        self.assertIn("count(DISTINCT test_name)", script)
        self.assertIn("WHERE test_name = 'Viral Load'", script)
        self.assertIn(
            "1152|96|9|384|1152|9|2025-07-15|2026-04-27",
            script,
        )
        self.assertIn("FROM public.service_request_flat_v1", script)
        self.assertIn("1152|1152|9", script)

    def test_http_readiness_and_backfill_calls_are_bounded(self):
        for relative_path in (
            "scripts/mvp-seed.sh",
            "scripts/mvp-health.sh",
            "analytics/openelis/backfill-hapi.sh",
        ):
            with self.subTest(path=relative_path):
                script = (ROOT / relative_path).read_text()
                self.assertIn("--connect-timeout", script)
                self.assertIn("--max-time", script)

    def test_health_gates_full_contract_and_emits_provenance(self):
        script = (ROOT / "scripts/mvp-health.sh").read_text()
        for marker in (
            "OpenELIS database",
            "OpenELIS application",
            "HAPI seed resources",
            "FHIR Data Pipes controller",
            "analytics mart exact rows",
            "model router",
            "hub query profile",
            "Catalyst gateway",
            "Catalyst UI",
            "mvp-provenance.json",
        ):
            self.assertIn(marker, script)


if __name__ == "__main__":
    unittest.main()
