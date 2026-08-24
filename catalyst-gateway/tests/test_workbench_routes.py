from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from src import gateway
from src.catalyst.analytics import (
    AnalyticsColumn,
    DatabaseDiagnostic,
    ManualAnalyticsError,
    ManualAnalyticsResult,
)
from src.catalyst.catalog import Catalog
from src.catalyst.contracts import ContractError, ContractRegistry
from src.catalyst.digest import canonical_sha256, utf8_sha256
from src.catalyst.hub import HubError
from src.catalyst.policy import SqlPolicy
from src.catalyst.service import CatalystService
from src.catalyst.workbench import workbench_query_digest
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
        writer_prompt = "Write one complete PostgreSQL query."
        reviewer_prompt = "Review and return one complete PostgreSQL query."
        profile_evidence = {
            "profileId": PROFILE_ID,
            "profileName": "Catalyst query checked",
            "profileDigest": "0" * 64,
            "writer": {
                "role": "writer",
                "providerId": "llama.cpp",
                "modelClass": "qwen-2.5",
                "modelId": "qwen2.5-coder-14b",
                "config": {"temperature": 0, "seed": 42},
                "systemPrompt": {
                    "promptId": "catalyst-query-generate",
                    "version": "1",
                    "promptRef": "med-agent-hub:prompt:catalyst-query-generate",
                    "promptDigest": utf8_sha256(writer_prompt),
                    "text": writer_prompt,
                },
            },
            "reviewer": {
                "role": "reviewer",
                "providerId": "llama.cpp",
                "modelClass": "gemma-4",
                "modelId": "gemma-e4b",
                "config": {"temperature": 0, "seed": 42},
                "systemPrompt": {
                    "promptId": "catalyst-query-review",
                    "version": "1",
                    "promptRef": "med-agent-hub:prompt:catalyst-query-review",
                    "promptDigest": utf8_sha256(reviewer_prompt),
                    "text": reviewer_prompt,
                },
            },
        }
        compact_profile = deepcopy(profile_evidence)
        compact_profile.pop("profileDigest")
        compact_profile["writer"]["systemPrompt"].pop("text")
        compact_profile["reviewer"]["systemPrompt"].pop("text")
        profile_evidence["profileDigest"] = canonical_sha256(compact_profile)
        return [
            {
                "id": PROFILE_ID,
                "label": "Catalyst query checked",
                "available": True,
                "supported_request_contracts": ["catalyst.query.session-context.v1"],
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
                "revisionCapable": True,
                "profileEvidence": profile_evidence,
            }
        ]

    async def generate_query(self, request: dict) -> dict:
        self.requests.append(deepcopy(request))
        if self.error is not None:
            raise self.error
        query = deepcopy(self.query)
        profile = (await self.list_query_profiles())[0]["profileEvidence"]
        stage = (
            "followup_generation"
            if request["catalystQuery"]["contractVersion"]
            == "catalyst.query.request.v2"
            else "initial_generation"
        )
        timestamp = "2026-07-18T12:00:00Z"
        invocations = [
            {
                "invocationId": "00000000-0000-0000-0000-000000000101",
                "role": "writer",
                "stage": stage,
                "attempt": 1,
                "providerId": profile["writer"]["providerId"],
                "modelId": profile["writer"]["modelId"],
                "configuration": {
                    "temperature": 0,
                    "maxTokens": None,
                    "responseFormat": "json_object",
                },
                "startedAt": timestamp,
                "endedAt": timestamp,
                "durationMs": 1,
                "requestDigest": canonical_sha256(request),
                "responseDigest": canonical_sha256(query),
                "failureDigest": None,
                "outcome": "succeeded",
            }
        ]
        invocations.append(
            {
                "invocationId": "00000000-0000-0000-0000-000000000102",
                "role": "reviewer",
                "stage": "review",
                "attempt": 1,
                "providerId": profile["reviewer"]["providerId"],
                "modelId": profile["reviewer"]["modelId"],
                "configuration": {
                    "temperature": 0,
                    "maxTokens": None,
                    "responseFormat": "json_object",
                },
                "startedAt": timestamp,
                "endedAt": timestamp,
                "durationMs": 1,
                "requestDigest": canonical_sha256(
                    {"request": request, "role": "reviewer"}
                ),
                "responseDigest": canonical_sha256(
                    {"query": query, "role": "reviewer"}
                ),
                "failureDigest": None,
                "outcome": "succeeded",
            }
        )
        query["_hubEvidence"] = {
            "profileEvidence": profile,
            "modelInvocations": invocations,
            "totalModelInvocationDurationMs": sum(
                invocation["durationMs"] for invocation in invocations
            ),
            "exactHubResponse": json.dumps(self.query, separators=(",", ":")),
            "hubResponseContentType": "application/json",
        }
        return query

    async def readiness(self) -> dict:
        return {
            "hub": {"ready": True},
            "queryProfile": {"ready": True},
            "modelRouter": {"ready": True},
        }

    async def aclose(self) -> None:
        return None


class IncompleteProfileHub(FakeHub):
    async def list_query_profiles(self) -> list[dict]:
        profiles = await super().list_query_profiles()
        profiles[0].pop("profileEvidence")
        return profiles


class SwitchableAvailabilityHub(FakeHub):
    available = True

    async def list_query_profiles(self) -> list[dict]:
        profiles = await super().list_query_profiles()
        profiles[0]["available"] = self.available
        profiles[0]["unavailable_reasons"] = (
            [] if self.available else ["model_not_advertised:google/gemma-4-e4b"]
        )
        return profiles


class FailingFollowupHub(FakeHub):
    """Ready on the first turn, generation failure on every one after it.

    Failure on a follow-up is a different situation from failure on an initial
    turn: the session already holds a query someone is working from.
    """

    def __init__(self, ready: dict, rejected: dict) -> None:
        super().__init__(ready)
        self.ready = ready
        self.rejected = rejected

    async def generate_query(self, request: dict) -> dict:
        followup = (
            request["catalystQuery"]["contractVersion"] == "catalyst.query.request.v2"
        )
        self.query = self.rejected if followup else self.ready
        return await super().generate_query(request)


class TransportFailureHub(FakeHub):
    async def generate_query(self, request: dict) -> dict:
        self.requests.append(deepcopy(request))
        profile = (await self.list_query_profiles())[0]["profileEvidence"]
        query = _rejected_query()
        query.pop("diagnosticCandidate")
        query["message"] = (
            "The model backend rejected the query-generation request (HTTP 502)."
        )
        timestamp = "2026-07-18T12:00:00Z"
        query["_hubEvidence"] = {
            "profileEvidence": profile,
            "modelInvocations": [
                {
                    "invocationId": "00000000-0000-0000-0000-000000000103",
                    "role": "writer",
                    "stage": "initial_generation",
                    "attempt": 1,
                    "providerId": profile["writer"]["providerId"],
                    "modelId": profile["writer"]["modelId"],
                    "configuration": {
                        "temperature": 0,
                        "maxTokens": None,
                        "responseFormat": "json_object",
                    },
                    "startedAt": timestamp,
                    "endedAt": timestamp,
                    "durationMs": 1,
                    "requestDigest": canonical_sha256(request),
                    "responseDigest": None,
                    "failureDigest": canonical_sha256(
                        {"status": 502, "role": "writer"}
                    ),
                    "outcome": "transport_failed",
                }
            ],
            "totalModelInvocationDurationMs": 1,
        }
        return query


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


class BlockingFakeAnalytics(FakeAnalytics):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute_manual(self, **kwargs) -> ManualAnalyticsResult:
        self.manual_calls.append(deepcopy(kwargs))
        self.started.set()
        await self.release.wait()
        return ManualAnalyticsResult(
            columns=[AnalyticsColumn(0, "test_name", "text", 25, "string")],
            rows=[[{"type": "string", "value": "Malaria"}]],
            truncated=False,
        )


