import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src import gateway
from src.catalyst.analytics import AnalyticsResult, SqlAnalyticsAdapter
from tests.fixture_dialect import FIXTURE
from src.catalyst.catalog import Catalog, DatasetBrowserProfile
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

# The OpenELIS dataset-browser mapping, mirroring what its shipped catalog
# declares. The adapter composes every dataset query from a profile like this
# one, so a source's own column names never leak into another source's SQL.
OPENELIS_DATASET_BROWSER = DatasetBrowserProfile(
    fact_view="analytics.lab_result_fact_v1",
    identity_column="observation_id",
    subject_column="patient_id",
    category_column="test_name",
    observed_at_column="observed_at",
    value_column="result_value",
    unit_column="result_unit",
    issued_at_column="issued_at",
    duration_column="receipt_to_release_minutes",
)


def catalog() -> Catalog:
    return Catalog(
        data_source="openelis-demo",
        catalog_version="2026.07",
        schema_version="analytics-v1",
        dialect="fixture",
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
            "dialect": "fixture",
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
            "profileId": "catalyst-query-e4b-qwen14b",
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
            "profileId": "catalyst-query-e4b-qwen14b",
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
                "id": "catalyst-query-e4b-qwen14b",
                "label": "Catalyst query — Gemma 4 E4B writer, Qwen 2.5 14B reviewer",
                "available": self.error is None,
                "required_models": [
                    "google/gemma-4-e4b",
                    "qwen2.5-14b-instruct-mlx",
                ],
                "role_models": {
                    "query_generate": "google/gemma-4-e4b",
                    "query_review": "qwen2.5-14b-instruct-mlx",
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
    catalog_override: Catalog | None = None,
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
        catalog=catalog_override or catalog(),
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
                    "profileId": "catalyst-query-e4b-qwen14b",
                    "traceId": "hub-runtime-schema",
                    "contextSourceIds": [context_id],
                },
            }

    hub = RuntimeHub()
    # The metadata describes a different relation. Database discovery alone
    # makes patient_flat_v1 available to the editor, writer, and execution path.
    curated = Catalog(
        data_source="openelis-demo",
        catalog_version="2026.07",
        schema_version="analytics-v1",
        dialect="fixture",
        context_source_id="catalog:openelis-demo:2026.07",
        views=[
            {
                "name": "analytics.lab_result_fact_v1",
                "version": "1",
                "grain": "one row per result",
                "fields": [
                    {
                        "name": "observation_id",
                        "type": "string",
                        "description": "FHIR Observation identifier",
                    }
                ],
            }
        ],
        freshness={},
    )
    service, _, _, _ = make_service(
        tmp_path,
        hub=hub,
        catalog_override=curated,
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
    assert len(registry.schemas) == 31
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
        "catalyst-superset-bundle-v1.schema.json",
        "catalyst-superset-import-latest-v1.schema.json",
        "catalyst-superset-import-receipt-v1.schema.json",
        "catalyst-superset-last-verified-v1.schema.json",
        "catalyst-superset-outbox-current-v1.schema.json",
        "catalyst-table-v1.schema.json",
        "catalyst-workbench-execute-request-v1.schema.json",
        "catalyst-workbench-editor-catalog-v1.schema.json",
        "catalyst-workbench-editor-snapshot-v1.schema.json",
        "catalyst-workbench-editor-snapshot-record-v1.schema.json",
        "catalyst-workbench-finding-v1.schema.json",
        "catalyst-workbench-generation-evidence-v1.schema.json",
        "catalyst-workbench-guidance-v1.schema.json",
        "catalyst-workbench-guidance-request-v1.schema.json",
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
            "dialect": "fixture",
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
        "profileId": "catalyst-query-e4b-qwen14b",
        "status": "passed",
        "stages": [
            "context",
            "query_generate",
            "query_review",
            "query_finalize",
        ],
        "roleModels": {
            "query_generate": "google/gemma-4-e4b",
            "query_review": "qwen2.5-14b-instruct-mlx",
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


def test_structured_readiness_and_catalyst_routes_are_exposed(tmp_path: Path):
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
    assert "/v1/chat/completions" not in paths
    assert "/v1/catalyst/queries" in paths
    assert "/v1/catalyst/previews/{preview_id}/execute" in paths
    assert "/v1/catalyst/executions/{preview_id}" in paths


def test_app_lifespan_closes_owned_clients(tmp_path: Path):
    service, hub, _, _ = make_service(tmp_path)
    app = gateway.create_app(catalyst_service=service)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert hub.closed is True
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
