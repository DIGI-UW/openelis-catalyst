import asyncio
import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from src import gateway
from src.catalyst.analytics import AnalyticsResult, PostgresAnalyticsAdapter
from src.catalyst.catalog import Catalog
from src.catalyst.contracts import ContractError, ContractRegistry
from src.catalyst.digest import canonical_sha256
from src.catalyst.hub import HubError
from src.catalyst.policy import (
    QueryInvariantError,
    SqlPolicy,
    validate_query_invariants,
)
from src.catalyst.request import build_query_request
from src.catalyst.service import CatalystService
from src.catalyst.storage import PreviewStore
from src.catalyst.table import TableError, build_table
from src.config import load_config


CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"


def catalog() -> Catalog:
    return Catalog(
        data_source="openelis-demo",
        catalog_version="2026.07",
        schema_version="analytics-v1",
        dialect="postgresql",
        context_source_id="catalog:openelis-demo:2026.07",
        views=[
            {
                "name": "analytics.lab_results",
                "version": "1",
                "grain": "one row per result",
                "fields": [
                    {
                        "name": "test_name",
                        "type": "string",
                        "description": "Display name",
                    },
                    {
                        "name": "result_count",
                        "type": "integer",
                        "description": "Result count",
                        "unit": "results",
                    },
                    {
                        "name": "result_date",
                        "type": "date",
                        "description": "Result date",
                    },
                ],
            }
        ],
        freshness={
            "sourceWatermark": "2026-07-15T12:00:00Z",
            "pipelineRunId": "pipeline-42",
            "completionState": "complete",
            "observedLagSeconds": 30,
        },
    )


def ready_query(question: str = "Count tests since July 1") -> dict:
    return {
        "contractVersion": "catalyst.query.v1",
        "deploymentMode": "demo",
        "status": "ready",
        "question": question,
        "target": {
            "dataSource": "openelis-demo",
            "catalogVersion": "2026.07",
            "dialect": "postgresql",
            "approvedViews": ["analytics.lab_results"],
        },
        "sql": (
            "SELECT test_name, COUNT(*) AS result_count "
            "FROM analytics.lab_results "
            "WHERE result_date >= :start_date "
            "GROUP BY test_name LIMIT 2"
        ),
        "parameters": [
            {
                "name": "start_date",
                "type": "date",
                "source": "question",
                "value": "2026-07-01",
            }
        ],
        "expectedColumns": [
            {"name": "test_name", "logicalType": "string", "nullable": False},
            {
                "name": "result_count",
                "logicalType": "integer",
                "nullable": False,
                "unit": "results",
            },
        ],
        "validation": {
            "status": "passed",
            "checks": [{"name": "review", "status": "passed"}],
        },
        "provenance": {
            "profileId": "catalyst-query-gemma-4-12b-q4",
            "traceId": "hub-trace-1",
            "contextSourceIds": ["catalog:openelis-demo:2026.07"],
        },
    }


def non_ready_query(status: str, question: str = "Question") -> dict:
    query = {
        "contractVersion": "catalyst.query.v1",
        "deploymentMode": "demo",
        "status": status,
        "question": question,
        "validation": {
            "status": "warned" if status == "needs_clarification" else "rejected",
            "checks": [{"name": "scope", "status": "warned"}],
        },
        "provenance": {
            "profileId": "catalyst-query-gemma-4-12b-q4",
            "traceId": "hub-trace-1",
            "contextSourceIds": ["catalog:openelis-demo:2026.07"],
        },
    }
    if status == "needs_clarification":
        query["clarification"] = "Which date range?"
    else:
        query["message"] = f"Question is {status}"
    if status == "rejected":
        generated = ready_query(question)
        query["diagnosticCandidate"] = {
            "executable": False,
            "candidate": {
                field: deepcopy(generated[field])
                for field in (
                    "status",
                    "target",
                    "sql",
                    "parameters",
                    "expectedColumns",
                )
            },
            "attempts": [
                {
                    "attempt": 1,
                    "status": "failed",
                    "finding_codes": ["policy.unbound_predicate_literal"],
                    "findings": [
                        {
                            "code": "policy.unbound_predicate_literal",
                            "stage": "query_lint",
                            "severity": "error",
                            "path": "$.sql",
                            "message": "A predicate literal was not bound.",
                        }
                    ],
                }
            ],
        }
    return query


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 16, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class FakeHub:
    def __init__(self, response: dict | None = None, error: HubError | None = None):
        self.response = response
        self.error = error
        self.requests: list[dict] = []
        self.closed = False

    async def generate_query(self, request: dict) -> dict:
        self.requests.append(request)
        if self.error:
            raise self.error
        assert self.response is not None
        return deepcopy(self.response)

    async def list_query_profiles(self) -> list[dict]:
        if self.error:
            raise self.error
        return [
            {
                "id": "catalyst-query-gemma-4-12b-q4",
                "label": "Catalyst governed query — Gemma 4 12B",
                "available": self.error is None,
                "required_models": ["gemma-4-12b-q4"],
                "role_models": {
                    "query_generate": "gemma-4-12b-q4",
                    "query_review": "gemma-4-12b-q4",
                },
                "stages": [
                    "context",
                    "query_generate",
                    "query_review",
                    "query_finalize",
                ],
                "outputContracts": ["catalyst.query.v1"],
            }
        ]

    async def readiness(self) -> dict:
        return {
            "hub": {"ready": self.error is None},
            "queryProfile": {"ready": self.error is None},
            "modelRouter": {"ready": self.error is None},
        }

    async def aclose(self) -> None:
        self.closed = True