def _client(
    tmp_path: Path,
    query: dict,
    *,
    analytics: FakeAnalytics | None = None,
    hub: FakeHub | None = None,
    catalog: Catalog | None = None,
    default_query_profile_id: str | None = None,
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
        default_query_profile_id=default_query_profile_id,
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


def test_initial_generation_rejects_incomplete_profile_evidence_before_events(
    tmp_path: Path,
) -> None:
    hub = IncompleteProfileHub(_ready_query())
    client, _ = _client(tmp_path, _ready_query(), hub=hub)

    response = client.post(
        "/v1/catalyst/workbench/sessions",
        json={
            "contractVersion": "catalyst.workbench.session.request.v1",
            "deploymentMode": "demo",
            "question": QUESTION,
            "profileId": PROFILE_ID,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "profile_evidence_unavailable"
    assert _workbench_session_count(tmp_path) == 0
    assert hub.requests == []


def test_initial_generation_rejects_runtime_unavailable_profile_before_events(
    tmp_path: Path,
) -> None:
    hub = SwitchableAvailabilityHub(_ready_query())
    hub.available = False
    client, _ = _client(tmp_path, _ready_query(), hub=hub)

    response = client.post(
        "/v1/catalyst/workbench/sessions",
        json={
            "contractVersion": "catalyst.workbench.session.request.v1",
            "deploymentMode": "demo",
            "question": QUESTION,
            "profileId": PROFILE_ID,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "profile_unavailable"
    assert _workbench_session_count(tmp_path) == 0
    assert hub.requests == []


def test_governed_generation_rejects_runtime_unavailable_profile_before_preview(
    tmp_path: Path,
) -> None:
    hub = SwitchableAvailabilityHub(_ready_query())
    hub.available = False
    client, _ = _client(tmp_path, _ready_query(), hub=hub)

    response = client.post(
        "/v1/catalyst/queries",
        json={
            "contractVersion": "catalyst.question.request.v1",
            "deploymentMode": "demo",
            "question": QUESTION,
            "profileId": PROFILE_ID,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "profile_unavailable"
    assert _preview_count(tmp_path) == 0
    assert hub.requests == []


def test_initial_backend_rejection_is_retained_as_writer_transport_failure(
    tmp_path: Path,
) -> None:
    hub = TransportFailureHub(_ready_query())
    client, _ = _client(tmp_path, _ready_query(), hub=hub)

    session = _create_session(client)

    assert session["currentVersion"] is None
    assert session["draftSeed"] is None
    turns = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()["turns"]
    assert len(turns) == 1
    assert turns[0]["status"] == "failed"
    failure = turns[0]["failure"]
    assert failure["stage"] == "writer_transport"
    assert failure["code"] == "writer_transport_failed"
    assert failure["message"] == (
        "The model backend rejected the query-generation request (HTTP 502)."
    )
    assert failure["rawEvidenceRef"] is None
    assert failure["diagnostic"]["retryable"] is True
    evidence = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns/"
        f"{turns[0]['turnId']}/generation-evidence"
    )
    assert evidence.status_code == 200
    assert evidence.json()["invocations"][0]["outcome"] == "transport_failed"


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
                    {
                        "name": "test_name",
                        "type": "string",
                        "description": "Test",
                    },
                    {
                        "name": "result_value",
                        "type": "decimal",
                        "description": "Value",
                        "nullable": True,
                        "unitColumn": "result_unit",
                    },
                    {
                        "name": "result_unit",
                        "type": "string",
                        "description": "Unit",
                    },
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
                        "nullable": True,
                    },
                    {
                        "name": "patient_id",
                        "type": "string",
                        "description": "Patient",
                        "nullable": False,
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
                        "qualifiedName": "analytics.lab_result_v1",
                        "grain": "one row per result",
                        "columns": [
                            {
                                "name": "observed_at",
                                "logicalType": "date-time",
                                "description": "Observed",
                                "nullable": True,
                            },
                            {
                                "name": "patient_id",
                                "logicalType": "string",
                                "description": "Patient",
                                "nullable": False,
                            },
                        ],
                    },
                    {
                        "name": "zz_result_v1",
                        "qualifiedName": "analytics.zz_result_v1",
                        "grain": "one row per result",
                        "columns": [
                            {
                                "name": "result_unit",
                                "logicalType": "string",
                                "description": "Unit",
                                "nullable": True,
                            },
                            {
                                "name": "result_value",
                                "logicalType": "decimal",
                                "description": "Value",
                                "nullable": True,
                                "unitColumn": "result_unit",
                            },
                            {
                                "name": "test_name",
                                "logicalType": "string",
                                "description": "Test",
                                "nullable": True,
                            },
                        ],
                    },
                ],
            },
            {
                "name": "reporting",
                "views": [
                    {
                        "name": "summary_v1",
                        "qualifiedName": "reporting.summary_v1",
                        "grain": "one row per summary",
                        "columns": [
                            {
                                "name": "a_label",
                                "logicalType": "string",
                                "description": "A",
                                "nullable": True,
                            },
                            {
                                "name": "z_count",
                                "logicalType": "integer",
                                "description": "Z",
                                "nullable": True,
                            },
                        ],
                    }
                ],
            },
        ],
    }
    assert editor_catalog.views == original_views
    assert _preview_count(tmp_path) == 0
    assert _workbench_session_count(tmp_path) == 0


def test_editor_catalog_route_exposes_every_approved_fact_column(
    tmp_path: Path,
) -> None:
    catalog_path = (
        Path(__file__).resolve().parents[2]
        / "analytics"
        / "catalog"
        / "analytics-catalog-v1.json"
    )
    client, _ = _client(
        tmp_path,
        _ready_query(),
        catalog=Catalog.load(catalog_path),
    )

    response = client.get("/v1/catalyst/workbench/catalog")

    assert response.status_code == 200, response.text
    view = response.json()["schemas"][0]["views"][0]
    assert view["qualifiedName"] == "analytics.lab_result_fact_v1"
    assert view["grain"].startswith("Exactly one row per FHIR Observation")
    assert [column["name"] for column in view["columns"]] == sorted(
        [
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
    )
    result_value = next(
        column for column in view["columns"] if column["name"] == "result_value"
    )
    assert result_value == {
        "name": "result_value",
        "logicalType": "decimal",
        "description": (
            "Numeric FHIR Quantity value; do not aggregate across unlike units."
        ),
        "nullable": True,
        "unitColumn": "result_unit",
    }


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


def test_collaboration_models_must_match_requested_profile(tmp_path: Path) -> None:
    query = _collaborative_query()
    query["modelCollaboration"]["reviewer"]["model"] = "unexpected-reviewer"
    client, _ = _client(tmp_path, query)

    session = _create_session(client)

    assert session["currentVersion"] is None
    turns = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()["turns"]
    assert turns[0]["status"] == "failed"
    assert turns[0]["failure"]["code"] == "hub_invalid_response"


def test_literal_predicate_is_valid_for_workbench_and_governed_preview(
    tmp_path: Path,
) -> None:
    query = _policy_bearing_ready_query()
    client, _ = _client(tmp_path, query)

    session = _create_session(client)

    assert session["currentVersion"]["sql"] == query["sql"]
    assert session["latestValidation"]["status"] == "valid"
    assert session["latestValidation"]["findings"] == []
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


def test_sql_policy_is_advisory_for_workbench_but_governed_route_is_unchanged(
    tmp_path: Path,
) -> None:
    query = _ready_query()
    query["sql"] = "DELETE FROM analytics.lab_results"
    query["parameters"] = []
    client, _ = _client(tmp_path, query)

    session = _create_session(client)

    assert session["currentVersion"]["sql"] == query["sql"]
    assert any(
        finding["ruleCode"] == "gateway_sql_policy.operation_not_allowed"
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
    assert governed.json()["violations"] == [
        {
            "code": "operation_not_allowed",
            "message": "Only a read-only SELECT statement is allowed.",
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

    assert session["latestValidation"]["status"] == "valid"
    assert session["latestValidation"]["findings"] == []
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
    assert execution["validationStatus"] == "valid"
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


@pytest.mark.asyncio
async def test_concurrent_same_key_workbench_requests_execute_once(
    tmp_path: Path,
) -> None:
    analytics = BlockingFakeAnalytics()
    client, _ = _client(tmp_path, _ready_query(), analytics=analytics)
    service = client.app.state.catalyst
    created = await service.create_workbench_session(
        {
            "contractVersion": "catalyst.workbench.session.request.v1",
            "deploymentMode": "demo",
            "question": QUESTION,
            "profileId": PROFILE_ID,
        }
    )
    assert created.status_code == 201
    version = created.body["currentVersion"]
    request = {
        "contractVersion": "catalyst.workbench.execute.request.v1",
        "versionId": version["versionId"],
        "queryDigest": version["queryDigest"],
        "idempotencyKey": "concurrent-run",
    }

    first = asyncio.create_task(
        service.execute_workbench_version(version["versionId"], request)
    )
    await asyncio.wait_for(analytics.started.wait(), timeout=1)
    duplicate = await service.execute_workbench_version(version["versionId"], request)

    assert duplicate.status_code == 409
    assert duplicate.body["error"]["code"] == "execution_in_progress"
    assert len(analytics.manual_calls) == 1

    analytics.release.set()
    completed = await asyncio.wait_for(first, timeout=1)
    assert completed.status_code == 200

    replay = await service.execute_workbench_version(version["versionId"], request)
    assert replay.status_code == 200
    assert replay.body["executionId"] == completed.body["executionId"]
    assert replay.body["replayed"] is True
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


def test_reformatted_snapshot_is_the_same_query_not_a_hand_edit(
    tmp_path: Path,
) -> None:
    # The editor presents SQL laid out, so a follow-up on an untouched query
    # sends reflowed text with a digest over that text -- correct, and what the
    # evidence record should hold. The turn path still judged "is this the
    # current version?" by digest equality, so a reflow was classified
    # promoted_human and minted a hand-authored version: the same byte-blind
    # comparison already fixed for the create-version path.
    hub = FakeHub(_ready_query())
    client, _ = _client(tmp_path, _ready_query(), hub=hub)
    session = _create_session(client)
    base = session["currentVersion"]
    reflowed = (
        "select test_name\nfrom analytics.lab_results\n"
        "where result_date >= :start_date\nlimit 2"
    )
    assert reflowed != base["sql"]
    snapshot = {
        "contractVersion": "catalyst.workbench.editor-snapshot.v1",
        "sql": reflowed,
        "parameters": base["parameters"],
        "expectedColumns": base["expectedColumns"],
        # The digest of what is sent -- the integrity check stays byte-exact.
        "editorDigest": workbench_query_digest(
            reflowed, base["parameters"], base["expectedColumns"]
        ),
    }

    followup = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns",
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "Collapse to one row per patient",
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": base["versionId"],
                "queryDigest": base["queryDigest"],
            },
            "editorSnapshot": snapshot,
        },
    )

    assert followup.status_code == 201, followup.text
    turn = followup.json()
    assert turn["snapshotClassification"] == "reused"
    # What the person saw is what is recorded.
    assert turn["editorSnapshot"]["content"]["sql"] == reflowed
    assert turn["manualVersion"] is None
    # The evidence the model reasons from is recorded against the stored
    # version's digest. A reused snapshot is that version, so a reflow must not
    # cost the model its validation and execution context.
    revision = hub.requests[-1]["catalystQuery"]["revision"]
    assert revision["validationContext"] is not None
    assert revision["selection"]["validationRef"] is not None


def test_session_without_a_profile_uses_the_configured_default(
    tmp_path: Path,
) -> None:
    # WS4: the demo server sets CATALYST_QUERY_PROFILE_ID to the one profile it
    # advertises, and a fresh session with no profileId still failed with
    # profile_unavailable naming the *code* default -- because this path read
    # the module constant instead of the configured one. A deployment's
    # configuration has to be what an unspecified request falls back to.
    # Any profile the hub advertises: the point is that configuration decides,
    # not that this particular id is special.
    configured = PROFILE_ID
    client, _ = _client(tmp_path, _ready_query(), default_query_profile_id=configured)

    response = client.post(
        "/v1/catalyst/workbench/sessions",
        json={
            "contractVersion": "catalyst.workbench.session.request.v1",
            "deploymentMode": "demo",
            "question": QUESTION,
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["profileId"] == configured


def test_repair_exhaustion_keeps_the_last_complete_candidate(
    tmp_path: Path,
) -> None:
    """A near-miss is worth more than a red cell.

    When the loop runs out of attempts it still holds the last complete
    candidate it built -- usually one identifier away from correct. Dropping it
    leaves someone with a failure and nothing to work from; keeping it as an
    unselected output on the failed turn lets them take it into the editor.

    A follow-up is where this costs something. An initial turn recovers the
    candidate as the session's draft because there is nothing to overwrite; a
    follow-up has a working query, so the attempt is kept beside it, not in
    place of it.
    """
    rejected = _rejected_query()
    hub = FailingFollowupHub(_ready_query(), rejected)
    client, _ = _client(tmp_path, _ready_query(), hub=hub)

    session = _create_session(client)
    base = session["currentVersion"]

    followup = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns",
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "Bind the date to a named parameter",
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": base["versionId"],
                "queryDigest": base["queryDigest"],
            },
            "editorSnapshot": {
                "contractVersion": "catalyst.workbench.editor-snapshot.v1",
                "sql": base["sql"],
                "parameters": base["parameters"],
                "expectedColumns": base["expectedColumns"],
                "editorDigest": workbench_query_digest(
                    base["sql"], base["parameters"], base["expectedColumns"]
                ),
            },
        },
    )
    assert followup.status_code == 201, followup.text
    turn = followup.json()

    assert turn["status"] == "failed"
    retained = [
        output
        for output in turn["outputVersions"]
        if output["role"] == "writer" and not output["selected"]
    ]
    assert retained, "the failed turn kept no candidate to edit"

    reloaded = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}"
    ).json()
    version = next(
        item
        for item in reloaded["versions"]
        if item["versionId"] == retained[0]["versionId"]
    )
    assert version["sql"] == rejected["diagnosticCandidate"]["candidate"]["sql"]
    assert version["authorType"] == "model"
    # A starting point, not the answer: nothing selected it, and the query the
    # person already had still stands.
    assert turn["selectedVersionId"] is None
    assert reloaded["currentVersionId"] == base["versionId"]


