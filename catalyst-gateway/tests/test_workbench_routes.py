from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from src import gateway
from src.catalyst.analytics import (
    AnalyticsColumn,
    DatabaseDiagnostic,
    ManualAnalyticsError,
    ManualAnalyticsResult,
)
from src.catalyst.catalog import Catalog
from src.catalyst.contracts import ContractRegistry
from src.catalyst.hub import HubError
from src.catalyst.policy import SqlPolicy
from src.catalyst.service import CatalystService
from src.catalyst.storage import PreviewStore, WorkbenchStore


CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"
QUESTION = "Show malaria results since 2026-01-01"
PROFILE_ID = "catalyst-query-checked"


def _catalog() -> Catalog:
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
                        "description": "Test name",
                    },
                    {
                        "name": "result_date",
                        "type": "date",
                        "description": "Result date",
                    },
                ],
            }
        ],
        freshness={},
    )


def _candidate(sql: str) -> dict:
    return {
        "status": "ready",
        "target": {
            "dataSource": "openelis-demo",
            "catalogVersion": "2026.07",
            "dialect": "postgresql",
            "approvedViews": ["analytics.lab_results"],
        },
        "sql": sql,
        "parameters": [],
        "expectedColumns": [
            {"name": "test_name", "logicalType": "string", "nullable": False}
        ],
    }


def _ready_query() -> dict:
    candidate = _candidate(
        "SELECT test_name FROM analytics.lab_results "
        "WHERE result_date >= :start_date LIMIT 2"
    )
    candidate["parameters"] = [
        {
            "name": "start_date",
            "type": "date",
            "source": "question",
            "value": "2026-01-01",
        }
    ]
    return {
        "contractVersion": "catalyst.query.v1",
        "deploymentMode": "demo",
        "question": QUESTION,
        **candidate,
        "validation": {
            "status": "passed",
            "checks": [{"name": "query_lint_attempt_1", "status": "passed"}],
        },
        "provenance": {
            "profileId": PROFILE_ID,
            "traceId": "hub-trace-ready",
            "contextSourceIds": ["catalog:openelis-demo:2026.07"],
        },
    }


def _collaborative_query() -> dict:
    query = _ready_query()
    writer = deepcopy(query)
    writer_candidate = {
        key: deepcopy(writer[key])
        for key in ("status", "target", "sql", "parameters", "expectedColumns")
    }
    writer_candidate["sql"] = writer_candidate["sql"].replace(
        "SELECT test_name", "SELECT COUNT(*)"
    )
    writer_candidate["expectedColumns"] = [
        {"name": "count", "logicalType": "integer", "nullable": False}
    ]
    reviewer_candidate = deepcopy(writer_candidate)
    reviewer_candidate["sql"] = reviewer_candidate["sql"].replace(
        "SELECT COUNT(*)", "SELECT COUNT(*) AS count"
    )
    query.update(reviewer_candidate)
    query["modelCollaboration"] = {
        "writer": {
            "model": "qwen2.5-coder-14b",
            "candidate": writer_candidate,
            "lintFindings": [
                {
                    "code": "output.projection_mismatch",
                    "stage": "output_agreement",
                    "severity": "error",
                    "path": "expectedColumns",
                    "message": "Projected SQL columns and expectedColumns must agree.",
                }
            ],
        },
        "reviewer": {
            "model": "gemma-e4b",
            "decision": "repair",
            "candidate": reviewer_candidate,
            "checks": [{"name": "projection", "status": "passed"}],
            "finalDecision": "approve",
            "finalChecks": [{"name": "projection", "status": "passed"}],
        },
        "finalLintFindings": [],
    }
    return query


def _rejected_query() -> dict:
    invalid = _candidate(
        "SELECT test_name FROM analytics.lab_results "
        "WHERE result_date >= '2026-01-01'"
    )
    finding = {
        "code": "policy.unbound_predicate_literal",
        "stage": "parameter_binding",
        "severity": "error",
        "path": "$.sql",
        "message": "Predicate literals must use named parameters.",
        "evidence": "2026-01-01",
        "suggestedAction": "Replace the literal with a named parameter.",
    }
    return {
        "contractVersion": "catalyst.query.v1",
        "deploymentMode": "demo",
        "status": "rejected",
        "question": QUESTION,
        "message": "Query generation failed its structured-output contract.",
        "diagnosticCandidate": {
            "executable": False,
            "candidate": invalid,
            "attempts": [
                {
                    "attempt": 1,
                    "status": "failed",
                    "finding_codes": [finding["code"]],
                    "findings": [finding],
                }
            ],
        },
        "validation": {
            "status": "rejected",
            "checks": [
                {
                    "name": "query_generate",
                    "status": "failed",
                    "message": "Generation did not pass lint.",
                }
            ],
        },
        "provenance": {
            "profileId": PROFILE_ID,
            "traceId": "hub-trace-rejected",
            "contextSourceIds": ["catalog:openelis-demo:2026.07"],
        },
    }