class FakeAnalytics:
    def __init__(
        self,
        result: AnalyticsResult | None = None,
        error: BaseException | None = None,
    ):
        self.result = result or AnalyticsResult(
            column_names=["test_name", "result_count"],
            rows=[("HIV viral load", 4)],
            truncated=False,
        )
        self.error = error
        self.calls = 0

    async def execute(self, **kwargs) -> AnalyticsResult:
        self.calls += 1
        if self.error:
            raise self.error
        return self.result

    async def freshness(self) -> dict:
        if self.error:
            raise self.error
        return deepcopy(catalog().freshness)

    async def readiness(self) -> dict:
        return {"ready": self.error is None, "dataSource": "openelis-demo"}

    async def dataset_overview(self) -> dict:
        return {
            "contractVersion": "catalyst.dataset-overview.v1",
            "datasetId": "test-cohort",
            "synthetic": True,
            "patients": 2,
            "results": 4,
            "testTypes": 1,
            "firstObservedAt": "2026-01-01T00:00:00Z",
            "lastObservedAt": "2026-02-01T00:00:00Z",
            "tests": [],
            "exampleQuestions": [],
        }

    async def dataset_rows(self, **kwargs) -> dict:
        return {
            "contractVersion": "catalyst.dataset-rows.v1",
            "total": 0,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
            "rows": [],
        }


def make_service(
    tmp_path: Path,
    response: dict | None = None,
    *,
    hub: FakeHub | None = None,
    analytics: FakeAnalytics | None = None,
    clock: Clock | None = None,
    execution_lease_seconds: int = 60,
) -> tuple[CatalystService, FakeHub, FakeAnalytics, ContractRegistry]:
    registry = ContractRegistry.load(CONTRACTS)
    actual_hub = hub or FakeHub(response or ready_query())
    actual_analytics = analytics or FakeAnalytics()
    store = PreviewStore(
        tmp_path / "previews.sqlite3",
        now=clock,
        execution_lease_seconds=execution_lease_seconds,
    )
    service = CatalystService(
        contracts=registry,
        catalog=catalog(),
        hub=actual_hub,
        analytics=actual_analytics,
        store=store,
        sql_policy=SqlPolicy(max_rows=2),
        max_rows=2,
        statement_timeout_ms=500,
    )
    return service, actual_hub, actual_analytics, registry


def execute_body(preview: dict, key: str = "idem-1") -> dict:
    return {
        "contractVersion": "catalyst.execute.request.v1",
        "previewId": preview["previewId"],
        "queryDigest": preview["queryDigest"],
        "accept": True,
        "idempotencyKey": key,
    }


def test_runtime_schema_is_shared_by_editor_hub_and_gateway_policy(
    tmp_path: Path,
):
    class RuntimeAnalytics(FakeAnalytics):
        async def discover_relations(self) -> list[dict]:
            return [
                {
                    "name": "public.patient_flat_v1",
                    "relationType": "table",
                    "grain": "one row per FHIR Patient",
                    "fields": [
                        {
                            "name": "patient_id",
                            "type": "string",
                            "databaseType": "uuid",
                            "description": "FHIR Patient identifier",
                            "nullable": False,
                        }
                    ],
                }
            ]

    class RuntimeHub(FakeHub):
        async def generate_query(self, request: dict) -> dict:
            self.requests.append(deepcopy(request))
            target = request["catalystQuery"]["target"]
            context_id = request["catalystQuery"]["catalog"]["contextSourceId"]
            return {
                "contractVersion": "catalyst.query.v1",
                "deploymentMode": "demo",
                "status": "ready",
                "question": request["messages"][0]["content"],
                "target": {
                    **target,
                    "approvedViews": ["public.patient_flat_v1"],
                },
                "sql": "SELECT patient_id FROM public.patient_flat_v1 LIMIT 2",
                "parameters": [],
                "expectedColumns": [
                    {
                        "name": "patient_id",
                        "logicalType": "string",
                        "nullable": False,
                    }
                ],
                "validation": {"status": "passed", "checks": []},
                "provenance": {
                    "profileId": "catalyst-query-gemma-4-12b-q4",
                    "traceId": "hub-runtime-schema",
                    "contextSourceIds": [context_id],
                },
            }

    hub = RuntimeHub()
    service, _, _, _ = make_service(
        tmp_path,
        hub=hub,
        analytics=RuntimeAnalytics(
            AnalyticsResult(
                column_names=["patient_id"],
                rows=[("patient-1",)],
                truncated=False,
            )
        ),
    )

    with TestClient(gateway.create_app(catalyst_service=service)) as client:
        editor = client.get("/v1/catalyst/workbench/catalog")
        preview = client.post(
            "/v1/catalyst/queries",
            json={
                "contractVersion": "catalyst.question.request.v1",
                "deploymentMode": "demo",
                "question": "List patients",
            },
        )
        assert preview.status_code == 201, preview.text
        execution = client.post(
            f"/v1/catalyst/previews/{preview.json()['previewId']}/execute",
            json=execute_body(preview.json(), "runtime-schema-execution"),
        )

    assert editor.status_code == 200, editor.text
    editor_body = editor.json()
    assert editor_body["catalogVersion"].startswith("2026.07+schema.")
    assert editor_body["schemas"] == [
        {
            "name": "public",
            "views": [
                {
                    "name": "patient_flat_v1",
                    "qualifiedName": "public.patient_flat_v1",
                    "grain": "one row per FHIR Patient",
                    "relationType": "table",
                    "columns": [
                        {
                            "name": "patient_id",
                            "logicalType": "string",
                            "databaseType": "uuid",
                            "description": "FHIR Patient identifier",
                            "nullable": False,
                        }
                    ],
                }
            ],
        }
    ]
    assert execution.status_code == 200, execution.text
    assert preview.json()["target"]["approvedViews"] == ["public.patient_flat_v1"]
    request = hub.requests[0]["catalystQuery"]
    assert request["target"]["catalogVersion"] == editor_body["catalogVersion"]
    assert [view["name"] for view in request["catalog"]["views"]] == [
        "public.patient_flat_v1"
    ]


