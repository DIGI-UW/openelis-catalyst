from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Sequence

import psycopg


class AnalyticsError(RuntimeError):
    """The governed analytics execution failed."""


@dataclass(frozen=True)
class AnalyticsResult:
    column_names: list[str]
    rows: list[Sequence[Any]]
    truncated: bool


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
        return AnalyticsResult(
            column_names=column_names,
            rows=rows[:max_rows],
            truncated=truncated,
        )

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
