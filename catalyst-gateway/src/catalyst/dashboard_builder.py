"""Durable, supervised Dataset → Widget → Dashboard → Superset bundle flow.

This is intentionally a small local-MVP persistence layer.  It owns desired
configuration and lineage, while Superset owns rendering.  It never copies
execution rows into builder state or calls the model/database to save or
publish a draft.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .digest import canonical_sha256
from .storage import WorkbenchStore


_NAMESPACE = uuid.UUID("8567e617-8772-585f-8f1a-c9e9a63b2f20")
_PARAMETER = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")
_PRESENTATION_KINDS = {
    "table",
    "big_number",
    "time_series_line",
    "time_series_area",
    "grouped_bar",
    "stacked_bar",
    "proportion_bar",
}
# Superset chart renderers require a native aggregate object. This is an export
# implementation detail; the saved Catalyst SQL remains the reporting contract.
_SUPERSET_METRIC_AGGREGATE = "MAX"


class DashboardBuilderError(RuntimeError):
    """A request cannot create a durable dashboard-builder artifact."""


@dataclass(frozen=True)
class BuilderEntity:
    kind: str
    logical_id: str
    version_id: str
    ordinal: int
    configuration: dict[str, Any]
    configuration_digest: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.logical_id,
            "versionId": self.version_id,
            "ordinal": self.ordinal,
            "configuration": self.configuration,
            "configurationDigest": self.configuration_digest,
            "createdAt": self.created_at,
        }


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _uuid5(name: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, name))


def _uuid4() -> str:
    return str(uuid.uuid4())


def _sql_literal(parameter: dict[str, Any]) -> str:
    value = parameter.get("value")
    kind = parameter.get("type")
    if value is None:
        return "NULL"
    if kind in {"integer", "number"}:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise DashboardBuilderError(
                f"Invalid numeric value for :{parameter['name']}."
            )
        return str(value)
    if kind == "boolean":
        if not isinstance(value, bool):
            raise DashboardBuilderError(
                f"Invalid boolean value for :{parameter['name']}."
            )
        return "TRUE" if value else "FALSE"
    if kind in {"string-list", "integer-list"}:
        if not isinstance(value, list):
            raise DashboardBuilderError(f"Invalid list value for :{parameter['name']}.")
        nested_type = "integer" if kind == "integer-list" else "string"
        return (
            "("
            + ", ".join(
                _sql_literal(
                    {"name": parameter["name"], "type": nested_type, "value": item}
                )
                for item in value
            )
            + ")"
        )
    if not isinstance(value, str):
        raise DashboardBuilderError(f"Invalid text value for :{parameter['name']}.")
    escaped = value.replace("'", "''")
    if kind == "date":
        return f"DATE '{escaped}'"
    if kind == "date-time":
        return f"TIMESTAMPTZ '{escaped}'"
    return f"'{escaped}'"


def compile_parameterized_sql(sql: str, parameters: list[dict[str, Any]]) -> str:
    """Compile the accepted workbench parameter values into PostgreSQL literals.

    The source Query version and its typed parameters remain the authority;
    compilation creates the Superset virtual-dataset SQL only.  Unbound names
    are rejected rather than guessed.
    """

    by_name = {str(item.get("name")): item for item in parameters}
    seen: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        parameter = by_name.get(name)
        if parameter is None:
            raise DashboardBuilderError(f"Superset export has no value for :{name}.")
        seen.add(name)
        return _sql_literal(parameter)

    compiled = _PARAMETER.sub(replace, sql)
    unused = sorted(set(by_name) - seen)
    if unused:
        raise DashboardBuilderError(
            "Superset export includes unused parameter values: " + ", ".join(unused)
        )
    return compiled


def suggest_presentation(columns: Iterable[dict[str, Any]], row_count: int) -> str:
    ordered = list(columns)
    logical = [str(column.get("logicalType", "unknown")) for column in ordered]
    temporal = [
        index for index, kind in enumerate(logical) if kind in {"date", "date-time"}
    ]
    numeric = [
        index for index, kind in enumerate(logical) if kind in {"integer", "decimal"}
    ]
    categorical = [index for index, kind in enumerate(logical) if kind == "string"]
    if len(ordered) == 1 and len(numeric) == 1 and row_count <= 1:
        return "big_number"
    if temporal and numeric:
        return "time_series_line"
    if categorical and numeric and len(ordered) <= 4:
        return "grouped_bar"
    return "table"


def _first_column(
    columns: Iterable[dict[str, Any]], logical_types: set[str]
) -> dict[str, Any] | None:
    return next(
        (
            column
            for column in columns
            if str(column.get("logicalType")) in logical_types
        ),
        None,
    )


def _column_binding(column: dict[str, Any]) -> dict[str, Any]:
    return {
        "ordinal": int(column["ordinal"]),
        "name": str(column["name"]),
        "logicalType": str(column["logicalType"]),
    }


def widget_bindings(
    *,
    presentation_kind: str,
    columns: Iterable[dict[str, Any]],
    row_count: int,
) -> dict[str, Any]:
    """Derive the sole supported chart bindings from an executed result schema."""

    ordered = list(columns)
    numeric = _first_column(ordered, {"integer", "decimal"})
    temporal = _first_column(ordered, {"date", "date-time"})
    categorical = [
        column
        for column in ordered
        if str(column.get("logicalType")) in {"string", "boolean"}
    ]
    if presentation_kind == "table":
        return {"columns": [_column_binding(column) for column in ordered]}
    if presentation_kind == "big_number":
        if row_count != 1 or len(ordered) != 1 or numeric is None:
            raise DashboardBuilderError(
                "Big number requires exactly one returned numeric cell."
            )
        return {"metricColumn": _column_binding(numeric)}
    if presentation_kind in {"time_series_line", "time_series_area"}:
        if temporal is None or numeric is None:
            raise DashboardBuilderError(
                "Time series requires a temporal and numeric result."
            )
        return {
            "xColumn": _column_binding(temporal),
            "metricColumn": _column_binding(numeric),
            "seriesColumns": [_column_binding(column) for column in categorical],
        }
    if presentation_kind in {"grouped_bar", "stacked_bar", "proportion_bar"}:
        if not categorical or numeric is None:
            raise DashboardBuilderError(
                "Bar chart requires a categorical and numeric result."
            )
        series = categorical[1] if len(categorical) > 1 else None
        if presentation_kind == "proportion_bar" and series is None:
            raise DashboardBuilderError(
                "Proportion bar requires a categorical series as well as a category."
            )
        return {
            "categoryColumn": _column_binding(categorical[0]),
            "metricColumn": _column_binding(numeric),
            "seriesColumns": [_column_binding(column) for column in categorical[1:]],
        }
    raise DashboardBuilderError("Unsupported presentation kind.")


def _metric(metric_column: dict[str, Any]) -> dict[str, Any]:
    name = str(metric_column["name"])
    return {
        "aggregate": _SUPERSET_METRIC_AGGREGATE,
        "column": {"column_name": name},
        "datasourceWarning": False,
        "expressionType": "SIMPLE",
        "hasCustomLabel": False,
        "label": f"{_SUPERSET_METRIC_AGGREGATE}({name})",
        "optionName": f"metric_{_SUPERSET_METRIC_AGGREGATE.lower()}_{name}",
        "sqlExpression": None,
    }


def _native_chart(
    *,
    title: str,
    presentation_kind: str,
    bindings: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    if presentation_kind == "table":
        return "table", {
            "viz_type": "table",
            "all_columns": [column["name"] for column in bindings["columns"]],
            "row_limit": 1000,
            "order_by_cols": [],
        }

    metric = _metric(bindings["metricColumn"])
    if presentation_kind == "big_number":
        return "big_number_total", {
            "viz_type": "big_number_total",
            "metric": metric,
        }
    if presentation_kind in {"time_series_line", "time_series_area"}:
        viz_type = (
            "echarts_timeseries_line"
            if presentation_kind == "time_series_line"
            else "echarts_area"
        )
        return viz_type, {
            "viz_type": viz_type,
            "x_axis": bindings["xColumn"]["name"],
            "metrics": [metric],
            "row_limit": 10000,
            "show_legend": True,
            "groupby": [column["name"] for column in bindings["seriesColumns"]],
        }

    params = {
        "viz_type": "echarts_timeseries_bar",
        "x_axis": bindings["categoryColumn"]["name"],
        "metrics": [metric],
        "row_limit": 10000,
        "show_legend": True,
    }
    if bindings["seriesColumns"]:
        params["groupby"] = [column["name"] for column in bindings["seriesColumns"]]
    if presentation_kind in {"stacked_bar", "proportion_bar"}:
        params["stack"] = "Stack"
    if presentation_kind == "proportion_bar":
        params["contributionMode"] = "row"
    return "echarts_timeseries_bar", params


class DashboardBuilder:
    """SQLite-backed immutable drafts and deterministic native bundle writer."""

    def __init__(
        self,
        path: str | Path,
        *,
        workbench: WorkbenchStore,
        outbox: str | Path,
        receipts: str | Path | None = None,
    ):
        self.path = str(path)
        self.workbench = workbench
        self.outbox = Path(outbox)
        self.receipts = Path(receipts) if receipts is not None else None
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path, timeout=5, isolation_level=None, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self._connection.close()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS catalyst_dashboard_entities (
                  kind TEXT NOT NULL,
                  logical_id TEXT NOT NULL,
                  version_id TEXT PRIMARY KEY,
                  ordinal INTEGER NOT NULL,
                  configuration_json TEXT NOT NULL,
                  configuration_digest TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  UNIQUE(kind, logical_id, ordinal)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS catalyst_dashboard_publications (
                  dashboard_version_id TEXT PRIMARY KEY,
                  publication_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )

    def _transaction(self):
        return self._connection

    def _append(
        self, kind: str, configuration: dict[str, Any], logical_id: str | None = None
    ) -> BuilderEntity:
        logical = logical_id or _uuid4()
        version_id = _uuid4()
        digest = canonical_sha256(configuration)
        timestamp = _utc_now()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal "
                    "FROM catalyst_dashboard_entities WHERE kind = ? AND logical_id = ?",
                    (kind, logical),
                ).fetchone()
                ordinal = int(row["next_ordinal"])
                self._connection.execute(
                    "INSERT INTO catalyst_dashboard_entities "
                    "(kind, logical_id, version_id, ordinal, configuration_json, configuration_digest, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        kind,
                        logical,
                        version_id,
                        ordinal,
                        _json(configuration),
                        digest,
                        timestamp,
                    ),
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        return BuilderEntity(
            kind, logical, version_id, ordinal, configuration, digest, timestamp
        )

    def _entity(self, kind: str, version_id: str) -> BuilderEntity:
        row = self._connection.execute(
            "SELECT * FROM catalyst_dashboard_entities WHERE kind = ? AND version_id = ?",
            (kind, version_id),
        ).fetchone()
        if row is None:
            raise DashboardBuilderError(f"{kind.title()} version was not found.")
        return BuilderEntity(
            kind=str(row["kind"]),
            logical_id=str(row["logical_id"]),
            version_id=str(row["version_id"]),
            ordinal=int(row["ordinal"]),
            configuration=json.loads(row["configuration_json"]),
            configuration_digest=str(row["configuration_digest"]),
            created_at=str(row["created_at"]),
        )

    def list(self, kind: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM catalyst_dashboard_entities WHERE kind = ? "
            "ORDER BY created_at DESC, ordinal DESC",
            (kind,),
        ).fetchall()
        return [
            BuilderEntity(
                kind=str(row["kind"]),
                logical_id=str(row["logical_id"]),
                version_id=str(row["version_id"]),
                ordinal=int(row["ordinal"]),
                configuration=json.loads(row["configuration_json"]),
                configuration_digest=str(row["configuration_digest"]),
                created_at=str(row["created_at"]),
            ).as_dict()
            for row in rows
        ]

    def save_dataset(
        self, *, session_id: str, execution_id: str, title: str
    ) -> dict[str, Any]:
        session = self.workbench.get_session(session_id)
        if session is None:
            raise DashboardBuilderError("Workbench session was not found.")
        execution = next(
            (
                item
                for item in session["executions"]
                if item["executionId"] == execution_id
            ),
            None,
        )
        if execution is None or execution.get("status") != "succeeded":
            raise DashboardBuilderError("Save Dataset requires a successful execution.")
        current = session.get("currentVersion")
        if not current or execution.get("versionId") != current.get("versionId"):
            raise DashboardBuilderError(
                "Save Dataset requires the currently visible query's successful execution."
            )
        result = execution.get("result")
        if not isinstance(result, dict):
            raise DashboardBuilderError("Execution result evidence is unavailable.")
        columns = list(result.get("columns") or [])
        query = execution.get("query") or {}
        timeline = self.workbench.list_turns(session_id)
        source_turn_id = str(timeline["currentTurnId"])
        configuration = {
            "title": title.strip() or f"Dataset from Query v{current['ordinal']}",
            "source": {
                "sessionId": session_id,
                "turnId": source_turn_id,
                "queryVersionId": current["versionId"],
                "queryDigest": current["queryDigest"],
                "executionId": execution_id,
                "dataSourceId": session.get("dataSourceId") or "openelis",
                "catalogVersion": session.get("catalogVersion") or "unknown",
                "resultSchemaDigest": canonical_sha256(columns),
                "resultDigest": canonical_sha256(
                    {
                        "columns": columns,
                        "rows": result.get("rows", []),
                        "rowCount": result.get("rowCount"),
                        "warnings": result.get("warnings", []),
                    }
                ),
            },
            "columns": columns,
            "parameterizedSql": str(query.get("sql") or current["sql"]),
            "parameters": list(query.get("parameters") or current["parameters"]),
            "compiledSql": compile_parameterized_sql(
                str(query.get("sql") or current["sql"]),
                list(query.get("parameters") or current["parameters"]),
            ),
            "rowCount": dict(result.get("rowCount") or {}),
            "resultBounds": {
                "returnedRows": int((result.get("rowCount") or {}).get("returned", 0)),
                "maxRows": int(execution.get("maxRows") or 1),
                "truncated": bool(
                    (result.get("rowCount") or {}).get("truncated", False)
                ),
                "truncationReason": (result.get("rowCount") or {}).get(
                    "truncationReason"
                ),
                "warningCodes": (
                    ["all_blank_columns"] if result.get("warnings") else []
                ),
            },
        }
        return self._append("dataset", configuration).as_dict()

    def save_widget(
        self,
        *,
        dataset_version_id: str,
        title: str,
        presentation_kind: str | None = None,
    ) -> dict[str, Any]:
        dataset = self._entity("dataset", dataset_version_id)
        row_count = int(dataset.configuration.get("rowCount", {}).get("returned", 0))
        suggestion = suggest_presentation(dataset.configuration["columns"], row_count)
        kind = presentation_kind or "table"
        if kind not in _PRESENTATION_KINDS:
            raise DashboardBuilderError("Unsupported presentation kind.")
        bindings = widget_bindings(
            presentation_kind=kind,
            columns=dataset.configuration["columns"],
            row_count=row_count,
        )
        configuration = {
            "title": title.strip() or dataset.configuration["title"],
            "datasetVersionId": dataset.version_id,
            "datasetConfigurationDigest": dataset.configuration_digest,
            "presentationKind": kind,
            "suggestedKind": suggestion,
            "columns": dataset.configuration["columns"],
            "bindings": bindings,
        }
        return self._append("widget", configuration).as_dict()

    def save_dashboard(
        self, *, title: str, widget_version_ids: Sequence[str]
    ) -> dict[str, Any]:
        if not widget_version_ids:
            raise DashboardBuilderError("A dashboard needs at least one saved widget.")
        widgets = [
            self._entity("widget", version_id) for version_id in widget_version_ids
        ]
        datasets = [
            self._entity("dataset", item.configuration["datasetVersionId"])
            for item in widgets
        ]
        sources = {
            (
                item.configuration["source"]["dataSourceId"],
                item.configuration["source"]["catalogVersion"],
            )
            for item in datasets
        }
        if len(sources) != 1:
            raise DashboardBuilderError(
                "A dashboard cannot mix data sources or catalog versions."
            )
        configuration = {
            "title": title.strip() or "Catalyst dashboard",
            "widgets": [
                {
                    "versionId": item.version_id,
                    "configurationDigest": item.configuration_digest,
                }
                for item in widgets
            ],
            "dataSourceId": datasets[0].configuration["source"]["dataSourceId"],
            "catalogVersion": datasets[0].configuration["source"]["catalogVersion"],
        }
        return self._append("dashboard", configuration).as_dict()

    def _native_assets(
        self, dashboard: BuilderEntity
    ) -> tuple[dict[str, bytes], dict[str, Any]]:
        widgets = [
            self._entity("widget", item["versionId"])
            for item in dashboard.configuration["widgets"]
        ]
        datasets: dict[str, BuilderEntity] = {
            item.configuration["datasetVersionId"]: self._entity(
                "dataset", item.configuration["datasetVersionId"]
            )
            for item in widgets
        }
        database_uuid = _uuid5(f"database:{dashboard.configuration['dataSourceId']}")
        dashboard_uuid = _uuid5(f"dashboard:{dashboard.logical_id}")
        bundle_id = _uuid5(
            f"bundle:{dashboard.version_id}:{dashboard.configuration_digest}"
        )
        bundle_root = f"catalyst_dashboard_{bundle_id}"
        slug = f"catalyst-{dashboard.logical_id}"
        database: dict[str, Any] = {
            "database_name": f"Catalyst {dashboard.configuration['dataSourceId']} analytics",
            "sqlalchemy_uri": os.environ.get(
                "CATALYST_SUPERSET_ANALYTICS_URI",
                "postgresql+psycopg2://catalyst_readonly:demo-readonly-change-me@analytics-db:5432/catalyst_analytics",
            ),
            "password": None,
            # Superset's native importer rejects an empty encrypted-extra map;
            # omit it when the local demo connection has no encrypted extras.
            "cache_timeout": None,
            "expose_in_sqllab": False,
            "allow_run_async": False,
            "allow_ctas": False,
            "allow_cvas": False,
            "allow_dml": False,
            "allow_csv_upload": False,
            "impersonate_user": False,
            "extra": {},
            "uuid": database_uuid,
            "version": "1.0.0",
        }
        members: dict[str, bytes] = {
            f"databases/{database_uuid}.yaml": _json(database).encode("utf-8")
        }
        dataset_uuids: dict[str, str] = {}
        for dataset in datasets.values():
            dataset_uuid = _uuid5(f"dataset:{dataset.version_id}")
            dataset_uuids[dataset.version_id] = dataset_uuid
            columns = []
            for column in dataset.configuration["columns"]:
                logical = str(column.get("logicalType", "string"))
                columns.append(
                    {
                        "column_name": column["name"],
                        "type": column.get("databaseType") or "text",
                        "is_dttm": logical in {"date", "date-time"},
                        "is_active": True,
                        "groupby": logical in {"string", "date", "date-time"},
                        "filterable": True,
                    }
                )
            dataset_config = {
                "table_name": f"catalyst_dataset_{dataset.version_id.replace('-', '_')}",
                "main_dttm_col": next(
                    (
                        column["name"]
                        for column in dataset.configuration["columns"]
                        if column.get("logicalType") in {"date", "date-time"}
                    ),
                    None,
                ),
                "description": dataset.configuration["title"],
                "schema": None,
                "sql": dataset.configuration["compiledSql"],
                "source_db_engine": "postgresql",
                "params": {},
                "template_params": None,
                "filter_select_enabled": False,
                "fetch_values_predicate": None,
                "extra": {},
                "uuid": dataset_uuid,
                "columns": columns,
                "metrics": [],
                "version": "1.0.0",
                "database_uuid": database_uuid,
            }
            members[f"datasets/{dataset_uuid}.yaml"] = _json(dataset_config).encode(
                "utf-8"
            )
        chart_uuids: dict[str, str] = {}
        chart_positions: list[str] = []
        position: dict[str, Any] = {
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"id": "ROOT_ID", "type": "ROOT", "children": ["GRID_ID"]},
            "GRID_ID": {
                "id": "GRID_ID",
                "type": "GRID",
                "parents": ["ROOT_ID"],
                "children": chart_positions,
            },
        }
        for index, widget in enumerate(widgets):
            chart_uuid = _uuid5(f"chart:{widget.version_id}")
            chart_uuids[widget.version_id] = chart_uuid
            dataset_uuid = dataset_uuids[widget.configuration["datasetVersionId"]]
            viz_type, params = _native_chart(
                title=widget.configuration["title"],
                presentation_kind=widget.configuration["presentationKind"],
                bindings=widget.configuration["bindings"],
            )
            chart = {
                "slice_name": widget.configuration["title"],
                "description": "Created by Catalyst local dashboard MVP.",
                "viz_type": viz_type,
                "params": params,
                "query_context": None,
                "cache_timeout": None,
                "uuid": chart_uuid,
                "version": "1.0.0",
                "dataset_uuid": dataset_uuid,
            }
            members[f"charts/{chart_uuid}.yaml"] = _json(chart).encode("utf-8")
            row_id = f"ROW-{index}"
            chart_id = f"CHART-{index}"
            chart_positions.append(row_id)
            position[row_id] = {
                "id": row_id,
                "type": "ROW",
                "parents": ["GRID_ID"],
                "children": [chart_id],
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
            }
            position[chart_id] = {
                "id": chart_id,
                "type": "CHART",
                "parents": [row_id],
                "children": [],
                # Superset discovers related assets from ``meta.uuid`` and
                # rewrites the temporary numeric chart ID during import.
                "meta": {
                    "chartId": index,
                    "uuid": chart_uuid,
                    "sliceName": widget.configuration["title"],
                    "width": 12,
                    "height": 50,
                },
            }
        dashboard_config = {
            "dashboard_title": dashboard.configuration["title"],
            "description": "Created by Catalyst local dashboard MVP.",
            "css": "",
            "slug": slug,
            "uuid": dashboard_uuid,
            "position": position,
            "metadata": {},
            "version": "1.0.0",
            "published": True,
        }
        members[f"dashboards/{dashboard_uuid}.yaml"] = _json(dashboard_config).encode(
            "utf-8"
        )
        members["metadata.yaml"] = _json(
            {"version": "1.0.0", "type": "Dashboard", "timestamp": dashboard.created_at}
        ).encode("utf-8")
        assets = [
            {
                "path": path,
                "sha256": hashlib.sha256(contents).hexdigest(),
                "bytes": len(contents),
            }
            for path, contents in sorted(members.items())
        ]
        manifest = {
            "schemaVersion": "catalyst.superset.bundle.v1",
            "bundleId": bundle_id,
            "bundleRoot": bundle_root,
            "targetSupersetVersion": "6.1.0",
            "dashboard": {
                "id": dashboard.logical_id,
                "versionId": dashboard.version_id,
                "configurationDigest": dashboard.configuration_digest,
                "author": {"actorKind": "human"},
                "createdAt": dashboard.created_at,
            },
            "dashboardSlug": slug,
            "widgets": [
                {
                    "id": item.logical_id,
                    "versionId": item.version_id,
                    "configurationDigest": item.configuration_digest,
                    "datasetVersionId": item.configuration["datasetVersionId"],
                    "presentationKind": item.configuration["presentationKind"],
                    "compatibilityDigest": canonical_sha256(
                        {"suggestedKind": item.configuration["suggestedKind"]}
                    ),
                    "vizMappingRevision": "catalyst.superset.viz.schema.v1",
                    "author": {"actorKind": "human"},
                    "createdAt": item.created_at,
                }
                for item in widgets
            ],
            "datasets": [
                {
                    "id": item.logical_id,
                    "versionId": item.version_id,
                    "configurationDigest": item.configuration_digest,
                    "source": item.configuration["source"],
                    "parameterizedSql": item.configuration["parameterizedSql"],
                    "parameterizedSqlDigest": canonical_sha256(
                        item.configuration["parameterizedSql"]
                    ),
                    "compiledSqlDigest": canonical_sha256(
                        item.configuration["compiledSql"]
                    ),
                    "typedParameters": item.configuration["parameters"],
                    "typedParametersDigest": canonical_sha256(
                        item.configuration["parameters"]
                    ),
                    "parameterCompilerRevision": "catalyst.postgresql-parameters.v1",
                    "resultSchema": item.configuration["columns"],
                    "resultBounds": item.configuration["resultBounds"],
                    "author": {"actorKind": "human"},
                    "createdAt": item.created_at,
                }
                for item in datasets.values()
            ],
            "assetUuids": {
                "database": database_uuid,
                "dashboard": dashboard_uuid,
                "datasetsByVersion": dataset_uuids,
                "chartsByVersion": chart_uuids,
            },
            "credentialPolicy": "local_demo_read_only",
            "dataSensitivityNotice": "demo_clinical_identifiers_may_be_present_in_sql_parameters",
            "containsResultRows": False,
            "manifestContainsCredentials": False,
            "generator": {
                "revision": "catalyst-dashboard-builder-mvp.v1",
                "parameterCompilerRevisions": ["catalyst.postgresql-parameters.v1"],
                "vizMappingRevisions": ["catalyst.superset.viz.schema.v1"],
            },
            "assetMembers": assets,
            "assetContentDigest": canonical_sha256(assets),
        }
        return members, manifest

    def publish(self, dashboard_version_id: str) -> dict[str, Any]:
        dashboard = self._entity("dashboard", dashboard_version_id)
        members, manifest = self._native_assets(dashboard)
        root = manifest["bundleRoot"]
        self.outbox.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=self.outbox, suffix=".zip", delete=False
        ) as stream:
            temporary = Path(stream.name)
            with zipfile.ZipFile(
                stream, mode="w", compression=zipfile.ZIP_DEFLATED
            ) as bundle:
                for path, contents in sorted(members.items()):
                    info = zipfile.ZipInfo(f"{root}/{path}")
                    info.date_time = (2026, 1, 1, 0, 0, 0)
                    info.external_attr = 0o100644 << 16
                    bundle.writestr(info, contents)
                info = zipfile.ZipInfo(f"{root}/catalyst/manifest.json")
                info.date_time = (2026, 1, 1, 0, 0, 0)
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, _json(manifest).encode("utf-8"))
        bundle_bytes = temporary.read_bytes()
        bundle_digest = hashlib.sha256(bundle_bytes).hexdigest()
        bundle_path = self.outbox / f"{bundle_digest}.zip"
        os.replace(temporary, bundle_path)
        pointer = {
            "schemaVersion": "catalyst.superset.outbox.current.v1",
            "publicationId": _uuid4(),
            "bundleId": manifest["bundleId"],
            "dashboard": {
                "id": dashboard.logical_id,
                "versionId": dashboard.version_id,
                "configurationDigest": dashboard.configuration_digest,
            },
            "targetSupersetVersion": "6.1.0",
            "bundle": {
                "fileName": bundle_path.name,
                "sha256": bundle_digest,
                "bytes": len(bundle_bytes),
            },
            "manifest": {
                "path": f"{root}/catalyst/manifest.json",
                "schemaVersion": manifest["schemaVersion"],
                "assetContentDigest": manifest["assetContentDigest"],
            },
            "publishedAt": _utc_now(),
        }
        temporary_pointer = self.outbox / ".current.json.tmp"
        temporary_pointer.write_text(_json(pointer), encoding="utf-8")
        os.replace(temporary_pointer, self.outbox / "current.json")
        publication = {
            "status": "bundle_ready",
            "dashboard": dashboard.as_dict(),
            "pointer": pointer,
            "manifest": manifest,
            "downloadPath": f"/v1/catalyst/dashboard-builder/dashboards/{dashboard.logical_id}/bundle",
        }
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO catalyst_dashboard_publications "
                "(dashboard_version_id, publication_json, created_at) VALUES (?, ?, ?)",
                (dashboard.version_id, _json(publication), _utc_now()),
            )
        return publication

    def publication(self, dashboard_version_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT publication_json FROM catalyst_dashboard_publications WHERE dashboard_version_id = ?",
            (dashboard_version_id,),
        ).fetchone()
        if row is None:
            return None
        publication = json.loads(row["publication_json"])
        if self.receipts is None:
            return publication

        bundle_digest = publication["pointer"]["bundle"]["sha256"]
        latest_path = self.receipts / "latest" / f"{bundle_digest}.json"
        if not latest_path.is_file():
            return publication

        try:
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            receipt = latest["latestReceipt"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return {
                **publication,
                "status": "import_failed",
                "importState": {
                    "outcome": "import_failed",
                    "errorCode": "import_receipt_invalid",
                    "recoveryAction": "rerun_import",
                },
            }

        if (
            latest.get("bundleDigest") != bundle_digest
            or receipt.get("outcome") != "imported"
        ):
            return {
                **publication,
                "status": "import_failed",
                "importState": {
                    "outcome": "import_failed",
                    "receiptId": receipt.get("receiptId"),
                    "receiptDigest": receipt.get("receiptDigest"),
                    "errorCode": receipt.get("errorCode") or "superset_import_failed",
                    "recoveryAction": receipt.get("recoveryAction") or "rerun_import",
                },
            }

        dashboard = publication["dashboard"]
        verified_path = self.receipts / "last-verified" / f"{dashboard['id']}.json"
        try:
            verified = json.loads(verified_path.read_text(encoding="utf-8"))
            verified_dashboard = verified["dashboard"]
            verified_receipt = verified["importReceipt"]
            dashboard_url = verified["supersetDashboard"]["url"]
            exact_match = (
                verified.get("bundleDigest") == bundle_digest
                and verified_dashboard.get("id") == dashboard["id"]
                and verified_dashboard.get("versionId") == dashboard["versionId"]
                and verified_dashboard.get("configurationDigest")
                == dashboard["configurationDigest"]
                and verified_receipt.get("receiptId") == receipt.get("receiptId")
                and verified_receipt.get("receiptDigest")
                == receipt.get("receiptDigest")
                and isinstance(dashboard_url, str)
                and bool(dashboard_url)
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            exact_match = False
            dashboard_url = None

        if not exact_match:
            return {
                **publication,
                "status": "import_failed",
                "importState": {
                    "outcome": "import_failed",
                    "receiptId": receipt.get("receiptId"),
                    "receiptDigest": receipt.get("receiptDigest"),
                    "errorCode": "last_verified_mismatch",
                    "recoveryAction": "rerun_import",
                },
            }

        return {
            **publication,
            "status": "imported",
            "importState": {
                "outcome": "imported",
                "receiptId": receipt["receiptId"],
                "receiptDigest": receipt["receiptDigest"],
                "dashboardUrl": dashboard_url,
            },
        }
