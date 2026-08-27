import asyncio
import json
from datetime import datetime, timezone
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.catalyst.analytics import AnalyticsError, SqlAnalyticsAdapter
from tests.fixture_dialect import FIXTURE
from src.catalyst.catalog import Catalog
from src.catalyst.policy import QueryInvariantError, validate_query_invariants
from src.catalyst.query_lint import lint_candidate
from src.catalyst.request import build_query_request


ROOT = Path(__file__).resolve().parents[2]
# The relation live discovery reports for this source. It is written out
# here rather than loaded from a generated catalog file, because the
# generated catalog is exactly what the connection now replaces.
DISCOVERED_RELATION = {'name': 'analytics.lab_result_fact_v1',
 'relationType': 'view',
 'unqualifiedVisible': False,
 'grain': 'Exactly one row per FHIR Observation, with at most one Specimen '
          'matched by resource key. Built over the lossless default '
          'projections: the per-coding cross product is collapsed per '
          'observation and the LOINC coding pivoted into the test_* '
          'columns.',
 'fields': [{'name': 'observation_id',
             'type': 'string',
             'databaseType': 'string',
             'description': 'FHIR Observation resource identifier and '
                            'stable row identity for the laboratory '
                            'result.',
             'nullable': True},
            {'name': 'patient_id',
             'type': 'string',
             'databaseType': 'string',
             'description': 'FHIR Patient resource identifier referenced '
                            'by the observation.',
             'nullable': True},
            {'name': 'service_request_id',
             'type': 'string',
             'databaseType': 'string',
             'description': 'FHIR ServiceRequest resource identifier '
                            'referenced by the observation.',
             'nullable': True},
            {'name': 'specimen_id',
             'type': 'string',
             'databaseType': 'string',
             'description': 'FHIR Specimen resource identifier referenced '
                            'by the observation.',
             'nullable': True},
            {'name': 'result_status',
             'type': 'string',
             'databaseType': 'string',
             'description': 'FHIR Observation status for the laboratory '
                            'result.',
             'nullable': True},
            {'name': 'observed_at',
             'type': 'date-time',
             'databaseType': 'timestamp',
             'description': 'FHIR Observation effective date and time used '
                            'to place the result clinically.',
             'nullable': True},
            {'name': 'issued_at',
             'type': 'date-time',
             'databaseType': 'timestamp',
             'description': 'FHIR Observation issued instant.',
             'nullable': True},
            {'name': 'test_code_system',
             'type': 'string',
             'databaseType': 'string',
             'description': 'Coding-system URI associated with the '
                            'observation test code.',
             'nullable': True},
            {'name': 'test_code',
             'type': 'string',
             'databaseType': 'string',
             'description': 'OpenELIS/FHIR test code for the observation.',
             'nullable': True},
            {'name': 'test_name',
             'type': 'string',
             'databaseType': 'string',
             'description': 'OpenELIS test display name. A question naming '
                            'an analyte must constrain this field rather '
                            'than assume the view contains only that '
                            'analyte.',
             'nullable': True},
            {'name': 'result_value',
             'type': 'decimal',
             'databaseType': 'decimal',
             'description': 'Numeric FHIR Quantity value; do not aggregate '
                            'across unlike units.',
             'nullable': True},
            {'name': 'result_unit',
             'type': 'string',
             'databaseType': 'string',
             'description': 'FHIR Quantity display unit.',
             'nullable': True},
            {'name': 'result_unit_system',
             'type': 'string',
             'databaseType': 'string',
             'description': 'Coding-system URI associated with the FHIR '
                            'Quantity unit.',
             'nullable': True},
            {'name': 'result_unit_code',
             'type': 'string',
             'databaseType': 'string',
             'description': 'Machine-readable FHIR Quantity unit code.',
             'nullable': True},
            {'name': 'specimen_received_at',
             'type': 'date-time',
             'databaseType': 'timestamp',
             'description': 'FHIR Specimen received date and time when a '
                            'matching specimen is available.',
             'nullable': True},
            {'name': 'receipt_to_release_minutes',
             'type': 'decimal',
             'databaseType': 'decimal',
             'description': 'Elapsed minutes from Specimen.receivedTime to '
                            'Observation.issued.',
             'nullable': True}]}


def _base_catalog() -> Catalog:
    """A source's catalog as the connection reports it.

    Live discovery is authoritative, so the catalog starts empty from
    configuration and is filled by what the connection exposes.
    """
    return Catalog.for_source(
        data_source="openelis-analytics", dialect="fixture"
    ).with_discovered_relations([DISCOVERED_RELATION])



def test_discovered_relations_expand_catalog_and_keep_curated_semantics():
    base = _base_catalog()
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
    assert expanded.catalog_version.startswith("live+schema.")
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
    # Curated unit linkage is not something a connection reports.
    assert fact["grain"].startswith("Exactly one row per FHIR Observation")
    # The connection can read both relations, so the writer sees both, and
    # discovery does not rank or hide either of them.
    assert [view["name"] for view in expanded.request_catalog()["views"]] == [
        "analytics.lab_result_fact_v1",
        "public.patient_flat_v1",
    ]


def test_discovery_makes_every_readable_relation_available_to_the_writer():
    """PostgreSQL grants, not catalog curation, define the query surface."""
    base = _base_catalog()
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

    expected = {curated, "public.raw_cross_product_flat"}
    assert expanded.relation_names == expected
    # ``approvedViews`` is the legacy request-contract field name. At runtime it
    # contains every relation the configured role can read.
    assert expanded.approved_view_names == expected
    assert {view["name"] for view in expanded.request_catalog()["views"]} == expected

    # A refresh keeps deriving the surface from the database instead of
    # freezing the catalog file's original relation set.
    assert expanded.with_discovered_relations(relations).approved_view_names == expected

    request = build_query_request(
        "Show the new readable relation",
        expanded,
        max_rows=100,
        statement_timeout_ms=5000,
        request_id="request-readable-relation",
        trace_id="trace-readable-relation",
    )
    findings = lint_candidate(
        {
            "status": "ready",
            "sql": "SELECT id FROM public.raw_cross_product_flat LIMIT 100",
            "parameters": [],
            "expectedColumns": [
                {"name": "id", "logicalType": "string", "nullable": False}
            ],
        },
        request["catalystQuery"],
    )
    assert not [
        finding for finding in findings if finding["code"] == "catalog.unapproved_view"
    ]


def test_discovery_succeeds_when_only_an_uncurated_relation_is_readable():
    """A missing curated relation must not make a readable database unusable."""
    base = _base_catalog()
    expanded = base.with_discovered_relations(
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

    assert expanded.approved_view_names == {"public.something_else"}
    assert [view["name"] for view in expanded.request_catalog()["views"]] == [
        "public.something_else"
    ]


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

    adapter = SqlAnalyticsAdapter(
        "fixture://analytics",
        dialect=FIXTURE,
        data_source_id=catalog.data_source,
        connect=lambda *args, **kwargs: Connection(),
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
        "dialect": "fixture",
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


