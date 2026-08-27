import json
import os
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.mvp.yml"
EXTERNAL_REVIEWED_PROFILE_ID = "catalyst-query-e4b-qwen14b"
SUPERSET_IMAGE = (
    "apache/superset:6.1.0-dev@sha256:"
    "5822dff49c41fd745ce33e38af502f9c64df30d133aeba148c5d89b35a1004ef"
)
SUPERSET_PLATFORM = "linux/arm64"
SUPERSET_DRIVER_REVISION = "psycopg2-binary==2.9.9"


class MvpComposeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = COMPOSE.read_text()
        cls.demo_compose = (ROOT / "docker-compose.demo.yml").read_text()
        cls.env = (ROOT / "env.recommended").read_text()
        cls.up_script = (ROOT / "scripts/mvp-up.sh").read_text()
        cls.health_script = (ROOT / "scripts/mvp-health.sh").read_text()
        cls.superset_script = (ROOT / "scripts/mvp-superset.sh").read_text()
        cls.model_config_script = (ROOT / "scripts/mvp-model-config.sh").read_text()
        cls.hub_bootstrap = (ROOT / "scripts/bootstrap-med-agent-hub.sh").read_text()
        cls.fhir_data_pipes_bootstrap = (
            ROOT / "scripts/bootstrap-fhir-data-pipes.sh"
        ).read_text()
        cls.openelis_bootstrap = (ROOT / "scripts/bootstrap-openelis.sh").read_text()

    def test_compose_assembles_only_the_required_mvp_services(self):
        self.assertIn(".openelis-docker/docker-compose.yml", self.compose)
        for service in (
            "analytics-db",
            "hapi-mtls-proxy",
            "fhir-data-pipes",
            "med-agent-hub",
            "catalyst-gateway",
            "catalyst-ui",
        ):
            self.assertRegex(self.compose, rf"(?m)^  {re.escape(service)}:")
        self.assertNotRegex(self.compose, r"(?m)^  model-router(?:-fake)?:")
        self.assertFalse((ROOT / "scripts/fake-model-router.py").exists())
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
        expected_context = 'context: "${MED_AGENT_HUB_CONTEXT:-./.med-agent-hub}"'
        self.assertIn(expected_context, self.compose)
        self.assertIn(
            'HUB_BUILD_REVISION: "${HUB_BUILD_REVISION:-unknown}"',
            self.compose,
        )
        self.assertIn(
            'hub_context="${MED_AGENT_HUB_CONTEXT:-${ROOT_DIR}/.med-agent-hub}"',
            self.up_script,
        )
        self.assertIn(
            'hub_build_revision="$(git -C "${hub_context}" rev-parse HEAD)"',
            self.up_script,
        )
        self.assertIn(
            'export HUB_BUILD_REVISION="${hub_build_revision}"',
            self.up_script,
        )
        self.assertIn(
            'CATALYST_QUERY_PROFILE_ID: "${MVP_RESOLVED_PROFILE_ID',
            self.compose,
        )
        self.assertIn(
            "urllib.request.urlopen('http://localhost:8080/health', timeout=3)",
            self.compose,
        )
        self.assertIn(
            'build: "${MED_AGENT_HUB_CONTEXT:-./.med-agent-hub}"',
            (ROOT / "docker-compose.full-stack.yml").read_text(),
        )
        self.assertIn("MED_AGENT_HUB_CONTEXT", self.up_script)
        self.assertIn("harness-sibling", self.health_script)
        self.assertNotIn('"patch"', self.health_script)
        self.assertNotIn("git apply", self.hub_bootstrap)
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
                "SUPERSET_PORT",
            ),
            "mvp-seed.sh": (
                "GATEWAY_PORT",
                "CATALYST_UI_PORT",
                "ANALYTICS_DB_PORT",
                "DATA_PIPES_PORT",
                "MED_AGENT_HUB_PORT",
                "OPENELIS_HTTPS_PORT",
                "HAPI_HTTPS_PORT",
                "SUPERSET_PORT",
            ),
            "mvp-health.sh": (
                "GATEWAY_PORT",
                "CATALYST_UI_PORT",
                "ANALYTICS_DB_PORT",
                "DATA_PIPES_PORT",
                "MED_AGENT_HUB_PORT",
                "OPENELIS_HTTPS_PORT",
                "HAPI_HTTPS_PORT",
                "SUPERSET_PORT",
            ),
        }
        for script_name, variables in expected.items():
            script = (ROOT / "scripts" / script_name).read_text()
            for variable in variables:
                with self.subTest(script=script_name, variable=variable):
                    self.assertIn(f"export {variable}=", script)

    def test_data_pipes_is_built_from_the_pinned_checkout_with_its_warehouse(self):
        self.assertIn("context: ./.fhir-data-pipes", self.compose)
        self.assertIn("./analytics/config:/app/config:ro", self.compose)
        self.assertTrue((ROOT / "analytics/config/flink-conf.yaml").is_file())
        self.assertIn('FHIRDATA_GENERATEPARQUETFILES: "true"', self.compose)
        self.assertIn('FHIRDATA_CREATEHIVERESOURCETABLES: "true"', self.compose)
        self.assertIn('FHIRDATA_CREATEPARQUETVIEWS: "true"', self.compose)
        # The thriftserver must share the warehouse at the same path the
        # controller registers, or its views resolve to nothing.
        self.assertIn("data-pipes-dwh:/dwh", self.compose)
        self.assertIn("sbin/start-thriftserver.sh", self.compose)
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

    def test_pinned_runtime_dependency_checkouts_are_reused_until_refresh_requested(self):
        for script, pinned_reference in (
            (self.fhir_data_pipes_bootstrap, "FHIR_DATA_PIPES_COMMIT"),
            (self.openelis_bootstrap, "OPENELIS_DOCKER_REF"),
        ):
            with self.subTest(reference=pinned_reference):
                self.assertIn('MVP_REFRESH_DEPENDENCIES:-false', script)
                self.assertIn('REFRESH_DEPENDENCIES}" != "true"', script)
                self.assertIn(
                    f'actual_commit}}" = "${{{pinned_reference}}}"', script
                )

    def test_router_identity_hub_and_ui_ports_do_not_collide(self):
        self.assertNotRegex(self.compose, r"(?m)^  model-router(?:-fake)?:")
        self.assertNotIn("./.models:/models:ro", self.compose)
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
            "proxy_read_timeout 1800s",
            (ROOT / "catalyst-ui/nginx.conf").read_text(),
        )
        ui_proxy = (ROOT / "catalyst-ui/nginx.conf").read_text()
        self.assertIn("resolver 127.0.0.11 valid=10s ipv6=off", ui_proxy)
        self.assertIn("proxy_pass $catalyst_gateway", ui_proxy)

    def test_external_gemma_router_is_the_recommended_manual_backend(self):
        self.assertIn("MVP_MODEL_BACKEND=external", self.env)
        self.assertIn(
            "MVP_EXTERNAL_ROUTER_URL=http://host.docker.internal:8077", self.env
        )
        self.assertIn(
            f"MVP_EXTERNAL_PROFILE_ID={EXTERNAL_REVIEWED_PROFILE_ID}",
            self.env,
        )
        self.assertIn(
            'LLM_BASE_URL: "${MVP_SELECTED_ROUTER_URL:-http://host.docker.internal:8077}"',
            self.compose,
        )
        self.assertIn("HUB_LLM_PROVIDER=openai-compatible", self.env)
        self.assertIn(
            'LLM_PROVIDER: "${HUB_LLM_PROVIDER:-openai-compatible}"', self.compose
        )
        self.assertIn("host.docker.internal:host-gateway", self.compose)
        self.assertIn("mvp_resolve_model_config", self.up_script)

    def test_no_fake_or_bundled_router_configuration_remains(self):
        for text in (
            self.env,
            self.compose,
            self.demo_compose,
            self.up_script,
            self.health_script,
        ):
            self.assertNotIn("MVP_FAKE_", text)
            self.assertNotIn("model-router-fake", text)
        self.assertNotRegex(self.demo_compose, r"(?m)^  model-router:")
        self.assertIn(
            "${MVP_EXTERNAL_ROUTER_URL:-http://host.docker.internal:8077}",
            self.demo_compose,
        )
        self.assertNotIn("CATALYST_ROUTER_URL", self.compose)
        self.assertNotIn("CATALYST_ROUTER_URL", self.demo_compose)
        self.assertNotIn("MVP_BUNDLED_", self.env)
        self.assertNotIn("MVP_BUNDLED_", self.up_script)

    def test_router_urls_are_mode_specific_and_stale_generic_url_is_ignored(self):
        self.assertIn(
            'MVP_RESOLVED_ROUTER_URL="${MVP_EXTERNAL_ROUTER_URL:-http://host.docker.internal:8077}"',
            self.model_config_script,
        )
        for script in (self.up_script, self.health_script, self.model_config_script):
            self.assertNotIn("MVP_HUB_LLM_BASE_URL", script)
        self.assertNotIn("MVP_HUB_LLM_BASE_URL", self.env)
        self.assertNotIn("MVP_HUB_LLM_BASE_URL", self.compose)
        self.assertIn('export MVP_SELECTED_ROUTER_URL="${router_url}"', self.up_script)

    def test_up_does_not_manage_a_product_router_service(self):
        self.assertNotIn("stale_model_services", self.up_script)
        self.assertNotIn("model-router", self.up_script)
        self.assertIn("/v1/hub/query-profiles", self.up_script)

    def test_health_never_infers_mode_from_leftover_router_containers(self):
        self.assertNotIn("running_services", self.health_script)
        self.assertNotIn('awk \'$0 == "model-router-fake"', self.health_script)
        self.assertIn("check_hub_router_config", self.health_script)
        self.assertIn('os.environ.get("LLM_BASE_URL", "")', self.health_script)
        self.assertIn('-e "EXPECTED_ROUTER_URL=${router_url}"', self.health_script)

    def test_health_and_provenance_use_the_selected_router_identity(self):
        self.assertNotIn("MVP_EXTERNAL_MODEL_ID", self.model_config_script)
        self.assertNotIn("MVP_BUNDLED_MODEL_ID", self.model_config_script)
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
        self.assertNotIn("MVP_EXPECTED_ROLE_MODELS_JSON", self.health_script)
        self.assertIn("/v1/hub/query-profiles", self.health_script)
        # A writer is required and a reviewer is optional: Hub advertises
        # writer-only profiles too, so health must not pin an exact role set.
        self.assertIn('if "query_generate" not in role_models:', self.health_script)
        self.assertIn(
            'if not set(role_models) <= {"query_generate", "query_review"}:',
            self.health_script,
        )
        self.assertIn(
            'evidence = profile.get("profileEvidence")',
            self.health_script,
        )
        self.assertIn(
            'evidence.get("profileId") != profile_id',
            self.health_script,
        )
        self.assertIn(
            'if not str(evidence.get("profileDigest", "")):',
            self.health_script,
        )
        self.assertIn('"roleModels": role_models', self.health_script)
        self.assertIn('model_router["modelIds"] = sorted', self.health_script)

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

    def test_superset_runtime_is_digest_pinned_initialized_and_persistent(self):
        for service in (
            "superset-metadata-db",
            "superset-init",
            "superset",
            "superset-importer",
        ):
            self.assertRegex(self.compose, rf"(?m)^  {service}:")
        self.assertGreaterEqual(self.compose.count(f"image: {SUPERSET_IMAGE}"), 3)
        self.assertIn("catalyst_superset_metadata", self.compose)
        init_script = (ROOT / "scripts/superset-init.sh").read_text()
        self.assertIn("superset db upgrade", init_script)
        self.assertIn("superset init", init_script)
        self.assertIn("service_completed_successfully", self.compose)
        self.assertIn("superset-metadata-data:/var/lib/postgresql/data", self.compose)
        self.assertIn("superset-home:/app/superset_home", self.compose)
        self.assertIn('127.0.0.1:${SUPERSET_PORT:-8088}:8088', self.compose)
        self.assertIn("/health", self.compose)

    def test_superset_runtime_records_the_pinned_platform_and_driver_revision(self):
        self.assertGreaterEqual(
            self.compose.count(
                'platform: "${SUPERSET_PLATFORM:-' + SUPERSET_PLATFORM + '}"'
            ),
            3,
        )
        self.assertIn(f"SUPERSET_PLATFORM={SUPERSET_PLATFORM}", self.env)
        self.assertIn(f"SUPERSET_DRIVER_REVISION={SUPERSET_DRIVER_REVISION}", self.env)
        self.assertIn(
            'CATALYST_SUPERSET_PLATFORM: "${SUPERSET_PLATFORM:-linux/arm64}"',
            self.compose,
        )
        self.assertIn(
            'CATALYST_SUPERSET_DRIVER_REVISION: '
            '"${SUPERSET_DRIVER_REVISION:-psycopg2-binary==2.9.9}"',
            self.compose,
        )
        self.assertIn("SUPERSET_PLATFORM=", self.health_script)
        self.assertIn("SUPERSET_DRIVER_REVISION=", self.health_script)
        self.assertIn('"platform": os.environ["SUPERSET_PLATFORM"]', self.health_script)
        self.assertIn(
            '"driverRevision": os.environ["SUPERSET_DRIVER_REVISION"]',
            self.health_script,
        )

    def test_hapi_proxy_allows_the_pinned_fhir_first_start_to_finish(self):
        proxy = self.compose[
            self.compose.index("  hapi-mtls-proxy:") : self.compose.index(
                "  fhir-data-pipes:"
            )
        ]
        self.assertIn("wget -qO /dev/null http://127.0.0.1:8080/fhir/metadata", proxy)
        self.assertIn("start_period: 10m", proxy)

    def test_superset_runtime_separates_read_only_input_and_writable_receipts(self):
        gitignore = (ROOT / ".gitignore").read_text()
        config = (ROOT / "superset/superset_config.py").read_text()
        roles = (ROOT / "analytics/sql/000_analytics_roles.sql").read_text()
        self.assertIn("/runtime/superset/", gitignore)
        self.assertIn("CATALYST_SUPERSET_METADATA_DSN", config)
        self.assertIn("SQLALCHEMY_DATABASE_URI", config)
        self.assertNotIn("catalyst_readonly", config)
        self.assertIn(
            "ALTER ROLE catalyst_readonly SET default_transaction_read_only = on;",
            roles,
        )
        self.assertIn("REVOKE CREATE ON SCHEMA public FROM PUBLIC;", roles)

    def test_superset_lifecycle_retains_state_until_explicit_reset(self):
        down_script = (ROOT / "scripts/mvp-down.sh").read_text()
        reset_script = (ROOT / "scripts/mvp-reset.sh").read_text()

        self.assertIn('"${compose[@]}" down --remove-orphans "$@"', down_script)
        self.assertNotIn("down --volumes", down_script)
        self.assertIn('"${compose[@]}" down --volumes --remove-orphans', reset_script)
        self.assertIn('"${ROOT_DIR}/runtime/superset/outbox"', self.up_script)
        self.assertIn(
            '"${ROOT_DIR}/runtime/superset/receipts/last-verified"',
            self.up_script,
        )
        self.assertRegex(self.compose, r"(?m)^  superset-metadata-data:$")
        self.assertRegex(self.compose, r"(?m)^  superset-home:$")

    def test_superset_local_config_is_injected_without_serializing_credentials(self):
        importer = (ROOT / "scripts/superset-import.py").read_text()
        provenance_writer = self.health_script[
            self.health_script.index("payload = {") :
        ]

        for variable in (
            "SUPERSET_SECRET_KEY",
            "SUPERSET_ADMIN_PASSWORD",
            "SUPERSET_METADATA_PASSWORD",
        ):
            with self.subTest(variable=variable):
                self.assertIn(f"${{{variable}:-", self.compose)
                self.assertNotIn(f'os.environ["{variable}"]', provenance_writer)
        self.assertIn('os.environ.get("SUPERSET_ADMIN_PASSWORD", "")', importer)
        self.assertIn('os.environ.get("SUPERSET_METADATA_PASSWORD", "")', importer)
        self.assertIn('"redacted": True', importer)
        self.assertNotIn('"password":', importer)

    def test_superset_importer_receipts_identify_the_exact_catalyst_revision(self):
        self.assertIn(
            'catalyst_revision="$(git -C "${ROOT_DIR}" rev-parse --verify HEAD)"',
            self.superset_script,
        )
        self.assertIn(
            'export CATALYST_IMPORTER_REVISION="${catalyst_revision}"',
            self.superset_script,
        )
        self.assertIn(
            'CATALYST_IMPORTER_REVISION: "${CATALYST_IMPORTER_REVISION:-}"',
            self.compose,
        )
        self.assertIn(
            'run --rm --no-deps superset-importer status', self.superset_script
        )
        self.assertIn(
            'up -d --wait --wait-timeout 180 analytics-db superset',
            self.superset_script,
        )
        self.assertIn(
            'run --rm --no-deps superset-importer import', self.superset_script
        )
        self.assertIn("./runtime/superset/outbox:/opt/catalyst/outbox:ro", self.compose)
        self.assertIn(
            "./runtime/superset/receipts:/opt/catalyst/receipts:rw", self.compose
        )
        self.assertIn(
            "postgresql://catalyst_readonly:demo-readonly-change-me@analytics-db:5432/catalyst_analytics",
            self.compose,
        )
        self.assertNotIn("set-database-uri", self.compose)

    def test_superset_init_and_operator_scripts_are_executable_and_parse(self):
        for name in ("superset-init.sh", "mvp-superset.sh"):
            script = ROOT / "scripts" / name
            self.assertTrue(os.access(script, os.X_OK))
            subprocess.run(["bash", "-n", script], check=True)


