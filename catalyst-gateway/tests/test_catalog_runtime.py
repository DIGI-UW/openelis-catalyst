import asyncio
import json
from datetime import datetime, timezone
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.catalyst.analytics import AnalyticsError, PostgresAnalyticsAdapter
from src.catalyst.catalog import Catalog
from src.catalyst.policy import QueryInvariantError, validate_query_invariants
from src.catalyst.request import build_query_request


ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "analytics" / "catalog" / "analytics-catalog-v1.json"


def test_checked_in_analytics_catalog_is_the_gateway_context():
    catalog = Catalog.load(CATALOG_PATH)

    assert catalog.data_source == "openelis-fhir-postgresql"
    assert catalog.catalog_version == "analytics-catalog-v1"
    assert catalog.schema_version == "analytics-v1"
    assert catalog.context_source_id == "catalog:analytics-catalog-v1"
    assert catalog.approved_view_names == {"analytics.lab_result_fact_v1"}
    request_fields = catalog.request_catalog()["views"][0]["fields"]
    assert [field["name"] for field in request_fields] == [
        "observation_id",
        "patient_id",
        "service_request_id",
        "specimen_id",
        "result_status",
        "observed_at",
        "issued_at",
        "test_code_system",
        "test_code",
        "test_name",
        "result_value",
        "result_unit",
        "result_unit_system",
        "result_unit_code",
        "specimen_received_at",
        "receipt_to_release_minutes",
    ]
    fields = {field["name"]: field for field in request_fields}
    assert fields["issued_at"]["type"] == "date-time"
    assert fields["receipt_to_release_minutes"]["type"] == "decimal"
    assert fields["observation_id"]["description"].startswith("FHIR Observation")
    assert all("nullable" not in field for field in request_fields)
    assert all("unitColumn" not in field for field in request_fields)

    editor_fields = {field["name"]: field for field in catalog.views[0]["fields"]}
    assert all(field["nullable"] is True for field in editor_fields.values())
    assert editor_fields["result_value"]["unitColumn"] == "result_unit"
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


def test_discovered_relations_expand_catalog_and_keep_curated_semantics():
    base = Catalog.load(CATALOG_PATH)
    fact_fields = deepcopy(base.views[0]["fields"])
    for field in fact_fields:
        field["description"] = "Database-derived description"
        field["databaseType"] = "text"
    relations = [
        {
            "name": "public.patient_flat_v1",
            "relationType": "table",
            "unqualifiedVisible": True,
            "grain": "Rows readable from public.patient_flat_v1 (table)",
            "fields": [
                {
                    "name": "patient_id",
                    "type": "string",
                    "databaseType": "text",
                    "description": "FHIR Patient identifier",
                    "nullable": False,
                }
            ],
        },
        {
            "name": "analytics.lab_result_fact_v1",
            "relationType": "view",
            "grain": "Database-derived grain",
            "fields": fact_fields,
        },
    ]

    expanded = base.with_discovered_relations(relations)
    repeated = base.with_discovered_relations(list(reversed(relations)))

    assert expanded.catalog_version == repeated.catalog_version
    assert expanded.catalog_version.startswith("analytics-catalog-v1+schema.")
    assert expanded.context_source_id == f"catalog:{expanded.catalog_version}"
    assert expanded.relation_names == {
        "analytics.lab_result_fact_v1",
        "public.patient_flat_v1",
    }
    assert expanded.available_relation_names == {
        "analytics.lab_result_fact_v1",
        "public.patient_flat_v1",
        "patient_flat_v1",
    }
    fact = next(
        view
        for view in expanded.views
        if view["name"] == "analytics.lab_result_fact_v1"
    )
    fields = {field["name"]: field for field in fact["fields"]}
    assert fields["observation_id"]["description"].startswith("FHIR Observation")
    assert fields["result_value"]["unitColumn"] == "result_unit"
    assert fact["grain"].startswith("Exactly one row per FHIR Observation")
    assert fact["semanticDimensions"][0]["field"] == "test_name"
    # Discovery describes the table, so the browser and the existence check can
    # see it -- but describing it is not approving it, and the request catalog
    # is what the writer reads. This assertion used to expect the discovered
    # table here, which is exactly how an unapproved relation reached the model.
    assert [view["name"] for view in expanded.request_catalog()["views"]] == [
        "analytics.lab_result_fact_v1"
    ]