def test_unresolved_findings_are_named_and_classified_as_semantics(
    tmp_path: Path,
) -> None:
    """A failure the lint explained must say what the lint said.

    Repair exhaustion on a real finding is a semantic failure, not a
    structured-output failure: the model honoured its contract every time and
    the request was still unsatisfiable. The stage/code must say so, and the
    finding that explains it -- code, path, evidence -- must reach the failure
    block, where it is read instead of the evidence document.
    """
    query = _rejected_query()
    query["diagnosticCandidate"].pop("candidate")
    query["diagnosticCandidate"]["rawOutput"] = '{"patches": []}'
    client, _ = _client(tmp_path, query)

    session = _create_session(client)
    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()
    failure = timeline["turns"][0]["failure"]

    assert failure["code"] == "generation_findings_unresolved"
    # The role that reported it varies with the profile; the stage names the
    # kind of failure, which is the part that has to be stable.
    assert failure["stage"].endswith("_findings")
    details = failure["diagnostic"]["details"]
    named = {detail["name"]: detail["value"] for detail in details}
    assert "policy.unbound_predicate_literal" in named
    detail = named["policy.unbound_predicate_literal"]
    assert "$.sql" in detail
    assert "named parameters" in detail
    # The message a person reads names the finding, not the pipeline stage.
    assert "structured-output contract" not in failure["message"]
    assert "named parameters" in failure["message"]
    # A lint finding's suggested action is about the query, so it is advice.
    assert "Replace the literal" in failure["message"]


