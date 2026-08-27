from __future__ import annotations

import asyncio
import base64
import math
import re
import threading
from contextlib import contextmanager
from urllib.parse import urlsplit
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .dialects import DialectAdapter


class AnalyticsError(RuntimeError):
    """The governed analytics execution failed."""


@dataclass(frozen=True)
class DatabaseDiagnostic:
    """Safe, stable fields from a database error response."""

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
    logical_type: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "name": self.name,
            "databaseType": self.database_type,
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


# Any scheme's connection URI, so a driver error that quotes the URI cannot
# leak its credentials regardless of which engine produced the message.
_CONNECTION_URI_PATTERN = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s\"']+")
_PASSWORD_PATTERN = re.compile(r"(?i)\b(password\s*=\s*)(?:'[^']*'|\"[^\"]*\"|[^\s;]+)")


def _dbapi_connect(connection_uri: str, *, connect_timeout: int = 5) -> Any:
    """Open a DB-API connection described entirely by the connection URI.

    Transport is a client library, not a per-engine class: the URI carries the
    host, port, credentials and database, and nothing here decides behavior
    from which scheme it happens to name.
    """
    from impala.dbapi import connect as _hs2_connect

    parts = urlsplit(connection_uri)
    if not parts.hostname:
        raise AnalyticsError(
            f"Connection URI {connection_uri!r} names no host."
        )
    database = parts.path.lstrip("/") or "default"
    return _hs2_connect(
        host=parts.hostname,
        port=parts.port or 10000,
        database=database,
        user=parts.username or "catalyst",
        # HiveServer2's SASL PLAIN exchange rejects an empty secret in the
        # client before the server ever sees it, so a URI without one still
        # sends a placeholder. The endpoint's own auth policy decides.
        password=parts.password or "catalyst",
        auth_mechanism="PLAIN",
        timeout=connect_timeout,
    )


