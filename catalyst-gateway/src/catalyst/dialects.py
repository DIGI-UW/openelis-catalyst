"""Dialect adapters: the one place a SQL grammar's specifics are declared.

Connection and execution are a single generic implementation driven by the
connection URI -- availability, discovery, exact execution, rows or the
database error. Nothing there needs to know which engine answered.

Syntax is the opposite kind of problem. Parsing, linting, layout, identifier
quoting, parameter style and the editor's language mode differ by grammar, and
squeezing them into configuration values would be as wrong as branching the
core on engine identity. So each dialect gets a small module here declaring
what its grammar needs, configuration names which adapter a source uses, and
the Gateway resolves it and asks it -- it never asks *which engine is this*.

This repository ships one production adapter, for Spark. A fixture adapter in
tests exercises the same code path, which is how the seam stays honest without
a second engine to maintain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class ExecutionGuarantee:
    """How one execution bound is imposed on a given engine.

    ``mechanism`` names the concrete thing that enforces the bound. When an
    engine cannot honor one, ``enforced`` is False and ``mechanism`` says why:
    the limitation is surfaced to the caller rather than silently dropped or
    emulated inside Catalyst, which would report a guarantee the database is
    not actually making.
    """

    mechanism: str
    enforced: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"mechanism": self.mechanism, "enforced": self.enforced}


@dataclass(frozen=True)
class DialectAdapter:
    """Everything the Gateway needs to know about one SQL grammar."""

    name: str
    # The dialect as declared to the model and the editor.
    sql_dialect: str
    # sqlglot's name for the grammar, used for parsing and layout.
    sqlglot_dialect: str
    # The UI editor's language mode.
    editor_language: str
    # How a statement is named in advisory findings ("Spark SQL statement").
    statement_label: str
    identifier_quote: str
    parameter_style: str
    row_bound: ExecutionGuarantee
    time_limit: ExecutionGuarantee
    read_only: ExecutionGuarantee
    # How this grammar's catalog is read. Discovery statements differ by engine
    # exactly as syntax does, so they live with the adapter rather than as a
    # branch in the Gateway. The callable receives an open DB-API cursor and
    # returns the same relation/field shape for every dialect.
    discover_relations: Callable[[Any], list[dict[str, Any]]]
    # Maps a native column type name onto the logical types the editor, the
    # model context and the typed result table share.
    logical_type: Callable[[str], str]

    def quote_identifier(self, name: str) -> str:
        quote = self.identifier_quote
        return f"{quote}{name.replace(quote, quote * 2)}{quote}"

    def limitations(self) -> list[str]:
        """Bounds this engine does not enforce, for the caller to surface."""
        return [
            f"{label}: {guarantee.mechanism}"
            for label, guarantee in (
                ("returned-row bound", self.row_bound),
                ("time limit", self.time_limit),
                ("read-only guarantee", self.read_only),
            )
            if not guarantee.enforced
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dialect": self.sql_dialect,
            "editorLanguage": self.editor_language,
            "identifierQuote": self.identifier_quote,
            "parameterStyle": self.parameter_style,
            "rowBound": self.row_bound.as_dict(),
            "timeLimit": self.time_limit.as_dict(),
            "readOnly": self.read_only.as_dict(),
            "limitations": self.limitations(),
        }


def _spark_logical_type(database_type: str) -> str:
    """Map a Spark type name onto the logical types the editor and model use."""
    text = database_type.strip().lower()
    base = text.split("(", 1)[0].strip()
    if base in {"boolean"}:
        return "boolean"
    if base in {"tinyint", "smallint", "int", "integer", "bigint"}:
        return "integer"
    if base in {"float", "double", "decimal", "numeric"}:
        return "decimal"
    if base in {"date"}:
        return "date"
    if base in {"timestamp", "timestamp_ntz", "timestamp_ltz"}:
        return "date-time"
    if base in {"string", "varchar", "char"}:
        return "string"
    if base in {"binary"}:
        return "binary"
    if base in {"interval"}:
        return "interval"
    # Complex types stay visible rather than being hidden or flattened: the
    # ViewDefinition output is full of arrays, and the model is told so. Their
    # native type text is carried alongside on databaseType either way.
    if base.startswith("array"):
        return "array"
    if base.startswith(("struct", "map")):
        return "json"
    return "string"


def _spark_discover_relations(cursor: Any) -> list[dict[str, Any]]:
    """Every table and view readable through this connection, with columns."""
    cursor.execute("SHOW TABLES")
    listed: list[tuple[str, str]] = []
    for row in cursor.fetchall():
        # SHOW TABLES returns (namespace, tableName, isTemporary).
        namespace = str(row[0] or "")
        name = str(row[1])
        listed.append((namespace, name))

    relations: list[dict[str, Any]] = []
    for namespace, name in listed:
        qualified = f"{namespace}.{name}" if namespace else name
        try:
            cursor.execute(f"DESCRIBE TABLE {SPARK.quote_identifier(name)}")
            described = cursor.fetchall()
        except Exception:
            # A relation that cannot be described is still reported, so the
            # model and editor see the same list the connection exposes.
            relations.append(
                {
                    "name": qualified,
                    "relationType": "relation",
                    "unqualifiedVisible": True,
                    "grain": f"Rows readable from {qualified}",
                    "fields": [],
                }
            )
            continue

        fields: list[dict[str, Any]] = []
        for row in described:
            column_name = str(row[0] or "").strip()
            # DESCRIBE TABLE appends partition sections after a blank line.
            if not column_name or column_name.startswith("#"):
                break
            database_type = str(row[1] or "").strip()
            comment = str(row[2]).strip() if len(row) > 2 and row[2] else ""
            fields.append(
                {
                    "name": column_name,
                    "type": _spark_logical_type(database_type),
                    "databaseType": database_type,
                    "description": comment
                    or f"{qualified}.{column_name} (Spark {database_type})",
                    "nullable": True,
                }
            )
        relations.append(
            {
                "name": qualified,
                "relationType": "view" if namespace == "" else "table",
                "unqualifiedVisible": True,
                "grain": f"Rows readable from {qualified}",
                "fields": fields,
            }
        )
    return relations


SPARK = DialectAdapter(
    name="spark",
    sql_dialect="spark",
    sqlglot_dialect="spark",
    editor_language="sparksql",
    statement_label="Spark SQL",
    identifier_quote="`",
    # Verified against the running Spark 4.0 thriftserver: the HiveServer2
    # client binds named "%(name)s" parameters and preserves their types.
    parameter_style="pyformat",
    row_bound=ExecutionGuarantee(
        "the client stops fetching once the returned-row bound is reached"
    ),
    # Spark's thriftserver has no server-side statement timeout to set, so the
    # bound is a client-side cancel of the running operation. Recorded as
    # unenforced because a cancelled statement is not the same promise a
    # server-side timeout makes: the engine may still be doing work when the
    # client stops waiting. Catalyst does not paper over that difference.
    time_limit=ExecutionGuarantee(
        "no server-side statement timeout exists; the client cancels the "
        "running operation when the time limit elapses",
        enforced=False,
    ),
    read_only=ExecutionGuarantee(
        "the warehouse volume is mounted read-only for the thriftserver, so "
        "a write reaches Spark and Spark itself refuses it"
    ),
    discover_relations=_spark_discover_relations,
    logical_type=_spark_logical_type,
)


_ADAPTERS: dict[str, DialectAdapter] = {SPARK.name: SPARK}


class UnknownDialectAdapter(KeyError):
    """A source names a dialect adapter this build does not contain."""


def resolve_dialect_adapter(name: str) -> DialectAdapter:
    """Return the adapter a source's configuration names."""
    try:
        return _ADAPTERS[name]
    except KeyError:
        known = ", ".join(sorted(_ADAPTERS)) or "none"
        raise UnknownDialectAdapter(
            f"No dialect adapter named {name!r}; this build has: {known}."
        ) from None


def register_dialect_adapter(adapter: DialectAdapter) -> None:
    """Add an adapter. Tests use this to prove the seam with a fixture.

    Production configuration never calls this: the one production adapter is
    declared above, and a second production engine is out of scope.
    """
    _ADAPTERS[adapter.name] = adapter


def unregister_dialect_adapter(name: str) -> None:
    _ADAPTERS.pop(name, None)