def test_the_loop_does_not_speak_to_the_reader_in_its_own_words(
    tmp_path: Path,
) -> None:
    """The correction loop reports on itself in its own vocabulary.

    "Stop retrying and reject this generation run" is addressed to the runner,
    and "anchored SQL text must occur exactly once" is about the patch format.
    Neither is something the reader can act on, so neither is quoted to them.
    The words survive in the diagnostic, where someone debugging the run looks.
    """
    query = _rejected_query()
    finding = {
        "code": "generation.unchanged_candidate",
        "stage": "query_correct",
        "severity": "error",
        "path": "$",
        "message": "The model repeated an unchanged candidate after feedback.",
        "evidence": "candidate output matched an earlier attempt",
        "suggestedAction": "Stop retrying and reject this generation run.",
    }
    query["diagnosticCandidate"].pop("candidate")
    query["diagnosticCandidate"]["rawOutput"] = '{"patches": []}'
    query["diagnosticCandidate"]["attempts"] = [
        {
            "attempt": 1,
            "status": "failed",
            "finding_codes": [finding["code"]],
            "findings": [finding],
        }
    ]
    client, _ = _client(tmp_path, query)

    session = _create_session(client)
    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()
    failure = timeline["turns"][0]["failure"]

    assert "Stop retrying" not in failure["message"]
    assert "unchanged candidate" not in failure["message"]
    assert (
        failure["message"] == "The model did not produce a usable query in 1 attempt."
    )
    # Nor does it reach the cell's technical tier, which is read in the thread.
    named = {
        detail["name"]: detail["value"] for detail in failure["diagnostic"]["details"]
    }
    assert "generation.unchanged_candidate" not in named
    # What the model returned is recorded, and one click away from the cell.
    assert failure["evidenceAvailable"] is True


def test_asking_for_a_field_the_dataset_lacks_becomes_a_question(
    tmp_path: Path,
) -> None:
    """Some failures are only answerable by the person who asked.

    A repair loop that ends on unknown identifiers has not malfunctioned: the
    request named something this dataset does not have, and no further attempt
    can change that. Classified as a question and phrased as one, the reply is
    a reworded instruction rather than a retry of the same request.
    """
    query = _rejected_query()
    finding = {
        "code": "catalog.unknown_column",
        "stage": "catalog_identifiers",
        "severity": "error",
        "path": "$.sql",
        "message": "SQL references fields absent from the approved catalog.",
        "evidence": "patient_last_name",
        "suggestedAction": "Replace or remove every field not in the catalog.",
    }
    query["diagnosticCandidate"].pop("candidate")
    query["diagnosticCandidate"]["rawOutput"] = '{"patches": []}'
    query["diagnosticCandidate"]["attempts"] = [
        {
            "attempt": 1,
            "status": "failed",
            "finding_codes": [finding["code"]],
            "findings": [finding],
        }
    ]
    client, _ = _client(tmp_path, query)

    session = _create_session(client)
    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()
    failure = timeline["turns"][0]["failure"]

    assert failure["code"] == "needs_clarification"
    assert failure["stage"].endswith("_findings")
    # It names what the query referenced and asks; the exhaustion only proves
    # the *model* found no such column, not that the data lacks the concept --
    # so the message states the first fact and never claims the second.
    assert "patient_last_name" in failure["message"]
    assert "?" in failure["message"]
    assert "couldn't find" in failure["message"]
    assert "This data has no" not in failure["message"]
    # Lint instructions are addressed to the model, not to the reader.
    assert "catalog" not in failure["message"].lower()


def test_a_mixed_failure_is_not_reduced_to_a_question(tmp_path: Path) -> None:
    """Only a purely unanswerable failure becomes a question.

    An unknown identifier alongside a finding the loop could still have fixed
    is not a question -- calling it one would ask the person to resolve
    something the pipeline gave up on.
    """
    query = _rejected_query()
    unknown = {
        "code": "catalog.unknown_column",
        "stage": "catalog_identifiers",
        "severity": "error",
        "path": "$.sql",
        "message": "SQL references fields absent from the approved catalog.",
        "evidence": "patient_last_name",
        "suggestedAction": "Replace or remove every field not in the catalog.",
    }
    query["diagnosticCandidate"].pop("candidate")
    query["diagnosticCandidate"]["rawOutput"] = '{"patches": []}'
    attempt = query["diagnosticCandidate"]["attempts"][0]
    attempt["findings"] = [*attempt["findings"], unknown]
    attempt["finding_codes"] = [finding["code"] for finding in attempt["findings"]]
    client, _ = _client(tmp_path, query)

    session = _create_session(client)
    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()

    assert timeline["turns"][0]["failure"]["code"] == "generation_findings_unresolved"


def test_the_query_finding_outlives_the_repair_that_failed_after_it(
    tmp_path: Path,
) -> None:
    """A later attempt reporting on the machinery does not bury the defect.

    This is the shape of every real exhaustion: attempt 1 produces a candidate
    with a bad identifier, and the patches sent to fix it fail to apply. The
    last thing that happened is a patch that would not apply -- a fact about
    the correction loop. What still stands is the identifier, which is what
    the person asked about and the only thing they can act on.
    """
    query = _rejected_query()
    unknown_column = {
        "code": "catalog.unknown_column",
        "stage": "catalog_identifiers",
        "severity": "error",
        "path": "$.sql",
        "message": "SQL references fields absent from the approved catalog.",
        "evidence": "t2.last_name",
        "suggestedAction": "Replace or remove every field not in the catalog.",
    }
    patch_rejected = {
        "code": "generation.patch_ambiguous",
        "stage": "query_correct",
        "severity": "error",
        "path": "$",
        "message": "Anchored SQL text 't2.last_name' must occur exactly once.",
        "evidence": "generation correction patch was rejected",
        "suggestedAction": "Return only permitted patch operations.",
    }
    query["diagnosticCandidate"].pop("candidate")
    query["diagnosticCandidate"]["rawOutput"] = '{"patches": []}'
    query["diagnosticCandidate"]["attempts"] = [
        {
            "attempt": 1,
            "status": "failed",
            "finding_codes": [unknown_column["code"]],
            "findings": [unknown_column],
        },
        {
            "attempt": 2,
            "status": "failed",
            "finding_codes": [patch_rejected["code"]],
            "findings": [patch_rejected],
        },
    ]
    client, _ = _client(tmp_path, query)

    session = _create_session(client)
    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()
    failure = timeline["turns"][0]["failure"]

    # The unknown identifier is unanswerable without asking, so it reaches the
    # reader as the question it is -- and the patch machinery says nothing.
    assert failure["code"] == "needs_clarification"
    assert "t2.last_name" in failure["message"]
    assert "Anchored SQL text" not in failure["message"]
    named = {
        detail["name"]: detail["value"] for detail in failure["diagnostic"]["details"]
    }
    assert "catalog.unknown_column" in named


def test_a_check_that_only_repeats_the_outcome_is_not_shown_twice(
    tmp_path: Path,
) -> None:
    """The stage check restates the outcome message, which has been replaced.

    The cell's message is now the finding in the reader's terms, and the check
    named `query_generate` carried the generic wording it replaced -- so the
    boilerplate came back on the next line. The check's name still earns its
    place when it says something the message does not.
    """
    query = _rejected_query()
    query["diagnosticCandidate"].pop("candidate")
    query["diagnosticCandidate"]["rawOutput"] = '{"patches": []}'
    query["validation"]["checks"] = [
        {
            "name": "query_generate",
            "status": "failed",
            "message": query["message"],
        },
        {
            "name": "query_lint_attempt_1",
            "status": "failed",
            "message": "Lint rejected attempt 1.",
        },
    ]
    client, _ = _client(tmp_path, query)

    session = _create_session(client)
    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()
    details = timeline["turns"][0]["failure"]["diagnostic"]["details"]

    named = {detail["name"]: detail["value"] for detail in details}
    assert "query_generate" not in named
    assert "Lint rejected attempt 1." in named["query_lint_attempt_1"]
    assert all(
        "structured-output contract" not in detail["value"] for detail in details
    )


