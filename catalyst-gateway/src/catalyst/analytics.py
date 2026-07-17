from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Sequence

import psycopg
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


class AnalyticsError(RuntimeError):
    """The governed analytics execution failed."""


@dataclass(frozen=True)
class AnalyticsResult:
    column_names: list[str]
    rows: list[Sequence[Any]]
    truncated: bool
    truncation_reason: str | None = None


class PostgresAnalyticsAdapter:
    def __init__(
        self,
        dsn: str,
        *,
        connect: Callable[..., Any] | None = None,
        connect_timeout_seconds: int = 5,
    ) -> None:
        self.dsn = dsn
        self._connect = connect or psycopg.connect
        self.connect_timeout_seconds = connect_timeout_seconds

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

    def _dataset_overview_sync(self) -> dict[str, Any]:
        with self._connect(
            self.dsn,
            connect_timeout=self.connect_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    """
                    SELECT
                        count(DISTINCT patient_id),
                        count(*),
                        count(DISTINCT test_name),
                        min(observed_at),
                        max(observed_at)
                    FROM analytics.lab_result_fact_v1
                    """
                )
                patients, results, test_types, first_at, last_at = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT
                        test_name,
                        result_unit,
                        count(*),
                        count(DISTINCT patient_id),
                        min(result_value),
                        percentile_cont(0.5) WITHIN GROUP (ORDER BY result_value),
                        max(result_value)
                    FROM analytics.lab_result_fact_v1
                    GROUP BY test_name, result_unit
                    ORDER BY count(*) DESC, test_name
                    """
                )
                tests = cursor.fetchall()
        return {
            "contractVersion": "catalyst.dataset-overview.v1",
            "datasetId": "catalyst-openelis-cohort-v1",
            "synthetic": True,
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
            "exampleQuestions": [
                "Show viral load results since 2026-01-01 with patient, value, unit, and date",
                "Count suppressed and unsuppressed viral load results since 2025-07-01",
                "Show the latest viral load result for each patient since 2025-07-01",
                "Compare median CD4 absolute count by month since 2026-01-01",
                "Show viral load results with receipt-to-release time over 24 hours since 2025-07-01",
            ],
        }

    def _dataset_rows_sync(
        self,
        test_name: str | None,
        patient_id: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        conditions: list[str] = []
        bindings: dict[str, Any] = {"limit": limit, "offset": offset}
        if test_name:
            conditions.append("test_name = %(test_name)s")
            bindings["test_name"] = test_name
        if patient_id:
            conditions.append("patient_id = %(patient_id)s")
            bindings["patient_id"] = patient_id
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connect(
            self.dsn,
            connect_timeout=self.connect_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    "SELECT count(*) FROM analytics.lab_result_fact_v1" + where,
                    {
                        key: value
                        for key, value in bindings.items()
                        if key not in {"limit", "offset"}
                    },
                )
                total = int(cursor.fetchone()[0])
                cursor.execute(
                    """
                    SELECT patient_id, test_name, result_value, result_unit,
                           observed_at, issued_at, receipt_to_release_minutes
                    FROM analytics.lab_result_fact_v1
                    """
                    + where
                    + " ORDER BY observed_at DESC, patient_id, test_name LIMIT %(limit)s OFFSET %(offset)s",
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
                    "patientId": str(row[0]),
                    "testName": str(row[1]),
                    "value": self._number_text(row[2]),
                    "unit": str(row[3]) if row[3] is not None else None,
                    "observedAt": self._iso(row[4]),
                    "issuedAt": self._iso(row[5]),
                    "turnaroundMinutes": self._number_text(row[6]),
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

    @staticmethod
    def _binding_value(parameter: dict[str, Any]) -> Any:
        value = parameter["value"]
        parameter_type = parameter["type"]
        if parameter_type == "date":
            return date.fromisoformat(value)
        if parameter_type == "date-time":
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parameter_type == "number":
            return Decimal(str(value))
        return value

    @staticmethod
    def _driver_sql(sql: str, parameter_names: set[str]) -> str:
        output: list[str] = []
        index = 0
        quote: str | None = None
        line_comment = False
        block_comment = False
        while index < len(sql):
            char = sql[index]
            following = sql[index + 1] if index + 1 < len(sql) else ""

            if line_comment:
                output.append(char)
                index += 1
                if char == "\n":
                    line_comment = False
                continue
            if block_comment:
                output.append(char)
                index += 1
                if char == "*" and following == "/":
                    output.append(following)
                    index += 1
                    block_comment = False
                continue
            if quote:
                output.append(char)
                index += 1
                if char == quote:
                    if following == quote:
                        output.append(following)
                        index += 1
                    else:
                        quote = None
                continue
            if char == "-" and following == "-":
                output.extend((char, following))
                index += 2
                line_comment = True
                continue
            if char == "/" and following == "*":
                output.extend((char, following))
                index += 2
                block_comment = True
                continue
            if char in {"'", '"'}:
                quote = char
                output.append(char)
                index += 1
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
                    output.append(f"%({name})s")
                    index = end
                    continue
            output.append(char)
            index += 1
        return "".join(output)
