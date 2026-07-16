from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.catalyst.analytics import PostgresAnalyticsAdapter
from src.catalyst.catalog import Catalog


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
    assert fields["specimen_received_at"]["type"] == "date-time"
    assert fields["receipt_to_release_minutes"]["type"] == "decimal"
    assert catalog.freshness == {}


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