def test_a_run_that_learned_nothing_says_so_without_contract_jargon(
    tmp_path: Path,
) -> None:
    """Attempts that never described the query still owe the reader a sentence.

    When every attempt failed on the machinery there is no finding to report,
    and the outcome's own wording is the structured-output boilerplate this
    work exists to remove. What is true and useful is that the model tried and
    did not get there.
    """
    query = _rejected_query()
    query["diagnosticCandidate"].pop("candidate")
    query["diagnosticCandidate"]["rawOutput"] = "not json"
    query["diagnosticCandidate"]["attempts"] = [
        {
            "attempt": attempt,
            "status": "failed",
            "finding_codes": ["contract.invalid_candidate"],
            "findings": [
                {
                    "code": "contract.invalid_candidate",
                    "stage": "output_contract",
                    "severity": "error",
                    "path": "$",
                    "message": "candidate failed the strict JSON Schema contract",
                    "evidence": "candidate failed the strict JSON Schema contract",
                    "suggestedAction": "Return exactly one complete JSON candidate.",
                }
            ],
        }
        for attempt in (1, 2, 3)
    ]
    client, _ = _client(tmp_path, query)

    session = _create_session(client)
    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()
    failure = timeline["turns"][0]["failure"]

    assert "3 attempts" in failure["message"]
    assert "structured-output contract" not in failure["message"]
    assert "JSON Schema" not in failure["message"]
    # Nothing was learned about the request, so this is not the semantic case.
    assert failure["code"] != "generation_findings_unresolved"


def test_shape_failures_stay_classified_as_output_contract(
    tmp_path: Path,
) -> None:
    """A model that cannot produce the contract is a different failure.

    No lint findings means nothing semantic was learned -- the output never
    became a candidate. That keeps the contract wording and the shape code, so
    the two cases stay distinguishable to anyone reading a turn.
    """
    query = _rejected_query()
    query["diagnosticCandidate"] = {"executable": False, "rawOutput": "not json"}
    client, _ = _client(tmp_path, query)

    session = _create_session(client)
    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()
    failure = timeline["turns"][0]["failure"]

    # Whatever the role, this is not the semantic case: nothing was learned
    # about the request, so the contract wording is the honest one.
    assert failure["code"] != "generation_findings_unresolved"
    assert not failure["stage"].endswith("_findings")
    assert "structured-output contract" in failure["message"]


def test_failed_turn_names_its_failed_checks_in_the_failure_block(
    tmp_path: Path,
) -> None:
    # WS3b decision: 201 + an explicit failure block IS the API contract for a
    # failed turn — and the block must name what failed, not just narrate it.
    # The named checks existed at failure time all along; they were dropped on
    # the way into diagnostic.details, which was always [].
    # The recoverable candidate is removed so the turn genuinely fails
    # (a recoverable rejection completes as an unresolved draft instead).
    query = _rejected_query()
    query["diagnosticCandidate"].pop("candidate")
    query["diagnosticCandidate"]["rawOutput"] = "SELECT 1"
    client, _ = _client(tmp_path, query)
    session = _create_session(client)

    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()
    turn = timeline["turns"][0]
    assert turn["status"] == "failed"
    failure = turn["failure"]
    assert failure["stage"]
    assert failure["code"]
    assert failure["message"]
    details = failure["diagnostic"]["details"]
    assert {
        "name": "query_generate",
        "value": "failed — Generation did not pass lint.",
    } in details
    # Passed checks are not failures; they stay out of the failure block.
    assert all(not detail["value"].startswith("passed") for detail in details)


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
    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()
    failed_turn = timeline["turns"][0]
    assert failed_turn["status"] == "failed"
    assert failed_turn["hubTraceId"] == "hub-trace-rejected"
    evidence = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns/"
        f"{failed_turn['turnId']}/generation-evidence"
    ).json()
    assert evidence["correlation"]["hubTraceId"] == "hub-trace-rejected"

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


def test_sql_policy_is_recomputed_for_later_human_versions(
    tmp_path: Path,
) -> None:
    """A version's validation is freshly computed from ITS OWN sql, not
    cached from its parent: a clean initial version followed by a human edit
    that introduces a policy violation must surface that violation on the
    edited version, even though the parent had none."""
    client, _ = _client(tmp_path, _ready_query())
    session = _create_session(client)
    parent = session["currentVersion"]
    assert session["latestValidation"]["findings"] == []

    edited = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/versions",
        json={
            "contractVersion": "catalyst.workbench.version.request.v1",
            "parentVersionId": parent["versionId"],
            "parentQueryDigest": parent["queryDigest"],
            "sql": "DELETE FROM analytics.lab_results",
            "parameters": [],
        },
    )

    assert edited.status_code == 201, edited.text
    findings = edited.json()["latestValidation"]["findings"]
    assert any(
        finding["ruleCode"] == "gateway_sql_policy.operation_not_allowed"
        for finding in findings
    )


def test_changed_manual_sql_drops_stale_model_expected_columns(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path, _ready_query())
    session = _create_session(client)
    parent = session["currentVersion"]
    assert parent["expectedColumns"]

    edited = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/versions",
        json={
            "contractVersion": "catalyst.workbench.version.request.v1",
            "parentVersionId": parent["versionId"],
            "parentQueryDigest": parent["queryDigest"],
            "sql": "SELECT COUNT(*) AS count FROM analytics.lab_results",
            "parameters": [],
            "expectedColumns": parent["expectedColumns"],
        },
    )

    assert edited.status_code == 201, edited.text
    assert edited.json()["currentVersion"]["expectedColumns"] == []


def test_reformatted_sql_reuses_the_current_version(tmp_path: Path) -> None:
    # The same query with its layout changed is not a new query, and running a
    # reformatted buffer must not mint a human-authored version of the model's
    # own work. The UI already refuses to send layout-only changes here; this
    # pins the same judgement at the API boundary, where any client can reach.
    client, _ = _client(tmp_path, _ready_query())
    session = _create_session(client)
    parent = session["currentVersion"]
    assert parent["authorType"] == "model"
    reflowed = (
        "select test_name\nfrom analytics.lab_results\n"
        "where result_date >= :start_date\nlimit 2"
    )
    assert reflowed != parent["sql"]

    saved = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/versions",
        json={
            "contractVersion": "catalyst.workbench.version.request.v1",
            "parentVersionId": parent["versionId"],
            "parentQueryDigest": parent["queryDigest"],
            "sql": reflowed,
            "parameters": parent["parameters"],
            "expectedColumns": parent["expectedColumns"],
        },
    )

    assert saved.status_code == 201, saved.text
    body = saved.json()
    # Read-only reuse: still the model's version, nothing appended.
    assert body["currentVersion"]["versionId"] == parent["versionId"]
    assert body["currentVersion"]["authorType"] == "model"
    assert len(body["versions"]) == len(session["versions"])


def test_reformatted_sql_keeps_columns_when_parameters_change(
    tmp_path: Path,
) -> None:
    # A changed parameter is a real edit and earns a human version — but the
    # declared columns describe the projection, and a reflowed projection is
    # the same projection. Only a genuine SQL change may drop them.
    client, _ = _client(tmp_path, _ready_query())
    session = _create_session(client)
    parent = session["currentVersion"]
    assert parent["expectedColumns"]
    edited_parameters = [dict(parent["parameters"][0], value="2026-02-01")]

    saved = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/versions",
        json={
            "contractVersion": "catalyst.workbench.version.request.v1",
            "parentVersionId": parent["versionId"],
            "parentQueryDigest": parent["queryDigest"],
            "sql": (
                "select test_name\nfrom analytics.lab_results\n"
                "where result_date >= :start_date\nlimit 2"
            ),
            "parameters": edited_parameters,
            "expectedColumns": parent["expectedColumns"],
        },
    )

    assert saved.status_code == 201, saved.text
    version = saved.json()["currentVersion"]
    assert version["versionId"] != parent["versionId"]
    assert version["authorType"] == "human"
    assert version["expectedColumns"] == parent["expectedColumns"]


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
        finding["ruleCode"].endswith("relation_not_found")
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
        # A real edit: under layout-insensitive comparison a trailing space is
        # (correctly) no longer a change, and this test is about staleness,
        # not about what counts as an edit.
        "sql": parent["sql"] + " OFFSET 0",
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


