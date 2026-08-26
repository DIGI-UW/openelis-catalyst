from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import psycopg
import pytest

from src.catalyst.analytics import (
    AnalyticsColumn,
    ManualAnalyticsError,
    ManualAnalyticsResult,
    PostgresAnalyticsAdapter,
)


class _Connection:
    def __init__(self, cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def cursor(self):
        return self._cursor


def test_manual_result_warns_about_blank_columns_without_treating_values_as_empty():
    result = ManualAnalyticsResult(
        columns=[
            AnalyticsColumn(0, "name_display", "text", 25, "string"),
            AnalyticsColumn(1, "active", "bool", 16, "boolean"),
            AnalyticsColumn(2, "score", "int4", 23, "integer"),
        ],
        rows=[
            [
                {"type": "null"},
                {"type": "boolean", "value": False},
                {"type": "integer", "value": 0},
            ],
            [
                {"type": "string", "value": "  "},
                {"type": "boolean", "value": True},
                {"type": "integer", "value": 1},
            ],
        ],
        truncated=False,
    )

    assert result.as_dict()["warnings"] == [
        "`name_display` was blank or NULL in all 2 returned rows. "
        "Select a populated column or revise the SQL expression."
    ]


def test_manual_result_does_not_describe_zero_rows_as_blank():
    result = ManualAnalyticsResult(
        columns=[AnalyticsColumn(0, "name_display", "text", 25, "string")],
        rows=[],
        truncated=False,
    )

    assert result.as_dict()["warnings"] == []


def test_manual_result_bounds_blank_warning_and_marks_truncated_scope():
    columns = [
        AnalyticsColumn(index, f"blank_{index}", "text", 25, "string")
        for index in range(10)
    ]
    result = ManualAnalyticsResult(
        columns=columns,
        rows=[[{"type": "null"} for _ in columns]],
        truncated=True,
        truncation_reason="configured_limit",
    )

    warning = result.as_dict()["warnings"][0]
    assert "`blank_0`, `blank_1`, `blank_2`" in warning
    assert "and 2 more were blank or NULL in all 1 displayed row" in warning
    assert "displayed rows only because results were truncated" in warning


@pytest.mark.asyncio
async def test_relation_discovery_uses_role_privileges_and_all_relation_kinds():
    calls: list[str] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            calls.append(sql)

        def fetchall(self):
            return [
                (
                    "analytics",
                    "lab_result_fact_v1",
                    "v",
                    False,
                    1,
                    "observation_id",
                    "text",
                    False,
                    "One row per result",
                    "FHIR Observation identifier",
                ),
                (
                    "external",
                    "reference_codes",
                    "f",
                    False,
                    1,
                    "code",
                    "character varying",
                    False,
                    None,
                    None,
                ),
                (
                    "public",
                    "patient_flat",
                    "r",
                    True,
                    1,
                    "patient_id",
                    "uuid",
                    False,
                    None,
                    None,
                ),
                (
                    "public",
                    "result_partitioned",
                    "p",
                    True,
                    1,
                    "payload",
                    "jsonb",
                    True,
                    None,
                    None,
                ),
                (
                    "reporting",
                    "daily_counts",
                    "m",
                    False,
                    1,
                    "result_count",
                    "bigint",
                    False,
                    None,
                    None,
                ),
            ]

    adapter = PostgresAnalyticsAdapter(
        "postgresql://demo",
        connect=lambda *args, **kwargs: _Connection(Cursor()),
    )

    relations = await adapter.discover_relations()

    discovery_sql = calls[1]
    assert "has_schema_privilege" in discovery_sql
    assert "has_column_privilege" in discovery_sql
    assert "pg_table_is_visible" in discovery_sql
    assert "relation.relkind IN ('r', 'p', 'v', 'm', 'f')" in discovery_sql
    assert "pg_catalog" in discovery_sql
    assert [relation["relationType"] for relation in relations] == [
        "view",
        "foreign-table",
        "table",
        "partitioned-table",
        "materialized-view",
    ]
    assert relations[0] == {
        "name": "analytics.lab_result_fact_v1",
        "relationType": "view",
        "unqualifiedVisible": False,
        "grain": "One row per result",
        "fields": [
            {
                "name": "observation_id",
                "type": "string",
                "databaseType": "text",
                "description": "FHIR Observation identifier",
                "nullable": False,
            }
        ],
    }
    assert relations[2]["unqualifiedVisible"] is True
    assert relations[3]["fields"][0]["type"] == "json"


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
async def test_manual_execution_treats_explicit_query_limit_as_complete_result():
    class Cursor:
        description = [SimpleNamespace(name="patient_id", type_code=25)]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            return None

        def fetchmany(self, count):
            assert count == 101
            return [("p-1",), ("p-2",)]

    adapter = PostgresAnalyticsAdapter(
        "postgresql://demo",
        connect=lambda *args, **kwargs: _Connection(Cursor()),
    )

    result = await adapter.execute_manual(
        sql=(
            "SELECT patient_id FROM analytics.lab_result_fact_v1 "
            "ORDER BY patient_id LIMIT 2"
        ),
        parameters=[],
        max_rows=100,
        statement_timeout_ms=500,
    )

    assert len(result.rows) == 2
    assert result.truncated is False
    assert result.truncation_reason is None


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


@pytest.mark.parametrize(
    ("submitted", "expected"),
    [
        # The case that sent us here: a per-cent sign in a TO_CHAR format string.
        (
            "SELECT TO_CHAR(ratio * 100, '990D9%') AS pct",
            "SELECT TO_CHAR(ratio * 100, '990D9%%') AS pct",
        ),
        # psycopg's scanner does not know about SQL quoting, so a LIKE pattern
        # needs doubling just as much as anything else.
        (
            "SELECT 1 WHERE name LIKE '%acid%'",
            "SELECT 1 WHERE name LIKE '%%acid%%'",
        ),
        # Modulo, outside any quoting at all.
        ("SELECT n % 2 FROM t", "SELECT n %% 2 FROM t"),
        # An escaped quote inside a literal must not end the literal early.
        ("SELECT 'it''s 50%' AS note", "SELECT 'it''s 50%%' AS note"),
        # Comments reach psycopg too.
        ("SELECT 1 -- 50% done\n", "SELECT 1 -- 50%% done\n"),
        ("SELECT /* 50% */ 1", "SELECT /* 50%% */ 1"),
        # Dollar-quoted bodies are opaque to Postgres, not to psycopg.
        ("SELECT $$100%$$", "SELECT $$100%%$$"),
        ("SELECT $tag$100%$tag$", "SELECT $tag$100%%$tag$"),
    ],
)
def test_driver_sql_doubles_literal_per_cent_signs_wherever_they_appear(
    submitted: str, expected: str
) -> None:
    assert PostgresAnalyticsAdapter._driver_sql(submitted, set()) == expected


def test_driver_sql_leaves_the_placeholder_it_generates_undoubled() -> None:
    assert (
        PostgresAnalyticsAdapter._driver_sql("SELECT * FROM t WHERE id = :pid", {"pid"})
        == "SELECT * FROM t WHERE id = %(pid)s"
    )


def test_driver_sql_can_rewrite_a_binding_and_escape_a_literal_in_one_statement() -> (
    None
):
    assert (
        PostgresAnalyticsAdapter._driver_sql(
            "SELECT TO_CHAR(v, '990D9%') FROM t WHERE id = :pid AND n % 2 = 0",
            {"pid"},
        )
        == "SELECT TO_CHAR(v, '990D9%%') FROM t WHERE id = %(pid)s AND n %% 2 = 0"
    )


def test_driver_sql_reads_a_doubled_per_cent_sign_as_two_literal_per_cent_signs() -> (
    None
):
    """Submitted SQL now means what it says.

    Before this escaping existed, ``%%`` was the workaround for getting a single
    per-cent sign past psycopg. It is no longer needed, and it no longer does
    that: two per-cent signs in the submitted SQL are two per-cent signs in the
    statement Postgres runs.
    """
    assert (
        PostgresAnalyticsAdapter._driver_sql("SELECT '990D9%%'", set())
        == "SELECT '990D9%%%%'"
    )


@pytest.mark.asyncio
async def test_manual_execution_escapes_per_cent_signs_and_always_binds_a_mapping():
    executed: list[tuple[str, object]] = []

    class Cursor:
        description = [SimpleNamespace(name="pct", type_code=25)]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            if sql.startswith("SELECT TO_CHAR"):
                executed.append((sql, params))

        def fetchmany(self, count):
            return [("99.5%",)]

    adapter = PostgresAnalyticsAdapter(
        "postgresql://demo",
        connect=lambda *args, **kwargs: _Connection(Cursor()),
    )

    await adapter.execute_manual(
        sql="SELECT TO_CHAR(0.995 * 100, '990D9%') AS pct",
        parameters=[],
        max_rows=10,
        statement_timeout_ms=500,
    )

    ((sql, params),) = executed
    assert "'990D9%%'" in sql
    # psycopg collapses ``%%`` back to ``%`` only when it has parameters to
    # convert, so an empty mapping has to be passed rather than None. If this
    # ever becomes ``bindings or None``, the doubled signs reach the database.
    assert params == {}