def _policy_bearing_ready_query() -> dict:
    query = _ready_query()
    query["sql"] = (
        "SELECT test_name FROM analytics.lab_results "
        "WHERE result_date >= '2026-01-01'"
    )
    query["parameters"] = []
    return query


class FakeHub:
    def __init__(self, query: dict, *, error: Exception | None = None) -> None:
        self.query = query
        self.error = error
        self.requests: list[dict] = []

    async def list_query_profiles(self) -> list[dict]:
        return [
            {
                "id": PROFILE_ID,
                "label": "Catalyst query checked",
                "available": True,
                "required_models": ["qwen2.5-coder-14b", "gemma-e4b"],
                "role_models": {
                    "query_generate": "qwen2.5-coder-14b",
                    "query_review": "gemma-e4b",
                },
                "role_knobs": {
                    "query_generate": {"temperature": 0, "seed": 42},
                    "query_review": {"temperature": 0, "seed": 42},
                },
                "profile_configuration_digest": "sha256:profile-config",
                "role_prompt_digests": {
                    "query_generate": {
                        "configured_prompt": "catalyst-query-generate",
                        "system_prompt_sha256": {
                            "catalyst-query-generate": "sha256:generate-prompt"
                        },
                    },
                    "query_review": {
                        "configured_prompt": "catalyst-query-review",
                        "system_prompt_sha256": {
                            "catalyst-query-review": "sha256:review-prompt"
                        },
                    },
                },
                "backend": {
                    "provider": "llama.cpp",
                    "endpoint": "http://router:8077",
                    "models_endpoint": "http://router:8077/v1/models",
                },
                "backend_model_metadata": {
                    "qwen2.5-coder-14b": {
                        "id": "qwen2.5-coder-14b",
                        "object": "model",
                        "owned_by": "llama.cpp",
                        "meta": {"n_params": 14_000_000_000},
                    },
                    "gemma-e4b": {
                        "id": "gemma-e4b",
                        "object": "model",
                        "owned_by": "llama.cpp",
                        "meta": {"n_params": 7_500_000_000},
                    },
                },
                "stages": ["query_generate", "query_lint", "query_review"],
            }
        ]

    async def generate_query(self, request: dict) -> dict:
        self.requests.append(deepcopy(request))
        if self.error is not None:
            raise self.error
        return deepcopy(self.query)

    async def readiness(self) -> dict:
        return {
            "hub": {"ready": True},
            "queryProfile": {"ready": True},
            "modelRouter": {"ready": True},
        }

    async def aclose(self) -> None:
        return None


