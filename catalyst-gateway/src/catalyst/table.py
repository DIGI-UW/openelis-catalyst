from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from .analytics import AnalyticsResult


class TableError(ValueError):
    """Analytics rows cannot satisfy the normative table contract."""


def build_table(
    *,
    preview: dict[str, Any],
    query: dict[str, Any],
    result: AnalyticsResult,
    freshness: dict[str, Any],
    accepted_at: str,
    duration_ms: int,
    statement_timeout_ms: int,
    max_rows: int,
    catalyst_trace_id: str,
) -> dict[str, Any]:
    columns = query["expectedColumns"]
    expected_names = [column["name"] for column in columns]
    if len(expected_names) != len(set(expected_names)):
        raise TableError("Expected column names must be unique.")
    if result.column_names != expected_names:
        raise TableError(
            "Analytics result columns do not exactly match expected query columns."
        )

    tagged_rows = [
        _tag_row(row, columns, row_index) for row_index, row in enumerate(result.rows)
    ]
    returned = len(tagged_rows)
    total = None if result.truncated else returned
    warnings: list[str] = []
    if result.truncated:
        warnings.append(
            f"Result was truncated to the configured limit of {max_rows} rows."
        )
    if freshness.get("completionState") == "partial":
        warnings.append("Analytics source freshness is partial.")

    return {
        "contractVersion": "catalyst.table.v1",
        "deploymentMode": "demo",
        "question": preview["question"],
        "preview": {
            "previewId": preview["previewId"],
            "queryDigest": preview["queryDigest"],
            "acceptedAt": accepted_at,
        },
        "query": {
            "sql": preview["sql"],
            "parameters": deepcopy(preview["parameters"]),
        },
        "table": {
            "columns": deepcopy(columns),
            "rows": tagged_rows,
            "rowCount": {
                "returned": returned,
                "total": total,
                "totalIsExact": not result.truncated,
                "truncated": result.truncated,
                "limit": max_rows,
            },
        },
        "source": {
            "dataSource": preview["target"]["dataSource"],
            "catalogVersion": preview["target"]["catalogVersion"],
            "views": deepcopy(preview["target"]["approvedViews"]),
            "freshness": deepcopy(freshness),
        },
        "execution": {
            "status": "succeeded",
            "durationMs": max(0, duration_ms),
            "statementTimeoutMs": statement_timeout_ms,
        },
        "provenance": {
            "catalystTraceId": catalyst_trace_id,
            "hubTraceId": query["provenance"]["traceId"],
            "profileId": "catalyst-query-checked",
        },
        "warnings": warnings,
    }


def _tag_row(
    row: Any,
    columns: list[dict[str, Any]],
    row_index: int,
) -> list[dict[str, Any]]:
    if not isinstance(row, (list, tuple)) or len(row) != len(columns):
        raise TableError(
            f"Row {row_index} has {len(row) if hasattr(row, '__len__') else 'unknown'} "
            f"cells; expected {len(columns)}."
        )
    return [
        _tag_cell(value, column, row_index)
        for value, column in zip(row, columns, strict=True)
    ]


def _tag_cell(
    value: Any,
    column: dict[str, Any],
    row_index: int,
) -> dict[str, Any]:
    if value is None:
        if not column["nullable"]:
            raise TableError(
                f"Row {row_index} column {column['name']} is unexpectedly null."
            )
        return {"type": "null"}

    logical_type = column["logicalType"]
    if logical_type == "string" and isinstance(value, str):
        return {"type": "string", "value": value}
    if (
        logical_type == "integer"
        and isinstance(value, int)
        and not isinstance(value, bool)
    ):
        return {"type": "integer", "value": value}
    if (
        logical_type == "decimal"
        and isinstance(value, (Decimal, int, float))
        and not isinstance(value, bool)
    ):
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        if decimal_value.is_finite():
            return {"type": "decimal", "value": format(decimal_value, "f")}
    if logical_type == "boolean" and isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if (
        logical_type == "date"
        and isinstance(value, date)
        and not isinstance(value, datetime)
    ):
        return {"type": "date", "value": value.isoformat()}
    if logical_type == "date-time" and isinstance(value, datetime):
        if value.tzinfo is None:
            raise TableError(
                f"Row {row_index} column {column['name']} has a naive timestamp."
            )
        timestamp = value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return {"type": "date-time", "value": timestamp}
    raise TableError(
        f"Row {row_index} column {column['name']} does not match {logical_type}."
    )
