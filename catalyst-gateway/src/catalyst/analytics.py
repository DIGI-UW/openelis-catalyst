from __future__ import annotations

import asyncio
import base64
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID

import psycopg
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .catalog import DatasetBrowserProfile


class AnalyticsError(RuntimeError):
    """The governed analytics execution failed."""


@dataclass(frozen=True)
class DatabaseDiagnostic:
    """Safe, stable fields from a PostgreSQL error response."""

    sqlstate: str | None
    severity: str | None
    message: str
    detail: str | None = None
    hint: str | None = None
    position: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sqlstate": self.sqlstate,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail,
            "hint": self.hint,
            "position": self.position,
        }


class ManualAnalyticsError(AnalyticsError):
    """An exact workbench query failed with a serializable diagnostic."""

    def __init__(self, diagnostic: DatabaseDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic.message)

    def as_dict(self) -> dict[str, Any]:
        return self.diagnostic.as_dict()


@dataclass(frozen=True)
class AnalyticsResult:
    column_names: list[str]
    rows: list[Sequence[Any]]
    truncated: bool
    truncation_reason: str | None = None


@dataclass(frozen=True)
class AnalyticsColumn:
    """Database-derived metadata for an exact workbench result column."""

    ordinal: int
    name: str
    database_type: str
    type_oid: int | None
    logical_type: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "name": self.name,
            "databaseType": self.database_type,
            "typeOid": self.type_oid,
            "logicalType": self.logical_type,
        }


@dataclass(frozen=True)
class ManualAnalyticsResult:
    """JSON-safe dynamic output from an exact workbench query."""

    columns: list[AnalyticsColumn]
    rows: list[list[dict[str, Any]]]
    truncated: bool
    truncation_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        warnings = manual_result_warnings(
            self.columns,
            self.rows,
            truncated=self.truncated,
        )
        return {
            "columns": [column.as_dict() for column in self.columns],
            "rows": self.rows,
            "rowCount": {
                "returned": len(self.rows),
                "truncated": self.truncated,
                "truncationReason": self.truncation_reason,
            },
            "warnings": warnings,
        }


def manual_result_warnings(
    columns: Sequence[AnalyticsColumn | Mapping[str, Any]],
    rows: Sequence[Sequence[Mapping[str, Any]]],
    *,
    truncated: bool,
) -> list[str]:
    """Describe blank projected columns without changing execution success."""

    if not rows or not columns:
        return []

    blank_columns: list[str] = []
    for index, column in enumerate(columns):
        name = (
            column.name if isinstance(column, AnalyticsColumn) else column.get("name")
        )
        if not isinstance(name, str) or not name:
            continue
        if all(len(row) > index and _manual_cell_is_blank(row[index]) for row in rows):
            blank_columns.append(name)
    if not blank_columns:
        return []

    displayed_names = ", ".join(f"`{name}`" for name in blank_columns[:8])
    if len(blank_columns) > 8:
        displayed_names += f", and {len(blank_columns) - 8} more"
    row_label = "row" if len(rows) == 1 else "rows"
    scope = "displayed" if truncated else "returned"
    verb = "was" if len(blank_columns) == 1 else "were"
    warning = (
        f"{displayed_names} {verb} blank or NULL in all {len(rows)} {scope} {row_label}. "
        "Select a populated column or revise the SQL expression."
    )
    if truncated:
        warning += (
            " This check covers displayed rows only because results were truncated."
        )
    return [warning]


def _manual_cell_is_blank(cell: Mapping[str, Any]) -> bool:
    if cell.get("type") == "null":
        return True
    return cell.get("type") == "string" and not str(cell.get("value", "")).strip()


_POSTGRES_TYPE_NAMES: dict[int, str] = {
    16: "bool",
    17: "bytea",
    20: "int8",
    21: "int2",
    23: "int4",
    25: "text",
    26: "oid",
    114: "json",
    700: "float4",
    701: "float8",
    790: "money",
    869: "inet",
    1000: "bool[]",
    1005: "int2[]",
    1007: "int4[]",
    1009: "text[]",
    1015: "varchar[]",
    1016: "int8[]",
    1021: "float4[]",
    1022: "float8[]",
    1042: "bpchar",
    1043: "varchar",
    1082: "date",
    1083: "time",
    1114: "timestamp",
    1115: "timestamp[]",
    1182: "date[]",
    1184: "timestamptz",
    1185: "timestamptz[]",
    1186: "interval",
    1231: "numeric[]",
    1700: "numeric",
    2950: "uuid",
    2951: "uuid[]",
    3802: "jsonb",
    3807: "jsonb[]",
}

