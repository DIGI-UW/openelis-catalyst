import json
import os
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANALYTICS = ROOT / "analytics"
PINNED_DATA_PIPES_COMMIT = "3ea890884d674e2f31257a2da421601f2d75b5e9"
PINNED_OPENELIS_DOCKER_COMMIT = "f118d0ae778a30028c16be2af549843ec166f655"


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

    def test_openelis_bootstrap_is_pinned_and_detached(self):
        script = (ROOT / "scripts/bootstrap-openelis.sh").read_text()
        self.assertIn(PINNED_OPENELIS_DOCKER_COMMIT, script)
        self.assertIn("checkout --detach FETCH_HEAD", script)
        self.assertIn("rev-parse HEAD", script)
        self.assertNotIn('OPENELIS_DOCKER_REF:-main', script)


class DataPipesConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config_path = ANALYTICS / "config/controller/application.yaml"
        cls.config_text = cls.config_path.read_text()
        cls.config = load_simple_yaml_section(cls.config_text, "fhirdata")

    def test_controller_materializes_the_shipped_spark_warehouse(self):
        self.assertEqual("FHIR_SEARCH", self.config["fhirFetchMode"])
        self.assertEqual(
            "http://hapi-mtls-proxy:8080/fhir",
            self.config["fhirServerUrl"],
        )
        self.assertEqual(
            "Patient,Observation,ServiceRequest,Specimen,DiagnosticReport",
            self.config["resourceList"],
        )
        self.assertTrue(self.config["generateParquetFiles"])
        self.assertTrue(self.config["createParquetViews"])
        self.assertTrue(self.config["createHiveResourceTables"])
        self.assertEqual("config/views", self.config["viewDefinitionsDir"])

        # The controller registers absolute Parquet locations into the Hive
        # metastore, so the prefix must be the same absolute path the
        # thriftserver mounts. A relative prefix resolves against the
        # container's /app working directory and silently diverges.
        self.assertTrue(
            self.config["dwhRootPrefix"].startswith("/dwh/"),
            self.config["dwhRootPrefix"],
        )

        # The PostgreSQL sink is retired, not merely unused: leaving the key
        # behind is what would let the substituted path quietly come back.
        self.assertNotIn("sinkDbConfigPath", self.config)
        self.assertFalse((ANALYTICS / "config/postgres-sink.json").exists())

        self.assertEqual(
            "config/thriftserver-hive-config.json",
            self.config["thriftserverHiveConfig"],
        )
        thriftserver = json.loads(
            (ANALYTICS / "config/thriftserver-hive-config.json").read_text()
        )
        self.assertEqual("hive2", thriftserver["databaseService"])
        self.assertEqual(
            "org.apache.hive.jdbc.HiveDriver", thriftserver["jdbcDriverClass"]
        )
        # Container-network address, not upstream's 172.17.0.1 host gateway.
        self.assertEqual("spark-thriftserver", thriftserver["databaseHostName"])
        self.assertEqual("10000", thriftserver["databasePort"])

    def test_view_definitions_are_upstream_defaults_plus_gap_fills(self):
        # The ingestion layer is the upstream fhir-data-pipes default views
        # (lossless: forEachOrNull keeps every coding) plus documented
        # gap-fill views for resources upstream ships none for. Curation
        # (single-row grains, coding pivots) lives in analytics/sql only.
        expected = {
            "patient_flat_view.json": ("Patient", "patient_flat"),
            "observation_flat_view.json": ("Observation", "observation_flat"),
            "service_request_flat_view.json": (
                "ServiceRequest",
                "service_request_flat",
            ),
            "specimen_flat_view.json": ("Specimen", "specimen_flat"),
            "diagnostic_report_flat_view.json": (
                "DiagnosticReport",
                "diagnostic_report_flat",
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
                self.assertEqual(["4.0"], view["fhirVersion"])
                columns = view["select"][0]["column"]
                names = [column["name"] for column in columns]
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual("getResourceKey()", columns[0]["path"])
                self.assertEqual("id", columns[0]["name"])

    def test_observation_extensions_survive_upstream_sync(self):
        # The observation view is the upstream default PLUS three additive
        # columns lab_result_fact_v1 depends on. An upstream sync that
        # clobbers them would silently null fact-view columns; fail here
        # instead.
        view = json.loads(
            (ANALYTICS / "config/views/observation_flat_view.json").read_text()
        )
        scalar_columns = {
            column["name"]: column for column in view["select"][0]["column"]
        }
        self.assertIn("issued", scalar_columns)
        self.assertEqual("instant", scalar_columns["issued"]["type"])
        self.assertEqual(
            "basedOn.first().getReferenceKey(ServiceRequest)",
            scalar_columns["service_request_id"]["path"],
        )
        self.assertEqual(
            "specimen.first().getReferenceKey(Specimen)",
            scalar_columns["specimen_id"]["path"],
        )
        # Losslessness: every coding is kept as rows, never picked at ingest.
        unnests = [
            block.get("forEachOrNull")
            for block in view["select"][1:]
        ]
        self.assertIn("code.coding", unnests)

    def test_specimen_gap_fill_feeds_turnaround_calculation(self):
        view = json.loads(
            (ANALYTICS / "config/views/specimen_flat_view.json").read_text()
        )
        scalar_columns = {
            column["name"]: column for column in view["select"][0]["column"]
        }
        self.assertEqual("receivedTime", scalar_columns["received_at"]["path"])
        self.assertEqual("dateTime", scalar_columns["received_at"]["type"])


class SeedContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = (
            ANALYTICS / "openelis/seed-openelis-3.2.1.sql"
        ).read_text()
        cls.cohort_seed = (
            ANALYTICS / "openelis/seed-catalyst-cohort-v1.sql"
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

    def test_cohort_seed_supplies_exportable_test_terminology(self):
        mappings = {
            "b50d156e-0f6f-40cd-921c-4e831602a623": "25836-8",
            "a6718123-8d56-4103-9bbe-26b19306b83d": "24467-3",
            "614652de-5e04-4fe7-a897-77d976317d2b": "8123-2",
            "466b3775-e117-4268-92a7-3d3de95d43b3": "718-7",
            "17ff4ca7-b8b6-44a1-bae0-97f38affc35c": "777-3",
            "e08bdd35-b7e4-4910-ae73-da5b6447e901": "6690-2",
            "d7f672c4-52ea-4c26-bdf0-e9527d2ba95f": "2160-0",
            "3a3661a1-a166-4590-90bc-937912789739": "1742-6",
            "8410a83b-d09a-475d-a71c-1fcbcca94e58": "2345-7",
        }
        for test_guid, loinc in mappings.items():
            self.assertIn(f"('{test_guid}', '{loinc}')", self.cohort_seed)
        self.assertIn(
            "AND NULLIF(btrim(test.loinc), '') IS NULL",
            self.cohort_seed,
        )
        self.assertIn(
            "Catalyst fixture test lacks an exportable LOINC code",
            self.cohort_seed,
        )
        self.assertIn(
            "Catalyst fixture test has a conflicting LOINC code",
            self.cohort_seed,
        )

    def test_backfill_waits_for_every_resource_contract(self):
        self.assertIn("/OEToFhir", self.backfill)
        self.assertIn("checkAll=true", self.backfill)
        self.assertIn("waitForResults=true", self.backfill)
        self.assertIn("HAPI_CLIENT_P12", self.backfill)
        self.assertIn("if not raw:", self.backfill)
        self.assertIn("checking HAPI state", self.backfill)
        for resource, count in {
            "Patient": 1,
            "Observation": 3,
            "ServiceRequest": 3,
            "Specimen": 3,
            "DiagnosticReport": 3,
        }.items():
            self.assertRegex(self.backfill, rf'wait_for_resource "{resource}" {count}\b')


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
