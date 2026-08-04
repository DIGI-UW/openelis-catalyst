"""Execute the curated OpenELIS SQL against a real PostgreSQL and assert its
semantics: the per-coding cross product collapses to one row per observation,
the LOINC coding wins the test_* pivot, and turnaround math joins exactly one
specimen. tests/analytics/test_contracts.py only checks the SQL's text shape;
this is the guard that goes red if the GROUP BY/FILTER/DISTINCT logic breaks.

Needs a reachable PostgreSQL (skips otherwise): set CATALYST_ANALYTICS_TEST_DSN,
or have the catalyst-mvp analytics-db container up (localhost:15443). A scratch
database is created and dropped around the run; seed tables mirror the
fhir-data-pipes sink column types exactly.
"""

import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FACT_SQL = ROOT / "analytics" / "sql" / "001_analytics_v1.sql"

DEFAULT_DSN = (
    "postgresql://catalyst_analytics_writer:demo-only-change-me"
    "@localhost:15443/catalyst_analytics"
)
SCRATCH_DB = "catalyst_fact_semantics_test"

# Mirrors the fhir-data-pipes sink schema (information_schema, live sink).
SEED_DDL = """
CREATE TABLE public.observation_flat (
    id varchar, patient_id varchar, encounter_id varchar, status varchar,
    obs_date timestamptz, val_quantity numeric, val_quantity_unit varchar,
    val_quantity_system varchar, val_quantity_code varchar, issued timestamptz,
    service_request_id varchar, specimen_id varchar, code_code varchar,
    code_sys varchar, code_display varchar, value_code varchar,
    value_sys varchar, value_display varchar
);
CREATE TABLE public.specimen_flat (
    id varchar, patient_id varchar, accession_number varchar,
    specimen_status varchar, collected_at timestamptz, received_at timestamptz,
    type_code varchar, type_sys varchar, type_display varchar
);
CREATE TABLE public.service_request_flat (
    id varchar, patient_id varchar, specimen_id varchar, request_status varchar,
    request_intent varchar, authored_at timestamptz, code_code varchar,
    code_sys varchar, code_display varchar
);
"""

LOINC = "http://loinc.org"
LOCAL = "https://openelis.org/tests"

SEED_ROWS = """
-- obs-1: two coding rows (LOINC + local) for the same observation.
INSERT INTO public.observation_flat
    (id, patient_id, status, obs_date, val_quantity, val_quantity_unit,
     issued, service_request_id, specimen_id, code_code, code_sys, code_display)
VALUES
    ('obs-1', 'p1', 'final', '2026-07-01T11:00:00Z', 1000, 'copies/mL',
     '2026-07-01T12:00:00Z', 'sr-1', 'spec-1',
     '25836-8', 'http://loinc.org', 'HIV-1 Viral Load'),
    ('obs-1', 'p1', 'final', '2026-07-01T11:00:00Z', 1000, 'copies/mL',
     '2026-07-01T12:00:00Z', 'sr-1', 'spec-1',
     'VL-1', 'https://openelis.org/tests', 'Viral Load (local)');
-- obs-2: local-only coding and no specimen.
INSERT INTO public.observation_flat
    (id, patient_id, status, obs_date, val_quantity, val_quantity_unit,
     issued, code_code, code_sys, code_display)
VALUES
    ('obs-2', 'p2', 'final', '2026-07-02T09:00:00Z', 13.5, 'g/dL',
     '2026-07-02T10:00:00Z',
     'HB-1', 'https://openelis.org/tests', 'Hemoglobin (local)');
-- spec-1: two type-coding rows for one specimen (the join fan-out trap).
INSERT INTO public.specimen_flat
    (id, patient_id, received_at, type_code, type_sys, type_display)
VALUES
    ('spec-1', 'p1', '2026-07-01T10:00:00Z', '122555007', 'sct', 'Venous blood'),
    ('spec-1', 'p1', '2026-07-01T10:00:00Z', 'PLAS', 'v2', 'Plasma');
-- sr-1: two coding rows for one request.
INSERT INTO public.service_request_flat
    (id, patient_id, specimen_id, request_status, request_intent, authored_at,
     code_code, code_sys, code_display)
VALUES
    ('sr-1', 'p1', 'spec-1', 'completed', 'order', '2026-07-01T09:00:00Z',
     '25836-8', 'http://loinc.org', 'HIV-1 Viral Load'),
    ('sr-1', 'p1', 'spec-1', 'completed', 'order', '2026-07-01T09:00:00Z',
     'VL-1', 'https://openelis.org/tests', 'Viral Load (local)');
"""