_LOGICAL_TYPES: dict[str, str] = {
    "bool": "boolean",
    "boolean": "boolean",
    "int2": "integer",
    "int4": "integer",
    "int8": "integer",
    "smallint": "integer",
    "integer": "integer",
    "bigint": "integer",
    "numeric": "decimal",
    "decimal": "decimal",
    "float4": "decimal",
    "float8": "decimal",
    "real": "decimal",
    "double precision": "decimal",
    "money": "decimal",
    "date": "date",
    "timestamp": "date-time",
    "timestamp without time zone": "date-time",
    "timestamptz": "date-time",
    "timestamp with time zone": "date-time",
    "time": "time",
    "timetz": "time",
    "interval": "interval",
    "json": "json",
    "jsonb": "json",
    "bytea": "binary",
    "text": "string",
    "varchar": "string",
    "character varying": "string",
    "bpchar": "string",
    "char": "string",
    "character": "string",
    "name": "string",
    "uuid": "string",
    "inet": "string",
    "cidr": "string",
    "macaddr": "string",
    "oid": "integer",
}

_POSTGRES_DSN_PATTERN = re.compile(r"(?i)\bpostgres(?:ql)?://[^\s\"']+")
_PASSWORD_PATTERN = re.compile(r"(?i)\b(password\s*=\s*)(?:'[^']*'|\"[^\"]*\"|[^\s;]+)")


