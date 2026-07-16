from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from .analytics import AnalyticsResult
from .catalog import Catalog
from .contracts import ContractError, ContractRegistry
from .digest import query_digest
from .hub import HubError
from .policy import QueryInvariantError, SqlPolicy, Violation, validate_query_invariants
from .request import build_query_request
from .storage import ExecutionDecision, PreviewStore
from .table import build_table


class HubProtocol(Protocol):
    async def generate_query(self, request: dict[str, Any]) -> dict[str, Any]: ...

    async def readiness(self) -> dict[str, dict[str, Any]]: ...


class AnalyticsProtocol(Protocol):
    async def execute(
        self,
        *,
        sql: str,
        parameters: list[dict[str, Any]],
        max_rows: int,
        statement_timeout_ms: int,
    ) -> AnalyticsResult: ...

    async def freshness(self) -> dict[str, Any]: ...

    async def readiness(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ServiceResponse:
    status_code: int
    body: dict[str, Any]


class CatalystService:
    def __init__(
        self,
        *,
        contracts: ContractRegistry,
        catalog: Catalog,
        hub: HubProtocol,
        analytics: AnalyticsProtocol,
        store: PreviewStore,
        sql_policy: SqlPolicy,
        max_rows: int,
        statement_timeout_ms: int,
        preview_ttl_seconds: int,
    ) -> None:
        self.contracts = contracts
        self.catalog = catalog
        self.hub = hub
        self.analytics = analytics
        self.store = store
        self.sql_policy = sql_policy
        self.max_rows = max_rows
        self.statement_timeout_ms = statement_timeout_ms
        self.preview_ttl_seconds = preview_ttl_seconds

    async def submit_question(self, payload: dict[str, Any]) -> ServiceResponse:
        try:
            self.contracts.validate(
                "catalyst-question-request-v1.schema.json",
                payload,
            )
            question = payload["question"]
            if not question.strip():
                raise ContractError("Question must contain non-whitespace text.")
        except (ContractError, KeyError, TypeError) as error:
            return self._error(400, "invalid_request", str(error))

        request_id = str(uuid.uuid4())
        catalyst_trace_id = str(uuid.uuid4())
        request = build_query_request(
            question,
            self.catalog,
            max_rows=self.max_rows,
            statement_timeout_ms=self.statement_timeout_ms,
            request_id=request_id,
            trace_id=catalyst_trace_id,
        )
        try:
            self.contracts.validate(
                "catalyst-query-request-v1.schema.json",
                request,
            )
            query = await self.hub.generate_query(request)
            self.contracts.validate("catalyst-query-v1.schema.json", query)
            validate_query_invariants(query, request)
        except HubError as error:
            return self._error(502, error.code, str(error))
        except (ContractError, QueryInvariantError) as error:
            return self._error(502, "hub_invalid_response", str(error))

        if query["status"] != "ready":
            return ServiceResponse(200, query)

        violations = self.sql_policy.evaluate(
            query,
            approved_views=self.catalog.approved_view_names,
        )
        if violations:
            outcome = self._policy_outcome(violations, catalyst_trace_id)
            self.contracts.validate(
                "catalyst-policy-outcome-v1.schema.json",
                outcome,
            )
            return ServiceResponse(422, outcome)

        preview = self.store.create_preview(
            query,
            ttl_seconds=self.preview_ttl_seconds,
            catalyst_trace_id=catalyst_trace_id,
        )
        self.contracts.validate("catalyst-preview-v1.schema.json", preview)
        return ServiceResponse(201, preview)

    async def execute_preview(
        self,
        preview_id: str,
        payload: dict[str, Any],
    ) -> ServiceResponse:
        try:
            self.contracts.validate(
                "catalyst-execute-request-v1.schema.json",
                payload,
            )
            if payload["previewId"] != preview_id:
                raise ContractError("Path and body preview IDs must match.")
        except (ContractError, KeyError, TypeError) as error:
            return self._error(400, "invalid_request", str(error))

        decision = self.store.begin_execution(
            preview_id,
            payload["queryDigest"],
            payload["idempotencyKey"],
        )
        if decision.action != "execute":
            return self._execution_response(decision)

        assert decision.preview is not None
        assert decision.query is not None
        assert decision.accepted_at is not None
        assert decision.catalyst_trace_id is not None
        started = time.perf_counter()
        try:
            self._revalidate_execution(decision.preview, decision.query)
            result = await self.analytics.execute(
                sql=decision.query["sql"],
                parameters=decision.query["parameters"],
                max_rows=self.max_rows,
                statement_timeout_ms=self.statement_timeout_ms,
            )
            freshness = await self.analytics.freshness()
            duration_ms = int((time.perf_counter() - started) * 1000)
            table = build_table(
                preview=decision.preview,
                query=decision.query,
                result=result,
                freshness=freshness,
                accepted_at=decision.accepted_at,
                duration_ms=duration_ms,
                statement_timeout_ms=self.statement_timeout_ms,
                max_rows=self.max_rows,
                catalyst_trace_id=decision.catalyst_trace_id,
            )
            self.contracts.validate("catalyst-table-v1.schema.json", table)
        except Exception as error:
            body = self.store.finish_failure(
                preview_id,
                payload["idempotencyKey"],
                f"Execution failed: {error}",
            )
            self.contracts.validate(
                "catalyst-execution-outcome-v1.schema.json",
                body,
            )
            return ServiceResponse(502, body)
        self.store.finish_success(
            preview_id,
            payload["idempotencyKey"],
            table,
        )
        return ServiceResponse(200, table)

    def poll_execution(
        self,
        preview_id: str,
        idempotency_key: str,
    ) -> ServiceResponse:
        if not idempotency_key:
            return self._error(
                400,
                "invalid_request",
                "idempotencyKey must not be empty.",
            )
        return self._execution_response(self.store.poll(preview_id, idempotency_key))

    async def readiness(self) -> dict[str, Any]:
        hub_result, analytics_result = await asyncio.gather(
            self.hub.readiness(),
            self.analytics.readiness(),
            return_exceptions=True,
        )
        if isinstance(hub_result, BaseException):
            hub_checks = {
                "hub": {"ready": False, "message": str(hub_result)},
                "queryProfile": {"ready": False},
                "modelRouter": {"ready": False},
            }
        else:
            hub_checks = hub_result
        if isinstance(analytics_result, BaseException):
            analytics_check = {
                "ready": False,
                "message": str(analytics_result),
            }
        else:
            analytics_check = analytics_result
        checks: dict[str, Any] = {
            "catalyst": {"ready": True},
            **hub_checks,
            "analytics": analytics_check,
            "execution": self.store.readiness(),
        }
        ready = all(
            isinstance(check, dict) and check.get("ready") is True
            for check in checks.values()
        )
        return {"status": "ready" if ready else "not_ready", "checks": checks}

    def _revalidate_execution(
        self,
        preview: dict[str, Any],
        query: dict[str, Any],
    ) -> None:
        self.contracts.validate("catalyst-query-v1.schema.json", query)
        if query_digest(query) != preview["queryDigest"]:
            raise ContractError("Stored query digest no longer matches the preview.")
        request = build_query_request(
            preview["question"],
            self.catalog,
            max_rows=self.max_rows,
            statement_timeout_ms=self.statement_timeout_ms,
            request_id="execution-revalidation",
            trace_id="execution-revalidation",
        )
        validate_query_invariants(query, request)
        violations = self.sql_policy.evaluate(
            query,
            approved_views=self.catalog.approved_view_names,
        )
        if violations:
            raise QueryInvariantError(violations)

    def _execution_response(self, decision: ExecutionDecision) -> ServiceResponse:
        assert decision.body is not None
        if decision.body.get("contractVersion") == "catalyst.execution.outcome.v1":
            self.contracts.validate(
                "catalyst-execution-outcome-v1.schema.json",
                decision.body,
            )
        else:
            self.contracts.validate(
                "catalyst-table-v1.schema.json",
                decision.body,
            )
        return ServiceResponse(decision.status_code, decision.body)

    @staticmethod
    def _policy_outcome(
        violations: list[Violation],
        catalyst_trace_id: str,
    ) -> dict[str, Any]:
        return {
            "contractVersion": "catalyst.policy.outcome.v1",
            "deploymentMode": "demo",
            "status": "rejected",
            "errorCode": "query_policy_rejected",
            "message": "Query failed Catalyst deterministic policy.",
            "violations": [violation.as_dict() for violation in violations],
            "catalystTraceId": catalyst_trace_id,
        }

    @staticmethod
    def _error(status_code: int, code: str, message: str) -> ServiceResponse:
        return ServiceResponse(
            status_code,
            {"error": {"code": code, "message": message}},
        )