def test_discovery_describes_new_relations_without_approving_them():
    """A readable table is not a query surface.

    ``with_discovered_relations`` re-describes every relation the PostgreSQL role
    can read. That is right for the dataset browser and for the "does this
    relation exist" check, and wrong for approval: before this was separated, a
    raw table in the same database silently became something a generated query
    was allowed to join, and the linter -- which derives its allowlist from the
    same request catalog -- had no reason to object.
    """
    base = Catalog.load(CATALOG_PATH)
    curated = "analytics.lab_result_fact_v1"
    relations = [
        {
            "name": curated,
            "relationType": "view",
            "grain": "Database-derived grain",
            "fields": deepcopy(base.views[0]["fields"]),
        },
        {
            "name": "public.raw_cross_product_flat",
            "relationType": "table",
            "grain": "Rows readable from public.raw_cross_product_flat (table)",
            "fields": [
                {
                    "name": "id",
                    "type": "string",
                    "databaseType": "text",
                    "description": "Raw identifier",
                    "nullable": False,
                }
            ],
        },
    ]

    expanded = base.with_discovered_relations(relations)

    # Described, so the browser and the existence check can see it.
    assert "public.raw_cross_product_flat" in expanded.relation_names
    # Not approved, so a generated query may not reference it...
    assert expanded.approved_view_names == {curated}
    # ...and it is not even described to the writer.
    assert [view["name"] for view in expanded.request_catalog()["views"]] == [curated]

    # Approval survives a second discovery pass rather than widening again.
    assert expanded.with_discovered_relations(relations).approved_view_names == {
        curated
    }


def test_discovery_rejects_a_schema_with_no_approved_view_left():
    """A curated view the role cannot read leaves nothing to query.

    Reported here, where the cause is visible, rather than downstream as an
    empty catalog failing the request contract.
    """
    base = Catalog.load(CATALOG_PATH)
    with pytest.raises(ValueError, match="No approved catalog view is readable"):
        base.with_discovered_relations(
            [
                {
                    "name": "public.something_else",
                    "relationType": "table",
                    "grain": "Rows readable from public.something_else (table)",
                    "fields": [
                        {
                            "name": "id",
                            "type": "string",
                            "databaseType": "text",
                            "description": "Raw identifier",
                            "nullable": False,
                        }
                    ],
                }
            ]
        )


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


def _dataset_sql_calls(catalog: Catalog) -> list[str]:
    """Run both dataset-browser queries against a fake driver, return their SQL."""

    calls: list[str] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            calls.append(sql)

        def fetchone(self):
            return (0, 0, 0, None, None, None)

        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def cursor(self):
            return Cursor()

    adapter = PostgresAnalyticsAdapter(
        "postgresql://analytics",
        data_source_id=catalog.data_source,
        connect=lambda *args, **kwargs: Connection(),
        dataset_browser=catalog.dataset_browser,
    )
    asyncio.run(adapter.dataset_overview())
    asyncio.run(
        adapter.dataset_rows(test_name=None, patient_id=None, limit=25, offset=0)
    )
    return calls