def test_loads_and_checks_all_normative_schemas():
    registry = ContractRegistry.load(CONTRACTS)
    assert len(registry.schemas) == 24
    assert set(registry.schemas) == {
        "catalyst-data-sources-v1.schema.json",
        "catalyst-execute-request-v1.schema.json",
        "catalyst-execution-outcome-v1.schema.json",
        "catalyst-policy-outcome-v1.schema.json",
        "catalyst-preview-v1.schema.json",
        "catalyst-query-completion-v1.schema.json",
        "catalyst-query-request-v1.schema.json",
        "catalyst-query-request-v2.schema.json",
        "catalyst-query-revision-context-v1.schema.json",
        "catalyst-query-v1.schema.json",
        "catalyst-question-request-v1.schema.json",
        "catalyst-table-v1.schema.json",
        "catalyst-workbench-execute-request-v1.schema.json",
        "catalyst-workbench-editor-catalog-v1.schema.json",
        "catalyst-workbench-editor-snapshot-v1.schema.json",
        "catalyst-workbench-editor-snapshot-record-v1.schema.json",
        "catalyst-workbench-finding-v1.schema.json",
        "catalyst-workbench-generation-evidence-v1.schema.json",
        "catalyst-workbench-session-request-v1.schema.json",
        "catalyst-workbench-session-v1.schema.json",
        "catalyst-workbench-turn-request-v1.schema.json",
        "catalyst-workbench-turn-timeline-v1.schema.json",
        "catalyst-workbench-turn-v1.schema.json",
        "catalyst-workbench-version-request-v1.schema.json",
    }
    registry.validate(
        "catalyst-workbench-editor-catalog-v1.schema.json",
        {
            "contractVersion": "catalyst.workbench.editor-catalog.v1",
            "catalogVersion": "analytics-catalog-v1",
            "schemaVersion": "analytics-v1",
            "dialect": "postgresql",
            "schemas": [
                {
                    "name": "analytics",
                    "views": [
                        {
                            "name": "lab_result_fact_v1",
                            "qualifiedName": "analytics.lab_result_fact_v1",
                            "grain": "one row per laboratory result",
                            "columns": [
                                {
                                    "name": "observed_at",
                                    "logicalType": "date-time",
                                    "description": "Observation effective time",
                                    "nullable": True,
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    registry.validate(
        "catalyst-question-request-v1.schema.json",
        {
            "contractVersion": "catalyst.question.request.v1",
            "deploymentMode": "demo",
            "question": "Count tests",
        },
    )
    with pytest.raises(ContractError):
        registry.validate(
            "catalyst-question-request-v1.schema.json",
            {
                "contractVersion": "wrong",
                "deploymentMode": "demo",
                "question": "",
            },
        )
    registry.validate(
        "catalyst-workbench-finding-v1.schema.json",
        {
            "contractVersion": "catalyst.workbench.finding.v1",
            "findingId": "finding-" + "0" * 24,
            "ruleCode": "gateway_sql_policy.relation_not_found",
            "severity": "error",
            "stage": "gateway_sql_policy",
            "message": "The relation is not in the readable PostgreSQL schema.",
            "path": "sql",
            "astUnit": None,
            "span": None,
            "evidence": {"relation": "analytics.not_a_view"},
            "suggestedAction": "Refresh the schema and use an available relation.",
            "repairability": "manual",
            "validatorRevision": "catalyst.workbench.validator.v1",
        },
    )
    registry.validate(
        "catalyst-workbench-session-request-v1.schema.json",
        {
            "contractVersion": "catalyst.workbench.session.request.v1",
            "deploymentMode": "demo",
            "question": "Count tests",
            "profileId": "catalyst-query-checked",
        },
    )
    registry.validate(
        "catalyst-workbench-session-v1.schema.json",
        {
            "contractVersion": "catalyst.workbench.session.v1",
            "sessionId": "8a9e8d3e-9e93-4151-bf3d-6fca75430caa",
            "question": "Count tests",
            "profileId": "catalyst-query-checked",
            "datasetId": "openelis-catalyst-demo",
            "datasetVersion": "v1",
            "catalogVersion": "v1",
            "currentVersionId": None,
            "draftSeed": {
                "status": "unresolved",
                "source": "raw_model_output",
                "sql": "SELECT 1",
                "parameters": [],
                "unresolvedPaths": [],
            },
            "browserState": {},
            "provenance": {},
            "status": "active",
            "createdAt": "2026-07-17T00:00:00Z",
            "updatedAt": "2026-07-17T00:00:00Z",
            "versions": [],
            "currentVersion": None,
            "validations": [],
            "latestValidation": None,
            "executions": [],
        },
    )
    registry.validate(
        "catalyst-workbench-version-request-v1.schema.json",
        {
            "contractVersion": "catalyst.workbench.version.request.v1",
            "parentVersionId": "version-1",
            "parentQueryDigest": "0" * 64,
            "sql": "SELECT 1",
            "parameters": [],
        },
    )
    registry.validate(
        "catalyst-workbench-execute-request-v1.schema.json",
        {
            "contractVersion": "catalyst.workbench.execute.request.v1",
            "versionId": "version-1",
            "queryDigest": "0" * 64,
            "idempotencyKey": "manual-1",
        },
    )


@pytest.mark.parametrize(
    ("mutator", "violation"),
    [
        (lambda q: q.update(question="Changed"), "question_mismatch"),
        (
            lambda q: q["target"].update(dataSource="other"),
            "target_mismatch",
        ),
        (
            lambda q: q["target"].update(catalogVersion="other"),
            "target_mismatch",
        ),
        (lambda q: q["target"].update(dialect="duckdb"), "target_mismatch"),
        (
            lambda q: q["parameters"].append(deepcopy(q["parameters"][0])),
            "duplicate_parameter",
        ),
        (lambda q: q.update(parameters=[]), "placeholder_mismatch"),
        (
            lambda q: q["parameters"].append(
                {
                    "name": "extra",
                    "type": "integer",
                    "source": "question",
                    "value": 1,
                }
            ),
            "placeholder_mismatch",
        ),
        (
            lambda q: q["provenance"].update(contextSourceIds=["other"]),
            "context_mismatch",
        ),
    ],
)
def test_runtime_query_invariants_are_strict(mutator, violation: str):
    request = build_query_request(
        "Count tests since July 1",
        catalog(),
        max_rows=2,
        statement_timeout_ms=500,
        request_id="request-1",
        trace_id="trace-1",
    )
    query = ready_query()
    mutator(query)
    with pytest.raises(QueryInvariantError) as error:
        validate_query_invariants(query, request)
    assert violation in {item.code for item in error.value.violations}


def test_runtime_invariants_do_not_treat_model_relation_names_as_a_whitelist():
    request = build_query_request(
        "Count tests since July 1",
        catalog(),
        max_rows=2,
        statement_timeout_ms=500,
        request_id="request-1",
        trace_id="trace-1",
    )
    query = ready_query()
    query["target"]["approvedViews"] = ["public.patient_flat_v1"]

    validate_query_invariants(query, request)


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DELETE FROM analytics.lab_results", "operation_not_allowed"),
        (
            "SELECT * FROM analytics.lab_results; SELECT 1",
            "multiple_statements",
        ),
        ("SELECT * FROM private.results", "relation_not_found"),
        ("SELECT * FROM analytics.lab_results LIMIT 3", "row_limit_exceeded"),
        (
            "SELECT * INTO analytics.copy FROM analytics.lab_results",
            "operation_not_allowed",
        ),
    ],
)
def test_sql_policy_rejects_unsafe_postgresql(sql: str, code: str):
    query = ready_query()
    query["sql"] = sql
    query["parameters"] = []
    violations = SqlPolicy(max_rows=2).evaluate(
        query,
        available_relations={"analytics.lab_results"},
    )
    assert code in {item.code for item in violations}


def test_sql_policy_accepts_one_parameterized_select():
    violations = SqlPolicy(max_rows=2).evaluate(
        ready_query(),
        available_relations={"analytics.lab_results"},
    )
    assert violations == []


def test_sql_policy_only_accepts_unqualified_relations_visible_on_search_path():
    query = ready_query()
    query["sql"] = "SELECT * FROM lab_results"
    query["parameters"] = []

    missing = SqlPolicy(max_rows=2).evaluate(
        query,
        available_relations={"analytics.lab_results"},
    )
    visible = SqlPolicy(max_rows=2).evaluate(
        query,
        available_relations={"analytics.lab_results", "lab_results"},
    )

    assert {item.code for item in missing} == {"relation_not_found"}
    assert visible == []


def test_sql_policy_does_not_hide_qualified_relation_matching_cte_name():
    query = ready_query()
    query["sql"] = "WITH x AS (SELECT * FROM private.x) SELECT * FROM x"
    query["parameters"] = []

    violations = SqlPolicy(max_rows=2).evaluate(
        query,
        available_relations={"public.x"},
    )

    assert {item.code for item in violations} == {"relation_not_found"}


def test_sql_policy_does_not_use_relations_as_a_security_whitelist():
    query = ready_query()
    query["sql"] = "SELECT * FROM any_schema.any_relation"
    query["parameters"] = []

    violations = SqlPolicy(max_rows=2).evaluate(query)

    assert violations == []


def test_sql_policy_allows_cte_rank_filter_literal_for_manual_iteration():
    query = ready_query()
    query["sql"] = (
        "WITH ranked AS ("
        "SELECT *, ROW_NUMBER() OVER (PARTITION BY test_name "
        "ORDER BY result_date DESC) AS rn FROM analytics.lab_results"
        ") SELECT * FROM ranked WHERE rn = 1"
    )
    query["parameters"] = []

    violations = SqlPolicy(max_rows=2).evaluate(
        query,
        available_relations={"analytics.lab_results"},
    )

    assert violations == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({}, "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"),
        (
            {"b": 2, "a": 1},
            "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777",
        ),
    ],
)
def test_rfc8785_sha256_vectors(value: dict, expected: str):
    assert canonical_sha256(value) == expected


def test_preview_store_is_transactional_idempotent_and_does_not_expire(tmp_path: Path):
    clock = Clock()
    store = PreviewStore(tmp_path / "state.sqlite3", now=clock)
    preview = store.create_preview(ready_query())

    claim = store.begin_execution(
        preview["previewId"],
        preview["queryDigest"],
        "same-key",
    )
    assert claim.action == "execute"
    assert claim.preview == preview

    active = store.begin_execution(
        preview["previewId"],
        preview["queryDigest"],
        "same-key",
    )
    assert active.status_code == 202
    assert active.body["status"] == "in_progress"
    assert active.body["replayed"] is True

    conflict = store.begin_execution(
        preview["previewId"],
        preview["queryDigest"],
        "different-key",
    )
    assert conflict.status_code == 409
    assert conflict.body["errorCode"] == "idempotency_conflict"

    table = {"contractVersion": "catalyst.table.v1", "marker": "stored"}
    store.finish_success(preview["previewId"], "same-key", table)
    replay = store.begin_execution(
        preview["previewId"],
        preview["queryDigest"],
        "same-key",
    )
    assert replay.status_code == 200
    assert replay.body == table

    delayed = store.create_preview(ready_query())
    clock.advance(60 * 60 * 24 * 365)
    accepted = store.begin_execution(
        delayed["previewId"],
        delayed["queryDigest"],
        "delayed-key",
    )
    assert accepted.action == "execute"

    missing = store.begin_execution("unknown", "digest", "key")
    assert missing.status_code == 404
    assert missing.body["status"] == "not_found"
    assert store.poll("unknown", "key").status_code == 404


def test_preview_store_replays_failure_and_poll_does_not_execute(tmp_path: Path):
    store = PreviewStore(tmp_path / "state.sqlite3")
    preview = store.create_preview(ready_query())
    claim = store.begin_execution(preview["previewId"], preview["queryDigest"], "key")
    assert claim.action == "execute"
    failed = store.finish_failure(preview["previewId"], "key", "database down")
    assert failed["status"] == "failed"

    replay = store.poll(preview["previewId"], "key")
    assert replay.status_code == 502
    assert replay.body["status"] == "failed"
    assert replay.body["replayed"] is True
    unknown_pair = store.poll(preview["previewId"], "other")
    assert unknown_pair.status_code == 404


def test_preview_store_terminates_a_stale_execution_lease(tmp_path: Path):
    clock = Clock()
    store = PreviewStore(
        tmp_path / "state.sqlite3",
        now=clock,
        execution_lease_seconds=5,
    )
    preview = store.create_preview(ready_query())
    store.begin_execution(preview["previewId"], preview["queryDigest"], "lease-key")

    clock.advance(6)
    stale = store.begin_execution(
        preview["previewId"],
        preview["queryDigest"],
        "lease-key",
    )

    assert stale.status_code == 502
    assert stale.body["status"] == "failed"
    assert stale.body["errorCode"] == "execution_failed"
    assert "lease expired" in stale.body["message"].lower()
    assert store.poll(preview["previewId"], "lease-key").status_code == 502


def test_table_builder_tags_types_empty_and_truncated(tmp_path: Path):
    query = ready_query()
    query["expectedColumns"] = [
        {"name": "text", "logicalType": "string", "nullable": False},
        {"name": "count", "logicalType": "integer", "nullable": False},
        {"name": "ratio", "logicalType": "decimal", "nullable": False},
        {"name": "flag", "logicalType": "boolean", "nullable": False},
        {"name": "day", "logicalType": "date", "nullable": False},
        {"name": "at", "logicalType": "date-time", "nullable": True},
    ]
    store = PreviewStore(tmp_path / "state.sqlite3")
    preview = store.create_preview(query)
    result = AnalyticsResult(
        column_names=["text", "count", "ratio", "flag", "day", "at"],
        rows=[
            (
                "HIV",
                2,
                Decimal("1.2300"),
                True,
                date(2026, 7, 15),
                datetime(2026, 7, 15, 12, tzinfo=timezone.utc),
            )
        ],
        truncated=True,
        truncation_reason="query_limit_reached",
    )
    table = build_table(
        preview=preview,
        query=query,
        result=result,
        freshness=catalog().freshness,
        accepted_at="2026-07-16T00:00:00Z",
        duration_ms=4,
        statement_timeout_ms=500,
        max_rows=1,
        catalyst_trace_id="trace-1",
    )
    assert table["table"]["rows"][0] == [
        {"type": "string", "value": "HIV"},
        {"type": "integer", "value": 2},
        {"type": "decimal", "value": "1.2300"},
        {"type": "boolean", "value": True},
        {"type": "date", "value": "2026-07-15"},
        {"type": "date-time", "value": "2026-07-15T12:00:00Z"},
    ]
    assert table["table"]["rowCount"] == {
        "returned": 1,
        "total": None,
        "totalIsExact": False,
        "truncated": True,
        "limit": 1,
    }
    assert table["source"]["freshness"]["pipelineRunId"] == "pipeline-42"
    assert table["provenance"]["hubTraceId"] == "hub-trace-1"
    assert table["warnings"] == [
        "Result reached the SQL row limit; additional matching rows may exist. "
        "Refine the question to narrow the result."
    ]

    empty = build_table(
        preview=preview,
        query=query,
        result=AnalyticsResult(
            column_names=[column["name"] for column in query["expectedColumns"]],
            rows=[],
            truncated=False,
        ),
        freshness=catalog().freshness,
        accepted_at="2026-07-16T00:00:00Z",
        duration_ms=1,
        statement_timeout_ms=500,
        max_rows=1,
        catalyst_trace_id="trace-1",
    )
    assert empty["table"]["rows"] == []
    assert empty["table"]["rowCount"]["total"] == 0
    assert empty["table"]["rowCount"]["totalIsExact"] is True


@pytest.mark.parametrize(
    "rows",
    [
        [("only-one-cell",)],
        [("HIV", "not-an-integer")],
    ],
)
def test_table_builder_rejects_row_shape_and_type(tmp_path: Path, rows: list):
    query = ready_query()
    store = PreviewStore(tmp_path / "state.sqlite3")
    preview = store.create_preview(query)
    with pytest.raises(TableError):
        build_table(
            preview=preview,
            query=query,
            result=AnalyticsResult(
                column_names=["test_name", "result_count"],
                rows=rows,
                truncated=False,
            ),
            freshness=catalog().freshness,
            accepted_at="2026-07-16T00:00:00Z",
            duration_ms=1,
            statement_timeout_ms=500,
            max_rows=2,
            catalyst_trace_id="trace-1",
        )


@pytest.mark.asyncio
async def test_postgres_adapter_uses_read_only_timeout_limit_and_driver_bindings():
    calls = []

    class Cursor:
        description = [
            SimpleNamespace(name="test_name"),
            SimpleNamespace(name="result_count"),
        ]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchmany(self, count):
            assert count == 3
            return [("HIV", 2), ("TB", 1), ("Malaria", 3)]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def cursor(self):
            return Cursor()

    adapter = PostgresAnalyticsAdapter(
        "postgresql://demo",
        connect=lambda *args, **kwargs: Connection(),
    )
    result = await adapter.execute(
        sql=(
            "SELECT test_name, result_count FROM analytics.lab_results "
            "WHERE result_date >= :start_date"
        ),
        parameters=[
            {
                "name": "start_date",
                "type": "date",
                "source": "question",
                "value": "2026-07-01",
            }
        ],
        max_rows=2,
        statement_timeout_ms=500,
    )
    assert calls[0][0] == "SET TRANSACTION READ ONLY"
    assert calls[1] == (
        "SELECT set_config('statement_timeout', %s, true)",
        ("500ms",),
    )
    assert "%(start_date)s" in calls[2][0]
    assert calls[2][1] == {"start_date": date(2026, 7, 1)}
    assert result.rows == [("HIV", 2), ("TB", 1)]
    assert result.truncated is True
    assert result.truncation_reason == "configured_limit"


@pytest.mark.asyncio
async def test_postgres_adapter_marks_a_reached_sql_limit_as_inexact():
    class Cursor:
        description = [SimpleNamespace(name="patient_id")]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            return None

        def fetchmany(self, _count):
            return [("patient-1",), ("patient-2",)]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def cursor(self):
            return Cursor()

    adapter = PostgresAnalyticsAdapter(
        "postgresql://demo",
        connect=lambda *args, **kwargs: Connection(),
    )
    result = await adapter.execute(
        sql="SELECT patient_id FROM analytics.lab_results LIMIT 2",
        parameters=[],
        max_rows=2,
        statement_timeout_ms=500,
    )

    assert result.rows == [("patient-1",), ("patient-2",)]
    assert result.truncated is True
    assert result.truncation_reason == "query_limit_reached"


@pytest.mark.asyncio
async def test_dataset_rows_include_stable_observation_identity_and_ordering():
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchone(self):
            return (1,)

        def fetchall(self):
            return [
                (
                    "observation-1",
                    "patient-1",
                    "Viral Load",
                    Decimal("9000"),
                    "copies/ml",
                    datetime(2026, 4, 27, 9, tzinfo=timezone.utc),
                    datetime(2026, 4, 27, 11, tzinfo=timezone.utc),
                    Decimal("120"),
                )
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def cursor(self):
            return Cursor()

    adapter = PostgresAnalyticsAdapter(
        "postgresql://demo",
        connect=lambda *args, **kwargs: Connection(),
    )

    result = await adapter.dataset_rows(
        test_name="Viral Load",
        patient_id=None,
        limit=25,
        offset=0,
    )

    row_query, bindings = calls[2]
    assert "SELECT observation_id, patient_id" in row_query
    assert "ORDER BY observed_at DESC NULLS LAST, observation_id" in row_query
    assert bindings == {
        "limit": 25,
        "offset": 0,
        "test_name": "Viral Load",
    }
    assert result["rows"] == [
        {
            "observationId": "observation-1",
            "patientId": "patient-1",
            "testName": "Viral Load",
            "value": "9000",
            "unit": "copies/ml",
            "observedAt": "2026-04-27T09:00:00Z",
            "issuedAt": "2026-04-27T11:00:00Z",
            "turnaroundMinutes": "120",
        }
    ]


@pytest.mark.asyncio
async def test_dataset_overview_uses_live_pipeline_identity_without_claiming_classification():
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchone(self):
            return (
                96,
                1152,
                9,
                datetime(2025, 7, 15, 9, tzinfo=timezone.utc),
                datetime(2026, 4, 27, 9, tzinfo=timezone.utc),
                "full-20260717T120000Z",
            )

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
        "postgresql://demo",
        data_source_id="openelis-fhir-postgresql",
        connect=lambda *args, **kwargs: Connection(),
    )

    overview = await adapter.dataset_overview()

    assert "FROM analytics.pipeline_freshness_v1" in calls[1][0]
    assert overview["datasetId"] == "full-20260717T120000Z"
    assert overview["dataSource"] == "openelis-fhir-postgresql"
    assert overview["pipelineRunId"] == "full-20260717T120000Z"
    assert overview["synthetic"] is None
    assert overview["exampleQuestions"] == []


@pytest.mark.parametrize(
    ("status", "expected_status"),
    [
        ("needs_clarification", 200),
        ("unsupported", 200),
        ("rejected", 200),
    ],
)
def test_query_route_returns_every_non_ready_contract_status(
    tmp_path: Path,
    status: str,
    expected_status: int,
):
    question = "Question"
    service, _, _, registry = make_service(tmp_path, non_ready_query(status, question))
    client = TestClient(gateway.create_app(catalyst_service=service))
    response = client.post(
        "/v1/catalyst/queries",
        json={
            "contractVersion": "catalyst.question.request.v1",
            "deploymentMode": "demo",
            "question": question,
        },
    )
    assert response.status_code == expected_status
    registry.validate("catalyst-query-v1.schema.json", response.json())
    assert response.json()["status"] == status
    if status == "rejected":
        assert response.json()["diagnosticCandidate"]["executable"] is False
        assert response.json()["diagnosticCandidate"]["candidate"]["sql"]


def test_query_route_builds_ready_preview(tmp_path: Path):
    question = "Count tests since July 1"
    service, hub, _, registry = make_service(tmp_path, ready_query(question))
    client = TestClient(gateway.create_app(catalyst_service=service))
    response = client.post(
        "/v1/catalyst/queries",
        json={
            "contractVersion": "catalyst.question.request.v1",
            "deploymentMode": "demo",
            "question": question,
        },
    )
    assert response.status_code == 201
    preview = response.json()
    registry.validate("catalyst-preview-v1.schema.json", preview)
    assert preview["state"] == "awaiting_acceptance"
    assert preview["question"] == question
    assert preview["reasoningTrace"] == {
        "traceId": "hub-trace-1",
        "profileId": "catalyst-query-gemma-4-12b-q4",
        "status": "passed",
        "stages": [
            "context",
            "query_generate",
            "query_review",
            "query_finalize",
        ],
        "roleModels": {
            "query_generate": "gemma-4-12b-q4",
            "query_review": "gemma-4-12b-q4",
        },
        "checks": [{"name": "review", "status": "passed"}],
    }
    assert hub.requests[0]["messages"] == [{"role": "user", "content": question}]


def test_query_route_rejects_sql_policy_violation_from_hub(tmp_path: Path):
    destructive = ready_query(question="Show test results")
    destructive["sql"] = "DELETE FROM analytics.lab_results"
    destructive["parameters"] = []
    service, hub, _, registry = make_service(tmp_path, destructive)
    client = TestClient(gateway.create_app(catalyst_service=service))

    response = client.post(
        "/v1/catalyst/queries",
        json={
            "contractVersion": "catalyst.question.request.v1",
            "deploymentMode": "demo",
            "question": "Show test results",
        },
    )

    assert response.status_code == 422
    registry.validate("catalyst-policy-outcome-v1.schema.json", response.json())
    assert len(hub.requests) == 1
    assert response.json()["violations"] == [
        {
            "code": "operation_not_allowed",
            "message": "Only a read-only SELECT statement is allowed.",
        }
    ]


def test_dataset_routes_expose_overview_and_bounded_rows(tmp_path: Path):
    service, _, _, _ = make_service(tmp_path)
    client = TestClient(gateway.create_app(catalyst_service=service))

    overview = client.get("/v1/catalyst/dataset")
    assert overview.status_code == 200
    assert overview.json()["contractVersion"] == "catalyst.dataset-overview.v1"
    assert overview.json()["patients"] == 2

    rows = client.get(
        "/v1/catalyst/dataset/rows",
        params={"testName": "Viral Load", "limit": 25, "offset": 0},
    )
    assert rows.status_code == 200
    assert rows.json() == {
        "contractVersion": "catalyst.dataset-rows.v1",
        "total": 0,
        "limit": 25,
        "offset": 0,
        "rows": [],
    }

    invalid = client.get("/v1/catalyst/dataset/rows", params={"limit": 101})
    assert invalid.status_code == 422


def test_query_route_maps_invalid_policy_and_hub_failures(tmp_path: Path):
    unsafe = ready_query()
    unsafe["sql"] = "DROP TABLE analytics.lab_results"
    unsafe["parameters"] = []
    service, _, _, registry = make_service(tmp_path, unsafe)
    client = TestClient(gateway.create_app(catalyst_service=service))
    body = {
        "contractVersion": "catalyst.question.request.v1",
        "deploymentMode": "demo",
        "question": unsafe["question"],
    }
    response = client.post("/v1/catalyst/queries", json=body)
    assert response.status_code == 422
    registry.validate("catalyst-policy-outcome-v1.schema.json", response.json())

    hub = FakeHub(error=HubError("hub_timeout", "Hub timed out"))
    service, _, _, _ = make_service(tmp_path, hub=hub)
    client = TestClient(gateway.create_app(catalyst_service=service))
    response = client.post("/v1/catalyst/queries", json=body)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "hub_timeout"

    response = client.post(
        "/v1/catalyst/queries",
        json={
            "contractVersion": "wrong",
            "deploymentMode": "demo",
            "question": "",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_execute_route_success_replay_conflict_and_poll(tmp_path: Path):
    service, _, analytics, registry = make_service(tmp_path)
    client = TestClient(gateway.create_app(catalyst_service=service))
    question_response = client.post(
        "/v1/catalyst/queries",
        json={
            "contractVersion": "catalyst.question.request.v1",
            "deploymentMode": "demo",
            "question": "Count tests since July 1",
        },
    )
    preview = question_response.json()
    body = execute_body(preview)
    response = client.post(
        f"/v1/catalyst/previews/{preview['previewId']}/execute",
        json=body,
    )
    assert response.status_code == 200
    registry.validate("catalyst-table-v1.schema.json", response.json())
    assert response.json()["table"]["rowCount"]["returned"] == 1
    assert analytics.calls == 1

    replay = client.post(
        f"/v1/catalyst/previews/{preview['previewId']}/execute",
        json=body,
    )
    assert replay.status_code == 200
    assert replay.json() == response.json()
    assert analytics.calls == 1

    poll = client.get(
        f"/v1/catalyst/executions/{preview['previewId']}",
        params={"idempotencyKey": "idem-1"},
    )
    assert poll.status_code == 200
    assert poll.json() == response.json()

    conflict_body = execute_body(preview, "other-key")
    conflict = client.post(
        f"/v1/catalyst/previews/{preview['previewId']}/execute",
        json=conflict_body,
    )
    assert conflict.status_code == 409
    registry.validate("catalyst-execution-outcome-v1.schema.json", conflict.json())


def test_execute_route_in_progress_not_found_delayed_and_bad_path(tmp_path: Path):
    clock = Clock()
    service, _, _, registry = make_service(tmp_path, clock=clock)
    client = TestClient(gateway.create_app(catalyst_service=service))
    missing = client.get(
        "/v1/catalyst/executions/missing",
        params={"idempotencyKey": "key"},
    )
    assert missing.status_code == 404
    registry.validate("catalyst-execution-outcome-v1.schema.json", missing.json())

    preview = service.store.create_preview(ready_query())
    service.store.begin_execution(
        preview["previewId"], preview["queryDigest"], "active"
    )
    active = client.get(
        f"/v1/catalyst/executions/{preview['previewId']}",
        params={"idempotencyKey": "active"},
    )
    assert active.status_code == 202
    assert active.json()["status"] == "in_progress"

    delayed = service.store.create_preview(ready_query())
    clock.advance(60 * 60 * 24 * 365)
    accepted = client.post(
        f"/v1/catalyst/previews/{delayed['previewId']}/execute",
        json=execute_body(delayed, "delayed"),
    )
    assert accepted.status_code == 200

    mismatch = execute_body(preview, "mismatch")
    mismatch["previewId"] = "other"
    bad_path = client.post(
        f"/v1/catalyst/previews/{preview['previewId']}/execute",
        json=mismatch,
    )
    assert bad_path.status_code == 400


def test_execute_route_stores_and_replays_execution_failure(tmp_path: Path):
    analytics = FakeAnalytics(error=RuntimeError("database unavailable"))
    service, _, _, registry = make_service(tmp_path, analytics=analytics)
    client = TestClient(gateway.create_app(catalyst_service=service))
    preview = service.store.create_preview(ready_query())
    body = execute_body(preview, "failure")
    response = client.post(
        f"/v1/catalyst/previews/{preview['previewId']}/execute",
        json=body,
    )
    assert response.status_code == 502
    registry.validate("catalyst-execution-outcome-v1.schema.json", response.json())
    assert response.json()["status"] == "failed"
    assert analytics.calls == 1

    replay = client.post(
        f"/v1/catalyst/previews/{preview['previewId']}/execute",
        json=body,
    )
    assert replay.status_code == 502
    assert replay.json()["replayed"] is True
    assert analytics.calls == 1


@pytest.mark.asyncio
async def test_execute_cancellation_is_reraised_and_stored(tmp_path: Path):
    analytics = FakeAnalytics(error=asyncio.CancelledError())
    service, _, _, registry = make_service(tmp_path, analytics=analytics)
    preview = service.store.create_preview(ready_query())
    body = execute_body(preview, "cancelled")

    with pytest.raises(asyncio.CancelledError):
        await service.execute_preview(preview["previewId"], body)

    replay = service.store.poll(preview["previewId"], "cancelled")
    assert replay.status_code == 502
    registry.validate("catalyst-execution-outcome-v1.schema.json", replay.body)
    assert replay.body["status"] == "failed"
    assert "cancelled" in replay.body["message"].lower()


def test_structured_readiness_and_legacy_route_are_both_exposed(tmp_path: Path):
    service, _, _, _ = make_service(tmp_path)
    app = gateway.create_app(catalyst_service=service)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "catalyst": {"ready": True},
            "hub": {"ready": True},
            "queryProfile": {"ready": True},
            "modelRouter": {"ready": True},
            "analytics": {"ready": True, "dataSource": "openelis-demo"},
            "execution": {"ready": True},
        },
    }
    paths = {route.path for route in app.router.routes}
    assert "/v1/chat/completions" in paths
    assert "/v1/catalyst/queries" in paths
    assert "/v1/catalyst/previews/{preview_id}/execute" in paths
    assert "/v1/catalyst/executions/{preview_id}" in paths


def test_app_lifespan_closes_owned_clients(tmp_path: Path):
    service, hub, _, _ = make_service(tmp_path)
    app = gateway.create_app(catalyst_service=service)
    a2a_client = app.state.a2a_client

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert hub.closed is True
    assert a2a_client._http_client.is_closed is True
    assert service.store.readiness() == {"ready": False}


def test_gateway_defaults_match_the_local_mvp(monkeypatch: pytest.MonkeyPatch):
    for name in (
        "MED_AGENT_HUB_BASE_URL",
        "CATALYST_ANALYTICS_DSN",
        "CATALYST_HUB_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert config.hub_base_url == "http://localhost:8082"
    assert config.analytics_dsn == (
        "postgresql://catalyst_readonly:demo-readonly-change-me"
        "@localhost:15433/catalyst_analytics"
    )
    assert config.hub_timeout_seconds == 360