def test_stale_followup_has_no_turn_event_or_hub_generation_side_effects(
    tmp_path: Path,
) -> None:
    hub = FakeHub(_ready_query())
    client, _ = _client(tmp_path, _ready_query(), hub=hub)
    session = _create_session(client)
    stale_base = session["currentVersion"]

    advanced = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/versions",
        json={
            "contractVersion": "catalyst.workbench.version.request.v1",
            "parentVersionId": stale_base["versionId"],
            "parentQueryDigest": stale_base["queryDigest"],
            "sql": stale_base["sql"] + " OFFSET 0",
            "parameters": stale_base["parameters"],
            "expectedColumns": stale_base["expectedColumns"],
        },
    )
    assert advanced.status_code == 201, advanced.text
    current = advanced.json()["currentVersion"]

    turns_url = f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    timeline_before = client.get(turns_url).json()
    hub_requests_before = deepcopy(hub.requests)
    with sqlite3.connect(tmp_path / "gateway.sqlite3") as connection:
        events_before = connection.execute(
            "SELECT * FROM catalyst_workbench_events "
            "WHERE session_id = ? ORDER BY sequence",
            (session["sessionId"],),
        ).fetchall()

    stale = client.post(
        turns_url,
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "Only include finalized observations",
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": stale_base["versionId"],
                "queryDigest": stale_base["queryDigest"],
            },
            "editorSnapshot": {
                "contractVersion": "catalyst.workbench.editor-snapshot.v1",
                "sql": stale_base["sql"],
                "parameters": stale_base["parameters"],
                "expectedColumns": stale_base["expectedColumns"],
                "editorDigest": stale_base["queryDigest"],
            },
        },
    )

    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "stale_query_version"
    assert stale.json()["error"]["details"] == {
        "currentVersionId": current["versionId"],
        "currentQueryDigest": current["queryDigest"],
    }
    assert client.get(turns_url).json() == timeline_before
    with sqlite3.connect(tmp_path / "gateway.sqlite3") as connection:
        events_after = connection.execute(
            "SELECT * FROM catalyst_workbench_events "
            "WHERE session_id = ? ORDER BY sequence",
            (session["sessionId"],),
        ).fetchall()
    assert events_after == events_before
    assert hub.requests == hub_requests_before


def test_followup_rejects_runtime_unavailable_profile_before_events(
    tmp_path: Path,
) -> None:
    hub = SwitchableAvailabilityHub(_ready_query())
    client, _ = _client(tmp_path, _ready_query(), hub=hub)
    session = _create_session(client)
    base = session["currentVersion"]
    turns_url = f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    timeline_before = client.get(turns_url).json()
    hub_requests_before = deepcopy(hub.requests)
    hub.available = False

    response = client.post(
        turns_url,
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "Only include finalized observations",
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": base["versionId"],
                "queryDigest": base["queryDigest"],
            },
            "editorSnapshot": {
                "contractVersion": "catalyst.workbench.editor-snapshot.v1",
                "sql": base["sql"],
                "parameters": base["parameters"],
                "expectedColumns": base["expectedColumns"],
                "editorDigest": base["queryDigest"],
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "profile_unavailable"
    assert client.get(turns_url).json() == timeline_before
    assert hub.requests == hub_requests_before


def test_initial_and_followup_turn_routes_preserve_exact_context_and_evidence(
    tmp_path: Path,
) -> None:
    hub = FakeHub(_ready_query())
    client, analytics = _client(tmp_path, _ready_query(), hub=hub)
    session = _create_session(client)

    initial = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    )
    assert initial.status_code == 200, initial.text
    initial_turn = initial.json()["turns"][0]
    assert initial_turn["kind"] == "initial"
    assert initial_turn["origin"] == "recorded"
    assert initial_turn["status"] == "completed"
    assert [event["status"] for event in initial_turn["events"]] == [
        "requested",
        "completed",
    ]

    base = session["currentVersion"]
    snapshot = {
        "contractVersion": "catalyst.workbench.editor-snapshot.v1",
        "sql": base["sql"],
        "parameters": base["parameters"],
        "expectedColumns": base["expectedColumns"],
        "editorDigest": base["queryDigest"],
    }
    followup = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns",
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "Only include finalized observations",
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": base["versionId"],
                "queryDigest": base["queryDigest"],
            },
            "editorSnapshot": snapshot,
        },
    )

    assert followup.status_code == 201, followup.text
    turn = followup.json()
    assert turn["status"] == "completed"
    assert turn["snapshotClassification"] == "reused"
    assert turn["editorSnapshot"]["content"] == snapshot
    assert turn["effectiveBaseVersion"] == turn["observedBase"]
    assert turn["resultingCurrentVersion"]["versionId"] == turn["selectedVersionId"]
    assert analytics.manual_calls == []  # generation never auto-runs SQL

    request = hub.requests[-1]
    assert request["messages"] == [
        {"role": "user", "content": "Only include finalized observations"}
    ]
    assert request["catalystQuery"]["contractVersion"] == ("catalyst.query.request.v2")
    revision = request["catalystQuery"]["revision"]
    assert revision["editorSnapshot"] == snapshot
    assert revision["instructionHistory"][-1]["instruction"] == QUESTION
    assert revision["validationContext"]["queryDigest"] == base["queryDigest"]
    assert revision["executionContext"] is None
    assert (
        "execution_result_rows"
        in revision["selection"]["omissions"]["prohibitedClasses"]
    )

    evidence = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns/"
        f"{turn['turnId']}/generation-evidence"
    )
    assert evidence.status_code == 200, evidence.text
    detail = evidence.json()
    assert detail["instruction"] == "Only include finalized observations"
    assert detail["editorSnapshot"]["content"] == snapshot
    assert detail["hubRequest"]["exactPayload"]
    assert "execution_result_rows" in detail["prohibitedClasses"]
    assert "hidden_reasoning" in detail["prohibitedClasses"]