def _connect(dsn, **kwargs):
    import psycopg

    return psycopg.connect(dsn, **kwargs)


class FactViewSemanticsTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        try:
            import psycopg  # noqa: F401
        except ImportError:  # pragma: no cover - environment-specific
            raise unittest.SkipTest("psycopg is not installed")
        cls.admin_dsn = os.environ.get("CATALYST_ANALYTICS_TEST_DSN", DEFAULT_DSN)
        try:
            admin = _connect(cls.admin_dsn, autocommit=True, connect_timeout=3)
        except Exception as error:  # pragma: no cover - environment-specific
            raise unittest.SkipTest(f"PostgreSQL is not reachable: {error}")
        with admin:
            admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH_DB}")
            admin.execute(f"CREATE DATABASE {SCRATCH_DB}")
        cls.scratch_dsn = cls.admin_dsn.rsplit("/", 1)[0] + f"/{SCRATCH_DB}"
        cls.conn = _connect(cls.scratch_dsn)
        cls.conn.execute(SEED_DDL)
        cls.conn.execute(SEED_ROWS)
        cls.conn.execute(FACT_SQL.read_text())
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        if not hasattr(cls, "conn"):
            return
        cls.conn.close()
        with _connect(cls.admin_dsn, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH_DB}")

    def _rows(self, sql):
        with self.conn.cursor() as cursor:
            cursor.execute(sql)
            columns = [d.name for d in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def test_service_request_compat_view_is_one_row_per_request(self):
        rows = self._rows("SELECT * FROM public.service_request_flat_v1")
        self.assertEqual(len(rows), 1)
        (request,) = rows
        self.assertEqual(request["id"], "sr-1")
        self.assertEqual(request["patient_id"], "p1")
        self.assertEqual(request["specimen_id"], "spec-1")
        self.assertEqual(request["test_code_system"], LOINC)
        self.assertEqual(request["test_code"], "25836-8")
        self.assertEqual(request["test_name"], "HIV-1 Viral Load")

    def test_fact_collapses_codings_pivots_loinc_and_joins_one_specimen(self):
        rows = self._rows(
            "SELECT * FROM analytics.lab_result_fact_v1 ORDER BY observation_id"
        )
        # One row per observation despite 2 coding rows and 2 specimen
        # type-coding rows for obs-1 (no cross-product fan-out).
        self.assertEqual([row["observation_id"] for row in rows], ["obs-1", "obs-2"])

        obs1, obs2 = rows
        # The LOINC coding wins the pivot.
        self.assertEqual(obs1["test_code_system"], LOINC)
        self.assertEqual(obs1["test_code"], "25836-8")
        self.assertEqual(obs1["test_name"], "HIV-1 Viral Load")
        # Local-only coding: no LOINC columns, display falls back via COALESCE.
        self.assertIsNone(obs2["test_code_system"])
        self.assertIsNone(obs2["test_code"])
        self.assertEqual(obs2["test_name"], "Hemoglobin (local)")
        # Turnaround: Specimen.receivedTime 10:00 -> Observation.issued 12:00.
        self.assertEqual(obs1["specimen_received_at"].isoformat(), "2026-07-01T10:00:00+00:00")
        self.assertEqual(float(obs1["receipt_to_release_minutes"]), 120.0)
        self.assertIsNone(obs2["specimen_received_at"])
        self.assertIsNone(obs2["receipt_to_release_minutes"])