class PostgresAnalyticsAdapter:
    def __init__(
        self,
        dsn: str,
        *,
        data_source_id: str | None = None,
        connect: Callable[..., Any] | None = None,
        connect_timeout_seconds: int = 5,
        dataset_browser: DatasetBrowserProfile | None = None,
    ) -> None:
        self.dsn = dsn
        self.data_source_id = data_source_id
        self._connect = connect or psycopg.connect
        self.connect_timeout_seconds = connect_timeout_seconds
        self.dataset_browser = dataset_browser

    def _require_dataset_browser(self) -> DatasetBrowserProfile:
        profile = self.dataset_browser
        if profile is None:
            raise AnalyticsError(
                "Data source "
                f"{self.data_source_id or self.dsn!r} declares no datasetBrowser "
                "profile in its analytics catalog, so its dataset cannot be "
                "browsed. Add one naming the fact view and its subject, "
                "category, value, and timestamp columns."
            )
        return profile

    async def execute(
        self,
        *,
        sql: str,
        parameters: list[dict[str, Any]],
        max_rows: int,
        statement_timeout_ms: int,
    ) -> AnalyticsResult:
        try:
            return await asyncio.to_thread(
                self._execute_sync,
                sql,
                parameters,
                max_rows,
                statement_timeout_ms,
            )
        except AnalyticsError:
            raise
        except Exception as error:
            raise AnalyticsError(f"PostgreSQL execution failed: {error}") from error

    async def execute_manual(
        self,
        *,
        sql: str,
        parameters: list[dict[str, Any]],
        max_rows: int,
        statement_timeout_ms: int,
    ) -> ManualAnalyticsResult:
        """Execute the submitted draft without policy gating or SQL rewriting."""

        try:
            return await asyncio.to_thread(
                self._execute_manual_sync,
                sql,
                parameters,
                max_rows,
                statement_timeout_ms,
            )
        except ManualAnalyticsError:
            raise
        except Exception as error:
            raise ManualAnalyticsError(
                self._database_diagnostic(error, self.dsn)
            ) from error

    async def readiness(self) -> dict[str, Any]:
        try:
            await asyncio.to_thread(self._check_ready_sync)
        except Exception as error:
            return {
                "ready": False,
                "dataSource": "postgresql",
                "message": str(error),
            }
        return {"ready": True, "dataSource": "postgresql"}

    async def freshness(self) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(self._freshness_sync)
        except AnalyticsError:
            raise
        except Exception as error:
            raise AnalyticsError(
                f"PostgreSQL freshness lookup failed: {error}"
            ) from error

    async def discover_relations(self) -> list[dict[str, Any]]:
        """Describe every non-system relation the configured role can SELECT."""

        try:
            return await asyncio.to_thread(self._discover_relations_sync)
        except AnalyticsError:
            raise
        except Exception as error:
            raise AnalyticsError(
                f"PostgreSQL relation discovery failed: {error}"
            ) from error

    async def dataset_overview(self) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(self._dataset_overview_sync)
        except AnalyticsError:
            raise
        except Exception as error:
            raise AnalyticsError(f"Dataset overview failed: {error}") from error

    async def dataset_rows(
        self,
        *,
        test_name: str | None,
        patient_id: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                self._dataset_rows_sync,
                test_name,
                patient_id,
                limit,
                offset,
            )
        except AnalyticsError:
            raise
        except Exception as error:
            raise AnalyticsError(f"Dataset row lookup failed: {error}") from error

    def _execute_sync(
        self,
        sql: str,
        parameters: list[dict[str, Any]],
        max_rows: int,
        statement_timeout_ms: int,
    ) -> AnalyticsResult:
        bindings = {
            parameter["name"]: self._binding_value(parameter)
            for parameter in parameters
        }
        driver_sql = self._driver_sql(sql, set(bindings))
        with self._connect(
            self.dsn,
            connect_timeout=self.connect_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{statement_timeout_ms}ms",),
                )
                # ``bindings`` is passed even when empty: _driver_sql doubled the
                # literal per-cent signs, and psycopg only collapses them back
                # when it has parameters to convert.
                cursor.execute(driver_sql, bindings)
                rows = list(cursor.fetchmany(max_rows + 1))
                description = cursor.description or ()
                column_names = [column.name for column in description]
        truncated = len(rows) > max_rows
        truncation_reason = "configured_limit" if truncated else None
        sql_limit = self._literal_sql_limit(sql)
        if not truncated and sql_limit is not None and len(rows) >= sql_limit:
            truncated = True
            truncation_reason = "query_limit_reached"
        return AnalyticsResult(
            column_names=column_names,
            rows=rows[:max_rows],
            truncated=truncated,
            truncation_reason=truncation_reason,
        )

    def _execute_manual_sync(
        self,
        sql: str,
        parameters: list[dict[str, Any]],
        max_rows: int,
        statement_timeout_ms: int,
    ) -> ManualAnalyticsResult:
        bindings = {
            parameter["name"]: self._binding_value(parameter)
            for parameter in parameters
        }
        # psycopg uses ``%(name)s`` for named bindings, and reads any other
        # per-cent sign as a malformed placeholder. Rewriting the named bindings
        # and doubling the literal per-cent signs are the only changes made to
        # the submitted SQL; no row limit is added.
        driver_sql = self._driver_sql(sql, set(bindings))
        with self._connect(
            self.dsn,
            connect_timeout=self.connect_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{statement_timeout_ms}ms",),
                )
                # ``bindings`` is passed even when empty: _driver_sql doubled the
                # literal per-cent signs, and psycopg only collapses them back
                # when it has parameters to convert.
                cursor.execute(driver_sql, bindings)
                raw_rows = list(cursor.fetchmany(max_rows + 1))
                description = tuple(cursor.description or ())
                columns = self._manual_columns(description, raw_rows, cursor)

        truncated = len(raw_rows) > max_rows
        truncation_reason = "configured_limit" if truncated else None
        bounded_rows = raw_rows[:max_rows]
        rows = [
            self._serialize_manual_row(row, columns, row_index)
            for row_index, row in enumerate(bounded_rows)
        ]
        return ManualAnalyticsResult(
            columns=columns,
            rows=rows,
            truncated=truncated,
            truncation_reason=truncation_reason,
        )

    @staticmethod
    def _literal_sql_limit(sql: str) -> int | None:
        try:
            statement = sqlglot.parse_one(sql, read="postgres")
        except ParseError:
            return None
        limit = statement.args.get("limit")
        if not isinstance(limit, exp.Limit):
            return None
        value = limit.args.get("expression")
        if not isinstance(value, exp.Literal) or value.is_string:
            return None
        try:
            return int(value.this)
        except ValueError:
            return None

    def _check_ready_sync(self) -> None:
        with self._connect(
            self.dsn,
            connect_timeout=self.connect_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("SELECT 1")
                cursor.fetchmany(1)

    def _freshness_sync(self) -> dict[str, Any]:
        with self._connect(
            self.dsn,
            connect_timeout=self.connect_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    """
                    SELECT
                        pipeline_run_id,
                        completion_state,
                        source_watermark,
                        observed_lag_seconds
                    FROM analytics.pipeline_freshness_v1
                    WHERE completion_state = 'succeeded'
                    ORDER BY completed_at DESC NULLS LAST
                    LIMIT 1
                    """
                )
                row = cursor.fetchone()
        if row is None:
            raise AnalyticsError("No succeeded analytics pipeline run is available.")
        pipeline_run_id, completion_state, source_watermark, observed_lag_seconds = row
        if completion_state != "succeeded" or not isinstance(
            source_watermark, datetime
        ):
            raise AnalyticsError("Latest analytics pipeline freshness is invalid.")
        if source_watermark.tzinfo is None:
            raise AnalyticsError("Analytics source watermark has no timezone.")
        return {
            "sourceWatermark": source_watermark.isoformat().replace("+00:00", "Z"),
            "pipelineRunId": str(pipeline_run_id),
            "completionState": "complete",
            "observedLagSeconds": max(0, int(observed_lag_seconds)),
        }

    def _discover_relations_sync(self) -> list[dict[str, Any]]:
        with self._connect(
            self.dsn,
            connect_timeout=self.connect_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    """
                    SELECT
                        namespace.nspname,
                        relation.relname,
                        relation.relkind,
                        pg_catalog.pg_table_is_visible(relation.oid),
                        attribute.attnum,
                        attribute.attname,
                        pg_catalog.format_type(attribute.atttypid, NULL),
                        NOT attribute.attnotnull,
                        pg_catalog.obj_description(relation.oid, 'pg_class'),
                        pg_catalog.col_description(relation.oid, attribute.attnum)
                    FROM pg_catalog.pg_class AS relation
                    JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    JOIN pg_catalog.pg_attribute AS attribute
                      ON attribute.attrelid = relation.oid
                    WHERE relation.relkind IN ('r', 'p', 'v', 'm', 'f')
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                      AND namespace.nspname NOT IN ('pg_catalog', 'information_schema')
                      AND namespace.nspname !~ '^pg_(toast|temp)'
                      AND pg_catalog.has_schema_privilege(namespace.oid, 'USAGE')
                      AND pg_catalog.has_table_privilege(relation.oid, 'SELECT')
                    ORDER BY namespace.nspname, relation.relname, attribute.attnum
                    """
                )
                rows = cursor.fetchall()

        relation_types = {
            "r": "table",
            "p": "partitioned-table",
            "v": "view",
            "m": "materialized-view",
            "f": "foreign-table",
        }
        by_name: dict[str, dict[str, Any]] = {}
        for (
            schema_name,
            relation_name,
            relation_kind,
            unqualified_visible,
            _ordinal,
            column_name,
            database_type,
            nullable,
            relation_description,
            column_description,
        ) in rows:
            qualified_name = f"{schema_name}.{relation_name}"
            relation_type = relation_types.get(str(relation_kind), "relation")
            relation = by_name.setdefault(
                qualified_name,
                {
                    "name": qualified_name,
                    "relationType": relation_type,
                    "unqualifiedVisible": bool(unqualified_visible),
                    "grain": (
                        str(relation_description)
                        if relation_description
                        else f"Rows readable from {qualified_name} ({relation_type})"
                    ),
                    "fields": [],
                },
            )
            rendered_database_type = str(database_type)
            logical_type = self._logical_type(rendered_database_type)
            if logical_type == "unknown":
                logical_type = "string"
            relation["fields"].append(
                {
                    "name": str(column_name),
                    "type": logical_type,
                    "databaseType": rendered_database_type,
                    "description": (
                        str(column_description)
                        if column_description
                        else (
                            f"{qualified_name}.{column_name} "
                            f"(PostgreSQL {rendered_database_type})"
                        )
                    ),
                    "nullable": bool(nullable),
                }
            )
        return list(by_name.values())

    def _dataset_overview_sync(self) -> dict[str, Any]:
        profile = self._require_dataset_browser()
        with self._connect(
            self.dsn,
            connect_timeout=self.connect_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    f"""
                    SELECT
                        count(DISTINCT {profile.subject_column}),
                        count(*),
                        count(DISTINCT {profile.category_column}),
                        min({profile.observed_at_column}),
                        max({profile.observed_at_column}),
                        (
                            SELECT pipeline_run_id
                            FROM analytics.pipeline_freshness_v1
                            WHERE completion_state = 'succeeded'
                            ORDER BY completed_at DESC NULLS LAST
                            LIMIT 1
                        )
                    FROM {profile.fact_view}
                    """
                )
                row = cursor.fetchone()
                # Single-row aggregate query (COUNT/MIN/MAX with no GROUP BY):
                # always returns exactly one row, even over an empty table.
                assert row is not None
                (
                    patients,
                    results,
                    test_types,
                    first_at,
                    last_at,
                    pipeline_run_id,
                ) = row
                unit = profile.unit_column
                value = profile.value_column
                # A source without a unit or a numeric value column still gets
                # per-category counts; only the columns it cannot supply are
                # reported as NULL.
                unit_select = unit if unit else "NULL"
                unit_group = f", {unit}" if unit else ""
                value_selects = (
                    f"""min({value}),
                        percentile_cont(0.5) WITHIN GROUP (ORDER BY {value}),
                        max({value})"""
                    if value
                    else "NULL, NULL, NULL"
                )
                cursor.execute(
                    f"""
                    SELECT
                        {profile.category_column},
                        {unit_select},
                        count(*),
                        count(DISTINCT {profile.subject_column}),
                        {value_selects}
                    FROM {profile.fact_view}
                    GROUP BY {profile.category_column}{unit_group}
                    ORDER BY count(*) DESC, {profile.category_column}
                    """
                )
                tests = cursor.fetchall()
        return {
            "contractVersion": "catalyst.dataset-overview.v1",
            "datasetId": (
                str(pipeline_run_id)
                if pipeline_run_id is not None
                else self.data_source_id
            ),
            "dataSource": self.data_source_id,
            "pipelineRunId": (
                str(pipeline_run_id) if pipeline_run_id is not None else None
            ),
            # The analytics pipeline does not currently carry a reviewed data
            # classification. Do not infer that a live OpenELIS load is synthetic.
            "synthetic": None,
            "patients": int(patients),
            "results": int(results),
            "testTypes": int(test_types),
            "firstObservedAt": self._iso(first_at),
            "lastObservedAt": self._iso(last_at),
            "tests": [
                {
                    "testName": str(test_name),
                    "unit": str(unit) if unit is not None else None,
                    "results": int(count),
                    "patients": int(patient_count),
                    "minimum": self._number_text(minimum),
                    "median": self._number_text(median),
                    "maximum": self._number_text(maximum),
                }
                for (
                    test_name,
                    unit,
                    count,
                    patient_count,
                    minimum,
                    median,
                    maximum,
                ) in tests
            ],
            "exampleQuestions": [],
        }

    def _dataset_rows_sync(
        self,
        test_name: str | None,
        patient_id: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        profile = self._require_dataset_browser()
        conditions: list[str] = []
        bindings: dict[str, Any] = {"limit": limit, "offset": offset}
        if test_name:
            conditions.append(f"{profile.category_column} = %(test_name)s")
            bindings["test_name"] = test_name
        if patient_id:
            conditions.append(f"{profile.subject_column} = %(patient_id)s")
            bindings["patient_id"] = patient_id
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        # Sources whose value is coded/text/boolean rather than numeric fall
        # back in declared order so a row shows what it actually carries
        # instead of an empty cell.
        value_candidates = [
            column
            for column in (profile.value_column, *profile.value_fallback_columns)
            if column
        ]
        if not value_candidates:
            value_select = "NULL"
        elif len(value_candidates) == 1:
            value_select = value_candidates[0]
        else:
            casts = ", ".join(f"{column}::text" for column in value_candidates)
            value_select = f"COALESCE({casts})"
        selects = ", ".join(
            (
                profile.identity_column,
                profile.subject_column,
                profile.category_column,
                value_select,
                profile.unit_column or "NULL",
                profile.observed_at_column,
                profile.issued_at_column or "NULL",
                profile.duration_column or "NULL",
            )
        )
        with self._connect(
            self.dsn,
            connect_timeout=self.connect_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    f"SELECT count(*) FROM {profile.fact_view}" + where,
                    {
                        key: value
                        for key, value in bindings.items()
                        if key not in {"limit", "offset"}
                    },
                )
                row = cursor.fetchone()
                # Single-row aggregate query (COUNT with no GROUP BY): always
                # returns exactly one row, even over an empty table.
                assert row is not None
                total = int(row[0])
                cursor.execute(
                    f"SELECT {selects}"
                    f" FROM {profile.fact_view}"
                    + where
                    + f" ORDER BY {profile.observed_at_column} DESC NULLS LAST,"
                    f" {profile.identity_column}"
                    " LIMIT %(limit)s OFFSET %(offset)s",
                    bindings,
                )
                rows = cursor.fetchall()
        return {
            "contractVersion": "catalyst.dataset-rows.v1",
            "total": total,
            "limit": limit,
            "offset": offset,
            "rows": [
                {
                    "observationId": str(row[0]),
                    "patientId": str(row[1]),
                    "testName": str(row[2]),
                    "value": self._number_text(row[3]),
                    "unit": str(row[4]) if row[4] is not None else None,
                    "observedAt": self._iso(row[5]),
                    "issuedAt": self._iso(row[6]),
                    "turnaroundMinutes": self._number_text(row[7]),
                }
                for row in rows
            ],
        }

    @staticmethod
    def _number_text(value: Any) -> str | None:
        if value is None:
            return None
        return format(value, "f") if isinstance(value, Decimal) else str(value)

    @staticmethod
    def _iso(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat().replace("+00:00", "Z")
        return str(value)

    @classmethod
    def _manual_columns(
        cls,
        description: Sequence[Any],
        rows: Sequence[Sequence[Any]],
        cursor: Any,
    ) -> list[AnalyticsColumn]:
        columns: list[AnalyticsColumn] = []
        for ordinal, description_column in enumerate(description):
            type_oid, database_type = cls._database_type(
                getattr(description_column, "type_code", None), cursor
            )
            logical_type = cls._logical_type(database_type)
            if logical_type == "unknown":
                sample = next(
                    (
                        row[ordinal]
                        for row in rows
                        if len(row) > ordinal and row[ordinal] is not None
                    ),
                    None,
                )
                logical_type = cls._value_logical_type(sample)
            columns.append(
                AnalyticsColumn(
                    ordinal=ordinal,
                    name=str(description_column.name),
                    database_type=database_type,
                    type_oid=type_oid,
                    logical_type=logical_type,
                )
            )
        return columns

    @staticmethod
    def _database_type(type_code: Any, cursor: Any) -> tuple[int | None, str]:
        type_oid: int | None = None
        if isinstance(type_code, int) and not isinstance(type_code, bool):
            type_oid = type_code
        else:
            candidate_oid = getattr(type_code, "oid", None)
            if isinstance(candidate_oid, int) and not isinstance(candidate_oid, bool):
                type_oid = candidate_oid

        if type_oid in _POSTGRES_TYPE_NAMES:
            return type_oid, _POSTGRES_TYPE_NAMES[type_oid]

        type_name = getattr(type_code, "name", None)
        if not type_name and isinstance(type_code, str):
            type_name = type_code
        if not type_name and type_oid is not None:
            adapters = getattr(cursor, "adapters", None)
            registry = getattr(adapters, "types", None)
            try:
                type_info = registry.get(type_oid) if registry is not None else None
            except (KeyError, TypeError):
                type_info = None
            type_name = getattr(type_info, "name", None)

        if type_name:
            return type_oid, str(type_name).strip().lower()
        if type_oid is not None:
            return type_oid, f"oid:{type_oid}"
        return None, "unknown"

    @staticmethod
    def _logical_type(database_type: str) -> str:
        normalized = database_type.strip().lower()
        if normalized.endswith("[]"):
            return "array"
        return _LOGICAL_TYPES.get(normalized, "unknown")

    @staticmethod
    def _value_logical_type(value: Any) -> str:
        if value is None:
            return "unknown"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, (Decimal, float)):
            return "decimal"
        if isinstance(value, datetime):
            return "date-time"
        if isinstance(value, date):
            return "date"
        if isinstance(value, time):
            return "time"
        if isinstance(value, Mapping):
            return "json"
        if isinstance(value, (list, tuple)):
            return "array"
        if isinstance(value, (bytes, bytearray, memoryview)):
            return "binary"
        return "string"

    @classmethod
    def _serialize_manual_row(
        cls,
        row: Sequence[Any],
        columns: Sequence[AnalyticsColumn],
        row_index: int,
    ) -> list[dict[str, Any]]:
        if len(row) != len(columns):
            raise AnalyticsError(
                f"PostgreSQL row {row_index} has {len(row)} cells; "
                f"expected {len(columns)}."
            )
        return [
            cls._serialize_manual_cell(value, column.logical_type)
            for value, column in zip(row, columns, strict=True)
        ]

    @classmethod
    def _serialize_manual_cell(cls, value: Any, logical_type: str) -> dict[str, Any]:
        if value is None:
            return {"type": "null"}
        if logical_type == "boolean" and isinstance(value, bool):
            return {"type": "boolean", "value": value}
        if (
            logical_type == "integer"
            and isinstance(value, int)
            and not isinstance(value, bool)
        ):
            return {"type": "integer", "value": value}
        if logical_type == "decimal" and isinstance(value, (Decimal, int, float)):
            return {"type": "decimal", "value": cls._decimal_text(value)}
        if logical_type == "date-time" and isinstance(value, datetime):
            if value.tzinfo is not None:
                rendered = (
                    value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                )
            else:
                rendered = value.isoformat()
            return {"type": "date-time", "value": rendered}
        if (
            logical_type == "date"
            and isinstance(value, date)
            and not isinstance(value, datetime)
        ):
            return {"type": "date", "value": value.isoformat()}
        if logical_type == "time" and isinstance(value, time):
            return {"type": "time", "value": value.isoformat()}
        if logical_type == "json":
            return {"type": "json", "value": cls._json_safe_value(value)}
        if logical_type == "array" and isinstance(value, (list, tuple)):
            return {
                "type": "array",
                "value": [cls._json_safe_value(item) for item in value],
            }
        if logical_type == "binary" and isinstance(
            value, (bytes, bytearray, memoryview)
        ):
            encoded = base64.b64encode(bytes(value)).decode("ascii")
            return {"type": "binary", "value": encoded}
        if logical_type == "interval":
            return {"type": "interval", "value": str(value)}
        if logical_type == "string":
            return {"type": "string", "value": str(value)}

        inferred = cls._value_logical_type(value)
        if inferred != "unknown" and inferred != logical_type:
            return cls._serialize_manual_cell(value, inferred)
        return {"type": "string", "value": str(value)}

    @classmethod
    def _json_safe_value(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, float):
            return cls._decimal_text(value)
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            return value.isoformat()
        if isinstance(value, (date, time)):
            return value.isoformat()
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return base64.b64encode(bytes(value)).decode("ascii")
        if isinstance(value, Mapping):
            return {
                str(key): cls._json_safe_value(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(value, (list, tuple)):
            return [cls._json_safe_value(item) for item in value]
        return str(value)

    @staticmethod
    def _decimal_text(value: Decimal | int | float) -> str:
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, float):
            if math.isnan(value):
                return "NaN"
            if math.isinf(value):
                return "Infinity" if value > 0 else "-Infinity"
            return repr(value)
        return str(value)

    @classmethod
    def _database_diagnostic(cls, error: Exception, dsn: str) -> DatabaseDiagnostic:
        diagnostic = getattr(error, "diag", None)
        sqlstate = getattr(error, "sqlstate", None)
        if sqlstate is None and diagnostic is not None:
            sqlstate = getattr(diagnostic, "sqlstate", None)
        severity = None
        if diagnostic is not None:
            severity = getattr(diagnostic, "severity_nonlocalized", None) or getattr(
                diagnostic, "severity", None
            )
        primary = (
            getattr(diagnostic, "message_primary", None)
            if diagnostic is not None
            else None
        )
        if not primary:
            primary = next(
                (line.strip() for line in str(error).splitlines() if line.strip()),
                "PostgreSQL execution failed.",
            )
        detail = (
            getattr(diagnostic, "message_detail", None)
            if diagnostic is not None
            else None
        )
        hint = (
            getattr(diagnostic, "message_hint", None)
            if diagnostic is not None
            else None
        )
        raw_position = (
            getattr(diagnostic, "statement_position", None)
            if diagnostic is not None
            else None
        )
        try:
            position = int(raw_position) if raw_position is not None else None
        except (TypeError, ValueError):
            position = None
        return DatabaseDiagnostic(
            sqlstate=str(sqlstate) if sqlstate else None,
            severity=(
                cls._sanitize_diagnostic_text(str(severity), dsn) if severity else None
            ),
            message=cls._sanitize_diagnostic_text(str(primary), dsn),
            detail=(
                cls._sanitize_diagnostic_text(str(detail), dsn) if detail else None
            ),
            hint=cls._sanitize_diagnostic_text(str(hint), dsn) if hint else None,
            position=position,
        )

    @staticmethod
    def _sanitize_diagnostic_text(value: str, dsn: str) -> str:
        sanitized = value.replace(dsn, "[redacted-postgresql-dsn]") if dsn else value
        sanitized = _POSTGRES_DSN_PATTERN.sub("[redacted-postgresql-dsn]", sanitized)
        return _PASSWORD_PATTERN.sub(r"\1[redacted]", sanitized)

    @staticmethod
    def _binding_value(parameter: dict[str, Any]) -> Any:
        value = parameter["value"]
        parameter_type = parameter["type"]
        if parameter_type == "date":
            return date.fromisoformat(value)
        if parameter_type == "date-time":
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parameter_type == "integer":
            if isinstance(value, bool):
                raise ValueError("Boolean values cannot be bound as integers.")
            return int(value)
        if parameter_type == "number":
            return Decimal(str(value))
        if parameter_type == "integer-list":
            if any(isinstance(item, bool) for item in value):
                raise ValueError("Boolean values cannot be bound as integers.")
            return [int(item) for item in value]
        if parameter_type == "string-list":
            return [str(item) for item in value]
        return value

    @staticmethod
    def _driver_sql(sql: str, parameter_names: set[str]) -> str:
        output: list[str] = []

        def emit(text: str) -> None:
            """Append text that came from the submitted SQL, per-cent signs doubled.

            psycopg finds placeholders by scanning the whole statement with a
            regular expression, without any knowledge of SQL quoting. A literal
            per-cent sign therefore reads as the start of a placeholder wherever
            it appears -- inside a string literal, a dollar-quoted body, or even
            a comment -- and ``TO_CHAR(x, '990D9%')`` is rejected outright. Every
            per-cent sign that came from the caller is doubled here; psycopg
            collapses ``%%`` back to a single ``%`` when it converts the query.

            It only does that collapsing when parameters are supplied, so the
            callers below must always pass their bindings mapping, even when it
            is empty. Substituting ``bindings or None`` would send ``%%`` through
            to the database unchanged.
            """
            output.append(text.replace("%", "%%"))

        index = 0
        quote: str | None = None
        dollar_quote: str | None = None
        line_comment = False
        block_comment = False
        while index < len(sql):
            char = sql[index]
            following = sql[index + 1] if index + 1 < len(sql) else ""

            if line_comment:
                emit(char)
                index += 1
                if char == "\n":
                    line_comment = False
                continue
            if block_comment:
                emit(char)
                index += 1
                if char == "*" and following == "/":
                    emit(following)
                    index += 1
                    block_comment = False
                continue
            if quote:
                emit(char)
                index += 1
                if char == quote:
                    if following == quote:
                        emit(following)
                        index += 1
                    else:
                        quote = None
                continue
            if dollar_quote:
                if sql.startswith(dollar_quote, index):
                    emit(dollar_quote)
                    index += len(dollar_quote)
                    dollar_quote = None
                else:
                    emit(char)
                    index += 1
                continue
            if char == "-" and following == "-":
                emit(char + following)
                index += 2
                line_comment = True
                continue
            if char == "/" and following == "*":
                emit(char + following)
                index += 2
                block_comment = True
                continue
            if char in {"'", '"'}:
                quote = char
                emit(char)
                index += 1
                continue
            if char == "$":
                delimiter_end = sql.find("$", index + 1)
                if delimiter_end != -1:
                    tag = sql[index + 1 : delimiter_end]
                    if not tag or (
                        (tag[0].isalpha() or tag[0] == "_")
                        and all(part.isalnum() or part == "_" for part in tag)
                    ):
                        dollar_quote = sql[index : delimiter_end + 1]
                        emit(dollar_quote)
                        index = delimiter_end + 1
                        continue
            if (
                char == ":"
                and following != ":"
                and (following.isalpha() or following == "_")
            ):
                end = index + 2
                while end < len(sql) and (sql[end].isalnum() or sql[end] == "_"):
                    end += 1
                name = sql[index + 1 : end]
                if name in parameter_names:
                    # Appended rather than emitted: this is the one per-cent sign
                    # in the output that psycopg is meant to read as a placeholder.
                    output.append(f"%({name})s")
                    index = end
                    continue
            emit(char)
            index += 1
        return "".join(output)
