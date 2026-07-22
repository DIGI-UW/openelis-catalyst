from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from .analytics import (
    AnalyticsError,
    AnalyticsResult,
    ManualAnalyticsError,
    ManualAnalyticsResult,
)
from .catalog import Catalog
from .contracts import ContractError, ContractRegistry
from .digest import canonical_sha256, query_digest, utf8_sha256
from .hub import HubError
from .policy import (
    QueryInvariantError,
    SqlPolicy,
    Violation,
    question_policy_violations,
    validate_query_invariants,
)
from .request import QUERY_PROFILE_ID, build_query_request, build_revision_query_request
from .storage import (
    ExecutionDecision,
    ActiveTurnGenerationError,
    EditorSnapshotDigestError,
    PreviewStore,
    StaleWorkbenchVersionError,
    WorkbenchNotFoundError,
    WorkbenchStore,
)
from .table import build_table
from .workbench import (
    build_advisory_validation,
    build_revision_context,
    normalize_findings,
    workbench_query_digest,
)


_WORKBENCH_PARAMETER_TYPES = frozenset(
    {
        "string",
        "integer",
        "number",
        "boolean",
        "date",
        "date-time",
        "string-list",
        "integer-list",
    }
)
_WORKBENCH_PARAMETER_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ProfileEvidenceError(ContractError):
    """An advertised profile lacks exact role/model/prompt evidence."""


class HubProtocol(Protocol):
    async def list_query_profiles(self) -> list[dict[str, Any]]: ...

    async def generate_query(self, request: dict[str, Any]) -> dict[str, Any]: ...

    async def readiness(self) -> dict[str, dict[str, Any]]: ...

    async def aclose(self) -> None: ...


