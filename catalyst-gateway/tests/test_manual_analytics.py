from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import psycopg
import pytest

from src.catalyst.analytics import ManualAnalyticsError, PostgresAnalyticsAdapter


class _Connection:
    def __init__(self, cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def cursor(self):
        return self._cursor


@pytest.mark.asyncio
async def test_manual_execution_preserves_sql_and_returns_dynamic_typed_rows():
    calls: list[tuple[str, object]] = []

    class Cursor:
        description = [
            SimpleNamespace(name="result_count", type_code=23),
            SimpleNamespace(name="ratio", type_code=1700),
            SimpleNamespace(name="active", type_code=16),
            SimpleNamespace(name="observed_on", type_code=1082),
            SimpleNamespace(name="issued_at", type_code=1184),
            SimpleNamespace(name="result_id", type_code=2950),
            SimpleNamespace(name="evidence", type_code=3802),
            SimpleNamespace(name="payload", type_code=17),
        ]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchmany(self, count):
            assert count == 4
            return [
                (
                    3,
                    Decimal("1.250"),
                    True,
                    date(2026, 7, 17),
                    datetime(2026, 7, 17, 19, 30, tzinfo=timezone.utc),
                    UUID("12345678-1234-5678-1234-567812345678"),
                    {"measurements": [Decimal("2.50"), None]},
                    b"\x00\xff",
                )
            ]

    adapter = PostgresAnalyticsAdapter(
        "postgresql://demo",
        connect=lambda *args, **kwargs: _Connection(Cursor()),
    )
    sql = (
        "SELECT :minimum::integer AS result_count, ':literal' AS literal, "
        "$$:dollar_literal$$ AS body"
    )

    result = await adapter.execute_manual(
        sql=sql,
        parameters=[
            {
                "name": "minimum",
                "type": "integer",
                "source": "human",
                "value": 3,
            }
        ],
        max_rows=3,
        statement_timeout_ms=750,
    )

    assert calls == [
        ("SET TRANSACTION READ ONLY", None),
        (
            "SELECT set_config('statement_timeout', %s, true)",
            ("750ms",),
        ),
        (
            "SELECT %(minimum)s::integer AS result_count, ':literal' AS literal, "
            "$$:dollar_literal$$ AS body",
            {"minimum": 3},
        ),
    ]
    payload = result.as_dict()
    assert payload["columns"] == [
        {
            "ordinal": 0,
            "name": "result_count",
            "databaseType": "int4",
            "typeOid": 23,
            "logicalType": "integer",
        },
        {
            "ordinal": 1,
            "name": "ratio",
            "databaseType": "numeric",
            "typeOid": 1700,
            "logicalType": "decimal",
        },
        {
            "ordinal": 2,
            "name": "active",
            "databaseType": "bool",
            "typeOid": 16,
            "logicalType": "boolean",
        },
        {
            "ordinal": 3,
            "name": "observed_on",
            "databaseType": "date",
            "typeOid": 1082,
            "logicalType": "date",
        },
        {
            "ordinal": 4,
            "name": "issued_at",
            "databaseType": "timestamptz",
            "typeOid": 1184,
            "logicalType": "date-time",
        },
        {
            "ordinal": 5,
            "name": "result_id",
            "databaseType": "uuid",
            "typeOid": 2950,
            "logicalType": "string",
        },
        {
            "ordinal": 6,
            "name": "evidence",
            "databaseType": "jsonb",
            "typeOid": 3802,
            "logicalType": "json",
        },
        {
            "ordinal": 7,
            "name": "payload",
            "databaseType": "bytea",
            "typeOid": 17,
            "logicalType": "binary",
        },
    ]
    assert payload["rows"] == [
        [
            {"type": "integer", "value": 3},
            {"type": "decimal", "value": "1.250"},
            {"type": "boolean", "value": True},
            {"type": "date", "value": "2026-07-17"},
            {"type": "date-time", "value": "2026-07-17T19:30:00Z"},
            {
                "type": "string",
                "value": "12345678-1234-5678-1234-567812345678",
            },
            {
                "type": "json",
                "value": {"measurements": ["2.50", None]},
            },
            {"type": "binary", "value": "AP8="},
        ]
    ]
    assert payload["rowCount"] == {
        "returned": 1,
        "truncated": False,
        "truncationReason": None,
    }
    # This is the actual API serialization constraint, including nested JSON.
    json.dumps(payload, allow_nan=False)


@pytest.mark.asyncio
async def test_manual_execution_fetches_only_bound_plus_one_without_sql_rewrite():
    submitted_sql: list[str] = []

    class Cursor:
        description = [SimpleNamespace(name="patient_id", type_code=25)]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            if sql.startswith("SELECT patient_id"):
                submitted_sql.append(sql)

        def fetchmany(self, count):
            assert count == 3
            return [("p-1",), ("p-2",), ("p-3",)]

    adapter = PostgresAnalyticsAdapter(
        "postgresql://demo",
        connect=lambda *args, **kwargs: _Connection(Cursor()),
    )
    sql = "SELECT patient_id FROM analytics.lab_result_fact_v1 ORDER BY patient_id"

    result = await adapter.execute_manual(
        sql=sql,
        parameters=[],
        max_rows=2,
        statement_timeout_ms=500,
    )

    assert submitted_sql == [sql]
    assert len(result.rows) == 2
    assert result.truncated is True
    assert result.truncation_reason == "configured_limit"


@pytest.mark.asyncio
async def test_manual_execution_has_deterministic_unknown_type_fallback():
    class Cursor:
        description = [SimpleNamespace(name="extension_value", type_code=99999)]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            return None

        def fetchmany(self, count):
            return [("value",)]

    adapter = PostgresAnalyticsAdapter(
        "postgresql://demo",
        connect=lambda *args, **kwargs: _Connection(Cursor()),
    )

    result = await adapter.execute_manual(
        sql="SELECT extension_value FROM analytics.extension_view",
        parameters=[],
        max_rows=10,
        statement_timeout_ms=500,
    )

    assert result.columns[0].as_dict() == {
        "ordinal": 0,
        "name": "extension_value",
        "databaseType": "oid:99999",
        "typeOid": 99999,
        "logicalType": "string",
    }


@pytest.mark.asyncio
async def test_manual_execution_preserves_sanitized_postgres_diagnostics():
    diagnostic = SimpleNamespace(
        severity_nonlocalized="ERROR",
        severity="ERROR localized",
        message_primary='column "missing" does not exist',
        message_detail=(
            "connection postgresql://alice:super-secret@database:5432/catalyst"
        ),
        message_hint="password=another-secret Check the selected field.",
        statement_position="18",
    )

    class UndefinedColumn(psycopg.Error):
        sqlstate = "42703"

        @property
        def diag(self):
            return diagnostic

    class Cursor:
        description = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            if sql.startswith("SELECT missing"):
                raise UndefinedColumn("postgresql://alice:super-secret@database")

    dsn = "postgresql://demo-user:demo-password@database:5432/catalyst"
    adapter = PostgresAnalyticsAdapter(
        dsn,
        connect=lambda *args, **kwargs: _Connection(Cursor()),
    )

    with pytest.raises(ManualAnalyticsError) as caught:
        await adapter.execute_manual(
            sql="SELECT missing FROM analytics.lab_result_fact_v1",
            parameters=[],
            max_rows=10,
            statement_timeout_ms=500,
        )

    payload = caught.value.as_dict()
    assert payload == {
        "sqlstate": "42703",
        "severity": "ERROR",
        "message": 'column "missing" does not exist',
        "detail": "connection [redacted-postgresql-dsn]",
        "hint": "password=[redacted] Check the selected field.",
        "position": 18,
    }
    serialized = json.dumps(payload)
    assert "super-secret" not in serialized
    assert "another-secret" not in serialized
    assert "demo-password" not in serialized
    assert str(caught.value) == 'column "missing" does not exist'
