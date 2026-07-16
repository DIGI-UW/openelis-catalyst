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
from src.catalyst.hub import HubClient, HubError
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
            "profileId": "catalyst-query-checked",
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
            "profileId": "catalyst-query-checked",
            "traceId": "hub-trace-1",
            "contextSourceIds": ["catalog:openelis-demo:2026.07"],
        },
    }
    if status == "needs_clarification":
        query["clarification"] = "Which date range?"
    else:
        query["message"] = f"Question is {status}"
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


def make_service(
    tmp_path: Path,
    response: dict | None = None,
    *,
    hub: FakeHub | None = None,
    analytics: FakeAnalytics | None = None,
    clock: Clock | None = None,
    ttl_seconds: int = 60,
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
        preview_ttl_seconds=ttl_seconds,
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


def test_loads_and_checks_all_nine_normative_schemas():
    registry = ContractRegistry.load(CONTRACTS)
    assert len(registry.schemas) == 9
    assert set(registry.schemas) == {
        "catalyst-execute-request-v1.schema.json",
        "catalyst-execution-outcome-v1.schema.json",
        "catalyst-policy-outcome-v1.schema.json",
        "catalyst-preview-v1.schema.json",
        "catalyst-query-completion-v1.schema.json",
        "catalyst-query-request-v1.schema.json",
        "catalyst-query-v1.schema.json",
        "catalyst-question-request-v1.schema.json",
        "catalyst-table-v1.schema.json",
    }
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("models_body", "code"),
    [
        ({"not_data": []}, "profile_incompatible"),
        ({"data": []}, "profile_unavailable"),
        (
            {
                "data": [
                    {
                        "id": "catalyst-query-checked",
                        "available": False,
                        "capabilities": {"outputContracts": ["catalyst.query.v1"]},
                    }
                ]
            },
            "profile_unavailable",
        ),
        (
            {
                "data": [
                    {
                        "id": "catalyst-query-checked",
                        "available": True,
                        "capabilities": {"outputContracts": ["other.v1"]},
                    }
                ]
            },
            "profile_incompatible",
        ),
    ],
)
async def test_hub_discovery_fails_closed(models_body: dict, code: str):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=models_body)

    client = HubClient(
        "http://hub",
        ContractRegistry.load(CONTRACTS),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(HubError) as error:
        await client.discover_query_profile()
    assert error.value.code == code
    await client.aclose()


@pytest.mark.asyncio
async def test_hub_discovery_and_completion_are_strict():
    sent = {}
    query = ready_query()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "catalyst-query-checked",
                            "available": True,
                            "capabilities": {
                                "outputContracts": ["catalyst.query.v1"],
                                "modelRouter": True,
                            },
                        }
                    ]
                },
            )
        sent.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "completion-1",
                "object": "chat.completion",
                "model": "catalyst-query-checked",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(query),
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    registry = ContractRegistry.load(CONTRACTS)
    client = HubClient(
        "http://hub",
        registry,
        transport=httpx.MockTransport(handler),
    )
    request = build_query_request(
        "Count tests since July 1",
        catalog(),
        max_rows=2,
        statement_timeout_ms=500,
        request_id="request-1",
        trace_id="trace-1",
    )
    result = await client.generate_query(request)
    assert result == query
    assert sent["model"] == "catalyst-query-checked"
    assert sent["stream"] is False
    assert sent["catalystQuery"]["requiredOutputContract"] == "catalyst.query.v1"
    assert "dsn" not in json.dumps(sent).lower()
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "code"),
    [
        (
            {
                "id": "x",
                "object": "chat.completion",
                "model": "wrong-profile",
                "choices": [],
            },
            "hub_invalid_response",
        ),
        (
            {
                "id": "x",
                "object": "chat.completion",
                "model": "catalyst-query-checked",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "```json\n{}\n```",
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
            "hub_invalid_response",
        ),
    ],
)
async def test_hub_rejects_invalid_completion(response: dict, code: str):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "catalyst-query-checked",
                            "available": True,
                            "capabilities": {"outputContracts": ["catalyst.query.v1"]},
                        }
                    ]
                },
            )
        return httpx.Response(200, json=response)

    client = HubClient(
        "http://hub",
        ContractRegistry.load(CONTRACTS),
        transport=httpx.MockTransport(handler),
    )
    request = build_query_request(
        "Question",
        catalog(),
        max_rows=2,
        statement_timeout_ms=500,
        request_id="request-1",
        trace_id="trace-1",
    )
    with pytest.raises(HubError) as error:
        await client.generate_query(request)
    assert error.value.code == code
    await client.aclose()


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
            lambda q: q["target"].update(approvedViews=["private.results"]),
            "unapproved_view",
        ),
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


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DELETE FROM analytics.lab_results", "operation_not_allowed"),
        (
            "SELECT * FROM analytics.lab_results; SELECT 1",
            "multiple_statements",
        ),
        ("SELECT * FROM private.results", "unapproved_view"),
        (
            "SELECT * FROM analytics.lab_results WHERE test_name = 'HIV'",
            "unbound_literal",
        ),
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
        approved_views={"analytics.lab_results"},
    )
    assert code in {item.code for item in violations}