class MvpScriptContractTests(unittest.TestCase):
    _MODEL_OVERRIDE_KEYS = (
        "MVP_MODEL_BACKEND",
        "MVP_EXTERNAL_ROUTER_URL",
        "MVP_EXTERNAL_PROFILE_ID",
        "MVP_PROFILE_ID",
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
            "backend": "external",
            "profileId": EXTERNAL_REVIEWED_PROFILE_ID,
            "routerUrl": "http://host.docker.internal:8077",
        }
        for script_name in ("mvp-up.sh", "mvp-seed.sh", "mvp-health.sh"):
            with self.subTest(script=script_name):
                resolved = self._resolved_model_config(
                    script_name,
                    "external",
                    MVP_EXTERNAL_ROUTER_URL=expected["routerUrl"],
                    MVP_EXTERNAL_PROFILE_ID=expected["profileId"],
                )
                self.assertEqual(resolved, expected)

    def test_unsupported_router_modes_fail_in_every_lifecycle_script(self):
        for script_name in ("mvp-up.sh", "mvp-seed.sh", "mvp-health.sh"):
            for backend in ("fake", "local"):
                with self.subTest(script=script_name, backend=backend):
                    with self.assertRaises(subprocess.CalledProcessError):
                        self._resolved_model_config(script_name, backend)

    def test_explicit_model_invocation_overrides_survive_every_env_file_load(self):
        overrides = {
            "MVP_EXTERNAL_ROUTER_URL": "http://custom-external-router:9000",
            "MVP_PROFILE_ID": "custom-hub-profile",
        }
        for script_name in ("mvp-up.sh", "mvp-seed.sh", "mvp-health.sh"):
            with self.subTest(script=script_name):
                resolved = self._resolved_model_config(
                    script_name,
                    "external",
                    **overrides,
                )
                self.assertEqual(
                    resolved["routerUrl"], overrides["MVP_EXTERNAL_ROUTER_URL"]
                )
                self.assertEqual(resolved["profileId"], "custom-hub-profile")

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
            "hub router configuration",
            "hub query profile",
            "gateway view of Hub query profile",
            "Catalyst gateway",
            "Catalyst UI",
            "Superset renderer",
            "mvp-provenance.json",
        ):
            self.assertIn(marker, script)
        self.assertIn('if body != "OK":', script)


if __name__ == "__main__":
    unittest.main()
