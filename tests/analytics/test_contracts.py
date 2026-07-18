import json
import os
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANALYTICS = ROOT / "analytics"
PINNED_DATA_PIPES_COMMIT = "3ea890884d674e2f31257a2da421601f2d75b5e9"


def load_simple_yaml_section(text, section):
    """Parse scalar keys in one top-level section without a test dependency."""
    values = {}
    in_section = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not raw_line.startswith((" ", "\t")):
            in_section = line == f"{section}:"
            continue
        if not in_section or not raw_line.startswith("  "):
            continue

        key, separator, raw_value = line.strip().partition(":")
        if not separator or not raw_value.strip():
            continue
        raw_value = raw_value.strip()
        if raw_value in {"true", "false"}:
            values[key] = raw_value == "true"
        elif raw_value.startswith('"'):
            values[key] = json.loads(raw_value)
        elif raw_value.isdigit():
            values[key] = int(raw_value)
        else:
            values[key] = raw_value
    return values


class BootstrapContractTests(unittest.TestCase):
    def test_data_pipes_bootstrap_is_pinned_and_checkout_is_ignored(self):
        script = (ROOT / "scripts/bootstrap-fhir-data-pipes.sh").read_text()
        self.assertIn(PINNED_DATA_PIPES_COMMIT, script)
        self.assertRegex(script, r"git (?:-C .* )?checkout --detach")
        self.assertRegex(script, r"rev-parse HEAD")
        self.assertIn(".fhir-data-pipes/", (ROOT / ".gitignore").read_text().splitlines())


class DataPipesConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config_path = ANALYTICS / "config/controller/application.yaml"
        cls.config_text = cls.config_path.read_text()
        cls.config = load_simple_yaml_section(cls.config_text, "fhirdata")

    def test_controller_uses_fhir_search_to_postgresql_without_spark(self):
        self.assertEqual("FHIR_SEARCH", self.config["fhirFetchMode"])
        self.assertEqual(
            "http://hapi-mtls-proxy:8080/fhir",
            self.config["fhirServerUrl"],
        )
        self.assertEqual(
            "Patient,Observation,ServiceRequest,Specimen,DiagnosticReport",
            self.config["resourceList"],
        )
        self.assertFalse(self.config["generateParquetFiles"])
        self.assertFalse(self.config["createParquetViews"])
        self.assertFalse(self.config["createHiveResourceTables"])
        self.assertEqual("config/views", self.config["viewDefinitionsDir"])
        self.assertEqual(
            "config/postgres-sink.json", self.config["sinkDbConfigPath"]
        )
        self.assertNotIn("spark", self.config_text.lower())

        sink = json.loads(
            (ANALYTICS / "config/postgres-sink.json").read_text()
        )
        self.assertEqual("postgresql", sink["databaseService"])
        self.assertEqual("org.postgresql.Driver", sink["jdbcDriverClass"])

    def test_minimal_view_definitions_are_single_row_projections(self):
        expected = {
            "observation_flat_v1.json": ("Observation", "observation_flat_v1"),
            "service_request_flat_v1.json": (
                "ServiceRequest",
                "service_request_flat_v1",
            ),
            "specimen_flat_v1.json": ("Specimen", "specimen_flat_v1"),
            "diagnostic_report_flat_v1.json": (
                "DiagnosticReport",
                "diagnostic_report_flat_v1",
            ),
        }
        views_dir = ANALYTICS / "config/views"
        self.assertEqual(set(expected), {path.name for path in views_dir.glob("*.json")})

        for file_name, (resource, name) in expected.items():
            with self.subTest(file_name=file_name):
                view = json.loads((views_dir / file_name).read_text())
                self.assertEqual(
                    "http://hl7.org/fhir/uv/sql-on-fhir/StructureDefinition/ViewDefinition",
                    view["resourceType"],
                )
                self.assertEqual(resource, view["resource"])
                self.assertEqual(name, view["name"])
                self.assertEqual("active", view["status"])
                self.assertEqual(["4.0"], view["fhirVersion"])
                self.assertEqual(1, len(view["select"]))
                self.assertNotIn("forEach", view["select"][0])
                self.assertNotIn("forEachOrNull", view["select"][0])
                columns = view["select"][0]["column"]
                names = [column["name"] for column in columns]
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual("getResourceKey()", columns[0]["path"])
                self.assertEqual("id", columns[0]["name"])


class SeedContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = (
            ANALYTICS / "openelis/seed-openelis-3.2.1.sql"
        ).read_text()
        cls.backfill = (ANALYTICS / "openelis/backfill-hapi.sh").read_text()
        cls.runner = (ROOT / "scripts/mvp-seed.sh").read_text()

    def test_seed_is_version_guarded_idempotent_and_fixed(self):
        self.assertIn(r"^3\.2\.1\.[0-9]+$", self.runner)
        self.assertIn("openelis_version", self.seed)
        self.assertIn("databasechangelog", self.seed.lower())
        self.assertIn("CATALYST-DEMO-PATIENT-001", self.seed)
        self.assertIn("b50d156e-0f6f-40cd-921c-4e831602a623", self.seed)
        self.assertIn("status.test.valid", self.seed)
        self.assertIn("v_finalized_status_id::text", self.seed)
        self.assertIn(
            "collection_date, received_date, status_id, collector",
            " ".join(self.seed.split()),
        )
        self.assertIn("AT TIME ZONE 'America/New_York'", self.seed)
        self.assertIn("IF v_patient_id IS NULL", self.seed)
        self.assertIn("IF v_sample_id IS NULL", self.seed)
        self.assertIn("IF v_result_id IS NULL", self.seed)

        value_rows = re.findall(
            r"\(\s*'CATVL\d{4}'.*?,\s*(1200|450|80)::numeric\s*,",
            self.seed,
        )
        self.assertEqual(["1200", "450", "80"], value_rows)
        self.assertEqual(3, len(set(re.findall(r"'CATVL\d{4}'", self.seed))))

    def test_backfill_waits_for_every_resource_contract(self):
        self.assertIn("/OEToFhir", self.backfill)
        self.assertIn("checkAll=true", self.backfill)
        self.assertIn("waitForResults=true", self.backfill)
        self.assertIn("HAPI_CLIENT_P12", self.backfill)
        for resource, count in {
            "Patient": 1,
            "Observation": 3,
            "ServiceRequest": 3,
            "Specimen": 3,
            "DiagnosticReport": 3,
        }.items():
            self.assertRegex(self.backfill, rf'wait_for_resource "{resource}" {count}\b')


class SemanticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = (ANALYTICS / "sql/001_analytics_v1.sql").read_text()
        cls.catalog = json.loads(
            (ANALYTICS / "catalog/analytics-catalog-v1.json").read_text()
        )
        cls.run_schema = json.loads(
            (ANALYTICS / "contracts/pipeline-run-v1.schema.json").read_text()
        )

    def test_lab_result_fact_has_one_observation_grain(self):
        normalized = " ".join(self.sql.lower().split())
        self.assertIn(
            "create or replace view analytics.lab_result_fact_v1 as", normalized
        )
        fact_sql = normalized.split(
            "create or replace view analytics.lab_result_fact_v1 as", 1
        )[1]
        self.assertIn("from public.observation_flat_v1 as observation", fact_sql)
        self.assertIn(
            "left join public.specimen_flat_v1 as specimen "
            "on specimen.id = observation.specimen_id",
            fact_sql,
        )
        self.assertNotIn("select *", fact_sql)
        self.assertIn("observation.id as observation_id", fact_sql)
        self.assertIn("specimen.received_at as specimen_received_at", fact_sql)
        self.assertIn("as receipt_to_release_minutes", fact_sql)

    def test_freshness_and_run_metadata_are_structured(self):
        normalized = " ".join(self.sql.lower().split())
        self.assertIn("analytics.pipeline_run_v1", normalized)
        self.assertIn("source_watermark", normalized)
        self.assertIn("completion_state", normalized)
        self.assertIn("pipeline_run_id", normalized)
        self.assertIn("observed_lag_seconds", normalized)

        self.assertEqual(
            "https://openelis.org/catalyst/contracts/analytics-pipeline-run-v1.schema.json",
            self.run_schema["$id"],
        )
        required = set(self.run_schema["required"])
        self.assertTrue(
            {
                "contractVersion",
                "pipelineRunId",
                "completionState",
                "sourceWatermark",
                "startedAt",
                "completedAt",
                "observedAt",
            }.issubset(required)
        )

    def test_catalog_matches_documented_analytics_contract(self):
        self.assertEqual("catalyst.analytics.catalog.v1", self.catalog["contractVersion"])
        self.assertEqual("analytics-catalog-v1", self.catalog["catalogVersion"])
        self.assertEqual("demo", self.catalog["deploymentMode"])
        self.assertEqual("postgresql", self.catalog["dialect"])
        self.assertEqual(1, len(self.catalog["views"]))

        view = self.catalog["views"][0]
        self.assertEqual("analytics.lab_result_fact_v1", view["name"])
        self.assertEqual("1", view["version"])
        self.assertTrue(view["approved"])
        self.assertTrue(view["demoDataOnly"])
        for field in (
            "grain",
            "columns",
            "allowedFilters",
            "allowedGroupings",
            "terminology",
            "freshness",
            "examples",
            "requiredConstraints",
        ):
            self.assertTrue(view[field], field)

        self.assertEqual(
            [
                "patient_id",
                "test_code",
                "test_name",
                "result_value",
                "result_unit",
                "issued_at",
                "receipt_to_release_minutes",
                "observed_at",
            ],
            [column["name"] for column in view["columns"]],
        )
        self.assertEqual(
            "analytics.pipeline_run_v1", self.catalog["freshness"]["relation"]
        )


class ShellContractTests(unittest.TestCase):
    def test_new_shell_scripts_parse_and_are_executable(self):
        scripts = [
            ROOT / "scripts/bootstrap-fhir-data-pipes.sh",
            ROOT / "scripts/mvp-seed.sh",
            ROOT / "scripts/mvp-analytics-health.sh",
            ANALYTICS / "openelis/backfill-hapi.sh",
        ]
        for script in scripts:
            with self.subTest(script=script):
                self.assertTrue(os.access(script, os.X_OK))
                subprocess.run(["bash", "-n", script], check=True)


if __name__ == "__main__":
    unittest.main()