class FakeAnalytics:
    def __init__(self, error: ManualAnalyticsError | None = None) -> None:
        self.error = error
        self.manual_calls: list[dict] = []

    async def execute_manual(self, **kwargs) -> ManualAnalyticsResult:
        self.manual_calls.append(deepcopy(kwargs))
        if self.error:
            raise self.error
        return ManualAnalyticsResult(
            columns=[AnalyticsColumn(0, "test_name", "text", 25, "string")],
            rows=[[{"type": "string", "value": "Malaria"}]],
            truncated=False,
        )

    async def execute(self, **_kwargs):  # pragma: no cover - governed compatibility
        raise AssertionError("governed execution was not expected")

    async def freshness(self) -> dict:
        return {}

    async def readiness(self) -> dict:
        return {"ready": True, "dataSource": "openelis-demo"}

    async def dataset_overview(self) -> dict:
        return {
            "contractVersion": "catalyst.dataset-overview.v1",
            "datasetId": "pipeline-run-1",
            "dataSource": "openelis-demo",
            "pipelineRunId": "pipeline-run-1",
            "synthetic": None,
            "patients": 1,
            "results": 1,
            "testTypes": 1,
            "firstObservedAt": "2026-01-01T00:00:00Z",
            "lastObservedAt": "2026-01-01T00:00:00Z",
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


def _client(
    tmp_path: Path,
    query: dict,
    *,
    analytics: FakeAnalytics | None = None,
    hub: FakeHub | None = None,
    catalog: Catalog | None = None,
) -> tuple[TestClient, FakeAnalytics]:
    database = tmp_path / "gateway.sqlite3"
    actual_analytics = analytics or FakeAnalytics()
    service = CatalystService(
        contracts=ContractRegistry.load(CONTRACTS),
        catalog=catalog or _catalog(),
        hub=hub or FakeHub(query),
        analytics=actual_analytics,
        store=PreviewStore(database),
        workbench_store=WorkbenchStore(database),
        sql_policy=SqlPolicy(max_rows=2),
        max_rows=2,
        statement_timeout_ms=500,
    )
    return TestClient(gateway.create_app(catalyst_service=service)), actual_analytics


def _create_session(client: TestClient, *, question: str = QUESTION) -> dict:
    response = client.post(
        "/v1/catalyst/workbench/sessions",
        json={
            "contractVersion": "catalyst.workbench.session.request.v1",
            "deploymentMode": "demo",
            "question": question,
            "profileId": PROFILE_ID,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _preview_count(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "gateway.sqlite3") as connection:
        row = connection.execute("SELECT COUNT(*) FROM catalyst_previews").fetchone()
    assert row is not None
    return int(row[0])


def _workbench_session_count(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "gateway.sqlite3") as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM catalyst_workbench_sessions"
        ).fetchone()
    assert row is not None
    return int(row[0])


def test_editor_catalog_route_exposes_versioned_contract(tmp_path: Path) -> None:
    editor_catalog = Catalog(
        data_source="openelis-demo",
        catalog_version="catalog-v3",
        schema_version="schema-v2",
        dialect="postgresql",
        context_source_id="catalog:openelis-demo:catalog-v3",
        views=[
            {
                "name": "reporting.summary_v1",
                "version": "1",
                "grain": "one row per summary",
                "fields": [
                    {"name": "z_count", "type": "integer", "description": "Z"},
                    {"name": "a_label", "type": "string", "description": "A"},
                ],
            },
            {
                "name": "analytics.zz_result_v1",
                "version": "1",
                "grain": "one row per result",
                "fields": [
                    {"name": "test_name", "type": "string", "description": "Test"}
                ],
            },
            {
                "name": "analytics.lab_result_v1",
                "version": "1",
                "grain": "one row per result",
                "fields": [
                    {
                        "name": "observed_at",
                        "type": "date-time",
                        "description": "Observed",
                    },
                    {
                        "name": "patient_id",
                        "type": "string",
                        "description": "Patient",
                    },
                ],
            },
        ],
        freshness={},
    )
    original_views = deepcopy(editor_catalog.views)
    client, _ = _client(tmp_path, _ready_query(), catalog=editor_catalog)

    response = client.get("/v1/catalyst/workbench/catalog")
    repeated = client.get("/v1/catalyst/workbench/catalog")

    assert response.status_code == 200, response.text
    assert repeated.status_code == 200, repeated.text
    assert repeated.content == response.content
    assert response.json() == {
        "contractVersion": "catalyst.workbench.editor-catalog.v1",
        "catalogVersion": "catalog-v3",
        "schemaVersion": "schema-v2",
        "dialect": "postgresql",
        "schemas": [
            {
                "name": "analytics",
                "views": [
                    {
                        "name": "lab_result_v1",
                        "columns": [
                            {"name": "observed_at", "logicalType": "date-time"},
                            {"name": "patient_id", "logicalType": "string"},
                        ],
                    },
                    {
                        "name": "zz_result_v1",
                        "columns": [{"name": "test_name", "logicalType": "string"}],
                    },
                ],
            },
            {
                "name": "reporting",
                "views": [
                    {
                        "name": "summary_v1",
                        "columns": [
                            {"name": "a_label", "logicalType": "string"},
                            {"name": "z_count", "logicalType": "integer"},
                        ],
                    }
                ],
            },
        ],
    }
    assert editor_catalog.views == original_views
    assert _preview_count(tmp_path) == 0
    assert _workbench_session_count(tmp_path) == 0


def test_editor_catalog_failure_is_useful_and_does_not_mutate_state(
    tmp_path: Path,
) -> None:
    broken_catalog = Catalog(
        data_source="openelis-demo",
        catalog_version="catalog-v3",
        schema_version="schema-v2",
        dialect="postgresql",
        context_source_id="catalog:openelis-demo:catalog-v3",
        views=[
            {
                "name": "unqualified_view",
                "version": "1",
                "grain": "one row per result",
                "fields": [
                    {"name": "test_name", "type": "string", "description": "Test"}
                ],
            }
        ],
        freshness={},
    )
    original_views = deepcopy(broken_catalog.views)
    client, _ = _client(tmp_path, _ready_query(), catalog=broken_catalog)

    response = client.get("/v1/catalyst/workbench/catalog")

    assert response.status_code == 503, response.text
    assert response.json()["contractVersion"] == "catalyst.workbench.error.v1"
    assert response.json()["error"]["code"] == "editor_catalog_unavailable"
    assert "schema-qualified" in response.json()["error"]["message"]
    assert broken_catalog.views == original_views
    assert _preview_count(tmp_path) == 0
    assert _workbench_session_count(tmp_path) == 0


def test_ready_generation_creates_restorable_version_and_validation(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path, _ready_query())
    session = _create_session(client)

    assert session["currentVersion"]["authorType"] == "model"
    assert session["currentVersion"]["sql"] == _ready_query()["sql"]
    assert session["latestValidation"]["status"] == "valid"
    assert session["latestValidation"]["advisory"] is True
    assert session["provenance"]["generationOutcome"]["contractVersion"] == (
        "catalyst.query.v1"
    )
    assert session["datasetId"] == "pipeline-run-1"
    assert session["datasetVersion"] == "pipeline-run-1"
    assert session["catalogVersion"] == "2026.07"
    assert session["provenance"]["datasetSnapshot"] == {
        "datasetId": "pipeline-run-1",
        "dataSource": "openelis-demo",
        "pipelineRunId": "pipeline-run-1",
        "synthetic": None,
        "patients": 1,
        "results": 1,
        "testTypes": 1,
        "firstObservedAt": "2026-01-01T00:00:00Z",
        "lastObservedAt": "2026-01-01T00:00:00Z",
    }
    expected_profile_snapshot = {
        "profileId": PROFILE_ID,
        "profileLabel": "Catalyst query checked",
        "profileAvailable": True,
        "requiredModels": ["qwen2.5-coder-14b", "gemma-e4b"],
        "roleModels": {
            "query_generate": "qwen2.5-coder-14b",
            "query_review": "gemma-e4b",
        },
        "roleKnobs": {
            "query_generate": {"temperature": 0, "seed": 42},
            "query_review": {"temperature": 0, "seed": 42},
        },
        "profileConfigurationDigest": "sha256:profile-config",
        "rolePromptDigests": {
            "query_generate": {
                "configured_prompt": "catalyst-query-generate",
                "system_prompt_sha256": {
                    "catalyst-query-generate": "sha256:generate-prompt"
                },
            },
            "query_review": {
                "configured_prompt": "catalyst-query-review",
                "system_prompt_sha256": {
                    "catalyst-query-review": "sha256:review-prompt"
                },
            },
        },
        "backend": {
            "provider": "llama.cpp",
            "endpoint": "http://router:8077",
            "models_endpoint": "http://router:8077/v1/models",
        },
        "backendModelMetadata": {
            "qwen2.5-coder-14b": {
                "id": "qwen2.5-coder-14b",
                "object": "model",
                "owned_by": "llama.cpp",
                "meta": {"n_params": 14_000_000_000},
            },
            "gemma-e4b": {
                "id": "gemma-e4b",
                "object": "model",
                "owned_by": "llama.cpp",
                "meta": {"n_params": 7_500_000_000},
            },
        },
        "stages": ["query_generate", "query_lint", "query_review"],
        "unavailableReasons": [],
    }
    assert session["provenance"]["profileSnapshot"] == expected_profile_snapshot
    version_provenance = session["currentVersion"]["provenance"]
    assert {
        key: version_provenance[key] for key in expected_profile_snapshot
    } == expected_profile_snapshot
    assert (
        version_provenance["catalystTraceId"]
        == (session["provenance"]["catalystTraceId"])
    )
    assert "previewId" not in session["currentVersion"]["provenance"]
    hub = client.app.state.catalyst.hub
    assert isinstance(hub, FakeHub)
    assert hub.requests[0]["model"] == PROFILE_ID
    assert (
        hub.requests[0]["catalystQuery"]["correlation"]["traceId"]
        == (session["provenance"]["catalystTraceId"])
    )
    assert _preview_count(tmp_path) == 0

    restored = client.get(f"/v1/catalyst/workbench/sessions/{session['sessionId']}")
    assert restored.status_code == 200
    assert restored.json() == session


def test_collaboration_persists_writer_and_reviewer_as_linked_versions(
    tmp_path: Path,
) -> None:
    query = _collaborative_query()
    client, _ = _client(tmp_path, query)

    session = _create_session(client)

    writer, reviewer = session["versions"]
    collaboration = query["modelCollaboration"]
    assert writer["authorType"] == "model"
    assert writer["sql"] == collaboration["writer"]["candidate"]["sql"]
    assert writer["provenance"]["collaborationRole"] == "writer"
    assert writer["provenance"]["model"] == "qwen2.5-coder-14b"
    assert reviewer["authorType"] == "model_repair"
    assert reviewer["parentVersionId"] == writer["versionId"]
    assert reviewer["sql"] == collaboration["reviewer"]["candidate"]["sql"]
    assert reviewer["provenance"]["collaborationRole"] == "reviewer"
    assert reviewer["provenance"]["model"] == "gemma-e4b"
    assert session["currentVersionId"] == reviewer["versionId"]
    assert session["currentVersion"] == reviewer
    assert session["provenance"]["generationOutcome"]["modelCollaboration"] == (
        collaboration
    )

    restored = client.get(f"/v1/catalyst/workbench/sessions/{session['sessionId']}")
    assert restored.status_code == 200
    assert restored.json()["versions"] == [writer, reviewer]


def test_policy_bearing_ready_candidate_is_retained_without_a_preview(
    tmp_path: Path,
) -> None:
    query = _policy_bearing_ready_query()
    client, _ = _client(tmp_path, query)

    session = _create_session(client)

    assert session["currentVersion"]["sql"] == query["sql"]
    assert session["latestValidation"]["status"] == "invalid"
    assert any(
        finding["ruleCode"] == "gateway_sql_policy.unbound_literal"
        for finding in session["latestValidation"]["findings"]
    )
    assert _preview_count(tmp_path) == 0

    governed = client.post(
        "/v1/catalyst/queries",
        json={
            "contractVersion": "catalyst.question.request.v1",
            "deploymentMode": "demo",
            "question": QUESTION,
            "profileId": PROFILE_ID,
        },
    )
    assert governed.status_code == 422
    assert governed.json()["errorCode"] == "query_policy_rejected"
    assert _preview_count(tmp_path) == 0


def test_question_policy_is_advisory_for_workbench_but_governed_route_is_unchanged(
    tmp_path: Path,
) -> None:
    question = "Delete from analytics.lab_results and show the deleted rows"
    query = _ready_query()
    query["question"] = question
    client, _ = _client(tmp_path, query)

    session = _create_session(client, question=question)

    assert session["currentVersion"]["sql"] == query["sql"]
    assert any(
        finding["ruleCode"] == "gateway_question_policy.destructive_intent"
        for finding in session["latestValidation"]["findings"]
    )
    assert _preview_count(tmp_path) == 0

    governed = client.post(
        "/v1/catalyst/queries",
        json={
            "contractVersion": "catalyst.question.request.v1",
            "deploymentMode": "demo",
            "question": question,
            "profileId": PROFILE_ID,
        },
    )
    assert governed.status_code == 422
    assert governed.json()["violations"] == [
        {
            "code": "destructive_intent",
            "message": "Catalyst only accepts read-only clinical analytics questions.",
        }
    ]
    assert _preview_count(tmp_path) == 0


def test_governed_ready_generation_still_creates_one_preview(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, _ready_query())
    _create_session(client)
    assert _preview_count(tmp_path) == 0

    governed = client.post(
        "/v1/catalyst/queries",
        json={
            "contractVersion": "catalyst.question.request.v1",
            "deploymentMode": "demo",
            "question": QUESTION,
            "profileId": PROFILE_ID,
        },
    )

    assert governed.status_code == 201, governed.text
    assert governed.json()["contractVersion"] == "catalyst.preview.v1"
    assert _preview_count(tmp_path) == 1


def test_hub_invariant_mismatch_is_retained_as_an_advisory_finding(
    tmp_path: Path,
) -> None:
    query = _ready_query()
    query["question"] = "A different question"
    client, _ = _client(tmp_path, query)

    session = _create_session(client)

    assert session["currentVersion"]["sql"] == query["sql"]
    assert any(
        finding["ruleCode"] == "gateway_invariant.question_mismatch"
        for finding in session["latestValidation"]["findings"]
    )
    assert _preview_count(tmp_path) == 0


def test_rejected_candidate_is_retained_and_executes_unchanged(
    tmp_path: Path,
) -> None:
    client, analytics = _client(tmp_path, _rejected_query())
    session = _create_session(client)
    version = session["currentVersion"]

    assert session["latestValidation"]["status"] == "invalid"
    assert any(
        finding["ruleCode"] == "gateway_sql_policy.unbound_literal"
        for finding in session["latestValidation"]["findings"]
    )
    assert (
        session["currentVersion"]["provenance"]["generationAttempts"]
        == (_rejected_query()["diagnosticCandidate"]["attempts"])
    )
    assert _preview_count(tmp_path) == 0

    request = {
        "contractVersion": "catalyst.workbench.execute.request.v1",
        "versionId": version["versionId"],
        "queryDigest": version["queryDigest"],
        "idempotencyKey": "manual-run-1",
    }
    first = client.post(
        f"/v1/catalyst/workbench/versions/{version['versionId']}/execute",
        json=request,
    )
    assert first.status_code == 200, first.text
    execution = first.json()
    assert execution["status"] == "succeeded"
    assert execution["validationStatus"] == "invalid"
    assert execution["query"] == {
        "sql": version["sql"],
        "parameters": version["parameters"],
    }
    assert analytics.manual_calls[0]["sql"] == version["sql"]

    replay = client.post(
        f"/v1/catalyst/workbench/versions/{version['versionId']}/execute",
        json=request,
    )
    assert replay.status_code == 200
    assert replay.json()["executionId"] == execution["executionId"]
    assert replay.json()["replayed"] is True
    assert len(analytics.manual_calls) == 1


def test_historical_generation_attempt_findings_do_not_validate_current_candidate(
    tmp_path: Path,
) -> None:
    query = _rejected_query()
    candidate = query["diagnosticCandidate"]["candidate"]
    ready = _ready_query()
    candidate["sql"] = ready["sql"]
    candidate["parameters"] = ready["parameters"]
    query["diagnosticCandidate"]["attempts"][0]["findings"][0].update(
        severity="warning",
        message="The first generation attempt used a literal.",
    )
    client, _ = _client(tmp_path, query)

    session = _create_session(client)

    assert session["currentVersion"]["sql"] == ready["sql"]
    assert session["latestValidation"]["status"] == "valid"
    assert session["latestValidation"]["findings"] == []
    assert (
        session["currentVersion"]["provenance"]["generationAttempts"]
        == (query["diagnosticCandidate"]["attempts"])
    )


def test_ready_candidate_historical_lint_warning_stays_in_generation_history(
    tmp_path: Path,
) -> None:
    query = _ready_query()
    query["validation"] = {
        "status": "warned",
        "checks": [
            {
                "name": "query_lint_attempt_1",
                "status": "warned",
                "message": "The first candidate needed deterministic correction.",
            },
            {"name": "query_review", "status": "passed"},
        ],
    }
    client, _ = _client(tmp_path, query)

    session = _create_session(client)

    assert session["latestValidation"]["status"] == "valid"
    assert session["latestValidation"]["findings"] == []
    assert (
        session["currentVersion"]["provenance"]["generationValidation"]
        == (query["validation"])
    )


def test_structured_raw_only_diagnostic_is_preserved_for_manual_recovery(
    tmp_path: Path,
) -> None:
    raw_output = "SELECT test_name FROM analytics.lab_results LIMIT 2"
    query = _rejected_query()
    query["diagnosticCandidate"].pop("candidate")
    query["diagnosticCandidate"]["rawOutput"] = raw_output
    client, _ = _client(tmp_path, query)

    session = _create_session(client)

    assert session["currentVersion"] is None
    assert session["provenance"]["generationRawOutput"] == raw_output
    assert (
        session["provenance"]["generationOutcome"]["diagnosticCandidate"]["rawOutput"]
        == raw_output
    )

    drafted = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/versions",
        json={
            "contractVersion": "catalyst.workbench.version.request.v1",
            "sql": raw_output,
            "parameters": [],
        },
    )

    assert drafted.status_code == 201, drafted.text
    assert drafted.json()["currentVersion"]["sql"] == raw_output


def test_structured_raw_json_derives_an_unresolved_editor_seed_and_restores_it(
    tmp_path: Path,
) -> None:
    raw_output = (
        '{"status":"ready","sql":"SELECT COUNT(DISTINCT patient_id) AS count '
        "FROM analytics.lb_result_fact_v1 WHERE test_name = :test_name AND "
        'result_value > :threshold","parameters":[{"value":"Viral Load",'
        '"type":"string"},{"value":1000,"type":"integer"}]}'
    )
    query = _rejected_query()
    query["diagnosticCandidate"].pop("candidate")
    query["diagnosticCandidate"]["rawOutput"] = raw_output
    client, _ = _client(tmp_path, query)

    session = _create_session(client)

    assert session["currentVersion"] is None
    assert session["provenance"]["generationRawOutput"] == raw_output
    assert session["draftSeed"] == {
        "status": "unresolved",
        "source": "raw_model_output",
        "sql": (
            "SELECT COUNT(DISTINCT patient_id) AS count FROM "
            "analytics.lb_result_fact_v1 WHERE test_name = :test_name AND "
            "result_value > :threshold"
        ),
        "parameters": [
            {
                "name": "",
                "type": "string",
                "source": "human",
                "value": "Viral Load",
            },
            {
                "name": "",
                "type": "integer",
                "source": "human",
                "value": 1000,
            },
        ],
        "unresolvedPaths": [
            "$.parameters[0].name",
            "$.parameters[0].source",
            "$.parameters[1].name",
            "$.parameters[1].source",
        ],
    }

    restored = client.get(f"/v1/catalyst/workbench/sessions/{session['sessionId']}")

    assert restored.status_code == 200, restored.text
    assert restored.json()["draftSeed"] == session["draftSeed"]
    assert restored.json()["provenance"]["generationRawOutput"] == raw_output


def test_raw_editor_seed_requires_one_exact_representable_json_object(
    tmp_path: Path,
) -> None:
    for raw_output in (
        "{not-json",
        '[{"sql":"SELECT 1","parameters":[]}]',
        '```json\n{"sql":"SELECT 1","parameters":[]}\n```',
        '{"sql":"SELECT 1","parameters":[{"type":"uuid","value":"x"}]}',
    ):
        (tmp_path / str(len(raw_output))).mkdir()
        query = _rejected_query()
        query["diagnosticCandidate"].pop("candidate")
        query["diagnosticCandidate"]["rawOutput"] = raw_output
        client, _ = _client(tmp_path / str(len(raw_output)), query)

        session = _create_session(client)

        assert session["currentVersion"] is None
        assert session["draftSeed"] is None
        assert session["provenance"]["generationRawOutput"] == raw_output


def test_immutable_version_suppresses_raw_editor_seed(tmp_path: Path) -> None:
    query = _rejected_query()
    query["diagnosticCandidate"]["rawOutput"] = (
        '{"sql":"SELECT wrong FROM analytics.wrong","parameters":[]}'
    )
    client, _ = _client(tmp_path, query)

    session = _create_session(client)

    assert session["currentVersion"] is not None
    assert session["draftSeed"] is None
    assert (
        session["currentVersion"]["sql"]
        == query["diagnosticCandidate"]["candidate"]["sql"]
    )


def test_raw_generation_failure_allows_parentless_first_human_draft(
    tmp_path: Path,
) -> None:
    raw_output = "SELECT test_name FROM analytics.lab_results LIMIT 2"
    hub = FakeHub(
        _ready_query(),
        error=HubError(
            "hub_invalid_response",
            "Hub returned an invalid structured query completion.",
            raw_output=raw_output,
        ),
    )
    client, _ = _client(tmp_path, _ready_query(), hub=hub)
    session = _create_session(client)

    assert session["currentVersion"] is None
    assert session["latestValidation"] is None
    assert session["provenance"]["generationOutcome"]["rawOutput"] == raw_output

    drafted = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/versions",
        json={
            "contractVersion": "catalyst.workbench.version.request.v1",
            "sql": raw_output,
            "parameters": [],
        },
    )

    assert drafted.status_code == 201, drafted.text
    drafted_session = drafted.json()
    assert drafted_session["currentVersion"]["parentVersionId"] is None
    assert drafted_session["currentVersion"]["authorType"] == "human"
    assert drafted_session["currentVersion"]["sql"] == raw_output
    assert drafted_session["latestValidation"]["status"] == "valid"


