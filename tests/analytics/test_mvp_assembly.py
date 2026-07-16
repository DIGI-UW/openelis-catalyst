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

    def test_data_pipes_is_built_from_the_pinned_checkout_without_spark(self):
        self.assertIn("context: ./.fhir-data-pipes", self.compose)
        self.assertIn("./analytics/config:/app/config:ro", self.compose)
        self.assertTrue((ROOT / "analytics/config/flink-conf.yaml").is_file())
        self.assertIn("FHIRDATA_GENERATEPARQUETFILES: \"false\"", self.compose)
        self.assertIn("FHIRDATA_CREATEHIVERESOURCETABLES: \"false\"", self.compose)
        self.assertIn("FHIRDATA_CREATEPARQUETVIEWS: \"false\"", self.compose)
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

    def test_router_alias_hub_and_ui_ports_do_not_collide(self):
        self.assertIn("bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF", self.env)
        self.assertIn("Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf", self.compose)
        self.assertIn("qwen2.5-coder-14b", self.compose)
        self.assertIn("./.models:/models:ro", self.compose)
        self.assertIn("--model", self.compose)
        self.assertIn("${MED_AGENT_HUB_PORT:-8082}:8080", self.compose)
        self.assertIn("${CATALYST_UI_PORT:-3000}:8080", self.compose)
        self.assertIn("./docs/contracts:/docs/contracts:ro", self.compose)
        self.assertIn("CATALYST_HUB_TIMEOUT_SECONDS", self.compose)


class MvpScriptContractTests(unittest.TestCase):
    def test_mvp_lifecycle_scripts_parse_and_are_executable(self):
        for name in (
            "mvp-reset.sh",
            "mvp-up.sh",
            "mvp-download-model.sh",
            "mvp-seed.sh",
            "mvp-health.sh",
            "mvp-down.sh",
        ):
            with self.subTest(name=name):
                script = ROOT / "scripts" / name
                self.assertTrue(os.access(script, os.X_OK))
                subprocess.run(["bash", "-n", script], check=True)

    def test_up_omits_openelis_frontend_and_proxy(self):
        script = (ROOT / "scripts/mvp-up.sh").read_text()
        self.assertIn("db.openelis.org", script)
        self.assertIn("oe.openelis.org", script)
        self.assertIn("fhir.openelis.org", script)
        self.assertNotIn("frontend.openelis.org", script)
        self.assertNotRegex(script, r'[" ]proxy[" )]')

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
