from __future__ import annotations

import asyncio
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from .analytics import AnalyticsResult, ManualAnalyticsError, ManualAnalyticsResult
from .catalog import Catalog
from .contracts import ContractError, ContractRegistry
from .digest import query_digest
from .hub import HubError
from .policy import (
    QueryInvariantError,
    SqlPolicy,
    Violation,
    question_policy_violations,
    validate_query_invariants,
)
from .request import QUERY_PROFILE_ID, build_query_request
from .storage import (
    ExecutionDecision,
    PreviewStore,
    StaleWorkbenchVersionError,
    WorkbenchNotFoundError,
    WorkbenchStore,
)
from .table import build_table
from .workbench import build_advisory_validation, normalize_findings


class HubProtocol(Protocol):
    async def list_query_profiles(self) -> list[dict[str, Any]]: ...

    async def generate_query(self, request: dict[str, Any]) -> dict[str, Any]: ...

    async def readiness(self) -> dict[str, dict[str, Any]]: ...

    async def aclose(self) -> None: ...


class AnalyticsProtocol(Protocol):
    async def execute(
        self,
        *,
        sql: str,
        parameters: list[dict[str, Any]],
        max_rows: int,
        statement_timeout_ms: int,
    ) -> AnalyticsResult: ...

    async def execute_manual(
        self,
        *,
        sql: str,
        parameters: list[dict[str, Any]],
        max_rows: int,
        statement_timeout_ms: int,
    ) -> ManualAnalyticsResult: ...

    async def freshness(self) -> dict[str, Any]: ...

    async def readiness(self) -> dict[str, Any]: ...

    async def dataset_overview(self) -> dict[str, Any]: ...

    async def dataset_rows(
        self,
        *,
        test_name: str | None,
        patient_id: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ServiceResponse:
    status_code: int
    body: dict[str, Any]


@dataclass(frozen=True)
class _HubGeneration:
    query: dict[str, Any]
    selected_profile: dict[str, Any]
    request: dict[str, Any]
    catalyst_trace_id: str
    invariant_violations: tuple[Violation, ...] = ()


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
        workbench_store: WorkbenchStore | None = None,
    ) -> None:
        self.contracts = contracts
        self.catalog = catalog
        self.hub = hub
        self.analytics = analytics
        self.store = store
        self.sql_policy = sql_policy
        self.max_rows = max_rows
        self.statement_timeout_ms = statement_timeout_ms
        self.workbench_store = workbench_store

    async def query_options(self) -> ServiceResponse:
        try:
            profiles = await self.hub.list_query_profiles()
        except HubError as error:
            return self._error(502, error.code, str(error))
        return ServiceResponse(
            200,
            {
                "contractVersion": "catalyst.query-options.v1",
                "defaultProfileId": QUERY_PROFILE_ID,
                "profiles": [
                    {
                        "id": profile.get("id"),
                        "label": profile.get("label") or profile.get("id"),
                        "available": profile.get("available") is True,
                        "requiredModels": profile.get("required_models", []),
                        "roleModels": profile.get("role_models", {}),
                        "stages": profile.get("stages", []),
                        "unavailableReasons": profile.get("unavailable_reasons", []),
                    }
                    for profile in profiles
                ],
            },
        )

    def workbench_editor_catalog(self) -> ServiceResponse:
        try:
            schemas_by_name: dict[str, list[dict[str, Any]]] = {}
            seen_views: set[str] = set()
            for source_view in self.catalog.views:
                if not isinstance(source_view, dict):
                    raise TypeError("Approved catalog views must be objects.")
                qualified_name = source_view.get("name")
                if not isinstance(qualified_name, str):
                    raise TypeError("Approved catalog view names must be strings.")
                name_parts = qualified_name.split(".")
                if len(name_parts) != 2 or any(not part for part in name_parts):
                    raise ValueError(
                        "Approved catalog view names must be schema-qualified: "
                        f"{qualified_name!r}."
                    )
                if qualified_name in seen_views:
                    raise ValueError(
                        f"Approved catalog view names must be unique: {qualified_name!r}."
                    )
                seen_views.add(qualified_name)

                fields = source_view.get("fields")
                if not isinstance(fields, list) or not fields:
                    raise ValueError(
                        "Approved catalog views must expose at least one column: "
                        f"{qualified_name!r}."
                    )
                columns: list[dict[str, str]] = []
                seen_columns: set[str] = set()
                for field in fields:
                    if not isinstance(field, dict):
                        raise TypeError(
                            f"Catalog columns for {qualified_name!r} must be objects."
                        )
                    column_name = field.get("name")
                    logical_type = field.get("type")
                    if not isinstance(column_name, str) or not column_name:
                        raise ValueError(
                            f"Catalog columns for {qualified_name!r} need names."
                        )
                    if column_name in seen_columns:
                        raise ValueError(
                            "Catalog column names must be unique within a view: "
                            f"{qualified_name}.{column_name}."
                        )
                    if not isinstance(logical_type, str) or not logical_type:
                        raise ValueError(
                            "Catalog columns must declare logical types: "
                            f"{qualified_name}.{column_name}."
                        )
                    seen_columns.add(column_name)
                    columns.append({"name": column_name, "logicalType": logical_type})

                schema_name, view_name = name_parts
                schemas_by_name.setdefault(schema_name, []).append(
                    {
                        "name": view_name,
                        "columns": sorted(columns, key=lambda column: column["name"]),
                    }
                )

            body = {
                "contractVersion": "catalyst.workbench.editor-catalog.v1",
                "catalogVersion": self.catalog.catalog_version,
                "schemaVersion": self.catalog.schema_version,
                "dialect": self.catalog.dialect,
                "schemas": [
                    {
                        "name": schema_name,
                        "views": sorted(
                            schemas_by_name[schema_name],
                            key=lambda view: view["name"],
                        ),
                    }
                    for schema_name in sorted(schemas_by_name)
                ],
            }
            self.contracts.validate(
                "catalyst-workbench-editor-catalog-v1.schema.json",
                body,
            )
        except (ContractError, KeyError, TypeError, ValueError) as error:
            return self._workbench_error(
                503,
                "editor_catalog_unavailable",
                f"The approved editor catalog is unavailable: {error}",
            )
        return ServiceResponse(200, body)

    async def dataset_overview(self) -> ServiceResponse:
        try:
            return ServiceResponse(200, await self.analytics.dataset_overview())
        except Exception as error:
            return self._error(502, "dataset_unavailable", str(error))

    async def dataset_rows(
        self,
        *,
        test_name: str | None,
        patient_id: str | None,
        limit: int,
        offset: int,
    ) -> ServiceResponse:
        try:
            body = await self.analytics.dataset_rows(
                test_name=test_name,
                patient_id=patient_id,
                limit=limit,
                offset=offset,
            )
            return ServiceResponse(200, body)
        except Exception as error:
            return self._error(502, "dataset_unavailable", str(error))

    async def submit_question(self, payload: dict[str, Any]) -> ServiceResponse:
        try:
            self.contracts.validate(
                "catalyst-question-request-v1.schema.json",
                payload,
            )
            question = payload["question"]
            profile_id = payload.get("profileId", QUERY_PROFILE_ID)
            if not question.strip():
                raise ContractError("Question must contain non-whitespace text.")
        except (ContractError, KeyError, TypeError) as error:
            return self._error(400, "invalid_request", str(error))

        catalyst_trace_id = str(uuid.uuid4())
        question_violations = question_policy_violations(question)
        if question_violations:
            outcome = self._policy_outcome(question_violations, catalyst_trace_id)
            self.contracts.validate(
                "catalyst-policy-outcome-v1.schema.json",
                outcome,
            )
            return ServiceResponse(422, outcome)

        try:
            generation = await self._generate_hub_query(
                question=question,
                profile_id=str(profile_id),
                catalyst_trace_id=catalyst_trace_id,
                enforce_invariants=True,
            )
        except HubError as error:
            return self._error(502, error.code, str(error))
        except (ContractError, QueryInvariantError) as error:
            return self._error(502, "hub_invalid_response", str(error))

        query = generation.query
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
            catalyst_trace_id=catalyst_trace_id,
            profile=generation.selected_profile,
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
        except asyncio.CancelledError:
            try:
                body = self.store.finish_failure(
                    preview_id,
                    payload["idempotencyKey"],
                    "Execution was cancelled before completion.",
                )
                self.contracts.validate(
                    "catalyst-execution-outcome-v1.schema.json",
                    body,
                )
            finally:
                raise
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

    async def create_workbench_session(
        self,
        payload: dict[str, Any],
    ) -> ServiceResponse:
        store = self.workbench_store
        if store is None:
            return self._workbench_error(
                503,
                "workbench_unavailable",
                "The manual query workbench is not configured.",
            )
        try:
            self.contracts.validate(
                "catalyst-workbench-session-request-v1.schema.json",
                payload,
            )
            question = str(payload["question"])
            if not question.strip():
                raise ContractError("Question must contain non-whitespace text.")
        except (ContractError, KeyError, TypeError) as error:
            return self._workbench_error(400, "invalid_request", str(error))

        profile_id = str(payload.get("profileId") or QUERY_PROFILE_ID)
        catalyst_trace_id = str(uuid.uuid4())
        hub_generation: _HubGeneration | None = None
        try:
            hub_generation = await self._generate_hub_query(
                question=question,
                profile_id=profile_id,
                catalyst_trace_id=catalyst_trace_id,
                enforce_invariants=False,
            )
            generation = ServiceResponse(200, hub_generation.query)
        except HubError as error:
            generation = self._error(502, error.code, str(error))
            if error.raw_output is not None:
                generation.body["rawOutput"] = error.raw_output
        except (ContractError, QueryInvariantError) as error:
            generation = self._error(502, "hub_invalid_response", str(error))

        try:
            overview = await self.analytics.dataset_overview()
        except Exception:
            overview = {}
        provenance = {
            "generationHttpStatus": generation.status_code,
            "generationOutcome": generation.body,
            "catalogContextSourceId": self.catalog.context_source_id,
            "catalystTraceId": catalyst_trace_id,
            "datasetSnapshot": {
                "datasetId": overview.get("datasetId"),
                "dataSource": overview.get("dataSource") or self.catalog.data_source,
                "pipelineRunId": overview.get("pipelineRunId"),
                "synthetic": overview.get("synthetic"),
                "patients": overview.get("patients"),
                "results": overview.get("results"),
                "testTypes": overview.get("testTypes"),
                "firstObservedAt": overview.get("firstObservedAt"),
                "lastObservedAt": overview.get("lastObservedAt"),
            },
        }
        raw_output = self._workbench_raw_output(generation.body)
        if raw_output is not None:
            provenance["generationRawOutput"] = raw_output
        if hub_generation is not None:
            provenance["profileSnapshot"] = self._profile_snapshot(
                hub_generation.selected_profile
            )
        session = store.create_session(
            question=question,
            profile_id=profile_id,
            dataset_id=str(overview.get("datasetId") or self.catalog.data_source),
            dataset_version=str(
                overview.get("pipelineRunId")
                or overview.get("datasetId")
                or self.catalog.catalog_version
            ),
            catalog_version=self.catalog.catalog_version,
            browser_state=dict(payload.get("browserState") or {}),
            provenance=provenance,
        )

        draft, source_findings, version_provenance = self._recover_workbench_draft(
            generation.body
        )
        if draft is not None:
            if hub_generation is not None:
                source_findings.extend(
                    {
                        **violation.as_dict(),
                        "stage": "gateway_invariant",
                        "severity": "error",
                        "path": "$.sql",
                    }
                    for violation in hub_generation.invariant_violations
                )
                version_provenance.update(
                    self._profile_snapshot(hub_generation.selected_profile)
                )
                version_provenance["catalystTraceId"] = catalyst_trace_id
            version = store.append_version(
                session["sessionId"],
                sql=draft["sql"],
                parameters=list(draft.get("parameters") or []),
                expected_columns=list(draft.get("expectedColumns") or []),
                author_type="model",
                provenance=version_provenance,
            )
            self._append_workbench_validation(
                version,
                question=question,
                source_findings=source_findings,
            )

        restored = store.get_session(session["sessionId"])
        assert restored is not None
        return ServiceResponse(201, restored)

    def get_workbench_session(self, session_id: str) -> ServiceResponse:
        store = self.workbench_store
        if store is None:
            return self._workbench_error(
                503,
                "workbench_unavailable",
                "The manual query workbench is not configured.",
            )
        session = store.get_session(session_id)
        if session is None:
            return self._workbench_error(
                404,
                "workbench_session_not_found",
                "Workbench session was not found.",
            )
        return ServiceResponse(200, session)

    def create_workbench_version(
        self,
        session_id: str,
        payload: dict[str, Any],
    ) -> ServiceResponse:
        store = self.workbench_store
        if store is None:
            return self._workbench_error(
                503,
                "workbench_unavailable",
                "The manual query workbench is not configured.",
            )
        try:
            self.contracts.validate(
                "catalyst-workbench-version-request-v1.schema.json",
                payload,
            )
            session = store.get_session(session_id)
            if session is None:
                raise WorkbenchNotFoundError("Workbench session was not found.")
            parent_version_id = payload.get("parentVersionId")
            parent_query_digest = payload.get("parentQueryDigest")
            parent = (
                store.get_version(str(parent_version_id))
                if parent_version_id is not None
                else None
            )
            if parent_version_id is not None and (
                parent is None or parent["sessionId"] != session_id
            ):
                raise WorkbenchNotFoundError("Parent query version was not found.")
            version = store.append_version(
                session_id,
                sql=str(payload["sql"]),
                parameters=list(payload["parameters"]),
                expected_columns=list(
                    payload.get(
                        "expectedColumns",
                        parent["expectedColumns"] if parent is not None else [],
                    )
                ),
                author_type="human",
                parent_version_id=(
                    str(parent_version_id) if parent_version_id is not None else None
                ),
                parent_query_digest=(
                    str(parent_query_digest)
                    if parent_query_digest is not None
                    else None
                ),
                provenance=(
                    {"editedFromVersionId": parent["versionId"]}
                    if parent is not None
                    else {
                        "parentlessInitialDraft": True,
                        "manualRecoveryFromRawGeneration": isinstance(
                            session.get("provenance", {}).get("generationRawOutput"),
                            str,
                        ),
                    }
                ),
            )
        except StaleWorkbenchVersionError as error:
            return self._workbench_error(
                409,
                "stale_query_version",
                str(error),
                details={
                    "currentVersionId": error.current_version_id,
                    "currentQueryDigest": error.current_query_digest,
                },
            )
        except WorkbenchNotFoundError as error:
            return self._workbench_error(404, "query_version_not_found", str(error))
        except (ContractError, KeyError, TypeError, ValueError) as error:
            return self._workbench_error(400, "invalid_request", str(error))

        session = store.get_session(session_id)
        assert session is not None
        self._append_workbench_validation(version, question=session["question"])
        restored = store.get_session(session_id)
        assert restored is not None
        return ServiceResponse(201, restored)

    def validate_workbench_version(self, version_id: str) -> ServiceResponse:
        store = self.workbench_store
        if store is None:
            return self._workbench_error(
                503,
                "workbench_unavailable",
                "The manual query workbench is not configured.",
            )
        version = store.get_version(version_id)
        if version is None:
            return self._workbench_error(
                404,
                "query_version_not_found",
                "Workbench query version was not found.",
            )
        session = store.get_session(version["sessionId"])
        assert session is not None
        validation = self._append_workbench_validation(
            version,
            question=session["question"],
        )
        return ServiceResponse(201, validation)

    async def execute_workbench_version(
        self,
        version_id: str,
        payload: dict[str, Any],
    ) -> ServiceResponse:
        store = self.workbench_store
        if store is None:
            return self._workbench_error(
                503,
                "workbench_unavailable",
                "The manual query workbench is not configured.",
            )
        try:
            self.contracts.validate(
                "catalyst-workbench-execute-request-v1.schema.json",
                payload,
            )
            if payload["versionId"] != version_id:
                raise ContractError("Path and body version IDs must match.")
        except (ContractError, KeyError, TypeError) as error:
            return self._workbench_error(400, "invalid_request", str(error))

        version = store.get_version(version_id)
        if version is None:
            return self._workbench_error(
                404,
                "query_version_not_found",
                "Workbench query version was not found.",
            )
        if payload["queryDigest"] != version["queryDigest"]:
            return self._workbench_error(
                409,
                "query_digest_mismatch",
                "Submitted digest does not match the immutable query version.",
            )
        session = store.get_session(version["sessionId"])
        assert session is not None
        idempotency_key = str(payload["idempotencyKey"])
        for existing in session["executions"]:
            if existing.get("idempotencyKey") != idempotency_key:
                continue
            if existing["versionId"] != version_id:
                return self._workbench_error(
                    409,
                    "idempotency_conflict",
                    "The idempotency key belongs to a different query version.",
                )
            return ServiceResponse(200, {**existing, "replayed": True})

        validation = next(
            (
                item
                for item in reversed(session["validations"])
                if item["versionId"] == version_id
            ),
            None,
        )
        validation_status = validation["status"] if validation else "not_run"
        started = time.perf_counter()
        execution: dict[str, Any] = {
            "contractVersion": "catalyst.workbench.execution.v1",
            "queryDigest": version["queryDigest"],
            "idempotencyKey": idempotency_key,
            "validationStatus": validation_status,
            "query": {
                "sql": version["sql"],
                "parameters": version["parameters"],
            },
            "statementTimeoutMs": self.statement_timeout_ms,
            "maxRows": self.max_rows,
            "replayed": False,
        }
        try:
            result = await self.analytics.execute_manual(
                sql=version["sql"],
                parameters=version["parameters"],
                max_rows=self.max_rows,
                statement_timeout_ms=self.statement_timeout_ms,
            )
            execution.update(
                {
                    "status": "succeeded",
                    "result": result.as_dict(),
                }
            )
        except ManualAnalyticsError as error:
            execution.update(
                {
                    "status": "failed",
                    "databaseDiagnostic": error.as_dict(),
                }
            )
        except Exception:
            execution.update(
                {
                    "status": "failed",
                    "databaseDiagnostic": {
                        "sqlstate": None,
                        "severity": "ERROR",
                        "message": (
                            "Manual execution failed before a database diagnostic "
                            "was available."
                        ),
                        "detail": None,
                        "hint": None,
                        "position": None,
                    },
                }
            )
        execution["durationMs"] = max(0, int((time.perf_counter() - started) * 1000))
        stored = store.append_execution(version_id, execution)
        return ServiceResponse(200, stored)

    def update_workbench_browser_state(
        self,
        session_id: str,
        payload: dict[str, Any],
    ) -> ServiceResponse:
        store = self.workbench_store
        if store is None:
            return self._workbench_error(
                503,
                "workbench_unavailable",
                "The manual query workbench is not configured.",
            )
        if not isinstance(payload.get("browserState"), dict):
            return self._workbench_error(
                400,
                "invalid_request",
                "browserState must be a JSON object.",
            )
        try:
            session = store.update_browser_state(
                session_id, dict(payload["browserState"])
            )
        except WorkbenchNotFoundError as error:
            return self._workbench_error(404, "workbench_session_not_found", str(error))
        return ServiceResponse(200, session)

    async def aclose(self) -> None:
        await self.hub.aclose()
        self.store.close()
        if self.workbench_store is not None:
            self.workbench_store.close()

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
        if self.workbench_store is not None:
            checks["workbench"] = self.workbench_store.readiness()
        ready = all(
            isinstance(check, dict) and check.get("ready") is True
            for check in checks.values()
        )
        return {"status": "ready" if ready else "not_ready", "checks": checks}

    async def _generate_hub_query(
        self,
        *,
        question: str,
        profile_id: str,
        catalyst_trace_id: str,
        enforce_invariants: bool,
    ) -> _HubGeneration:
        """Run the shared Hub request pipeline without creating a preview."""

        profiles = await self.hub.list_query_profiles()
        selected_profile = next(
            (profile for profile in profiles if profile.get("id") == profile_id),
            None,
        )
        if selected_profile is None or selected_profile.get("available") is not True:
            raise HubError(
                "profile_unavailable",
                f"Hub does not advertise available profile {profile_id}.",
            )

        request = build_query_request(
            question,
            self.catalog,
            max_rows=self.max_rows,
            statement_timeout_ms=self.statement_timeout_ms,
            request_id=str(uuid.uuid4()),
            trace_id=catalyst_trace_id,
            profile_id=profile_id,
        )
        self.contracts.validate(
            "catalyst-query-request-v1.schema.json",
            request,
        )
        query = await self.hub.generate_query(request)
        self.contracts.validate("catalyst-query-v1.schema.json", query)

        invariant_violations: tuple[Violation, ...] = ()
        try:
            validate_query_invariants(query, request)
        except QueryInvariantError as error:
            if enforce_invariants:
                raise
            invariant_violations = tuple(error.violations)

        return _HubGeneration(
            query=query,
            selected_profile=selected_profile,
            request=request,
            catalyst_trace_id=catalyst_trace_id,
            invariant_violations=invariant_violations,
        )

    @staticmethod
    def _profile_snapshot(profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "profileId": profile.get("id"),
            "profileLabel": profile.get("label") or profile.get("id"),
            "profileAvailable": profile.get("available") is True,
            "requiredModels": list(profile.get("required_models") or []),
            "roleModels": dict(profile.get("role_models") or {}),
            "roleKnobs": deepcopy(profile.get("role_knobs") or {}),
            "profileConfigurationDigest": profile.get("profile_configuration_digest"),
            "rolePromptDigests": deepcopy(profile.get("role_prompt_digests") or {}),
            "backend": deepcopy(profile.get("backend") or {}),
            "backendModelMetadata": deepcopy(
                profile.get("backend_model_metadata") or {}
            ),
            "stages": list(profile.get("stages") or []),
            "unavailableReasons": list(profile.get("unavailable_reasons") or []),
        }

    @staticmethod
    def _recover_workbench_draft(
        outcome: dict[str, Any],
    ) -> tuple[
        dict[str, Any] | None,
        list[dict[str, Any]],
        dict[str, Any],
    ]:
        contract_version = outcome.get("contractVersion")
        if contract_version == "catalyst.preview.v1":
            trace = dict(outcome.get("reasoningTrace") or {})
            trace_checks = list(trace.get("checks") or [])
            findings = [
                {
                    "code": str(check.get("name") or "hub_check"),
                    "stage": "med_agent_hub",
                    "severity": (
                        "error" if check.get("status") == "failed" else "warning"
                    ),
                    "path": "$.sql",
                    "message": str(
                        check.get("message") or "Hub validation reported a finding."
                    ),
                }
                for check in trace_checks
                if check.get("status") in {"failed", "warned"}
                and not CatalystService._is_historical_generation_check(check)
            ]
            return (
                {
                    "sql": outcome["sql"],
                    "parameters": list(outcome.get("parameters") or []),
                    "expectedColumns": list(outcome.get("expectedColumns") or []),
                },
                findings,
                {
                    "sourceContract": contract_version,
                    "previewId": outcome.get("previewId"),
                    "hubTraceId": trace.get("traceId"),
                    "profileId": trace.get("profileId"),
                    "roleModels": dict(trace.get("roleModels") or {}),
                    "stages": list(trace.get("stages") or []),
                    "generationChecks": trace_checks,
                },
            )

        if contract_version == "catalyst.query.v1" and outcome.get("status") == "ready":
            generation_checks = list(outcome.get("validation", {}).get("checks", []))
            findings = [
                {
                    "code": str(check.get("name") or "hub_check"),
                    "stage": "med_agent_hub",
                    "severity": (
                        "error" if check.get("status") == "failed" else "warning"
                    ),
                    "path": "$.sql",
                    "message": str(
                        check.get("message") or "Hub validation reported a finding."
                    ),
                }
                for check in generation_checks
                if check.get("status") in {"failed", "warned"}
                and not CatalystService._is_historical_generation_check(check)
            ]
            provenance = dict(outcome.get("provenance") or {})
            provenance.update(
                {
                    "sourceContract": contract_version,
                    "sourceStatus": outcome.get("status"),
                    "hubTraceId": provenance.get("traceId"),
                    "generationValidation": dict(outcome.get("validation") or {}),
                }
            )
            return (
                {
                    "sql": outcome["sql"],
                    "parameters": list(outcome.get("parameters") or []),
                    "expectedColumns": list(outcome.get("expectedColumns") or []),
                },
                findings,
                provenance,
            )

        diagnostic = outcome.get("diagnosticCandidate")
        candidate = (
            diagnostic.get("candidate") if isinstance(diagnostic, dict) else None
        )
        attempts: list[dict[str, Any]] = []
        if isinstance(diagnostic, dict):
            attempts = [
                dict(attempt)
                for attempt in diagnostic.get("attempts") or []
                if isinstance(attempt, dict)
            ]
        provenance = dict(outcome.get("provenance") or {})
        provenance["sourceContract"] = contract_version
        provenance["sourceStatus"] = outcome.get("status")
        provenance["hubTraceId"] = provenance.get("traceId")
        provenance["generationAttempts"] = attempts
        provenance["generationValidation"] = dict(outcome.get("validation") or {})
        if not isinstance(candidate, dict) or not isinstance(candidate.get("sql"), str):
            return None, [], provenance
        return (
            {
                "sql": candidate["sql"],
                "parameters": list(candidate.get("parameters") or []),
                "expectedColumns": list(candidate.get("expectedColumns") or []),
            },
            [],
            provenance,
        )

    @staticmethod
    def _is_historical_generation_check(check: dict[str, Any]) -> bool:
        return str(check.get("name") or "").startswith("query_lint_attempt_")

    @staticmethod
    def _workbench_raw_output(outcome: dict[str, Any]) -> str | None:
        raw_output = outcome.get("rawOutput")
        if isinstance(raw_output, str):
            return raw_output
        diagnostic = outcome.get("diagnosticCandidate")
        if isinstance(diagnostic, dict):
            raw_output = diagnostic.get("rawOutput")
            if isinstance(raw_output, str):
                return raw_output
        return None

    def _append_workbench_validation(
        self,
        version: dict[str, Any],
        *,
        question: str,
        source_findings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        store = self.workbench_store
        assert store is not None
        started = time.perf_counter()
        raw_findings = list(source_findings or [])
        raw_findings.extend(
            {
                **violation.as_dict(),
                "stage": "gateway_question_policy",
                "severity": "error",
                "path": "$.question",
            }
            for violation in question_policy_violations(question)
        )
        query = {
            "contractVersion": "catalyst.query.v1",
            "deploymentMode": "demo",
            "status": "ready",
            "question": question,
            "target": {
                **self.catalog.request_target(),
                "approvedViews": sorted(self.catalog.approved_view_names),
            },
            "sql": version["sql"],
            "parameters": version["parameters"],
            "expectedColumns": version["expectedColumns"],
            "validation": {"status": "passed", "checks": []},
            "provenance": {
                "profileId": str(
                    version.get("provenance", {}).get("profileId") or "manual-workbench"
                ),
                "traceId": str(
                    version.get("provenance", {}).get("hubTraceId")
                    or "manual-workbench"
                ),
                "contextSourceIds": [self.catalog.context_source_id],
            },
        }
        raw_findings.extend(
            {
                **violation.as_dict(),
                "stage": "gateway_sql_policy",
                "severity": "error",
                "path": "$.sql",
            }
            for violation in self.sql_policy.evaluate(
                query,
                approved_views=self.catalog.approved_view_names,
            )
        )
        request = build_query_request(
            question,
            self.catalog,
            max_rows=self.max_rows,
            statement_timeout_ms=self.statement_timeout_ms,
            request_id="workbench-validation",
            trace_id="workbench-validation",
            profile_id=str(query["provenance"]["profileId"]),
        )
        try:
            validate_query_invariants(query, request)
        except QueryInvariantError as error:
            raw_findings.extend(
                {
                    **violation.as_dict(),
                    "stage": "gateway_invariant",
                    "severity": "error",
                    "path": "$.sql",
                }
                for violation in error.violations
            )
        findings = normalize_findings(
            raw_findings,
            query_digest=version["queryDigest"],
            default_stage="gateway",
        )
        validation = build_advisory_validation(
            query_digest=version["queryDigest"],
            findings=findings,
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
        )
        return store.append_validation(version["versionId"], validation)

    @staticmethod
    def _workbench_error(
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> ServiceResponse:
        return ServiceResponse(
            status_code,
            {
                "contractVersion": "catalyst.workbench.error.v1",
                "error": {
                    "code": code,
                    "message": message,
                    **({"details": details} if details else {}),
                },
            },
        )

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
