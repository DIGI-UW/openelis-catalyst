from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


@dataclass(frozen=True)
class Violation:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class QueryInvariantError(ValueError):
    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        super().__init__("; ".join(item.message for item in violations))


def validate_query_invariants(
    query: dict[str, Any],
    request: dict[str, Any],
) -> None:
    violations: list[Violation] = []
    expected_question = request["messages"][0]["content"]
    if query.get("question") != expected_question:
        violations.append(
            Violation(
                "question_mismatch",
                "Hub response question does not exactly match the submitted question.",
            )
        )

    context = request["catalystQuery"]
    expected_context_id = context["catalog"]["contextSourceId"]
    actual_context_ids = set(query.get("provenance", {}).get("contextSourceIds", []))
    if actual_context_ids != {expected_context_id}:
        violations.append(
            Violation(
                "context_mismatch",
                "Hub response context sources do not exactly match the approved catalog.",
            )
        )

    if query.get("status") == "ready":
        target = query.get("target", {})
        expected_target = context["target"]
        if any(
            target.get(field) != expected_target.get(field)
            for field in ("dataSource", "catalogVersion", "dialect")
        ):
            violations.append(
                Violation(
                    "target_mismatch",
                    "Hub response target does not match the requested analytics target.",
                )
            )

        approved_catalog = {
            view["name"] for view in context["catalog"].get("views", [])
        }
        returned_views = target.get("approvedViews", [])
        if any(view not in approved_catalog for view in returned_views):
            violations.append(
                Violation(
                    "unapproved_view",
                    "Hub response names a view outside the approved catalog.",
                )
            )

        parameters = query.get("parameters", [])
        names = [parameter.get("name") for parameter in parameters]
        if len(names) != len(set(names)):
            violations.append(
                Violation(
                    "duplicate_parameter",
                    "Query parameter names must be unique.",
                )
            )

        placeholders: set[str] = set()
        try:
            for statement in sqlglot.parse(query.get("sql", ""), read="postgres"):
                placeholders.update(
                    node.name
                    for node in statement.find_all(exp.Placeholder)
                    if node.name
                )
        except ParseError:
            # The SQL policy reports parse failures. Keep invariant reporting focused.
            placeholders = set(names)
        if placeholders != set(names):
            violations.append(
                Violation(
                    "placeholder_mismatch",
                    "SQL placeholders and bound parameter names must match exactly.",
                )
            )

    if violations:
        raise QueryInvariantError(violations)


class SqlPolicy:
    def __init__(self, *, max_rows: int, max_ast_nodes: int = 500) -> None:
        self.max_rows = max_rows
        self.max_ast_nodes = max_ast_nodes

    def evaluate(
        self,
        query: dict[str, Any],
        *,
        approved_views: set[str],
    ) -> list[Violation]:
        sql = query.get("sql", "")
        try:
            statements = sqlglot.parse(sql, read="postgres")
        except ParseError as error:
            return [Violation("invalid_sql", f"SQL could not be parsed: {error}")]

        if len(statements) != 1:
            return [
                Violation(
                    "multiple_statements",
                    "Exactly one PostgreSQL statement is allowed.",
                )
            ]
        statement = statements[0]
        if statement is None:
            return [Violation("invalid_sql", "SQL statement is empty.")]

        violations: list[Violation] = []
        if not isinstance(statement, exp.Select) or any(
            statement.find(node_type) is not None
            for node_type in (
                exp.Into,
                exp.Insert,
                exp.Update,
                exp.Delete,
                exp.Create,
                exp.Drop,
                exp.Alter,
                exp.Command,
                exp.Lock,
                exp.Merge,
            )
        ):
            violations.append(
                Violation(
                    "operation_not_allowed",
                    "Only a read-only SELECT statement is allowed.",
                )
            )

        nodes = list(statement.walk())
        if len(nodes) > self.max_ast_nodes:
            violations.append(
                Violation(
                    "query_too_complex",
                    "Query exceeds the configured AST complexity limit.",
                )
            )

        cte_names = {
            cte.alias_or_name.casefold()
            for cte in statement.find_all(exp.CTE)
            if cte.alias_or_name
        }
        referenced_views = {
            self._table_name(table)
            for table in statement.find_all(exp.Table)
            if table.name.casefold() not in cte_names
            and table.find_ancestor(exp.Into) is None
        }
        approved_folded = {view.casefold() for view in approved_views}
        invalid_views = sorted(
            view for view in referenced_views if view.casefold() not in approved_folded
        )
        if invalid_views or not referenced_views:
            detail = ", ".join(invalid_views) if invalid_views else "none"
            violations.append(
                Violation(
                    "unapproved_view",
                    f"Query references unapproved analytics views: {detail}.",
                )
            )

        if self._has_unbound_predicate_literal(statement):
            violations.append(
                Violation(
                    "unbound_literal",
                    "Predicate values from the question must use named parameters.",
                )
            )

        limit = statement.args.get("limit")
        if limit is not None:
            limit_value = self._limit_value(limit, query.get("parameters", []))
            if limit_value is None or limit_value > self.max_rows:
                violations.append(
                    Violation(
                        "row_limit_exceeded",
                        f"Query LIMIT must be an integer no greater than {self.max_rows}.",
                    )
                )
        return self._deduplicate(violations)

    @staticmethod
    def _table_name(table: exp.Table) -> str:
        return ".".join(
            part
            for part in (
                table.catalog,
                table.db,
                table.name,
            )
            if part
        )

    @staticmethod
    def _has_unbound_predicate_literal(statement: exp.Expression) -> bool:
        predicate_types = (
            exp.EQ,
            exp.NEQ,
            exp.GT,
            exp.GTE,
            exp.LT,
            exp.LTE,
            exp.Between,
            exp.In,
            exp.Like,
            exp.ILike,
        )
        for literal in statement.find_all(exp.Literal):
            if literal.find_ancestor(exp.Limit) is not None:
                continue
            if any(
                literal.find_ancestor(node_type) is not None
                for node_type in predicate_types
            ):
                return True
        return False

    @staticmethod
    def _limit_value(
        limit: exp.Expression,
        parameters: list[dict[str, Any]],
    ) -> int | None:
        expression = limit.args.get("expression")
        if isinstance(expression, exp.Literal) and not expression.is_string:
            try:
                return int(expression.this)
            except ValueError:
                return None
        if isinstance(expression, exp.Placeholder):
            values = {
                parameter.get("name"): parameter.get("value")
                for parameter in parameters
            }
            value = values.get(expression.name)
            return (
                value
                if isinstance(value, int) and not isinstance(value, bool)
                else None
            )
        return None

    @staticmethod
    def _deduplicate(violations: list[Violation]) -> list[Violation]:
        seen: set[tuple[str, str]] = set()
        result: list[Violation] = []
        for violation in violations:
            key = (violation.code, violation.message)
            if key not in seen:
                seen.add(key)
                result.append(violation)
        return result