def test_followup_rejects_bad_snapshot_digest_without_events(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, _ready_query())
    session = _create_session(client)
    base = session["currentVersion"]
    before = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()

    response = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns",
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "Refine this",
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": base["versionId"],
                "queryDigest": base["queryDigest"],
            },
            "editorSnapshot": {
                "contractVersion": "catalyst.workbench.editor-snapshot.v1",
                "sql": base["sql"],
                "parameters": base["parameters"],
                "expectedColumns": base["expectedColumns"],
                "editorDigest": "0" * 64,
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "editor_snapshot_digest_mismatch"
    after = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()
    assert after == before


# --- the writer's terminal answers, as turns -------------------------------


def _clarification_query(question: str = "Which date window did you mean?") -> dict:
    return {
        "contractVersion": "catalyst.query.v1",
        "deploymentMode": "demo",
        "status": "needs_clarification",
        "question": QUESTION,
        "clarification": question,
        "validation": {"status": "warned", "checks": []},
        "provenance": {
            "profileId": PROFILE_ID,
            "traceId": "hub-trace-clarify",
            "contextSourceIds": ["catalog:openelis-demo:2026.07"],
        },
    }


def _unsupported_query(reason: str = "This data holds no home address.") -> dict:
    return {
        "contractVersion": "catalyst.query.v1",
        "deploymentMode": "demo",
        "status": "unsupported",
        "question": QUESTION,
        "message": reason,
        "validation": {"status": "rejected", "checks": []},
        "provenance": {
            "profileId": PROFILE_ID,
            "traceId": "hub-trace-unsupported",
            "contextSourceIds": ["catalog:openelis-demo:2026.07"],
        },
    }


def test_a_clarification_turn_publishes_the_writers_question(tmp_path: Path) -> None:
    """The question is the answer: stored verbatim, not summarised."""
    question = "Which date window and which result types did you mean?"
    client, _ = _client(tmp_path, _clarification_query(question))

    session = _create_session(client)
    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()
    turn = timeline["turns"][0]

    assert turn["writerOutcome"] == "needs_clarification"
    assert turn["failure"]["code"] == "needs_clarification"
    assert turn["failure"]["message"] == question
    assert turn["outputVersions"] == []
    assert turn["selectedVersionId"] is None


def test_an_unsupported_turn_publishes_the_writers_reason(tmp_path: Path) -> None:
    reason = "This data holds no home address for a patient."
    client, _ = _client(tmp_path, _unsupported_query(reason))

    session = _create_session(client)
    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()
    turn = timeline["turns"][0]

    assert turn["writerOutcome"] == "unsupported"
    assert turn["failure"]["code"] == "unsupported"
    assert turn["failure"]["message"] == reason


def test_a_ready_turn_publishes_its_outcome_too(tmp_path: Path) -> None:
    """One field answers "what did the writer do" for every turn."""
    client, _ = _client(tmp_path, _ready_query())
    session = _create_session(client)
    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()

    assert timeline["turns"][0]["writerOutcome"] == "ready"


def test_a_clarification_leaves_the_working_query_alone(tmp_path: Path) -> None:
    """A question must not disturb what the person already had."""
    hub = FailingFollowupHub(_ready_query(), _clarification_query())
    client, _ = _client(tmp_path, _ready_query(), hub=hub)
    session = _create_session(client)
    base = session["currentVersion"]

    followup = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns",
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "Show recent results",
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": base["versionId"],
                "queryDigest": base["queryDigest"],
            },
            "editorSnapshot": {
                "contractVersion": "catalyst.workbench.editor-snapshot.v1",
                "sql": base["sql"],
                "parameters": base["parameters"],
                "expectedColumns": base["expectedColumns"],
                "editorDigest": workbench_query_digest(
                    base["sql"], base["parameters"], base["expectedColumns"]
                ),
            },
        },
    )
    assert followup.status_code == 201, followup.text
    turn = followup.json()

    assert turn["writerOutcome"] == "needs_clarification"
    assert turn["outputVersions"] == []
    reloaded = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}"
    ).json()
    assert reloaded["currentVersionId"] == base["versionId"]


def test_a_hand_written_query_is_held_to_the_same_surface_as_the_writer(
    tmp_path: Path,
) -> None:
    """One reviewed surface, or the model is governed and the person is not.

    Before Phase 1 the writer was restricted to the approved views while a
    hand-written query was checked only against everything the database
    happened to expose, so a person could join a relation the model was never
    told existed -- and did.
    """
    catalog = Catalog(
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
                "approved": True,
                "fields": [
                    {"name": "test_name", "type": "string", "description": "Test"},
                ],
            },
            {
                "name": "public.raw_side_table",
                "version": "1",
                "grain": "readable, but never reviewed",
                "approved": False,
                "fields": [
                    {"name": "test_name", "type": "string", "description": "Test"},
                ],
            },
        ],
        freshness={},
        approved_names=frozenset({"analytics.lab_results"}),
    )
    client, _ = _client(tmp_path, _ready_query(), catalog=catalog)
    session = _create_session(client)
    base = session["currentVersion"]

    response = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/versions",
        json={
            "contractVersion": "catalyst.workbench.version.request.v1",
            "sql": "SELECT test_name FROM public.raw_side_table",
            "parameters": [],
            "expectedColumns": [
                {"name": "test_name", "logicalType": "string", "nullable": True}
            ],
            "parentVersionId": base["versionId"],
            "parentQueryDigest": base["queryDigest"],
        },
    )
    assert response.status_code == 201, response.text
    validation = response.json()["latestValidation"]

    assert validation["status"] == "invalid"
    codes = {finding["ruleCode"] for finding in validation["findings"]}
    assert any("relation" in code or "catalog" in code for code in codes), codes


# --- pin controls ----------------------------------------------------------


def test_a_person_pins_guidance_from_the_composer(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, _ready_query())
    session = _create_session(client)

    response = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/guidance",
        json={
            "contractVersion": "catalyst.workbench.guidance.request.v1",
            "text": "Exclude do_not_perform rows.",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["guidance"][0]["text"] == "Exclude do_not_perform rows."
    assert body["guidance"][0]["source"] == "human"
    assert body["guidance"][0]["state"] == "active"


def test_a_session_carries_its_active_guidance(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, _ready_query())
    session = _create_session(client)
    client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/guidance",
        json={
            "contractVersion": "catalyst.workbench.guidance.request.v1",
            "text": "Names live on the patient dimension.",
        },
    )

    reloaded = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}"
    ).json()

    assert [entry["text"] for entry in reloaded["guidance"]] == [
        "Names live on the patient dimension."
    ]


def test_unpinning_stops_delivery_and_keeps_the_record(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, _ready_query())
    session = _create_session(client)
    pinned = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/guidance",
        json={
            "contractVersion": "catalyst.workbench.guidance.request.v1",
            "text": "temporary",
        },
    ).json()["guidance"][0]

    response = client.delete(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}"
        f"/guidance/{pinned['entryId']}"
    )

    assert response.status_code == 200, response.text
    assert response.json()["guidance"] == []


def test_blank_guidance_is_refused_with_a_clear_error(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, _ready_query())
    session = _create_session(client)

    response = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/guidance",
        json={
            "contractVersion": "catalyst.workbench.guidance.request.v1",
            "text": "   ",
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_request"


def test_pinned_guidance_reaches_the_writer_on_the_next_turn(
    tmp_path: Path,
) -> None:
    """A composer pin becomes active on the next turn, not retroactively."""
    hub = FailingFollowupHub(_ready_query(), _ready_query())
    client, _ = _client(tmp_path, _ready_query(), hub=hub)
    session = _create_session(client)
    base = session["currentVersion"]
    client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/guidance",
        json={
            "contractVersion": "catalyst.workbench.guidance.request.v1",
            "text": "Exclude do_not_perform rows.",
        },
    )

    client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns",
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "Group by medication name",
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": base["versionId"],
                "queryDigest": base["queryDigest"],
            },
            "editorSnapshot": {
                "contractVersion": "catalyst.workbench.editor-snapshot.v1",
                "sql": base["sql"],
                "parameters": base["parameters"],
                "expectedColumns": base["expectedColumns"],
                "editorDigest": workbench_query_digest(
                    base["sql"], base["parameters"], base["expectedColumns"]
                ),
            },
        },
    )

    revision = hub.requests[-1]["catalystQuery"]["revision"]
    context = revision["sessionContext"]
    assert [item["text"] for item in context["guidance"]["entries"]] == [
        "Exclude do_not_perform rows."
    ]
    # The initial turn ran before the pin existed and must not have carried it.
    initial = hub.requests[0]["catalystQuery"]
    assert "sessionContext" not in initial or not initial["sessionContext"].get(
        "guidance"
    )


def test_the_request_records_what_the_caps_left_out(tmp_path: Path) -> None:
    hub = FailingFollowupHub(_ready_query(), _ready_query())
    client, _ = _client(tmp_path, _ready_query(), hub=hub)
    session = _create_session(client)
    base = session["currentVersion"]
    for index in range(21):
        client.post(
            f"/v1/catalyst/workbench/sessions/{session['sessionId']}/guidance",
            json={
                "contractVersion": "catalyst.workbench.guidance.request.v1",
                "text": f"entry {index}",
            },
        )

    client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns",
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "Group by medication name",
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": base["versionId"],
                "queryDigest": base["queryDigest"],
            },
            "editorSnapshot": {
                "contractVersion": "catalyst.workbench.editor-snapshot.v1",
                "sql": base["sql"],
                "parameters": base["parameters"],
                "expectedColumns": base["expectedColumns"],
                "editorDigest": workbench_query_digest(
                    base["sql"], base["parameters"], base["expectedColumns"]
                ),
            },
        },
    )

    context = hub.requests[-1]["catalystQuery"]["revision"]["sessionContext"]
    assert len(context["guidance"]["entries"]) == 20
    assert context["omissions"][0]["reason"] == "active_entry_cap"
    assert len(context["omissions"][0]["itemIds"]) == 1