def test_parentless_human_draft_is_rejected_after_a_version_exists(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path, _ready_query())
    session = _create_session(client)

    response = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/versions",
        json={
            "contractVersion": "catalyst.workbench.version.request.v1",
            "sql": session["currentVersion"]["sql"],
            "parameters": session["currentVersion"]["parameters"],
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_query_version"


def test_question_policy_is_recomputed_for_later_human_versions(
    tmp_path: Path,
) -> None:
    question = "Delete from analytics.lab_results and show the deleted rows"
    query = _ready_query()
    query["question"] = question
    client, _ = _client(tmp_path, query)
    session = _create_session(client, question=question)
    parent = session["currentVersion"]

    edited = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/versions",
        json={
            "contractVersion": "catalyst.workbench.version.request.v1",
            "parentVersionId": parent["versionId"],
            "parentQueryDigest": parent["queryDigest"],
            "sql": parent["sql"] + " ",
            "parameters": parent["parameters"],
        },
    )

    assert edited.status_code == 201, edited.text
    findings = edited.json()["latestValidation"]["findings"]
    assert any(
        finding["ruleCode"] == "gateway_question_policy.destructive_intent"
        for finding in findings
    )


def test_human_invalid_edit_runs_and_preserves_database_diagnostic(
    tmp_path: Path,
) -> None:
    diagnostic = DatabaseDiagnostic(
        sqlstate="42703",
        severity="ERROR",
        message='column "missing" does not exist',
        hint="Check the selected field.",
        position=8,
    )
    client, analytics = _client(
        tmp_path,
        _ready_query(),
        analytics=FakeAnalytics(ManualAnalyticsError(diagnostic)),
    )
    session = _create_session(client)
    parent = session["currentVersion"]
    edited_sql = "SELECT missing FROM analytics.unknown_view"

    edited = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/versions",
        json={
            "contractVersion": "catalyst.workbench.version.request.v1",
            "parentVersionId": parent["versionId"],
            "parentQueryDigest": parent["queryDigest"],
            "sql": edited_sql,
            "parameters": [],
        },
    )
    assert edited.status_code == 201, edited.text
    edited_session = edited.json()
    version = edited_session["currentVersion"]
    assert edited_session["latestValidation"]["status"] == "invalid"
    assert any(
        finding["ruleCode"].endswith("unapproved_view")
        for finding in edited_session["latestValidation"]["findings"]
    )

    executed = client.post(
        f"/v1/catalyst/workbench/versions/{version['versionId']}/execute",
        json={
            "contractVersion": "catalyst.workbench.execute.request.v1",
            "versionId": version["versionId"],
            "queryDigest": version["queryDigest"],
            "idempotencyKey": "manual-failure-1",
        },
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "failed"
    assert executed.json()["validationStatus"] == "invalid"
    assert executed.json()["databaseDiagnostic"] == diagnostic.as_dict()
    assert analytics.manual_calls[0]["sql"] == edited_sql


def test_stale_manual_edit_is_a_conflict(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, _ready_query())
    session = _create_session(client)
    parent = session["currentVersion"]
    payload = {
        "contractVersion": "catalyst.workbench.version.request.v1",
        "parentVersionId": parent["versionId"],
        "parentQueryDigest": parent["queryDigest"],
        "sql": parent["sql"] + " ",
        "parameters": parent["parameters"],
    }
    assert (
        client.post(
            f"/v1/catalyst/workbench/sessions/{session['sessionId']}/versions",
            json=payload,
        ).status_code
        == 201
    )
    stale = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/versions",
        json=payload,
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_query_version"