def _write_catalog(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _second_source_payload(dataset_browser: dict | None = None) -> dict:
    """A catalog whose fact view shares no column names with OpenELIS."""

    payload: dict = {
        "contractVersion": "catalyst.analytics.catalog.v1",
        "catalogVersion": "second-source-v1",
        "dataSource": "second-source-postgresql",
        "dialect": "postgresql",
        "schemaVersion": "analytics-v1",
        "views": [
            {
                "name": "analytics.encounter_fact_v1",
                "version": "1",
                "approved": True,
                "grain": "one row per encounter",
                "columns": [
                    {
                        "name": "encounter_id",
                        "logicalType": "string",
                        "nullable": False,
                        "description": "Encounter identity",
                    },
                    {
                        "name": "subject_ref",
                        "logicalType": "string",
                        "nullable": False,
                        "description": "Subject reference",
                    },
                    {
                        "name": "concept_label",
                        "logicalType": "string",
                        "nullable": False,
                        "description": "Concept label",
                    },
                    {
                        "name": "started_at",
                        "logicalType": "timestamp",
                        "nullable": False,
                        "description": "Encounter start",
                    },
                    {
                        "name": "measure_numeric",
                        "logicalType": "decimal",
                        "nullable": True,
                        "description": "Numeric measure",
                    },
                    {
                        "name": "measure_text",
                        "logicalType": "string",
                        "nullable": True,
                        "description": "Text measure",
                    },
                ],
            }
        ],
    }
    if dataset_browser is not None:
        payload["datasetBrowser"] = dataset_browser
    return payload


SECOND_SOURCE_BROWSER = {
    "factView": "analytics.encounter_fact_v1",
    "identityColumn": "encounter_id",
    "subjectColumn": "subject_ref",
    "categoryColumn": "concept_label",
    "observedAtColumn": "started_at",
    "valueColumn": "measure_numeric",
    "valueFallbackColumns": ["measure_text"],
}


def test_shipped_openelis_catalog_drives_its_own_dataset_browser_sql():
    catalog = Catalog.load(CATALOG_PATH)

    assert catalog.dataset_browser is not None
    assert catalog.dataset_browser.fact_view == "analytics.lab_result_fact_v1"

    for sql in _dataset_sql_calls(catalog):
        assert "analytics.hiv_observation_fact_v1" not in sql
    joined = "\n".join(_dataset_sql_calls(catalog))
    assert "FROM analytics.lab_result_fact_v1" in joined
    assert "test_name" in joined
    assert "result_value" in joined


def test_second_data_source_never_queries_the_openelis_fact_view(tmp_path):
    """Regression: the dataset browser used to hardcode the OpenELIS view, so
    switching sources failed with `relation "analytics.lab_result_fact_v1"
    does not exist` against the other source's database."""

    catalog = Catalog.load(
        _write_catalog(tmp_path, _second_source_payload(SECOND_SOURCE_BROWSER))
    )

    calls = _dataset_sql_calls(catalog)
    joined = "\n".join(calls)

    assert "analytics.lab_result_fact_v1" not in joined
    assert "test_name" not in joined
    assert "result_value" not in joined
    assert "FROM analytics.encounter_fact_v1" in joined
    assert "concept_label" in joined
    # Non-numeric values still render: the declared fallbacks are coalesced.
    assert "COALESCE(measure_numeric::text, measure_text::text)" in joined
    # Columns this source does not have are reported as NULL, not invented.
    assert "NULL" in joined


def test_catalog_without_dataset_browser_reports_a_configuration_error(tmp_path):
    catalog = Catalog.load(_write_catalog(tmp_path, _second_source_payload()))

    assert catalog.dataset_browser is None
    with pytest.raises(AnalyticsError, match="declares no datasetBrowser"):
        _dataset_sql_calls(catalog)


@pytest.mark.parametrize(
    "mutation, expected",
    (
        ({"factView": "analytics.not_approved_v1"}, "not an approved catalog view"),
        ({"categoryColumn": "no_such_column"}, "outside analytics.encounter_fact_v1"),
        ({"valueFallbackColumns": ["nope"]}, "outside analytics.encounter_fact_v1"),
        ({"identityColumn": 'x"; DROP TABLE y --'}, "plain lowercase SQL identifier"),
    ),
)
def test_dataset_browser_profile_rejects_unusable_declarations(
    tmp_path, mutation, expected
):
    browser = {**SECOND_SOURCE_BROWSER, **mutation}
    with pytest.raises(ValueError, match=expected):
        Catalog.load(_write_catalog(tmp_path, _second_source_payload(browser)))