class HubWithoutSessionContext(FailingFollowupHub):
    """A Hub that has not been taught the Phase 1 request shape."""

    async def list_query_profiles(self) -> list[dict]:
        profiles = await super().list_query_profiles()
        for profile in profiles:
            profile.pop("supported_request_contracts", None)
        return profiles


def test_a_hub_that_cannot_read_the_new_shape_is_not_sent_it(
    tmp_path: Path,
) -> None:
    """Catalyst deploys before the Hub does, so the new layer is negotiated.

    Sending a context the Hub does not understand risks it being ignored
    silently -- or worse, echoed back as if it had been honoured.
    """
    hub = HubWithoutSessionContext(_ready_query(), _ready_query())
    client, _ = _client(tmp_path, _ready_query(), hub=hub)
    session = _create_session(client)
    base = session["currentVersion"]
    client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/guidance",
        json={
            "contractVersion": "catalyst.workbench.guidance.request.v1",
            "text": "Exclude do_not_perform rows.",
        },
    )

    client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns",
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "Group by medication name",
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": base["versionId"],
                "queryDigest": base["queryDigest"],
            },
            "editorSnapshot": {
                "contractVersion": "catalyst.workbench.editor-snapshot.v1",
                "sql": base["sql"],
                "parameters": base["parameters"],
                "expectedColumns": base["expectedColumns"],
                "editorDigest": workbench_query_digest(
                    base["sql"], base["parameters"], base["expectedColumns"]
                ),
            },
        },
    )

    revision = hub.requests[-1]["catalystQuery"]["revision"]
    assert "sessionContext" not in revision


# --- answering the writer's question ---------------------------------------


def test_a_clarification_can_be_answered_and_produces_the_first_query(
    tmp_path: Path,
) -> None:
    """The writer asked, so there is no query yet -- the answer still lands.

    A session whose opening turn asked a question holds no version, so the
    person answering has nothing to snapshot. The turn carries no editor
    content and the answer produces the session's first query.
    """
    hub = FakeHub(_clarification_query("Which date window did you mean?"))
    client, _ = _client(tmp_path, _clarification_query(), hub=hub)
    session = _create_session(client)
    assert session["currentVersion"] is None

    hub.query = _ready_query()
    answered = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns",
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "The last 90 days, and only CD4 count.",
            "profileId": PROFILE_ID,
            "observedBase": None,
            "editorSnapshot": None,
        },
    )

    assert answered.status_code == 201, answered.text
    turn = answered.json()
    assert turn["status"] == "completed"
    assert turn["snapshotClassification"] == "not_applicable"
    assert turn["editorSnapshot"] is None
    assert turn["effectiveBaseVersion"] is None
    assert turn["selectedVersionId"] is not None
    assert turn["resultingCurrentVersion"]["versionId"] == turn["selectedVersionId"]

    revision = hub.requests[-1]["catalystQuery"]["revision"]
    assert revision["editorSnapshot"] is None
    assert revision["baseClassification"] == "not_applicable"
    # The original question stays the history; the answer is the current
    # instruction. Together they are what the writer has to work from.
    assert [entry["instruction"] for entry in revision["instructionHistory"]] == [
        QUESTION
    ]
    assert revision["currentInstruction"] == "The last 90 days, and only CD4 count."


def test_an_absent_snapshot_is_refused_when_the_session_has_a_query(
    tmp_path: Path,
) -> None:
    """Only a session with nothing to snapshot may omit the editor content.

    Dropping the snapshot on a session that has a current version would hide a
    human edit and silently regenerate from scratch, so it is a contract error
    rather than a shortcut.
    """
    hub = FakeHub(_ready_query())
    client, _ = _client(tmp_path, _ready_query(), hub=hub)
    session = _create_session(client)
    assert session["currentVersion"] is not None
    turns_url = f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    before = client.get(turns_url).json()

    response = client.post(
        turns_url,
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "Only include finalized observations",
            "profileId": PROFILE_ID,
            "observedBase": None,
            "editorSnapshot": None,
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "editor_snapshot_required"
    assert client.get(turns_url).json() == before


def _answered_question_turn(tmp_path: Path) -> dict:
    """A real published turn that answered the writer's question.

    Taken from the route rather than hand-built, so the fixture cannot drift
    away from what the Gateway actually publishes.
    """
    hub = FakeHub(_clarification_query())
    client, _ = _client(tmp_path, _clarification_query(), hub=hub)
    session = _create_session(client)
    hub.query = _ready_query()
    answered = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns",
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "The last 90 days, and only CD4 count.",
            "profileId": PROFILE_ID,
            "observedBase": None,
            "editorSnapshot": None,
        },
    )
    assert answered.status_code == 201, answered.text
    return answered.json()


_A_VERSION_REF = {
    "versionId": "00000000-0000-0000-0000-0000000000b1",
    "queryDigest": "a" * 64,
}


def _a_real_snapshot_record(tmp_path: Path, turn: dict) -> dict:
    """A snapshot record the Gateway itself published, re-addressed to `turn`.

    Hand-building one risks tripping the record's own rules instead of the
    correlation under test, which is exactly the confound this avoids.
    """
    donor = tmp_path / "donor"
    donor.mkdir()
    hub = FakeHub(_ready_query())
    client, _ = _client(donor, _ready_query(), hub=hub)
    session = _create_session(client)
    base = session["currentVersion"]
    revised = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns",
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "Only include finalized observations",
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": base["versionId"],
                "queryDigest": base["queryDigest"],
            },
            "editorSnapshot": {
                "contractVersion": "catalyst.workbench.editor-snapshot.v1",
                "sql": base["sql"],
                "parameters": base["parameters"],
                "expectedColumns": base["expectedColumns"],
                "editorDigest": base["queryDigest"],
            },
        },
    )
    assert revised.status_code == 201, revised.text
    record = deepcopy(revised.json()["editorSnapshot"])
    record["sessionId"] = turn["sessionId"]
    record["turnId"] = turn["turnId"]
    return record


def test_a_turn_that_revised_nothing_must_carry_no_editor_content(
    tmp_path: Path,
) -> None:
    """An absent editor and a based-on-nothing turn are one fact, not two.

    Each rejected shape below is otherwise complete and satisfies every other
    rule in the turn contract -- including the observed-base rule that a
    revising turn must name what it observed -- so only the correlation
    between the editor and the classification can be what refuses it.
    """
    contracts = ContractRegistry.load(CONTRACTS)
    answered = _answered_question_turn(tmp_path)

    contracts.validate("catalyst-workbench-turn-v1.schema.json", answered)

    revised_nothing_but_claims_a_base = {
        **answered,
        "snapshotClassification": "reused",
        "observedBase": _A_VERSION_REF,
        "effectiveBaseVersion": _A_VERSION_REF,
    }
    with pytest.raises(ContractError, match="not_applicable"):
        contracts.validate(
            "catalyst-workbench-turn-v1.schema.json",
            revised_nothing_but_claims_a_base,
        )

    claims_nothing_but_carries_an_editor = {
        **answered,
        "editorSnapshot": _a_real_snapshot_record(tmp_path, answered),
    }
    with pytest.raises(ContractError, match="editorSnapshot"):
        contracts.validate(
            "catalyst-workbench-turn-v1.schema.json",
            claims_nothing_but_carries_an_editor,
        )
