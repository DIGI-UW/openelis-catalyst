from __future__ import annotations

from dataclasses import dataclass
import re
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


def _phrase_in_question(question: str, phrase: str) -> bool:
    pattern = rf"(?<!\w){re.escape(phrase.strip())}(?!\w)"
    return re.search(pattern, question, flags=re.IGNORECASE) is not None


_DESTRUCTIVE_QUESTION_PATTERNS = (
    re.compile(r"\bdelete\s+(?:all\b|from\b)", re.IGNORECASE),
    re.compile(r"\bdrop\s+(?:table|view|schema|database)\b", re.IGNORECASE),
    re.compile(r"\btruncate(?:\s+table)?\s+[A-Za-z_]", re.IGNORECASE),
    re.compile(r"\binsert\s+into\b", re.IGNORECASE),
    re.compile(r"\bupdate\s+[A-Za-z_][A-Za-z0-9_.]*\s+set\b", re.IGNORECASE),
    re.compile(r"\balter\s+(?:table|view|schema|database)\b", re.IGNORECASE),
)


def question_policy_violations(question: str) -> list[Violation]:
    """Reject explicit write instructions before any model can reinterpret them."""
    if any(pattern.search(question) for pattern in _DESTRUCTIVE_QUESTION_PATTERNS):
        return [
            Violation(
                "destructive_intent",
                "Catalyst only accepts read-only clinical analytics questions.",
            )
        ]
    return []


def _named_semantic_requirements(
    question: str, catalog: dict[str, Any]
) -> list[tuple[str, str]]:
    requirements: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for view in catalog.get("views", []):
        for dimension in view.get("semanticDimensions", []):
            if dimension.get("semanticType") != "analyte":
                continue
            field = str(dimension.get("field", ""))
            for value in dimension.get("values", []):
                canonical = str(value.get("canonical", ""))
                phrases = [canonical, *value.get("aliases", [])]
                if not canonical or not any(
                    _phrase_in_question(question, phrase) for phrase in phrases
                ):
                    continue
                key = (field.casefold(), canonical.casefold())
                if key not in seen:
                    requirements.append((field, canonical))
                    seen.add(key)
    return requirements


def _predicate_parameter_names(statement: exp.Expression, field: str) -> set[str]:
    names: set[str] = set()

    def is_field(node: exp.Expression | None) -> bool:
        return isinstance(node, exp.Column) and node.name.casefold() == field.casefold()

    for predicate in statement.find_all(exp.EQ):
        left = predicate.args.get("this")
        right = predicate.args.get("expression")
        if is_field(left) and isinstance(right, exp.Placeholder) and right.name:
            names.add(right.name)
        elif is_field(right) and isinstance(left, exp.Placeholder) and left.name:
            names.add(left.name)
    for predicate in statement.find_all(exp.In):
        if not is_field(predicate.args.get("this")):
            continue
        names.update(
            placeholder.name
            for placeholder in predicate.expressions
            if isinstance(placeholder, exp.Placeholder) and placeholder.name
        )
    return names


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
        statements: list[exp.Expression | None] = []
        try:
            statements = sqlglot.parse(query.get("sql", ""), read="postgres")
            for statement in statements:
                if statement is None:
                    continue
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

        if len(statements) == 1 and statements[0] is not None:
            parameter_values = {
                parameter.get("name"): parameter.get("value")
                for parameter in parameters
            }
            for field, canonical in _named_semantic_requirements(
                expected_question, context["catalog"]
            ):
                bound_names = _predicate_parameter_names(statements[0], field)
                if not any(
                    str(parameter_values.get(name, "")).casefold()
                    == canonical.casefold()
                    for name in bound_names
                ):
                    violations.append(
                        Violation(
                            "missing_semantic_filter",
                            f"The named analyte {canonical!r} must be constrained "
                            f"by {field} using its canonical bound value.",
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
        available_relations: set[str] | None = None,
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
        referenced_relations = {
            self._table_name(table)
            for table in statement.find_all(exp.Table)
            if not (
                not table.catalog
                and not table.db
                and table.name.casefold() in cte_names
            )
            and table.find_ancestor(exp.Into) is None
        }
        if available_relations is not None:
            available_folded = {relation.casefold() for relation in available_relations}
            missing_relations = sorted(
                relation
                for relation in referenced_relations
                if relation.casefold() not in available_folded
            )
            if missing_relations:
                violations.append(
                    Violation(
                        "relation_not_found",
                        "Query references relations not present in the current "
                        "readable PostgreSQL schema: "
                        + ", ".join(missing_relations)
                        + ".",
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
