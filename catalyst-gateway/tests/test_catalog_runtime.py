from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.catalyst.analytics import PostgresAnalyticsAdapter
from src.catalyst.catalog import Catalog
from src.catalyst.policy import QueryInvariantError, validate_query_invariants
from src.catalyst.request import build_query_request


ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "analytics" / "catalog" / "analytics-catalog-v1.json"


def test_checked_in_analytics_catalog_is_the_gateway_context():
    catalog = Catalog.load(CATALOG_PATH)

    assert catalog.data_source == "openelis-fhir-postgresql"
    assert catalog.catalog_version == "analytics-catalog-v1"
    assert catalog.context_source_id == "catalog:analytics-catalog-v1"
    assert catalog.approved_view_names == {"analytics.lab_result_fact_v1"}
    fields = {
        field["name"]: field
        for field in catalog.request_catalog()["views"][0]["fields"]
    }
    assert set(fields) == {
        "patient_id",
        "test_code",
        "test_name",
        "result_value",
        "result_unit",
        "issued_at",
        "receipt_to_release_minutes",
        "observed_at",
    }
    assert fields["issued_at"]["type"] == "date-time"
    assert fields["receipt_to_release_minutes"]["type"] == "decimal"
    semantic_dimension = catalog.request_catalog()["views"][0]["semanticDimensions"][0]
    assert semantic_dimension["field"] == "test_name"
    assert {value["canonical"] for value in semantic_dimension["values"]} == {
        "Viral Load",
        "CD4 absolute count",
        "CD4 percent  (%)",
        "Hemoglobin",
        "Platelets",
        "White Blood Cells Count (WBC)",
        "Creatinine",
        "GPT/ALAT",
        "Glucose",
    }
    assert catalog.freshness == {}


def _viral_load_request(catalog: Catalog) -> dict:
    return build_query_request(
        "Show viral load results since 2026-01-01",
        catalog,
        max_rows=100,
        statement_timeout_ms=5000,
        request_id="request-1",
        trace_id="trace-1",
    )


def _viral_load_query(catalog: Catalog, sql: str, parameters: list[dict]) -> dict:
    return {
        "status": "ready",
        "question": "Show viral load results since 2026-01-01",
        "target": {
            **catalog.request_target(),
            "approvedViews": ["analytics.lab_result_fact_v1"],
        },
        "sql": sql,
        "parameters": parameters,
        "provenance": {
            "contextSourceIds": [catalog.context_source_id],
        },
    }


def test_named_analyte_requires_its_canonical_catalog_predicate():
    catalog = Catalog.load(CATALOG_PATH)
    query = _viral_load_query(
        catalog,
        (
            "SELECT patient_id FROM analytics.lab_result_fact_v1 "
            "WHERE observed_at >= :start_date"
        ),
        [
            {
                "name": "start_date",
                "type": "date",
                "source": "question",
                "value": "2026-01-01",
            }
        ],
    )

    with pytest.raises(QueryInvariantError) as error:
        validate_query_invariants(query, _viral_load_request(catalog))

    assert {violation.code for violation in error.value.violations} == {
        "missing_semantic_filter"
    }


def test_named_analyte_accepts_its_canonical_catalog_predicate():
    catalog = Catalog.load(CATALOG_PATH)
    query = _viral_load_query(
        catalog,
        (
            "SELECT patient_id FROM analytics.lab_result_fact_v1 "
            "WHERE test_name = :test_name AND observed_at >= :start_date"
        ),
        [
            {
                "name": "test_name",
                "type": "string",
                "source": "question",
                "value": "Viral Load",
            },
            {
                "name": "start_date",
                "type": "date",
                "source": "question",
                "value": "2026-01-01",
            },
        ],
    )

    validate_query_invariants(query, _viral_load_request(catalog))


@pytest.mark.asyncio
async def test_postgres_adapter_reads_latest_succeeded_freshness_live():
    calls = []
    watermark = datetime(2026, 3, 15, 9, tzinfo=timezone.utc)

    class Cursor:
        description = [
            SimpleNamespace(name="pipeline_run_id"),
            SimpleNamespace(name="completion_state"),
            SimpleNamespace(name="source_watermark"),
            SimpleNamespace(name="observed_lag_seconds"),
        ]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchone(self):
            return ("full-20260716T000000Z", "succeeded", watermark, 60)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def cursor(self):
            return Cursor()

    adapter = PostgresAnalyticsAdapter(
        "postgresql://analytics",
        connect=lambda *args, **kwargs: Connection(),
    )
    freshness = await adapter.freshness()

    assert calls[0][0] == "SET TRANSACTION READ ONLY"
    assert "analytics.pipeline_freshness_v1" in calls[1][0]
    assert freshness == {
        "sourceWatermark": "2026-03-15T09:00:00Z",
        "pipelineRunId": "full-20260716T000000Z",
        "completionState": "complete",
        "observedLagSeconds": 60,
    }
