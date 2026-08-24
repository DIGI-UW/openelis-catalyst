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
    validate_query_invariants,
)
from .request import QUERY_PROFILE_ID, build_query_request, build_revision_query_request
from .sql_layout import sql_layout_matches
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

# Stages where the correction loop reports on its own run rather than on the
# query. Their findings' suggested actions instruct the model what to return
# next, so they are never repeated to a person as advice.
_LOOP_STAGES = frozenset({"output_contract", "query_correct"})


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


@dataclass
class DataSourceBundle:
    """One targetable data source: its catalog + analytics adapter.

    Turns target a bundle per generation; the runtime-discovered catalog is
    cached per bundle so switching sources mid-session never mixes schemas.
    A registered-but-unprovisioned source (catalog not yet on disk) is listed
    with available=False, carries no catalog/adapter, and cannot be targeted.
    """

    source_id: str
    label: str
    catalog: Catalog | None = None
    analytics: AnalyticsProtocol | None = None
    available: bool = True
    runtime_snapshot: Catalog | None = None


@dataclass(frozen=True)
class _RetainedAttempt:
    """A candidate a failed turn keeps, so the failure has somewhere to go."""

    candidate: dict[str, Any]
    validation: dict[str, Any]
    rejected_by_reviewer: bool


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
        catalog: Catalog | None = None,
        hub: HubProtocol,
        analytics: AnalyticsProtocol | None = None,
        store: PreviewStore,
        sql_policy: SqlPolicy,
        max_rows: int,
        statement_timeout_ms: int,
        workbench_store: WorkbenchStore | None = None,
        data_sources: tuple[DataSourceBundle, ...] | None = None,
        default_data_source_id: str | None = None,
        default_query_profile_id: str | None = None,
    ) -> None:
        self.contracts = contracts
        self.hub = hub
        self.store = store
        self.sql_policy = sql_policy
        self.max_rows = max_rows
        self.statement_timeout_ms = statement_timeout_ms
        self.workbench_store = workbench_store
        self.default_query_profile_id = default_query_profile_id or QUERY_PROFILE_ID
        # Data-source registry. When bundles are not supplied (e.g. unit tests
        # that build a single-source service), derive one bundle wrapping the
        # ctor catalog/analytics so every path routes through the registry.
        if data_sources:
            self._bundles: dict[str, DataSourceBundle] = {}
            for bundle in data_sources:
                if bundle.source_id in self._bundles:
                    raise ValueError(f"duplicate data source id {bundle.source_id!r}")
                self._bundles[bundle.source_id] = bundle
            self._default_data_source_id = (
                default_data_source_id or data_sources[0].source_id
            )
        else:
            if catalog is None or analytics is None:
                raise ValueError(
                    "catalog and analytics are required when no data_sources "
                    "bundles are supplied"
                )
            derived_id = default_data_source_id or catalog.data_source
            self._bundles = {
                derived_id: DataSourceBundle(
                    source_id=derived_id,
                    label=derived_id,
                    catalog=catalog,
                    analytics=analytics,
                )
            }
            self._default_data_source_id = derived_id
        default_bundle = self._bundles.get(self._default_data_source_id)
        if (
            default_bundle is None
            or not default_bundle.available
            or default_bundle.catalog is None
            or default_bundle.analytics is None
        ):
            raise ValueError(
                "default data source "
                f"{self._default_data_source_id!r} is not registered and available"
            )
        # Legacy single-source alias, still read by submit_question/readiness.
        self.analytics = default_bundle.analytics

    def data_sources(self) -> ServiceResponse:
        """List the data sources the workbench can target (for the UI switcher)."""
        return ServiceResponse(
            200,
            {
                "contractVersion": "catalyst.data-sources.v1",
                "defaultDataSourceId": self._default_data_source_id,
                "dataSources": [
                    {
                        "id": bundle.source_id,
                        "label": bundle.label,
                        "available": bundle.available,
                    }
                    for bundle in self._bundles.values()
                ],
            },
        )

    def _resolve_data_source(self, source_id: str | None) -> DataSourceBundle | None:
        """Bundle for source_id (default when omitted); None when unregistered
        or registered-but-unavailable."""
        bundle = self._bundles.get(source_id or self._default_data_source_id)
        if (
            bundle is None
            or not bundle.available
            or bundle.catalog is None
            or bundle.analytics is None
        ):
            return None
        return bundle

    def _require_bundle(
        self,
        source_id: str | None,
        *,
        workbench: bool = True,
    ) -> DataSourceBundle | ServiceResponse:
        """Resolve a targetable bundle or the 400 the caller should return."""
        bundle = self._resolve_data_source(source_id)
        if bundle is not None:
            return bundle
        error = self._workbench_error if workbench else self._error
        return error(
            400,
            "unknown_data_source",
            f"Data source {source_id!r} is not registered.",
        )

    def _version_bundle(
        self,
        version: dict[str, Any],
        session: dict[str, Any],
    ) -> DataSourceBundle | ServiceResponse:
        """The bundle a stored version targets: its recorded source, else the
        session's."""
        source_id = str(
            (version.get("provenance") or {}).get("dataSourceId")
            or self._session_data_source_id(session)
        )
        return self._require_bundle(source_id)

    async def _runtime_catalog(self, bundle: DataSourceBundle | None = None) -> Catalog:
        if bundle is None:
            bundle = self._bundles[self._default_data_source_id]
        assert bundle.catalog is not None  # _resolve_data_source guards this
        discover = getattr(bundle.analytics, "discover_relations", None)
        if discover is None:
            return bundle.runtime_snapshot or bundle.catalog
        relations = await discover()
        try:
            catalog = bundle.catalog.with_discovered_relations(relations)
        except (KeyError, TypeError, ValueError) as error:
            raise AnalyticsError(
                f"PostgreSQL schema discovery returned an unusable catalog: {error}"
            ) from error
        bundle.runtime_snapshot = catalog
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
                "defaultProfileId": self.default_query_profile_id,
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

    async def workbench_editor_catalog(
        self, data_source_id: str | None = None
    ) -> ServiceResponse:
        bundle = self._require_bundle(data_source_id)
        if isinstance(bundle, ServiceResponse):
            return bundle
        try:
            catalog = await self._runtime_catalog(bundle)
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

            body: dict[str, Any] = {
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

    async def dataset_overview(
        self, data_source_id: str | None = None
    ) -> ServiceResponse:
        bundle = self._require_bundle(data_source_id, workbench=False)
        if isinstance(bundle, ServiceResponse):
            return bundle
        assert bundle.analytics is not None  # _resolve_data_source guards this
        try:
            return ServiceResponse(200, await bundle.analytics.dataset_overview())
        except Exception as error:
            return self._error(502, "dataset_unavailable", str(error))

    async def dataset_rows(
        self,
        *,
        test_name: str | None,
        patient_id: str | None,
        limit: int,
        offset: int,
        data_source_id: str | None = None,
    ) -> ServiceResponse:
        bundle = self._require_bundle(data_source_id, workbench=False)
        if isinstance(bundle, ServiceResponse):
            return bundle
        assert bundle.analytics is not None  # _resolve_data_source guards this
        try:
            body = await bundle.analytics.dataset_rows(
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
            # The deployment's configured default, not the module constant: a
            # demo host that advertises one profile sets it, and a request that
            # names none must land there rather than on a build-time guess.
            profile_id = payload.get("profileId", self.default_query_profile_id)
            if not question.strip():
                raise ContractError("Question must contain non-whitespace text.")
        except (ContractError, KeyError, TypeError) as error:
            return self._error(400, "invalid_request", str(error))

        catalyst_trace_id = str(uuid.uuid4())
        try:
            generation = await self._generate_hub_query(
                question=question,
                profile_id=str(profile_id),
                catalyst_trace_id=catalyst_trace_id,
                enforce_invariants=True,
            )
        except HubError as error:
            return self._error(
                422 if error.code == "profile_unavailable" else 502,
                error.code,
                str(error),
            )
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
            # A session may be opened before there is a question: choosing
            # where to work should not require already knowing what to ask.
            question = str(payload.get("question") or "")
        except (ContractError, KeyError, TypeError) as error:
            return self._workbench_error(400, "invalid_request", str(error))

        profile_id = str(payload.get("profileId") or self.default_query_profile_id)
        bundle = self._require_bundle(payload.get("dataSourceId"))
        if isinstance(bundle, ServiceResponse):
            return bundle
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
                    f"Gateway does not advertise available profile {profile_id}.",
                )
            profile_evidence = self._require_profile_evidence(selected_profile)
            initial_profile_snapshot = self._turn_profile_snapshot(selected_profile)
            runtime_catalog = await self._runtime_catalog(bundle)
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

        assert bundle.analytics is not None  # _resolve_data_source guards this
        try:
            overview = await bundle.analytics.dataset_overview()
        except Exception:
            overview = {}
        provenance: dict[str, Any] = {
            "dataSourceId": bundle.source_id,
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
            name=(
                str(payload["name"]).strip()
                if isinstance(payload.get("name"), str) and payload["name"].strip()
                else None
            ),
            browser_state=dict(payload.get("browserState") or {}),
            provenance=provenance,
        )
        if not question.strip():
            # An empty session: named, grounded in a source, nothing asked
            # of a model yet.
            restored = store.get_session(session["sessionId"])
            assert restored is not None
            return ServiceResponse(201, self._present_workbench_session(restored))

        return await self._seed_workbench_session(
            store,
            session,
            question=question,
            profile_id=profile_id,
            bundle=bundle,
            runtime_catalog=runtime_catalog,
            catalyst_trace_id=catalyst_trace_id,
            selected_profile=selected_profile,
            profile_evidence=profile_evidence,
            initial_profile_snapshot=initial_profile_snapshot,
        )

    def rename_workbench_session(
        self, session_id: str, payload: dict[str, Any]
    ) -> ServiceResponse:
        store = self.workbench_store
        if store is None:
            return self._workbench_error(
                503,
                "workbench_unavailable",
                "The manual query workbench is not configured.",
            )
        if store.get_session(session_id) is None:
            return self._workbench_error(
                404, "workbench_session_not_found", "Workbench session was not found."
            )
        name = str(payload.get("name") or "").strip()
        if not name:
            return self._workbench_error(
                400, "invalid_request", "Session name must contain text."
            )
        store.rename_session(session_id, name)
        renamed = store.get_session(session_id)
        assert renamed is not None
        return ServiceResponse(200, self._present_workbench_session(renamed))

    async def ask_workbench_session_question(
        self, session_id: str, payload: dict[str, Any]
    ) -> ServiceResponse:
        """Ask the first question of a session that was opened empty."""
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
                404, "workbench_session_not_found", "Workbench session was not found."
            )
        question = str(payload.get("question") or "")
        if not question.strip():
            return self._workbench_error(
                400, "invalid_request", "Question must contain non-whitespace text."
            )
        if store.list_turns(session_id)["turns"]:
            # A session's first question is asked once. Later questions are
            # turns, which carry a base and a revision context this does not.
            return self._workbench_error(
                409,
                "session_already_started",
                "This session already has a turn. Refine it with a follow-up "
                "instead.",
            )

        profile_id = str(payload.get("profileId") or session["profileId"])
        # The session is grounded in one source; a question cannot move it.
        bundle = self._require_bundle(self._session_data_source_id(session))
        if isinstance(bundle, ServiceResponse):
            return bundle
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
                    f"Gateway does not advertise available profile {profile_id}.",
                )
            profile_evidence = self._require_profile_evidence(selected_profile)
            initial_profile_snapshot = self._turn_profile_snapshot(selected_profile)
            runtime_catalog = await self._runtime_catalog(bundle)
        except HubError as error:
            return self._workbench_error(502, error.code, str(error))
        except AnalyticsError as error:
            return self._workbench_error(502, "catalog_unavailable", str(error))
        except ContractError as error:
            return self._workbench_error(
                422, "profile_evidence_unavailable", str(error)
            )

        catalog_conflict = self._workbench_catalog_conflict(session, runtime_catalog)
        if catalog_conflict is not None:
            return catalog_conflict

        store.set_session_question(session_id, question)
        session = store.get_session(session_id)
        assert session is not None
        return await self._seed_workbench_session(
            store,
            session,
            question=question,
            profile_id=profile_id,
            bundle=bundle,
            runtime_catalog=runtime_catalog,
            catalyst_trace_id=str(uuid.uuid4()),
            selected_profile=selected_profile,
            profile_evidence=profile_evidence,
            initial_profile_snapshot=initial_profile_snapshot,
        )

    async def _seed_workbench_session(
        self,
        store: Any,
        session: dict[str, Any],
        *,
        question: str,
        profile_id: str,
        bundle: DataSourceBundle,
        runtime_catalog: Catalog,
        catalyst_trace_id: str,
        selected_profile: dict[str, Any],
        profile_evidence: dict[str, Any],
        initial_profile_snapshot: dict[str, Any],
    ) -> ServiceResponse:
        """Generate a session's first query and record it as the initial turn.

        Addressed by session id alone, so it serves both a session created
        with a question and the first question asked of one opened empty.
        """

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
        initial_turn = store.claim_initial_turn(
            session["sessionId"],
            instruction=question,
            instruction_digest=utf8_sha256(question),
            profile_snapshot=initial_profile_snapshot,
            catalyst_trace_id=catalyst_trace_id,
            hub_request=initial_request,
            profile_evidence=profile_evidence,
            data_source_id=bundle.source_id,
            catalog_version=runtime_catalog.catalog_version,
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

        # The session already carries the provenance it was created with;
        # this records what the generation added to it.
        provenance = dict(session["provenance"])
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

        attempt = (
            self._retained_attempt(
                generation.body,
                turn_id=initial_turn["turnId"],
                catalog=runtime_catalog,
                question=question,
            )
            if generation.body.get("status") != "ready"
            else None
        )
        # A reviewer rejection is terminal for the turn: the writer's candidate
        # is kept unselected. A writer that gave up mid-repair falls through to
        # recovery instead -- an initial turn has no query to protect, so its
        # near-miss becomes the session's first draft.
        if attempt is not None and attempt.rejected_by_reviewer:
            evidence = dict(hub_generation.hub_evidence or {}) if hub_generation else {}
            stage, code = self._model_failure_stage(
                evidence,
                reviewer=True,
                outcome_body=generation.body,
            )
            store.fail_turn(
                initial_turn["turnId"],
                stage=stage,
                code=code,
                message=self._failure_summary(
                    generation.body,
                    str(generation.body.get("message") or "Initial review failed."),
                ),
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
                retained_writer=attempt.candidate,
                retained_writer_validation=attempt.validation,
                details=self._failure_details(generation.body),
                writer_outcome=self._terminal_writer_answer(generation.body),
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
            evidence = (
                dict(hub_generation.hub_evidence or {})
                if hub_generation is not None
                else {}
            )
            if evidence.get("modelInvocations"):
                stage, code = self._model_failure_stage(
                    evidence,
                    reviewer=False,
                    outcome_body=generation.body,
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
            # Nothing to retain here by construction: a candidate with SQL
            # would have been recovered as the draft above.
            store.fail_turn(
                initial_turn["turnId"],
                stage=stage,
                code=code,
                message=self._failure_summary(
                    generation.body,
                    str(
                        generation.body.get("error", {}).get("message")
                        or generation.body.get("message")
                        or "Initial query generation did not produce a usable "
                        "candidate."
                    ),
                ),
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
                details=self._failure_details(generation.body),
                writer_outcome=self._terminal_writer_answer(generation.body),
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

    def list_workbench_sessions(self, limit: int = 20) -> ServiceResponse:
        store = self.workbench_store
        if store is None:
            return self._workbench_error(
                503,
                "workbench_unavailable",
                "The manual workbench is not configured.",
            )
        return ServiceResponse(
            200,
            {
                "contractVersion": "catalyst.workbench.session-list.v1",
                "sessions": store.list_sessions(limit=limit),
            },
        )

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
            prior_turns = store.list_turns(session_id)["turns"]
            requested_source = payload.get("dataSourceId")
            session_source_id = self._session_data_source_id(session)
            if requested_source and str(requested_source) != session_source_id:
                return self._workbench_error(
                    409,
                    "data_source_immutable",
                    "This workbench session is grounded in one data source. "
                    "Start a new session to query another source.",
                    details={
                        "sessionDataSourceId": session_source_id,
                        "requestedDataSourceId": str(requested_source),
                    },
                )
            bundle = self._require_bundle(session_source_id)
            if isinstance(bundle, ServiceResponse):
                return bundle
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
                    f"Gateway does not advertise available profile {profile_id}.",
                )
            if not self._profile_revision_capable(selected_profile):
                return self._workbench_error(
                    422,
                    "profile_not_revision_capable",
                    f"Profile {profile_id} cannot run the different-family revision flow.",
                )
            profile_evidence = self._require_profile_evidence(selected_profile)
            profile_snapshot = self._turn_profile_snapshot(selected_profile)
            runtime_catalog = await self._runtime_catalog(bundle)
            catalog_conflict = self._workbench_catalog_conflict(
                session,
                runtime_catalog,
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
                data_source_id=bundle.source_id,
                catalog_version=runtime_catalog.catalog_version,
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
                # A follow-up already has a working query behind it, so a
                # failed attempt is kept beside that query rather than
                # replacing it: the turn stays failed and the candidate is one
                # click from the editor.
                attempt = self._retained_attempt(
                    query,
                    turn_id=claimed["turnId"],
                    catalog=runtime_catalog,
                    question=instruction,
                )
                stage, failure_code = self._model_failure_stage(
                    hub_evidence,
                    reviewer=attempt is not None and attempt.rejected_by_reviewer,
                    outcome_body=query,
                )
                failed = store.fail_turn(
                    claimed["turnId"],
                    stage=stage,
                    code=failure_code,
                    message=self._failure_summary(
                        query,
                        str(query.get("message") or "Follow-up generation failed."),
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
                    retained_writer=attempt.candidate if attempt else None,
                    retained_writer_validation=(
                        attempt.validation if attempt else None
                    ),
                    details=self._failure_details(query),
                    writer_outcome=self._terminal_writer_answer(query),
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
                    details=self._failure_details(query),
                    writer_outcome=self._terminal_writer_answer(query),
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
            requested_source = payload.get("dataSourceId")
            session_source_id = self._session_data_source_id(session)
            if requested_source and str(requested_source) != session_source_id:
                return self._workbench_error(
                    409,
                    "data_source_immutable",
                    "This workbench session is grounded in one data source. "
                    "Start a new session to query another source.",
                    details={
                        "sessionDataSourceId": session_source_id,
                        "requestedDataSourceId": str(requested_source),
                    },
                )
            bundle = self._require_bundle(session_source_id)
            if isinstance(bundle, ServiceResponse):
                return bundle
            runtime_catalog = await self._runtime_catalog(bundle)
            catalog_conflict = self._workbench_catalog_conflict(
                session,
                runtime_catalog,
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
            if parent is not None and not sql_layout_matches(sql, parent["sql"]):
                # Expected columns describe model output, not an independently
                # editable contract. Once a human changes SQL, retaining the old
                # projection would present stale schema as if it were verified.
                # A reformat is not such a change: a reflowed query has the
                # same projection, so the model's declaration still holds.
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
                    {
                        "editedFromVersionId": parent["versionId"],
                        "dataSourceId": bundle.source_id,
                    }
                    if parent is not None
                    else {
                        "parentlessInitialDraft": True,
                        "dataSourceId": bundle.source_id,
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
        bundle = self._version_bundle(version, session)
        if isinstance(bundle, ServiceResponse):
            return bundle
        try:
            runtime_catalog = await self._runtime_catalog(bundle)
        except AnalyticsError as error:
            return self._workbench_error(502, "catalog_unavailable", str(error))
        catalog_conflict = self._workbench_catalog_conflict(
            session,
            runtime_catalog,
        )
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
        bundle = self._version_bundle(version, session)
        if isinstance(bundle, ServiceResponse):
            return bundle
        try:
            runtime_catalog = await self._runtime_catalog(bundle)
        except AnalyticsError as error:
            return self._workbench_error(502, "catalog_unavailable", str(error))
        catalog_conflict = self._workbench_catalog_conflict(
            session,
            runtime_catalog,
        )
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
        assert bundle.analytics is not None  # _resolve_data_source guards this
        try:
            result = await bundle.analytics.execute_manual(
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
        # Scoped to the default data source only; per-source readiness across
        # the full registry is future work (tracked in the 008 amendment),
        # not a gap in this check.
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
                f"Gateway does not advertise available profile {profile_id}.",
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

        # Reviewer evidence is only required for profiles that declare a reviewer.
        # Writer-only profiles finalize on the writer's lint-passing candidate.
        has_reviewer = isinstance(profile_snapshot.get("reviewer"), dict)
        if ready and (
            ("writer" not in succeeded_roles and not repaired_linted_writer)
            or (has_reviewer and "reviewer" not in succeeded_roles)
        ):
            missing = []
            if "writer" not in succeeded_roles and not repaired_linted_writer:
                missing.append("successful writer or coherent reviewer repair")
            if has_reviewer and "reviewer" not in succeeded_roles:
                missing.append("successful reviewer invocation")
            raise HubError(
                "hub_invalid_response",
                "A ready Hub query requires completed writer"
                + (" and successful reviewer" if has_reviewer else "")
                + f" invocation evidence; missing: {', '.join(missing)}.",
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

    def _session_data_source_id(self, session: dict[str, Any]) -> str:
        """The source this session was created against.

        A session is grounded in one catalog: its query versions chain through
        parentVersionId and its follow-ups are written relative to the previous
        query, so a version whose parent was written against a different schema
        would describe a lineage that never existed. The source is therefore
        fixed at creation and never moves.
        """
        source_id = (session.get("provenance") or {}).get("dataSourceId")
        return str(source_id) if source_id else self._default_data_source_id

    def _workbench_catalog_conflict(
        self,
        session: dict[str, Any],
        runtime_catalog: Catalog,
    ) -> ServiceResponse | None:
        # One session, one source, one baseline: the catalog this session was
        # created against.
        baseline = str(session["catalogVersion"])
        if runtime_catalog.catalog_version == baseline:
            return None
        return self._workbench_error(
            409,
            "stale_catalog_version",
            "The readable PostgreSQL catalog changed after this workbench session "
            "was created. Start a new session before saving, validating, running, "
            "or refining this query.",
            details={
                "sessionCatalogVersion": baseline,
                "runtimeCatalogVersion": runtime_catalog.catalog_version,
            },
        )

    @staticmethod
    def _profile_snapshot(profile: dict[str, Any]) -> dict[str, Any]:
        raw_evidence = profile.get("profileEvidence")
        evidence = raw_evidence if isinstance(raw_evidence, dict) else {}
        raw_writer = evidence.get("writer")
        writer = raw_writer if isinstance(raw_writer, dict) else {}
        raw_reviewer = evidence.get("reviewer")
        reviewer = raw_reviewer if isinstance(raw_reviewer, dict) else {}
        raw_writer_prompt = writer.get("systemPrompt")
        writer_prompt = raw_writer_prompt if isinstance(raw_writer_prompt, dict) else {}
        raw_reviewer_prompt = reviewer.get("systemPrompt")
        reviewer_prompt = (
            raw_reviewer_prompt if isinstance(raw_reviewer_prompt, dict) else {}
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
        # Writer is always required; reviewer is present only for reviewed
        # profiles. A writer-only profile legitimately omits the reviewer leg.
        present_roles = ["writer"]
        if "reviewer" in exact:
            present_roles.append("reviewer")
        for role_name in present_roles:
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
        if "reviewer" in compact:
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

        snapshot: dict[str, Any] = {
            "profileId": exact["profileId"],
            "profileName": exact["profileName"],
            "profileDigest": "0" * 64,
            "writer": compact_role(exact["writer"]),
            "omissions": [],
        }
        # Writer-only profiles simply omit the reviewer leg. Recorded turns must
        # keep omissions empty (that field is only for legacy-loaded turns), and
        # the profileSnapshot no longer requires a reviewer, so an absent reviewer
        # with empty omissions is contract-valid.
        if "reviewer" in exact:
            snapshot["reviewer"] = compact_role(exact["reviewer"])
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

    @classmethod
    def _model_failure_stage(
        cls,
        evidence: dict[str, Any],
        *,
        reviewer: bool,
        outcome_body: dict[str, Any] | None = None,
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
        terminal_role = terminal.get("role")
        role = (
            str(terminal_role)
            if terminal_role in {"writer", "reviewer"}
            else ("reviewer" if reviewer else "writer")
        )
        answered = cls._terminal_writer_answer(outcome_body)
        if answered is not None:
            return f"{role}_decision", answered
        transport_outcomes = {"timed_out", "cancelled", "transport_failed"}
        if outcome not in transport_outcomes and cls._unresolved_findings(outcome_body):
            # The request was understood and could not be satisfied: a
            # different thing from output that never became a candidate, and
            # it calls for a different response from whoever reads it.
            if cls._unanswerable_without_asking(outcome_body):
                return f"{role}_findings", "needs_clarification"
            return f"{role}_findings", "generation_findings_unresolved"
        if outcome == "contract_failed":
            return f"{role}_output_contract", f"{role}_output_contract_failed"
        if outcome == "validation_failed":
            return f"{role}_output_contract", f"{role}_output_contract_failed"
        if outcome == "timed_out":
            return f"{role}_transport", f"{role}_timeout"
        if outcome == "cancelled":
            return f"{role}_transport", f"{role}_cancelled"
        if outcome == "succeeded":
            # The model call itself worked; the turn still failed because the
            # decision was a rejection, not because of a transport problem.
            return f"{role}_decision", f"{role}_rejected"
        return f"{role}_transport", f"{role}_transport_failed"

    def _retained_attempt(
        self,
        outcome: dict[str, Any] | None,
        *,
        turn_id: str,
        catalog: Catalog,
        question: str,
    ) -> _RetainedAttempt | None:
        """The best complete candidate a failed generation produced, if any.

        Two things end a generation holding a usable candidate: a reviewer
        rejecting one the writer had already linted clean, and the writer's own
        repair loop running out of attempts on a candidate that is usually one
        identifier from correct. Both leave something worth editing, and which
        one happened decides how the failure is described -- so this resolves
        the candidate and reports its origin together, rather than letting
        callers infer the second from the first.
        """
        if not isinstance(outcome, dict):
            return None
        collaboration = outcome.get("modelCollaboration")
        writer = (
            collaboration.get("writer") if isinstance(collaboration, dict) else None
        )
        if (
            isinstance(writer, dict)
            and writer.get("disposition") == "retained_unselected"
            and isinstance(writer.get("candidate"), dict)
            and writer["candidate"].get("sql")
        ):
            return self._retain(
                writer["candidate"],
                turn_id=turn_id,
                catalog=catalog,
                question=question,
                findings=list(writer.get("lintFindings") or []),
                model=writer.get("model"),
                rejected_by_reviewer=True,
            )

        candidate = self._diagnostic_candidate(outcome)
        if candidate is None or not candidate.get("sql"):
            return None
        return self._retain(
            candidate,
            turn_id=turn_id,
            catalog=catalog,
            question=question,
            findings=self._unresolved_findings(outcome),
            model=None,
            rejected_by_reviewer=False,
        )

    def _retain(
        self,
        candidate: dict[str, Any],
        *,
        turn_id: str,
        catalog: Catalog,
        question: str,
        findings: list[dict[str, Any]],
        model: Any,
        rejected_by_reviewer: bool,
    ) -> _RetainedAttempt:
        retained: dict[str, Any] = {
            **deepcopy(candidate),
            "provenance": {
                "turnId": turn_id,
                "collaborationRole": "writer",
                "lintFindings": deepcopy(findings),
                **({"model": model} if model is not None else {}),
            },
        }
        validation = self._build_workbench_validation(
            {
                **retained,
                "queryDigest": workbench_query_digest(
                    str(retained["sql"]),
                    list(retained.get("parameters") or []),
                    list(retained.get("expectedColumns") or []),
                ),
            },
            question=question,
            catalog=catalog,
            source_findings=deepcopy(findings),
        )
        return _RetainedAttempt(
            candidate=retained,
            validation=validation,
            rejected_by_reviewer=rejected_by_reviewer,
        )

    @staticmethod
    def _diagnostic_candidate(outcome: dict[str, Any] | None) -> dict[str, Any] | None:
        """The candidate a failed generation left behind, if it left one."""
        if not isinstance(outcome, dict):
            return None
        diagnostic = outcome.get("diagnosticCandidate")
        candidate = (
            diagnostic.get("candidate") if isinstance(diagnostic, dict) else None
        )
        return candidate if isinstance(candidate, dict) else None

    @staticmethod
    def _generation_attempts(outcome: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Each attempt the generation loop made, oldest first."""
        if not isinstance(outcome, dict):
            return []
        diagnostic = outcome.get("diagnosticCandidate")
        attempts = diagnostic.get("attempts") if isinstance(diagnostic, dict) else None
        return [
            attempt
            for attempt in (attempts if isinstance(attempts, list) else [])
            if isinstance(attempt, dict)
        ]

    @classmethod
    def _unresolved_findings(
        cls, outcome: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        """The findings about the query that the loop never resolved.

        These name a specific defect in a specific place -- an unknown column,
        an unbound literal -- and are the only part of a failed turn that tells
        someone what to do next.

        The last attempt is usually not where they are. A loop that found a bad
        identifier spends its remaining attempts patching it, and those attempts
        report on the patching: an anchor that matched twice, output that was
        not a candidate. Those are facts about the machinery, and they bury the
        identifier that is still wrong. So the newest attempt that said anything
        about the query is the one that still stands.
        """
        for attempt in reversed(cls._generation_attempts(outcome)):
            findings = attempt.get("findings")
            about_query = [
                finding
                for finding in (findings if isinstance(findings, list) else [])
                if isinstance(finding, dict)
                and finding.get("code")
                and str(finding.get("stage") or "") not in _LOOP_STAGES
            ]
            if about_query:
                return about_query
        return []

    @staticmethod
    def _terminal_writer_answer(outcome: dict[str, Any] | None) -> str | None:
        """The writer's own terminal answer, when it gave one.

        `needs_clarification` and `unsupported` are answers the writer chose
        and stated; they are not inferred from findings and not the Gateway's
        `rejected`.
        """
        if not isinstance(outcome, dict):
            return None
        status = outcome.get("status")
        return str(status) if status in {"needs_clarification", "unsupported"} else None

    @classmethod
    def _unanswerable_without_asking(cls, outcome: dict[str, Any] | None) -> bool:
        """Every unresolved finding names something the dataset does not have.

        No further attempt can invent the field, so the turn is a question for
        the person who asked rather than a failure of the run. One finding of
        any other kind and it is not: that one might still have been fixable,
        and asking about it would put the loop's work onto the reader.
        """
        findings = cls._unresolved_findings(outcome)
        return bool(findings) and all(
            str(finding.get("code") or "").startswith("catalog.unknown")
            for finding in findings
        )

    @classmethod
    def _clarifying_question(cls, outcome: dict[str, Any] | None) -> str | None:
        """The unknown identifiers, put back as a question.

        The finding's own wording instructs a model to obey a catalog. What
        the reader needs is the name the query referenced and an invitation to
        say what was meant. The exhaustion proves only that the *model* found
        no such column -- the concept may well exist under another name -- so
        the question reports the model's failure, never a fact about the data.
        """
        names = [
            stripped
            for finding in cls._unresolved_findings(outcome)
            for raw in str(finding.get("evidence") or "").split(",")
            if (stripped := raw.strip())
        ]
        if not names:
            return None
        subject = (
            f"“{names[0]}”"
            if len(names) == 1
            else ", ".join(f"“{name}”" for name in names)
        )
        return (
            f"The model couldn't find {subject} here. Which field did you "
            "mean, or should the request be worded differently?"
        )

    @classmethod
    def _attempts_summary(cls, outcome: dict[str, Any] | None) -> str | None:
        """What to say when every attempt was about the machinery.

        There is no finding to quote and the outcome's own wording is contract
        boilerplate. What is true, and enough to act on beside a retained
        attempt, is that the model tried this many times and did not get there.
        """
        attempts = len(cls._generation_attempts(outcome))
        if attempts < 1:
            return None
        times = "attempt" if attempts == 1 else "attempts"
        return f"The model did not produce a usable query in {attempts} {times}."

    @classmethod
    def _failure_summary(cls, outcome: dict[str, Any] | None, fallback: str) -> str:
        """What a person reads when a turn fails.

        A finding says which identifier is wrong and how to fix it; the
        pipeline stage says only that a pipeline exists.
        """
        answered = cls._terminal_writer_answer(outcome)
        if answered is not None:
            # The writer's own text is the answer; storing anything else here
            # would put words in its mouth.
            spoken = outcome.get(
                "clarification" if answered == "needs_clarification" else "message"
            )
            if isinstance(spoken, str) and spoken.strip():
                return spoken
        if cls._unanswerable_without_asking(outcome):
            question = cls._clarifying_question(outcome)
            if question is not None:
                return question
        findings = cls._unresolved_findings(outcome)
        if not findings:
            return cls._attempts_summary(outcome) or fallback
        first = findings[0]
        parts = [str(first.get("message") or "").strip()]
        action = str(first.get("suggestedAction") or "").strip()
        if action and action not in parts[0]:
            parts.append(action)
        summary = " ".join(part for part in parts if part)
        remaining = len(findings) - 1
        if remaining > 0:
            noun = "finding" if remaining == 1 else "findings"
            summary = f"{summary} ({remaining} more {noun})"
        return summary or fallback

    @staticmethod
    def _failure_check_details(outcome: dict[str, Any] | None) -> list[dict[str, Any]]:
        """The named checks that failed, out of a generation outcome.

        A failed turn's diagnostic must name what failed -- these are read in
        the cell instead of the person opening Evidence. Passed checks are not
        failures; transport errors carry no outcome and get an empty list.
        """
        if not isinstance(outcome, dict):
            return []
        validation = outcome.get("validation")
        checks = validation.get("checks") if isinstance(validation, dict) else None
        # The stage check that only restates the outcome's own message would
        # put the generic wording back under the summary that replaced it.
        outcome_message = str(outcome.get("message") or "").strip()
        details: list[dict[str, Any]] = []
        for check in checks if isinstance(checks, list) else []:
            if not isinstance(check, dict) or check.get("status") == "passed":
                continue
            status = str(check.get("status") or "failed")
            message = check.get("message")
            if outcome_message and str(message or "").strip() == outcome_message:
                continue
            value = (
                f"{status} — {message}"
                if isinstance(message, str) and message
                else status
            )
            # The diagnostic contract's detail shape is {name, value}.
            details.append(
                {
                    "name": str(check.get("name") or "unnamed_check")[:100],
                    "value": value[:4000],
                }
            )
        return details[:32]

    @classmethod
    def _failure_details(cls, outcome: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Pointed findings first, then the named checks.

        A finding carries the path and the offending fragment, so it is what a
        person needs; the checks say which stage reported it.
        """
        details = [
            {
                "name": str(finding.get("code"))[:100],
                "value": " ".join(
                    part
                    for part in (
                        str(finding.get("path") or "").strip(),
                        str(finding.get("message") or "").strip(),
                        (
                            f"Found: {finding['evidence']}"
                            if finding.get("evidence")
                            else ""
                        ),
                        str(finding.get("suggestedAction") or "").strip(),
                    )
                    if part
                )[:4000],
            }
            for finding in cls._unresolved_findings(outcome)
        ]
        return (details + cls._failure_check_details(outcome))[:32]

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

        candidate = CatalystService._diagnostic_candidate(outcome)
        provenance = dict(outcome.get("provenance") or {})
        provenance["sourceContract"] = contract_version
        provenance["sourceStatus"] = outcome.get("status")
        provenance["hubTraceId"] = provenance.get("traceId")
        provenance["generationAttempts"] = [
            dict(attempt) for attempt in CatalystService._generation_attempts(outcome)
        ]
        provenance["generationValidation"] = dict(outcome.get("validation") or {})
        if candidate is None or not isinstance(candidate.get("sql"), str):
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

    def pin_workbench_guidance(
        self, session_id: str, payload: dict[str, Any]
    ) -> ServiceResponse:
        """Pin one instruction to a session, exactly as written."""
        try:
            self.contracts.validate(
                "catalyst-workbench-guidance-request-v1.schema.json", payload
            )
        except ContractError as invalid:
            return self._workbench_error(422, "invalid_request", str(invalid))
        session = self.workbench_store.get_session(session_id)
        if session is None:
            return self._workbench_error(404, "session_not_found", session_id)
        text = str(payload["text"])
        if not text.strip():
            return self._workbench_error(
                422, "invalid_request", "guidance text must not be blank"
            )
        try:
            self.workbench_store.pin_guidance(
                session_id,
                text=text,
                source=str(payload.get("source", "human")),
                origin_turn_id=payload.get("originTurnId"),
                supersedes=payload.get("supersedes"),
            )
        except KeyError:
            return self._workbench_error(404, "session_not_found", session_id)
        except ValueError as invalid:
            return self._workbench_error(422, "invalid_request", str(invalid))
        restored = self.workbench_store.get_session(session_id)
        assert restored is not None
        return ServiceResponse(201, self._present_workbench_session(restored))

    def unpin_workbench_guidance(
        self, session_id: str, entry_id: str
    ) -> ServiceResponse:
        """Stop delivering an entry. Its text and history stay."""
        if self.workbench_store.get_session(session_id) is None:
            return self._workbench_error(404, "session_not_found", session_id)
        try:
            self.workbench_store.unpin_guidance(session_id, entry_id)
        except KeyError:
            return self._workbench_error(404, "guidance_not_found", entry_id)
        restored = self.workbench_store.get_session(session_id)
        assert restored is not None
        return ServiceResponse(200, self._present_workbench_session(restored))

    def _present_workbench_session(self, session: dict[str, Any]) -> dict[str, Any]:
        presented = deepcopy(session)
        presented["guidance"] = self.workbench_store.active_guidance(
            str(session["sessionId"])
        )
        # Last-turn-wins, matching turn targeting: a reload must land on the
        presented["dataSourceId"] = self._session_data_source_id(session)
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
        query = {
            "contractVersion": "catalyst.query.v1",
            "deploymentMode": "demo",
            "status": "ready",
            "question": question,
            "target": {
                **catalog.request_target(),
                # The curated allowlist, not every readable relation. Runtime
                # discovery describes the whole schema for the browser; it must
                # not widen what a generated query is allowed to reference.
                "approvedViews": sorted(catalog.approved_view_names),
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
                available_relations=catalog.approved_view_names,
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
            available_relations=catalog.approved_view_names,
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