class AnalyticsProtocol(Protocol):
    async def discover_relations(self) -> list[dict[str, Any]]: ...

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
    catalog: Catalog
    invariant_violations: tuple[Violation, ...] = ()
    hub_evidence: dict[str, Any] | None = None


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
        datasets: tuple[dict[str, Any], ...] | None = None,
        default_dataset_id: str | None = None,
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
        self._runtime_catalog_snapshot = catalog
        # Dataset registry (discovery only at this layer; per-request routing is
        # threaded through the query methods in a later change). When not supplied
        # (e.g. unit tests that build a single-dataset service), derive one entry
        # from the loaded catalog so /datasets always reports the active dataset.
        if datasets:
            self._datasets = tuple(dict(entry) for entry in datasets)
            self._default_dataset_id = default_dataset_id or self._datasets[0]["id"]
        else:
            derived_id = default_dataset_id or catalog.data_source
            self._datasets = (
                {"id": derived_id, "label": derived_id, "available": True},
            )
            self._default_dataset_id = derived_id

    def datasets(self) -> ServiceResponse:
        """List the datasets the workbench can query (for the dataset switcher)."""
        return ServiceResponse(
            200,
            {
                "contractVersion": "catalyst.datasets.v1",
                "defaultDatasetId": self._default_dataset_id,
                "datasets": [dict(entry) for entry in self._datasets],
            },
        )

    async def _runtime_catalog(self) -> Catalog:
        discover = getattr(self.analytics, "discover_relations", None)
        if discover is None:
            return self._runtime_catalog_snapshot
        relations = await discover()
        try:
            catalog = self.catalog.with_discovered_relations(relations)
        except (KeyError, TypeError, ValueError) as error:
            raise AnalyticsError(
                f"PostgreSQL schema discovery returned an unusable catalog: {error}"
            ) from error
        self._runtime_catalog_snapshot = catalog
        return catalog

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
                        "revisionCapable": self._profile_revision_capable(profile),
                        "requiredModels": profile.get("required_models", []),
                        "roleModels": profile.get("role_models", {}),
                        "stages": profile.get("stages", []),
                        "unavailableReasons": profile.get("unavailable_reasons", []),
                        "provenance": self._profile_snapshot(profile),
                    }
                    for profile in profiles
                ],
            },
        )

    async def workbench_editor_catalog(self) -> ServiceResponse:
        try:
            catalog = await self._runtime_catalog()
            schemas_by_name: dict[str, list[dict[str, Any]]] = {}
            seen_views: set[str] = set()
            for source_view in catalog.views:
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
                grain = source_view.get("grain")
                if not isinstance(grain, str) or not grain:
                    raise ValueError(
                        "Approved catalog views must declare their row grain: "
                        f"{qualified_name!r}."
                    )
                columns: list[dict[str, Any]] = []
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
                    description = field.get("description")
                    if not isinstance(description, str) or not description:
                        raise ValueError(
                            "Catalog columns must declare descriptions: "
                            f"{qualified_name}.{column_name}."
                        )
                    nullable = field.get("nullable", True)
                    if not isinstance(nullable, bool):
                        raise ValueError(
                            "Catalog columns must declare boolean nullability: "
                            f"{qualified_name}.{column_name}."
                        )
                    seen_columns.add(column_name)
                    column = {
                        "name": column_name,
                        "logicalType": logical_type,
                        "description": description,
                        "nullable": nullable,
                    }
                    database_type = field.get("databaseType")
                    if isinstance(database_type, str) and database_type:
                        column["databaseType"] = database_type
                    unit_column = field.get("unitColumn")
                    if unit_column is not None:
                        if not isinstance(unit_column, str) or not unit_column:
                            raise ValueError(
                                "Catalog unit columns must be named fields: "
                                f"{qualified_name}.{column_name}."
                            )
                        column["unitColumn"] = unit_column
                    columns.append(column)

                for column in columns:
                    unit_column = column.get("unitColumn")
                    if unit_column is not None and unit_column not in seen_columns:
                        raise ValueError(
                            "Catalog unit columns must reference the same view: "
                            f"{qualified_name}.{unit_column}."
                        )

                schema_name, view_name = name_parts
                presented_view = {
                    "name": view_name,
                    "qualifiedName": qualified_name,
                    "grain": grain,
                    "columns": sorted(columns, key=lambda column: column["name"]),
                }
                relation_type = source_view.get("relationType")
                if isinstance(relation_type, str) and relation_type:
                    presented_view["relationType"] = relation_type
                schemas_by_name.setdefault(schema_name, []).append(presented_view)

            body = {
                "contractVersion": "catalyst.workbench.editor-catalog.v1",
                "catalogVersion": catalog.catalog_version,
                "schemaVersion": catalog.schema_version,
                "dialect": catalog.dialect,
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
        except (
            AnalyticsError,
            ContractError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            return self._workbench_error(
                503,
                "editor_catalog_unavailable",
                f"The readable PostgreSQL catalog is unavailable: {error}",
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
        except AnalyticsError as error:
            return self._error(502, "catalog_unavailable", str(error))
        except (ContractError, QueryInvariantError) as error:
            return self._error(502, "hub_invalid_response", str(error))

        query = generation.query
        if query["status"] != "ready":
            return ServiceResponse(200, query)

        violations = self.sql_policy.evaluate(
            query,
            available_relations=generation.catalog.available_relation_names,
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
            await self._revalidate_execution(decision.preview, decision.query)
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
        try:
            profiles = await self.hub.list_query_profiles()
            selected_profile = next(
                (profile for profile in profiles if profile.get("id") == profile_id),
                None,
            )
            if (
                selected_profile is None
                or selected_profile.get("available") is not True
            ):
                return self._workbench_error(
                    422,
                    "profile_unavailable",
                    f"Hub does not advertise available profile {profile_id}.",
                )
            profile_evidence = self._require_profile_evidence(selected_profile)
            initial_profile_snapshot = self._turn_profile_snapshot(selected_profile)
            runtime_catalog = await self._runtime_catalog()
        except HubError as error:
            return self._workbench_error(502, error.code, str(error))
        except AnalyticsError as error:
            return self._workbench_error(502, "catalog_unavailable", str(error))
        except ContractError as error:
            return self._workbench_error(
                422,
                "profile_evidence_unavailable",
                str(error),
            )

        initial_request = build_query_request(
            question,
            runtime_catalog,
            max_rows=self.max_rows,
            statement_timeout_ms=self.statement_timeout_ms,
            request_id=str(uuid.uuid4()),
            trace_id=catalyst_trace_id,
            profile_id=profile_id,
        )
        self.contracts.validate(
            "catalyst-query-request-v1.schema.json", initial_request
        )
        try:
            overview = await self.analytics.dataset_overview()
        except Exception:
            overview = {}
        provenance = {
            "catalogContextSourceId": runtime_catalog.context_source_id,
            "catalystTraceId": catalyst_trace_id,
            "profileSnapshot": self._profile_snapshot(selected_profile),
            "datasetSnapshot": {
                "datasetId": overview.get("datasetId"),
                "dataSource": overview.get("dataSource") or runtime_catalog.data_source,
                "pipelineRunId": overview.get("pipelineRunId"),
                "synthetic": overview.get("synthetic"),
                "patients": overview.get("patients"),
                "results": overview.get("results"),
                "testTypes": overview.get("testTypes"),
                "firstObservedAt": overview.get("firstObservedAt"),
                "lastObservedAt": overview.get("lastObservedAt"),
            },
        }
        session = store.create_session(
            question=question,
            profile_id=profile_id,
            dataset_id=str(overview.get("datasetId") or runtime_catalog.data_source),
            dataset_version=str(
                overview.get("pipelineRunId")
                or overview.get("datasetId")
                or runtime_catalog.catalog_version
            ),
            catalog_version=runtime_catalog.catalog_version,
            browser_state=dict(payload.get("browserState") or {}),
            provenance=provenance,
        )
        initial_turn = store.claim_initial_turn(
            session["sessionId"],
            instruction=question,
            instruction_digest=utf8_sha256(question),
            profile_snapshot=initial_profile_snapshot,
            catalyst_trace_id=catalyst_trace_id,
            hub_request=initial_request,
            profile_evidence=profile_evidence,
        )

        hub_generation: _HubGeneration | None = None
        try:
            returned = await self.hub.generate_query(initial_request)
            query = deepcopy(returned)
            hub_evidence = query.pop("_hubEvidence", None)
            self.contracts.validate("catalyst-query-v1.schema.json", query)
            self._require_hub_profile_binding(
                query,
                hub_evidence,
                profile_id=profile_id,
                profile_evidence=profile_evidence,
            )
            self._require_hub_invocation_binding(
                query,
                hub_evidence,
                profile_snapshot=initial_profile_snapshot,
            )
            invariant_violations: tuple[Violation, ...] = ()
            try:
                validate_query_invariants(query, initial_request)
            except QueryInvariantError as error:
                invariant_violations = tuple(error.violations)
            hub_generation = _HubGeneration(
                query=query,
                selected_profile=selected_profile,
                request=initial_request,
                catalyst_trace_id=catalyst_trace_id,
                catalog=runtime_catalog,
                invariant_violations=invariant_violations,
                hub_evidence=(hub_evidence if isinstance(hub_evidence, dict) else None),
            )
            generation = ServiceResponse(200, hub_generation.query)
        except HubError as error:
            generation = self._error(502, error.code, str(error))
            if error.raw_output is not None:
                generation.body["rawOutput"] = error.raw_output
        except (ContractError, QueryInvariantError) as error:
            generation = self._error(502, "hub_invalid_response", str(error))

        provenance.update(
            {
                "generationHttpStatus": generation.status_code,
                "generationOutcome": generation.body,
            }
        )
        raw_output = self._workbench_raw_output(generation.body)
        if raw_output is not None:
            provenance["generationRawOutput"] = raw_output
        store.update_session_provenance(session["sessionId"], provenance)

        collaboration_failure = generation.body.get("modelCollaboration")
        writer_failure = (
            collaboration_failure.get("writer")
            if isinstance(collaboration_failure, dict)
            else None
        )
        if (
            generation.body.get("status") != "ready"
            and isinstance(writer_failure, dict)
            and writer_failure.get("disposition") == "retained_unselected"
            and isinstance(writer_failure.get("candidate"), dict)
        ):
            candidate = dict(writer_failure["candidate"])
            retained = {
                **candidate,
                "provenance": {
                    "turnId": initial_turn["turnId"],
                    "collaborationRole": "writer",
                    "model": writer_failure.get("model"),
                    "lintFindings": deepcopy(writer_failure.get("lintFindings") or []),
                },
            }
            retained_validation = self._build_workbench_validation(
                {
                    **retained,
                    "queryDigest": workbench_query_digest(
                        candidate["sql"],
                        list(candidate.get("parameters") or []),
                        list(candidate.get("expectedColumns") or []),
                    ),
                },
                question=question,
                catalog=runtime_catalog,
                source_findings=list(writer_failure.get("lintFindings") or []),
            )
            evidence = dict(hub_generation.hub_evidence or {}) if hub_generation else {}
            stage, code = self._model_failure_stage(evidence, reviewer=True)
            store.fail_turn(
                initial_turn["turnId"],
                stage=stage,
                code=code,
                message=str(generation.body.get("message") or "Initial review failed."),
                raw_evidence=raw_output,
                hub_trace_id=self._response_hub_trace_id(generation.body),
                hub_response=evidence or generation.body,
                invocations=self._generation_invocations(
                    evidence,
                    request=initial_request,
                    response=generation.body,
                    profile_snapshot=initial_turn["profileSnapshot"],
                    kind="initial",
                    failed=True,
                ),
                retained_writer=retained,
                retained_writer_validation=retained_validation,
            )
            restored = store.get_session(session["sessionId"])
            assert restored is not None
            projection_error = self._validate_workbench_turn_state(
                store, session["sessionId"]
            )
            if projection_error is not None:
                return projection_error
            return ServiceResponse(201, self._present_workbench_session(restored))

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
            collaboration = self._workbench_collaboration(generation.body, draft)
            outputs: list[dict[str, Any]] = []
            validation_sources: list[list[dict[str, Any]]] = []
            if collaboration is not None:
                writer = collaboration["writer"]
                reviewer = collaboration["reviewer"]
                writer_candidate = writer["candidate"]
                writer_provenance = {
                    **deepcopy(version_provenance),
                    "collaborationRole": "writer",
                    "model": writer["model"],
                    "lintFindings": deepcopy(writer["lintFindings"]),
                }
                outputs.append(
                    {
                        **writer_candidate,
                        "authorType": "model",
                        "provenance": writer_provenance,
                    }
                )
                validation_sources.append(list(writer["lintFindings"]))
                reviewer_provenance = {
                    **deepcopy(version_provenance),
                    "collaborationRole": "reviewer",
                    "model": reviewer["model"],
                    "decision": reviewer["decision"],
                    "checks": deepcopy(reviewer["checks"]),
                    "finalDecision": reviewer.get("finalDecision"),
                    "finalChecks": deepcopy(reviewer.get("finalChecks") or []),
                    "finalLintFindings": deepcopy(collaboration["finalLintFindings"]),
                }
                outputs.append(
                    {
                        **draft,
                        "authorType": "model_repair",
                        "provenance": reviewer_provenance,
                    }
                )
                validation_sources.append(
                    [
                        *source_findings,
                        *list(collaboration["finalLintFindings"]),
                    ]
                )
            else:
                outputs.append(
                    {
                        **draft,
                        "authorType": "model",
                        "provenance": version_provenance,
                    }
                )
                validation_sources.append(source_findings)

            hub_evidence = (
                dict(hub_generation.hub_evidence or {})
                if hub_generation is not None
                else {}
            )
            validation_payloads = [
                self._build_workbench_validation(
                    {
                        **output,
                        "queryDigest": workbench_query_digest(
                            output["sql"],
                            list(output.get("parameters") or []),
                            list(output.get("expectedColumns") or []),
                        ),
                    },
                    question=question,
                    catalog=runtime_catalog,
                    source_findings=findings,
                )
                for output, findings in zip(outputs, validation_sources)
            ]
            store.complete_turn(
                initial_turn["turnId"],
                outputs=outputs,
                selected_index=len(outputs) - 1,
                hub_trace_id=(
                    generation.body.get("provenance", {}).get("traceId")
                    if isinstance(generation.body.get("provenance"), dict)
                    else None
                ),
                hub_response=hub_evidence or generation.body,
                invocations=self._generation_invocations(
                    hub_evidence,
                    request=initial_request,
                    response=generation.body,
                    profile_snapshot=initial_turn["profileSnapshot"],
                    kind="initial",
                ),
                validations=validation_payloads,
            )
        else:
            code = str(
                generation.body.get("error", {}).get("code") or "generation_failed"
            )
            stage = (
                "writer_transport"
                if generation.status_code >= 500 and code != "hub_invalid_response"
                else "writer_output_contract"
            )
            store.fail_turn(
                initial_turn["turnId"],
                stage=stage,
                code=code,
                message=str(
                    generation.body.get("error", {}).get("message")
                    or generation.body.get("message")
                    or "Initial query generation did not produce a usable candidate."
                ),
                raw_evidence=raw_output,
                hub_trace_id=self._response_hub_trace_id(generation.body),
                hub_response=generation.body,
                invocations=self._generation_invocations(
                    dict(hub_generation.hub_evidence or {})
                    if hub_generation is not None
                    else {},
                    request=initial_request,
                    response=generation.body,
                    profile_snapshot=initial_turn["profileSnapshot"],
                    kind="initial",
                    failed=True,
                ),
            )

        restored = store.get_session(session["sessionId"])
        assert restored is not None
        projection_error = self._validate_workbench_turn_state(
            store, session["sessionId"]
        )
        if projection_error is not None:
            return projection_error
        return ServiceResponse(201, self._present_workbench_session(restored))

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
        return ServiceResponse(200, self._present_workbench_session(session))

    def get_workbench_turns(self, session_id: str) -> ServiceResponse:
        store = self.workbench_store
        if store is None:
            return self._workbench_error(
                503,
                "workbench_unavailable",
                "The manual query workbench is not configured.",
            )
        try:
            timeline = store.list_turns(session_id)
            self.contracts.validate(
                "catalyst-workbench-turn-timeline-v1.schema.json", timeline
            )
        except WorkbenchNotFoundError as error:
            return self._workbench_error(404, "workbench_session_not_found", str(error))
        except ContractError as error:
            return self._workbench_error(
                500, "workbench_contract_violation", str(error)
            )
        return ServiceResponse(200, timeline)

    def get_workbench_generation_evidence(
        self,
        session_id: str,
        turn_id: str,
    ) -> ServiceResponse:
        store = self.workbench_store
        if store is None:
            return self._workbench_error(
                503,
                "workbench_unavailable",
                "The manual query workbench is not configured.",
            )
        evidence = store.get_generation_evidence(session_id, turn_id)
        if evidence is None:
            return self._workbench_error(
                404,
                "generation_evidence_not_found",
                "Generation evidence was not found.",
            )
        try:
            self.contracts.validate(
                "catalyst-workbench-generation-evidence-v1.schema.json", evidence
            )
        except ContractError as error:
            return self._workbench_error(
                500, "workbench_contract_violation", str(error)
            )
        return ServiceResponse(200, evidence)

    async def create_workbench_turn(
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
                "catalyst-workbench-turn-request-v1.schema.json", payload
            )
            instruction = str(payload["instruction"])
            if not instruction.strip():
                raise ContractError("Instruction must contain non-whitespace text.")
            snapshot = dict(payload["editorSnapshot"])
            expected_digest = workbench_query_digest(
                snapshot["sql"],
                list(snapshot["parameters"]),
                list(snapshot["expectedColumns"]),
            )
            if snapshot["editorDigest"] != expected_digest:
                return self._workbench_error(
                    422,
                    "editor_snapshot_digest_mismatch",
                    "editorDigest does not match the exact editor content.",
                )
            session = store.get_session(session_id)
            if session is None:
                raise WorkbenchNotFoundError("Workbench session was not found.")
            profiles = await self.hub.list_query_profiles()
            profile_id = str(payload["profileId"])
            selected_profile = next(
                (profile for profile in profiles if profile.get("id") == profile_id),
                None,
            )
            if (
                selected_profile is None
                or selected_profile.get("available") is not True
            ):
                return self._workbench_error(
                    422,
                    "profile_unavailable",
                    f"Hub does not advertise available profile {profile_id}.",
                )
            if not self._profile_revision_capable(selected_profile):
                return self._workbench_error(
                    422,
                    "profile_not_revision_capable",
                    f"Profile {profile_id} cannot run the different-family revision flow.",
                )
            profile_evidence = self._require_profile_evidence(selected_profile)
            profile_snapshot = self._turn_profile_snapshot(selected_profile)
            runtime_catalog = await self._runtime_catalog()
            catalog_conflict = self._workbench_catalog_conflict(
                session, runtime_catalog
            )
            if catalog_conflict is not None:
                return catalog_conflict
        except WorkbenchNotFoundError as error:
            return self._workbench_error(404, "workbench_session_not_found", str(error))
        except HubError as error:
            return self._workbench_error(502, error.code, str(error))
        except AnalyticsError as error:
            return self._workbench_error(502, "catalog_unavailable", str(error))
        except ProfileEvidenceError as error:
            return self._workbench_error(
                422,
                "profile_evidence_unavailable",
                str(error),
            )
        except (ContractError, KeyError, TypeError, ValueError) as error:
            return self._workbench_error(400, "invalid_request", str(error))

        catalyst_trace_id = str(uuid.uuid4())
        observed_base = payload.get("observedBase")
        prior_turns = store.list_turns(session_id)["turns"]
        prepared: dict[str, dict[str, Any]] = {}

        def prepare_request(
            resolution: dict[str, Any],
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            revision = build_revision_context(
                session=session,
                prior_turns=prior_turns,
                turn_id=resolution["turnId"],
                instruction=instruction,
                base_classification=resolution["snapshotClassification"],
                observed_base=resolution["observedBase"],
                effective_base=resolution["effectiveBaseVersion"],
                editor_snapshot=snapshot,
            )
            request = build_revision_query_request(
                instruction,
                runtime_catalog,
                revision=revision,
                max_rows=self.max_rows,
                statement_timeout_ms=self.statement_timeout_ms,
                request_id=str(uuid.uuid4()),
                trace_id=catalyst_trace_id,
                profile_id=profile_id,
            )
            self.contracts.validate("catalyst-query-request-v2.schema.json", request)
            prepared.update(revision=revision, request=request)
            return revision, request

        try:
            claimed = store.claim_turn(
                session_id,
                instruction=instruction,
                instruction_digest=utf8_sha256(instruction),
                profile_snapshot=profile_snapshot,
                observed_base=(
                    dict(observed_base) if observed_base is not None else None
                ),
                editor_snapshot=snapshot,
                revision_context={},
                hub_request_digest="0" * 64,
                catalyst_trace_id=catalyst_trace_id,
                request_factory=prepare_request,
                profile_evidence=profile_evidence,
            )
        except ActiveTurnGenerationError as error:
            return self._workbench_error(409, "turn_generation_in_progress", str(error))
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
        except EditorSnapshotDigestError as error:
            return self._workbench_error(
                422, "editor_snapshot_digest_mismatch", str(error)
            )

        try:
            revision = prepared["revision"]
            request = prepared["request"]
            returned = await self.hub.generate_query(request)
            query = deepcopy(returned)
            hub_evidence = query.pop("_hubEvidence", None)
            hub_evidence = hub_evidence if isinstance(hub_evidence, dict) else {}
            self.contracts.validate("catalyst-query-v1.schema.json", query)
            self._require_hub_profile_binding(
                query,
                hub_evidence,
                profile_id=profile_id,
                profile_evidence=profile_evidence,
            )
            self._require_hub_invocation_binding(
                query,
                hub_evidence,
                profile_snapshot=profile_snapshot,
            )
            raw_output = self._workbench_raw_output(query)
            if query.get("status") != "ready":
                collaboration_failure = query.get("modelCollaboration")
                writer_data = (
                    collaboration_failure.get("writer")
                    if isinstance(collaboration_failure, dict)
                    else None
                )
                retained_writer = None
                retained_validation = None
                if (
                    isinstance(writer_data, dict)
                    and writer_data.get("disposition") == "retained_unselected"
                    and isinstance(writer_data.get("candidate"), dict)
                ):
                    candidate = dict(writer_data["candidate"])
                    retained_writer = {
                        **candidate,
                        "provenance": {
                            "turnId": claimed["turnId"],
                            "collaborationRole": "writer",
                            "model": writer_data.get("model"),
                            "lintFindings": deepcopy(
                                writer_data.get("lintFindings") or []
                            ),
                        },
                    }
                    retained_validation = self._build_workbench_validation(
                        {
                            **retained_writer,
                            "queryDigest": workbench_query_digest(
                                candidate["sql"],
                                list(candidate.get("parameters") or []),
                                list(candidate.get("expectedColumns") or []),
                            ),
                        },
                        question=instruction,
                        catalog=runtime_catalog,
                        source_findings=list(writer_data.get("lintFindings") or []),
                    )
                stage, failure_code = self._model_failure_stage(
                    hub_evidence,
                    reviewer=retained_writer is not None,
                )
                failed = store.fail_turn(
                    claimed["turnId"],
                    stage=stage,
                    code=failure_code,
                    message=str(query.get("message") or "Follow-up generation failed."),
                    raw_evidence=raw_output,
                    hub_trace_id=self._response_hub_trace_id(query),
                    hub_response=hub_evidence or query,
                    invocations=self._generation_invocations(
                        hub_evidence,
                        request=request,
                        response=query,
                        profile_snapshot=profile_snapshot,
                        kind="followup",
                        failed=True,
                    ),
                    retained_writer=retained_writer,
                    retained_writer_validation=retained_validation,
                )
                return self._workbench_terminal_turn_response(
                    store, session_id, failed["turnId"]
                )
            draft, source_findings, version_provenance = self._recover_workbench_draft(
                query
            )
            if draft is None:
                failed = store.fail_turn(
                    claimed["turnId"],
                    stage="writer_validation",
                    code="generation_failed",
                    message=str(
                        query.get("message")
                        or "Follow-up generation did not produce a usable candidate."
                    ),
                    raw_evidence=raw_output,
                    hub_trace_id=self._response_hub_trace_id(query),
                    hub_response=hub_evidence or query,
                    invocations=self._generation_invocations(
                        hub_evidence,
                        request=request,
                        response=query,
                        profile_snapshot=profile_snapshot,
                        kind="followup",
                        failed=True,
                    ),
                )
                return self._workbench_terminal_turn_response(
                    store, session_id, failed["turnId"]
                )

            version_provenance.update(self._profile_snapshot(selected_profile))
            version_provenance.update(
                turnId=claimed["turnId"],
                catalystTraceId=catalyst_trace_id,
                revisionContextDigest=revision["contextDigest"],
            )
            collaboration = self._workbench_collaboration(query, draft)
            outputs: list[dict[str, Any]] = []
            validation_sources: list[list[dict[str, Any]]] = []
            if collaboration is not None:
                writer = collaboration["writer"]
                reviewer = collaboration["reviewer"]
                outputs.append(
                    {
                        **writer["candidate"],
                        "authorType": "model",
                        "provenance": {
                            **deepcopy(version_provenance),
                            "collaborationRole": "writer",
                            "model": writer["model"],
                            "lintFindings": deepcopy(writer["lintFindings"]),
                        },
                    }
                )
                validation_sources.append(list(writer["lintFindings"]))
                outputs.append(
                    {
                        **draft,
                        "authorType": "model_repair",
                        "provenance": {
                            **deepcopy(version_provenance),
                            "collaborationRole": "reviewer",
                            "model": reviewer["model"],
                            "decision": reviewer["decision"],
                            "checks": deepcopy(reviewer["checks"]),
                            "finalDecision": reviewer.get("finalDecision"),
                            "finalChecks": deepcopy(reviewer.get("finalChecks") or []),
                        },
                    }
                )
                validation_sources.append(
                    [*source_findings, *list(collaboration["finalLintFindings"])]
                )
            else:
                outputs.append(
                    {
                        **draft,
                        "authorType": "model",
                        "provenance": version_provenance,
                    }
                )
                validation_sources.append(source_findings)
            validation_payloads = [
                self._build_workbench_validation(
                    {
                        **output,
                        "queryDigest": workbench_query_digest(
                            output["sql"],
                            list(output.get("parameters") or []),
                            list(output.get("expectedColumns") or []),
                        ),
                    },
                    question=instruction,
                    catalog=runtime_catalog,
                    source_findings=findings,
                )
                for output, findings in zip(outputs, validation_sources)
            ]
            completed = store.complete_turn(
                claimed["turnId"],
                outputs=outputs,
                selected_index=len(outputs) - 1,
                hub_trace_id=(
                    query.get("provenance", {}).get("traceId")
                    if isinstance(query.get("provenance"), dict)
                    else None
                ),
                hub_response=hub_evidence or query,
                invocations=self._generation_invocations(
                    hub_evidence,
                    request=request,
                    response=query,
                    profile_snapshot=profile_snapshot,
                    kind="followup",
                ),
                raw_evidence=raw_output,
                validations=validation_payloads,
            )
            return self._workbench_terminal_turn_response(
                store, session_id, completed["turnId"]
            )
        except HubError as error:
            failed = store.fail_turn(
                claimed["turnId"],
                stage=(
                    "writer_transport"
                    if error.code in {"hub_timeout", "hub_unavailable", "hub_cancelled"}
                    else "writer_output_contract"
                ),
                code=error.code,
                message=str(error),
                raw_evidence=error.raw_output,
                invocations=self._generation_invocations(
                    {},
                    request=request,
                    response={"error": {"code": error.code, "message": str(error)}},
                    profile_snapshot=profile_snapshot,
                    kind="followup",
                    failed=True,
                ),
            )
            return self._workbench_terminal_turn_response(
                store, session_id, failed["turnId"]
            )
        except Exception as error:
            failed = store.fail_turn(
                claimed["turnId"],
                stage="gateway_persistence",
                code="followup_generation_failed",
                message=str(error),
                raw_evidence=None,
            )
            return self._workbench_terminal_turn_response(
                store, session_id, failed["turnId"]
            )

    async def create_workbench_version(
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
            runtime_catalog = await self._runtime_catalog()
            catalog_conflict = self._workbench_catalog_conflict(
                session, runtime_catalog
            )
            if catalog_conflict is not None:
                return catalog_conflict
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
            sql = str(payload["sql"])
            expected_columns = list(
                payload.get(
                    "expectedColumns",
                    parent["expectedColumns"] if parent is not None else [],
                )
            )
            if parent is not None and sql != parent["sql"]:
                # Expected columns describe model output, not an independently
                # editable contract. Once a human changes SQL, retaining the old
                # projection would present stale schema as if it were verified.
                expected_columns = []
            version = store.append_version(
                session_id,
                sql=sql,
                parameters=list(payload["parameters"]),
                expected_columns=expected_columns,
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
        except ActiveTurnGenerationError as error:
            return self._workbench_error(
                409,
                "turn_generation_in_progress",
                str(error),
            )
        except WorkbenchNotFoundError as error:
            return self._workbench_error(404, "query_version_not_found", str(error))
        except AnalyticsError as error:
            return self._workbench_error(502, "catalog_unavailable", str(error))
        except (ContractError, KeyError, TypeError, ValueError) as error:
            return self._workbench_error(400, "invalid_request", str(error))

        session = store.get_session(session_id)
        assert session is not None
        self._append_workbench_validation(
            version,
            question=self._active_workbench_instruction(store, session_id),
            catalog=runtime_catalog,
        )
        restored = store.get_session(session_id)
        assert restored is not None
        return ServiceResponse(201, self._present_workbench_session(restored))

    async def validate_workbench_version(self, version_id: str) -> ServiceResponse:
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
        try:
            runtime_catalog = await self._runtime_catalog()
        except AnalyticsError as error:
            return self._workbench_error(502, "catalog_unavailable", str(error))
        catalog_conflict = self._workbench_catalog_conflict(session, runtime_catalog)
        if catalog_conflict is not None:
            return catalog_conflict
        validation = self._append_workbench_validation(
            version,
            question=self._active_workbench_instruction(store, version["sessionId"]),
            catalog=runtime_catalog,
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
        try:
            runtime_catalog = await self._runtime_catalog()
        except AnalyticsError as error:
            return self._workbench_error(502, "catalog_unavailable", str(error))
        catalog_conflict = self._workbench_catalog_conflict(session, runtime_catalog)
        if catalog_conflict is not None:
            return catalog_conflict
        idempotency_key = str(payload["idempotencyKey"])
        decision = store.begin_execution(version_id, idempotency_key)
        if decision.action == "replay":
            assert decision.execution is not None
            return ServiceResponse(200, decision.execution)
        if decision.action == "conflict":
            return self._workbench_error(
                409,
                "idempotency_conflict",
                "The idempotency key belongs to a different query version.",
            )
        if decision.action == "in_progress":
            return self._workbench_error(
                409,
                "execution_in_progress",
                "An execution with this idempotency key is already in progress.",
            )
        if decision.action != "execute":  # pragma: no cover - storage invariant
            raise RuntimeError(f"Unsupported execution decision: {decision.action}")

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
        except asyncio.CancelledError:
            execution.update(
                {
                    "status": "failed",
                    "databaseDiagnostic": {
                        "sqlstate": None,
                        "severity": "ERROR",
                        "message": "Manual execution was cancelled before completion.",
                        "detail": None,
                        "hint": None,
                        "position": None,
                    },
                }
            )
            execution["durationMs"] = max(
                0, int((time.perf_counter() - started) * 1000)
            )
            store.append_execution(version_id, execution)
            raise
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
        return ServiceResponse(200, self._present_workbench_session(session))

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

        runtime_catalog = await self._runtime_catalog()

        request = build_query_request(
            question,
            runtime_catalog,
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
        returned = await self.hub.generate_query(request)
        query = deepcopy(returned)
        hub_evidence = query.pop("_hubEvidence", None)
        self.contracts.validate("catalyst-query-v1.schema.json", query)
        self._require_hub_profile_binding(
            query,
            hub_evidence,
            profile_id=profile_id,
            profile_evidence=(
                selected_profile.get("profileEvidence")
                if isinstance(selected_profile.get("profileEvidence"), dict)
                else None
            ),
        )

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
            catalog=runtime_catalog,
            invariant_violations=invariant_violations,
            hub_evidence=(hub_evidence if isinstance(hub_evidence, dict) else None),
        )

    @staticmethod
    def _require_hub_profile_binding(
        query: dict[str, Any],
        hub_evidence: Any,
        *,
        profile_id: str,
        profile_evidence: dict[str, Any] | None = None,
    ) -> None:
        if query.get("provenance", {}).get("profileId") != profile_id:
            raise HubError(
                "hub_invalid_response",
                "Hub query provenance profileId does not match the requested profile.",
            )
        response_profile = (
            hub_evidence.get("profileEvidence")
            if isinstance(hub_evidence, dict)
            else None
        )
        if response_profile is None:
            return
        if not isinstance(response_profile, dict):
            raise HubError(
                "hub_invalid_response",
                "Hub response profileEvidence must be an object.",
            )
        if response_profile.get("profileId") != profile_id:
            raise HubError(
                "hub_invalid_response",
                "Hub response profileEvidence does not match the requested profile.",
            )
        if profile_evidence is not None and response_profile != profile_evidence:
            raise HubError(
                "hub_invalid_response",
                "Hub response profileEvidence does not match profile discovery.",
            )

    def _require_hub_invocation_binding(
        self,
        query: dict[str, Any],
        hub_evidence: Any,
        *,
        profile_snapshot: dict[str, Any],
    ) -> None:
        """Validate Hub-owned model evidence before committing generated versions."""

        invocations = (
            hub_evidence.get("modelInvocations")
            if isinstance(hub_evidence, dict)
            else None
        )
        ready = query.get("status") == "ready"
        if invocations is None:
            if ready:
                raise HubError(
                    "hub_invalid_response",
                    "A ready Hub query must include writer and reviewer invocation "
                    "evidence.",
                )
            return
        if not isinstance(invocations, list):
            raise HubError(
                "hub_invalid_response",
                "Hub modelInvocations must be an array.",
            )

        evidenced_query = deepcopy(query)
        evidenced_query["modelInvocations"] = deepcopy(invocations)
        if isinstance(hub_evidence, dict) and isinstance(
            hub_evidence.get("totalModelInvocationDurationMs"), int
        ):
            evidenced_query["totalModelInvocationDurationMs"] = hub_evidence[
                "totalModelInvocationDurationMs"
            ]
        try:
            self.contracts.validate(
                "catalyst-query-v1.schema.json",
                evidenced_query,
            )
        except ContractError as error:
            raise HubError(
                "hub_invalid_response",
                f"Hub model invocation evidence is invalid: {error}",
            ) from error

        validation_failed_roles: set[str] = set()
        succeeded_roles: set[str] = set()
        for invocation in invocations:
            role = str(invocation["role"])
            expected = profile_snapshot.get(role)
            if not isinstance(expected, dict):
                raise HubError(
                    "hub_invalid_response",
                    f"Hub invocation role {role!r} is not part of the requested profile.",
                )
            if invocation.get("providerId") != expected.get(
                "providerId"
            ) or invocation.get("modelId") != expected.get("modelId"):
                raise HubError(
                    "hub_invalid_response",
                    f"Hub {role} invocation does not match the requested profile.",
                )
            outcome = invocation.get("outcome")
            if outcome == "validation_failed":
                validation_failed_roles.add(role)
            if outcome == "succeeded":
                succeeded_roles.add(role)

        collaboration = query.get("modelCollaboration")
        repaired_linted_writer = False
        if isinstance(collaboration, dict):
            for role in ("writer", "reviewer"):
                role_evidence = collaboration.get(role)
                expected = profile_snapshot.get(role)
                if (
                    not isinstance(role_evidence, dict)
                    or not isinstance(expected, dict)
                    or role_evidence.get("model") != expected.get("modelId")
                ):
                    raise HubError(
                        "hub_invalid_response",
                        f"Hub {role} collaboration evidence does not match the "
                        "requested profile.",
                    )
            writer_evidence = collaboration.get("writer")
            reviewer_evidence = collaboration.get("reviewer")
            reviewer_candidate = (
                reviewer_evidence.get("candidate")
                if isinstance(reviewer_evidence, dict)
                else None
            )
            repaired_linted_writer = (
                "writer" in validation_failed_roles
                and isinstance(writer_evidence, dict)
                and isinstance(writer_evidence.get("lintFindings"), list)
                and bool(writer_evidence["lintFindings"])
                and isinstance(reviewer_evidence, dict)
                and reviewer_evidence.get("decision") == "repair"
                and isinstance(reviewer_candidate, dict)
                and collaboration.get("finalLintFindings") == []
                and writer_evidence.get("disposition") in {None, "superseded"}
                and reviewer_evidence.get("disposition") in {None, "selected"}
                and all(
                    query.get(key) == reviewer_candidate.get(key)
                    for key in (
                        "status",
                        "target",
                        "sql",
                        "parameters",
                        "expectedColumns",
                    )
                )
            )

        if ready and (
            ("writer" not in succeeded_roles and not repaired_linted_writer)
            or "reviewer" not in succeeded_roles
        ):
            missing = []
            if "writer" not in succeeded_roles and not repaired_linted_writer:
                missing.append("successful writer or coherent reviewer repair")
            if "reviewer" not in succeeded_roles:
                missing.append("successful reviewer invocation")
            raise HubError(
                "hub_invalid_response",
                "A ready Hub query requires completed writer and successful reviewer "
                f"invocation evidence; missing: {', '.join(missing)}.",
            )

    @staticmethod
    def _active_workbench_instruction(
        store: WorkbenchStore,
        session_id: str,
    ) -> str:
        turns = store.list_turns(session_id)["turns"]
        for turn in reversed(turns):
            instruction = turn.get("instruction")
            if isinstance(instruction, str) and instruction.strip():
                return instruction
        session = store.get_session(session_id)
        assert session is not None
        return str(session["question"])

    def _workbench_catalog_conflict(
        self,
        session: dict[str, Any],
        runtime_catalog: Catalog,
    ) -> ServiceResponse | None:
        session_catalog_version = str(session["catalogVersion"])
        if runtime_catalog.catalog_version == session_catalog_version:
            return None
        return self._workbench_error(
            409,
            "stale_catalog_version",
            "The readable PostgreSQL catalog changed after this workbench session "
            "was created. Start a new session before saving, validating, running, "
            "or refining this query.",
            details={
                "sessionCatalogVersion": session_catalog_version,
                "runtimeCatalogVersion": runtime_catalog.catalog_version,
            },
        )

    @staticmethod
    def _profile_snapshot(profile: dict[str, Any]) -> dict[str, Any]:
        evidence = (
            profile.get("profileEvidence")
            if isinstance(profile.get("profileEvidence"), dict)
            else {}
        )
        writer = (
            evidence.get("writer") if isinstance(evidence.get("writer"), dict) else {}
        )
        reviewer = (
            evidence.get("reviewer")
            if isinstance(evidence.get("reviewer"), dict)
            else {}
        )
        writer_prompt = (
            writer.get("systemPrompt")
            if isinstance(writer.get("systemPrompt"), dict)
            else {}
        )
        reviewer_prompt = (
            reviewer.get("systemPrompt")
            if isinstance(reviewer.get("systemPrompt"), dict)
            else {}
        )
        return {
            "profileId": profile.get("id"),
            "profileLabel": profile.get("label") or profile.get("id"),
            "profileAvailable": profile.get("available") is True,
            "requiredModels": list(profile.get("required_models") or []),
            "roleModels": dict(profile.get("role_models") or {}),
            "roleKnobs": deepcopy(
                profile.get("role_knobs")
                or {
                    "query_generate": writer.get("config"),
                    "query_review": reviewer.get("config"),
                }
            ),
            "profileConfigurationDigest": (
                profile.get("profile_configuration_digest")
                or evidence.get("profileDigest")
            ),
            "rolePromptDigests": deepcopy(
                profile.get("role_prompt_digests")
                or {
                    "query_generate": writer_prompt.get("promptDigest"),
                    "query_review": reviewer_prompt.get("promptDigest"),
                }
            ),
            "backend": deepcopy(profile.get("backend") or {}),
            "backendModelMetadata": deepcopy(
                profile.get("backend_model_metadata") or {}
            ),
            "stages": list(profile.get("stages") or []),
            "unavailableReasons": list(profile.get("unavailable_reasons") or []),
        }

    @staticmethod
    def _profile_revision_capable(profile: dict[str, Any]) -> bool:
        explicit = profile.get("revisionCapable")
        if explicit is None:
            explicit = profile.get("revision_capable")
        if isinstance(explicit, bool):
            return explicit
        evidence = profile.get("profileEvidence")
        if isinstance(evidence, dict):
            writer = evidence.get("writer")
            reviewer = evidence.get("reviewer")
            if isinstance(writer, dict) and isinstance(reviewer, dict):
                return writer.get("modelClass") != reviewer.get("modelClass")
        role_models = profile.get("role_models")
        if isinstance(role_models, dict):
            return role_models.get("query_generate") != role_models.get("query_review")
        return False

    @staticmethod
    def _require_profile_evidence(profile: dict[str, Any]) -> dict[str, Any]:
        exact = profile.get("profileEvidence")
        if not isinstance(exact, dict):
            raise ProfileEvidenceError(
                f"Profile {profile.get('id')} does not expose profileEvidence."
            )
        if exact.get("profileId") != profile.get("id"):
            raise ProfileEvidenceError(
                "Profile discovery ID and profileEvidence.profileId do not match."
            )
        for key in ("profileId", "profileName", "profileDigest"):
            if not isinstance(exact.get(key), str) or not exact[key]:
                raise ProfileEvidenceError(f"profileEvidence.{key} is required.")
        if not re.fullmatch(r"[a-f0-9]{64}", exact["profileDigest"]):
            raise ProfileEvidenceError(
                "profileEvidence.profileDigest must be a lowercase SHA-256 digest."
            )
        for role_name in ("writer", "reviewer"):
            role = exact.get(role_name)
            if not isinstance(role, dict) or role.get("role") != role_name:
                raise ProfileEvidenceError(
                    f"profileEvidence.{role_name} must identify the {role_name} role."
                )
            for key in ("providerId", "modelClass", "modelId"):
                if not isinstance(role.get(key), str) or not role[key]:
                    raise ProfileEvidenceError(
                        f"profileEvidence.{role_name}.{key} is required."
                    )
            config = role.get("config")
            if not isinstance(config, dict) or len(config) > 32:
                raise ProfileEvidenceError(
                    f"profileEvidence.{role_name}.config must be a bounded object."
                )
            if any(
                not isinstance(key, str)
                or not key
                or not CatalystService._profile_config_value(value)
                for key, value in config.items()
            ):
                raise ProfileEvidenceError(
                    f"profileEvidence.{role_name}.config has an unsupported value."
                )
            prompt = role.get("systemPrompt")
            if not isinstance(prompt, dict):
                raise ProfileEvidenceError(
                    f"profileEvidence.{role_name}.systemPrompt is required."
                )
            for key in ("promptId", "version", "promptRef", "promptDigest", "text"):
                if not isinstance(prompt.get(key), str) or not prompt[key]:
                    raise ProfileEvidenceError(
                        f"profileEvidence.{role_name}.systemPrompt.{key} is required."
                    )
            if not re.fullmatch(r"[a-f0-9]{64}", prompt["promptDigest"]):
                raise ProfileEvidenceError(
                    f"profileEvidence.{role_name}.systemPrompt.promptDigest must be "
                    "a lowercase SHA-256 digest."
                )
            if utf8_sha256(prompt["text"]) != prompt["promptDigest"]:
                raise ProfileEvidenceError(
                    f"profileEvidence.{role_name}.systemPrompt.promptDigest does not "
                    "match the exact prompt text."
                )
        compact = deepcopy(exact)
        supplied_digest = compact.pop("profileDigest")
        compact["writer"]["systemPrompt"].pop("text")
        compact["reviewer"]["systemPrompt"].pop("text")
        if canonical_sha256(compact) != supplied_digest:
            raise ProfileEvidenceError(
                "profileEvidence.profileDigest does not match its compact snapshot."
            )
        return deepcopy(exact)

    @staticmethod
    def _profile_config_value(value: Any) -> bool:
        if value is None or isinstance(value, (str, int, float, bool)):
            return True
        return (
            isinstance(value, list)
            and len(value) <= 32
            and all(
                item is None or isinstance(item, (str, int, float, bool))
                for item in value
            )
        )

    @staticmethod
    def _turn_profile_snapshot(profile: dict[str, Any]) -> dict[str, Any]:
        exact = CatalystService._require_profile_evidence(profile)

        def compact_role(role: dict[str, Any]) -> dict[str, Any]:
            prompt = {
                key: role["systemPrompt"][key]
                for key in ("promptId", "version", "promptRef", "promptDigest")
            }
            return {
                **{
                    key: deepcopy(role[key])
                    for key in (
                        "role",
                        "providerId",
                        "modelClass",
                        "modelId",
                        "config",
                    )
                },
                "systemPrompt": prompt,
            }

        snapshot = {
            "profileId": exact["profileId"],
            "profileName": exact["profileName"],
            "profileDigest": "0" * 64,
            "writer": compact_role(exact["writer"]),
            "reviewer": compact_role(exact["reviewer"]),
            "omissions": [],
        }
        snapshot["profileDigest"] = canonical_sha256(
            {key: value for key, value in snapshot.items() if key != "profileDigest"}
        )
        return snapshot

    @staticmethod
    def _generation_invocations(
        evidence: dict[str, Any],
        *,
        request: dict[str, Any],
        response: dict[str, Any],
        profile_snapshot: dict[str, Any],
        kind: str,
        failed: bool = False,
    ) -> list[dict[str, Any]]:
        supplied = evidence.get("modelInvocations")
        if isinstance(supplied, list) and supplied:
            fields = (
                "invocationId",
                "role",
                "stage",
                "attempt",
                "providerId",
                "modelId",
                "startedAt",
                "endedAt",
                "durationMs",
                "requestDigest",
                "responseDigest",
                "failureDigest",
                "outcome",
            )
            projected: list[dict[str, Any]] = []
            for item in supplied:
                if not isinstance(item, dict):
                    continue
                invocation = {key: deepcopy(item.get(key)) for key in fields}
                configuration = item.get("configuration")
                if isinstance(configuration, dict):
                    invocation["configuration"] = deepcopy(configuration)
                projected.append(invocation)
            return projected
        # Model invocations are Hub-owned evidence. A Gateway-to-Hub request is
        # not relabelled as a writer call when the Hub could not report whether
        # dispatch occurred (transport failure, cancellation, or non-2xx).
        return []

    @staticmethod
    def _model_failure_stage(
        evidence: dict[str, Any],
        *,
        reviewer: bool,
    ) -> tuple[str, str]:
        invocations = evidence.get("modelInvocations")
        terminal = (
            invocations[-1]
            if isinstance(invocations, list)
            and invocations
            and isinstance(invocations[-1], dict)
            else {}
        )
        outcome = str(terminal.get("outcome") or "validation_failed")
        role = "reviewer" if reviewer else "writer"
        if outcome == "contract_failed":
            return f"{role}_output_contract", f"{role}_output_contract_failed"
        if outcome == "validation_failed":
            return f"{role}_validation", f"{role}_validation_failed"
        if outcome == "timed_out":
            return f"{role}_transport", f"{role}_timeout"
        if outcome == "cancelled":
            return f"{role}_transport", f"{role}_cancelled"
        return f"{role}_transport", f"{role}_transport_failed"

    @staticmethod
    def _response_hub_trace_id(outcome: dict[str, Any]) -> str | None:
        provenance = outcome.get("provenance")
        trace_id = provenance.get("traceId") if isinstance(provenance, dict) else None
        return trace_id if isinstance(trace_id, str) and trace_id else None

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
            collaboration = outcome.get("modelCollaboration")
            if isinstance(collaboration, dict):
                provenance["modelCollaboration"] = deepcopy(collaboration)
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
    def _workbench_collaboration(
        outcome: dict[str, Any], final_draft: dict[str, Any]
    ) -> dict[str, Any] | None:
        collaboration = outcome.get("modelCollaboration")
        if not isinstance(collaboration, dict):
            return None
        writer = collaboration.get("writer")
        reviewer = collaboration.get("reviewer")
        if not isinstance(writer, dict) or not isinstance(reviewer, dict):
            return None
        writer_candidate = writer.get("candidate")
        reviewer_candidate = reviewer.get("candidate")
        if not isinstance(writer_candidate, dict) or not isinstance(
            reviewer_candidate, dict
        ):
            return None
        if writer.get("model") == reviewer.get("model"):
            return None
        for candidate in (writer_candidate, reviewer_candidate):
            if not isinstance(candidate.get("sql"), str):
                return None
            if not isinstance(candidate.get("parameters"), list):
                return None
            if not isinstance(candidate.get("expectedColumns"), list):
                return None
        if any(
            reviewer_candidate.get(field) != final_draft.get(field)
            for field in ("sql", "parameters", "expectedColumns")
        ):
            return None
        return deepcopy(collaboration)

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

    @staticmethod
    def _raw_workbench_draft_seed(raw_output: object) -> dict[str, Any] | None:
        if not isinstance(raw_output, str):
            return None
        try:
            candidate = json.loads(raw_output)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(candidate, dict):
            return None
        sql = candidate.get("sql")
        raw_parameters = candidate.get("parameters")
        if not isinstance(sql, str) or not sql.strip():
            return None
        if not isinstance(raw_parameters, list):
            return None

        parameters: list[dict[str, Any]] = []
        unresolved_paths: list[str] = []
        for index, raw_parameter in enumerate(raw_parameters):
            if not isinstance(raw_parameter, dict) or "value" not in raw_parameter:
                return None
            parameter_type = raw_parameter.get("type")
            if parameter_type not in _WORKBENCH_PARAMETER_TYPES:
                return None

            name = raw_parameter.get("name")
            if not isinstance(name, str):
                name = ""
                unresolved_paths.append(f"$.parameters[{index}].name")
            elif not _WORKBENCH_PARAMETER_NAME.fullmatch(name):
                unresolved_paths.append(f"$.parameters[{index}].name")

            source = raw_parameter.get("source")
            if source not in {"question", "human"}:
                source = "human"
                unresolved_paths.append(f"$.parameters[{index}].source")

            parameters.append(
                {
                    "name": name,
                    "type": parameter_type,
                    "source": source,
                    "value": deepcopy(raw_parameter["value"]),
                }
            )

        return {
            "status": "unresolved",
            "source": "raw_model_output",
            "sql": sql,
            "parameters": parameters,
            "unresolvedPaths": unresolved_paths,
        }

    def _present_workbench_session(self, session: dict[str, Any]) -> dict[str, Any]:
        presented = deepcopy(session)
        presented["draftSeed"] = None
        if presented.get("currentVersion") is not None:
            return presented
        raw_output = presented.get("provenance", {}).get("generationRawOutput")
        presented["draftSeed"] = self._raw_workbench_draft_seed(raw_output)
        return presented

    def _append_workbench_validation(
        self,
        version: dict[str, Any],
        *,
        question: str,
        catalog: Catalog,
        source_findings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        store = self.workbench_store
        assert store is not None
        validation = self._build_workbench_validation(
            version,
            question=question,
            catalog=catalog,
            source_findings=source_findings,
        )
        return store.append_validation(version["versionId"], validation)

    def _build_workbench_validation(
        self,
        version: dict[str, Any],
        *,
        question: str,
        catalog: Catalog,
        source_findings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
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
                **catalog.request_target(),
                "approvedViews": sorted(catalog.relation_names),
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
                "contextSourceIds": [catalog.context_source_id],
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
                available_relations=catalog.available_relation_names,
            )
        )
        request = build_query_request(
            question,
            catalog,
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
        return build_advisory_validation(
            query_digest=version["queryDigest"],
            findings=findings,
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
        )

    def _validate_workbench_turn_state(
        self,
        store: WorkbenchStore,
        session_id: str,
    ) -> ServiceResponse | None:
        try:
            timeline = store.list_turns(session_id)
            self.contracts.validate(
                "catalyst-workbench-turn-timeline-v1.schema.json", timeline
            )
            for turn in timeline["turns"]:
                evidence = store.get_generation_evidence(session_id, turn["turnId"])
                if evidence is None:
                    raise ContractError(
                        f"Turn {turn['turnId']} has no generation evidence."
                    )
                self.contracts.validate(
                    "catalyst-workbench-generation-evidence-v1.schema.json",
                    evidence,
                )
                reference = turn["generationEvidenceRef"]
                if (
                    reference["evidenceId"] != evidence["evidenceId"]
                    or reference["evidenceDigest"] != evidence["evidenceDigest"]
                ):
                    raise ContractError(
                        f"Turn {turn['turnId']} generation evidence reference drifted."
                    )
        except (ContractError, WorkbenchNotFoundError) as error:
            return self._workbench_error(
                500,
                "workbench_contract_violation",
                str(error),
            )
        return None

    def _workbench_terminal_turn_response(
        self,
        store: WorkbenchStore,
        session_id: str,
        turn_id: str,
    ) -> ServiceResponse:
        projection_error = self._validate_workbench_turn_state(store, session_id)
        if projection_error is not None:
            return projection_error
        timeline = store.list_turns(session_id)
        turn = next(
            (item for item in timeline["turns"] if item["turnId"] == turn_id),
            None,
        )
        if turn is None:
            return self._workbench_error(
                500,
                "workbench_contract_violation",
                f"Terminal turn {turn_id} is missing from its session timeline.",
            )
        return ServiceResponse(201, turn)

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

    async def _revalidate_execution(
        self,
        preview: dict[str, Any],
        query: dict[str, Any],
    ) -> None:
        catalog = await self._runtime_catalog()
        self.contracts.validate("catalyst-query-v1.schema.json", query)
        if query_digest(query) != preview["queryDigest"]:
            raise ContractError("Stored query digest no longer matches the preview.")
        request = build_query_request(
            preview["question"],
            catalog,
            max_rows=self.max_rows,
            statement_timeout_ms=self.statement_timeout_ms,
            request_id="execution-revalidation",
            trace_id="execution-revalidation",
        )
        validate_query_invariants(query, request)
        violations = self.sql_policy.evaluate(
            query,
            available_relations=catalog.available_relation_names,
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