@contextmanager
def _time_limit(cursor: Any, statement_timeout_ms: int, dialect: DialectAdapter):
    """Impose the configured time limit using whatever the engine supports.

    Where the engine has no server-side statement timeout, the adapter records
    that as an unenforced guarantee and this cancels the running operation
    instead. It does not pretend the two are the same: a cancelled statement
    may still be executing when the client stops waiting.
    """
    if statement_timeout_ms <= 0 or dialect.time_limit.enforced:
        yield
        return

    expired = threading.Event()

    def _cancel() -> None:
        expired.set()
        cancel = getattr(cursor, "cancel_operation", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                # The statement may have finished between the timer firing and
                # this call; a failed cancel must not mask the real result.
                pass

    timer = threading.Timer(statement_timeout_ms / 1000.0, _cancel)
    timer.start()
    try:
        yield
    except Exception as error:
        if expired.is_set():
            raise AnalyticsError(
                f"The query exceeded the {statement_timeout_ms} ms time limit "
                "and was cancelled."
            ) from error
        raise
    finally:
        timer.cancel()


class SqlAnalyticsAdapter:
    """One connection/execution implementation for every configured source.

    Everything engine-specific about *transport* is in the connection URI, and
    everything engine-specific about *grammar* is in the dialect adapter this
    is constructed with. Nothing here asks which engine answered, which is why
    a second engine is configuration plus one adapter module rather than a
    second class beside this one.
    """

    def __init__(
        self,
        connection_uri: str,
        *,
        dialect: DialectAdapter,
        data_source_id: str | None = None,
        connect: Callable[..., Any] | None = None,
        connect_timeout_seconds: int = 5,
    ) -> None:
        self.connection_uri = connection_uri
        self.dialect = dialect
        self.data_source_id = data_source_id
        self._connect = connect or _dbapi_connect
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
            raise AnalyticsError(f"Query execution failed: {error}") from error

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
                self._database_diagnostic(error, self.connection_uri)
            ) from error

    async def readiness(self) -> dict[str, Any]:
        try:
            await asyncio.to_thread(self._check_ready_sync)
        except Exception as error:
            return {
                "ready": False,
                "dataSource": self.data_source_id,
                "dialect": self.dialect.sql_dialect,
                "message": str(error),
            }
        return {
            "ready": True,
            "dataSource": self.data_source_id,
            "dialect": self.dialect.sql_dialect,
        }

    async def freshness(self) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(self._freshness_sync)
        except AnalyticsError:
            raise
        except Exception as error:
            raise AnalyticsError(
                f"Freshness lookup failed: {error}"
            ) from error

    async def discover_relations(self) -> list[dict[str, Any]]:
        """Describe every non-system relation the configured role can SELECT."""

        try:
            return await asyncio.to_thread(self._discover_relations_sync)
        except AnalyticsError:
            raise
        except Exception as error:
            raise AnalyticsError(
                f"Relation discovery failed: {error}"
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
            self.connection_uri,
            connect_timeout=self.connect_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                with _time_limit(cursor, statement_timeout_ms, self.dialect):
                    # ``bindings`` is passed even when empty: _driver_sql doubled
                    # the literal per-cent signs, and a pyformat driver only
                    # collapses them back when it has parameters to convert.
                    cursor.execute(driver_sql, bindings)
                    # A statement with no result set -- DDL, or anything the
                    # engine answers without rows -- has no description to
                    # fetch against. Reading it as zero rows is the honest
                    # answer; fetching anyway raises from inside the driver
                    # and looks like a Catalyst bug rather than the
                    # engine's own response.
                    description = cursor.description or ()
                    rows = (
                        list(cursor.fetchmany(max_rows + 1)) if description else []
                    )
                column_names = [str(column[0]) for column in description]
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
        # The driver uses ``%(name)s`` for named bindings and reads any other
        # per-cent sign as a malformed placeholder. Rewriting the named bindings
        # and doubling the literal per-cent signs are the only changes made to
        # the submitted SQL; no row limit is added.
        driver_sql = self._driver_sql(sql, set(bindings))
        with self._connect(
            self.connection_uri,
            connect_timeout=self.connect_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                with _time_limit(cursor, statement_timeout_ms, self.dialect):
                    cursor.execute(driver_sql, bindings)
                    # A statement with no result set -- DDL, or anything the
                    # engine answers without rows -- has no description to
                    # fetch against. Reading it as zero rows is the honest
                    # answer; fetching anyway raises from inside the driver
                    # and looks like a Catalyst bug rather than the
                    # engine's own response.
                    description = tuple(cursor.description or ())
                    raw_rows = (
                        list(cursor.fetchmany(max_rows + 1)) if description else []
                    )
                columns = self._manual_columns(description, raw_rows)

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

    def _literal_sql_limit(self, sql: str) -> int | None:
        try:
            statement = sqlglot.parse_one(
                sql, read=self.dialect.sqlglot_dialect
            )
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
            self.connection_uri,
            connect_timeout=self.connect_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchall()

    def _freshness_sync(self) -> dict[str, Any]:
        """What the connection can say about itself without a curated table.

        The retired PostgreSQL path kept a hand-registered pipeline_run row.
        Nothing writes one on the warehouse path, and inventing an equivalent
        would be a second source of truth, so freshness reports only what the
        connection actually knows.
        """
        return {
            "dataSource": self.data_source_id,
            "dialect": self.dialect.sql_dialect,
            "relationCount": len(self._discover_relations_sync()),
        }

    def _discover_relations_sync(self) -> list[dict[str, Any]]:
        """Every relation and column readable through this connection."""
        with self._connect(
            self.connection_uri,
            connect_timeout=self.connect_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                return self.dialect.discover_relations(cursor)

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

    def _manual_columns(
        self,
        description: Sequence[Any],
        rows: Sequence[Sequence[Any]],
    ) -> list[AnalyticsColumn]:
        """Typed columns from the driver's own description of the result.

        DB-API says a description entry is a sequence whose first item is the
        column name and whose second is its type; reading it positionally is
        what keeps this generic across drivers.
        """
        columns: list[AnalyticsColumn] = []
        for ordinal, description_column in enumerate(description):
            database_type = str(description_column[1] or "").strip()
            logical_type = (
                self.dialect.logical_type(database_type) if database_type else "unknown"
            )
            if logical_type == "unknown":
                sample = next(
                    (
                        row[ordinal]
                        for row in rows
                        if len(row) > ordinal and row[ordinal] is not None
                    ),
                    None,
                )
                logical_type = self._value_logical_type(sample)
            columns.append(
                AnalyticsColumn(
                    ordinal=ordinal,
                    name=str(description_column[0]),
                    database_type=database_type or "unknown",
                    logical_type=logical_type,
                )
            )
        return columns

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
    def _sanitize_diagnostic_text(value: str, uri: str) -> str:
        sanitized = value.replace(uri, "[redacted-connection-uri]") if uri else value
        sanitized = _CONNECTION_URI_PATTERN.sub("[redacted-connection-uri]", sanitized)
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

            A pyformat driver finds placeholders by scanning the whole statement with a
            regular expression, without any knowledge of SQL quoting. A literal
            per-cent sign therefore reads as the start of a placeholder wherever
            it appears -- inside a string literal, a dollar-quoted body, or even
            a comment -- and ``TO_CHAR(x, '990D9%')`` is rejected outright. Every
            per-cent sign that came from the caller is doubled here; the driver
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
                    # in the output the driver is meant to read as a placeholder.
                    output.append(f"%({name})s")
                    index = end
                    continue
            emit(char)
            index += 1
        return "".join(output)