def test_sql_policy_accepts_one_parameterized_select():
    violations = SqlPolicy(max_rows=2).evaluate(
        ready_query(),
        approved_views={"analytics.lab_results"},
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


def test_preview_store_is_transactional_idempotent_and_expiring(tmp_path: Path):
    clock = Clock()
    store = PreviewStore(tmp_path / "state.sqlite3", now=clock)
    preview = store.create_preview(ready_query(), ttl_seconds=5)

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

    expiring = store.create_preview(ready_query(), ttl_seconds=5)
    clock.advance(6)
    expired = store.begin_execution(
        expiring["previewId"],
        expiring["queryDigest"],
        "expiry-key",
    )
    assert expired.status_code == 410
    assert expired.body["status"] == "expired"

    missing = store.begin_execution("unknown", "digest", "key")
    assert missing.status_code == 404
    assert missing.body["status"] == "not_found"
    assert store.poll("unknown", "key").status_code == 404


def test_preview_store_replays_failure_and_poll_does_not_execute(tmp_path: Path):
    store = PreviewStore(tmp_path / "state.sqlite3")
    preview = store.create_preview(ready_query(), ttl_seconds=30)
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
    preview = store.create_preview(ready_query(), ttl_seconds=30)
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
    preview = store.create_preview(query, ttl_seconds=30)
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
    preview = store.create_preview(query, ttl_seconds=30)
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
    assert hub.requests[0]["messages"] == [{"role": "user", "content": question}]


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


def test_execute_route_in_progress_not_found_expiry_and_bad_path(tmp_path: Path):
    clock = Clock()
    service, _, _, registry = make_service(tmp_path, clock=clock, ttl_seconds=1)
    client = TestClient(gateway.create_app(catalyst_service=service))
    missing = client.get(
        "/v1/catalyst/executions/missing",
        params={"idempotencyKey": "key"},
    )
    assert missing.status_code == 404
    registry.validate("catalyst-execution-outcome-v1.schema.json", missing.json())

    preview = service.store.create_preview(ready_query(), ttl_seconds=10)
    service.store.begin_execution(
        preview["previewId"], preview["queryDigest"], "active"
    )
    active = client.get(
        f"/v1/catalyst/executions/{preview['previewId']}",
        params={"idempotencyKey": "active"},
    )
    assert active.status_code == 202
    assert active.json()["status"] == "in_progress"

    expiring = service.store.create_preview(ready_query(), ttl_seconds=1)
    clock.advance(2)
    expired = client.post(
        f"/v1/catalyst/previews/{expiring['previewId']}/execute",
        json=execute_body(expiring, "expired"),
    )
    assert expired.status_code == 410
    assert expired.json()["status"] == "expired"

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
    preview = service.store.create_preview(ready_query(), ttl_seconds=30)
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
    preview = service.store.create_preview(ready_query(), ttl_seconds=30)
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
