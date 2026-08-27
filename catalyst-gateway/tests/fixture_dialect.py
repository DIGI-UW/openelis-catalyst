"""A second dialect adapter that exists only for tests.

The production build ships exactly one adapter, for Spark. This fixture points
a source at a *different* grammar so the same connection and execution code is
exercised through a second adapter -- which is how the seam is proven without
building or maintaining a second production engine.

If any engine-specific behavior leaks back into the Gateway, a source using
this adapter is what fails.
"""

from __future__ import annotations

from typing import Any

from src.catalyst.dialects import DialectAdapter, ExecutionGuarantee


def _fixture_logical_type(database_type: str) -> str:
    return {
        "num": "integer",
        "txt": "string",
        "moment": "date-time",
    }.get(database_type.strip().lower(), "unknown")


def _fixture_discover_relations(cursor: Any) -> list[dict[str, Any]]:
    cursor.execute("LIST RELATIONS")
    relations: list[dict[str, Any]] = []
    for name, columns in cursor.fetchall():
        relations.append(
            {
                "name": str(name),
                "relationType": "table",
                "unqualifiedVisible": True,
                "grain": f"Rows readable from {name}",
                "fields": [
                    {
                        "name": str(column),
                        "type": _fixture_logical_type(str(column_type)),
                        "databaseType": str(column_type),
                        "description": f"{name}.{column}",
                        "nullable": True,
                    }
                    for column, column_type in columns
                ],
            }
        )
    return relations


FIXTURE = DialectAdapter(
    name="fixture",
    sql_dialect="fixture",
    # A real, different grammar: quoting and parsing must actually change with
    # the adapter rather than being Spark's behavior under another name.
    sqlglot_dialect="duckdb",
    editor_language="fixturesql",
    statement_label="Fixture SQL",
    identifier_quote='"',
    parameter_style="pyformat",
    row_bound=ExecutionGuarantee("the fixture driver stops at the row bound"),
    # Deliberately unenforced, so the "surface the limitation rather than
    # emulate it" path has a test that does not depend on Spark.
    time_limit=ExecutionGuarantee(
        "the fixture engine has no statement timeout", enforced=False
    ),
    read_only=ExecutionGuarantee("the fixture connection is opened read-only"),
    discover_relations=_fixture_discover_relations,
    logical_type=_fixture_logical_type,
)
