"""Deterministic candidate parsing, normalization, and semantic grounding.

Ported verbatim (behaviour-preserving) from the med-agent-hub's
``catalyst_query.py`` as part of moving governed-query orchestration into the
gateway. Every function here is pure: it decodes and canonicalises a model's
structured candidate/review output, grounds parameter names against the
question and catalog semantics, and applies scoped correction patches — no
network, no model calls. The pipeline's model-calling steps drive these.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional

from jsonschema import Draft202012Validator

from .query_lint import turnaround_threshold
from .query_schemas import (
    CANDIDATE_VALIDATOR as _CANDIDATE_VALIDATOR,
    PATCH_VALIDATOR as _PATCH_VALIDATOR,
    REVIEW_VALIDATOR as _REVIEW_VALIDATOR,
    QueryContractError,
    QueryPatchError,
    validation_error as _validation_error,
)


def _parse_exact_object(
    content: str,
    validator: Draft202012Validator,
    *,
    label: str,
) -> Dict[str, Any]:
    value = _decode_exact_object(content, label=label)
    error = _validation_error(validator, value, label)
    if str(error):
        raise error
    return value


def _parse_review_object(
    content: str,
    *,
    label: str,
    flat_repair: bool,
    question: str,
    extension: Mapping[str, Any],
) -> Dict[str, Any]:
    value = _decode_exact_object(content, label=label)
    # Structured-output backends commonly emit every declared property,
    # using null for ones the model considers unset. Treat null the same
    # as absent so decision-conditional hydration below sees them as missing.
    if value.get("candidate") is None:
        value.pop("candidate", None)
    if value.get("message") is None:
        value.pop("message", None)
    decision = value.get("decision")
    default_status = (
        {
            "approve": "passed",
            "repair": "warned",
            "reject": "failed",
        }.get(decision)
        if isinstance(decision, str)
        else None
    )
    checks = value.get("checks")
    if default_status is not None and (checks is None or checks == []):
        value["checks"] = [
            {
                "name": "reviewer_output_hydrated",
                "status": default_status,
                "message": (
                    "The reviewer returned a decision without labelled checks; "
                    "the Hub retained that decision and hydrated this evidence marker."
                ),
            }
        ]
    elif isinstance(checks, list):
        hydrated_checks = []
        for index, check in enumerate(checks, start=1):
            if not isinstance(check, Mapping):
                hydrated_checks.append(check)
                continue
            hydrated = deepcopy(dict(check))
            if (
                not isinstance(hydrated.get("name"), str)
                or not hydrated["name"].strip()
            ):
                hydrated["name"] = f"review_check_{index}"
            if "status" not in hydrated and default_status is not None:
                hydrated["status"] = default_status
            hydrated_checks.append(hydrated)
        value["checks"] = hydrated_checks
    if decision == "reject":
        message = value.get("message")
        if not isinstance(message, str) or not message.strip():
            value["message"] = (
                "The reviewer rejected the candidate without a labelled "
                "message; the Hub hydrated this evidence marker."
            )
    elif (
        decision == "repair"
        and not flat_repair
        and not isinstance(value.get("candidate"), Mapping)
    ):
        # A repair decision with no usable candidate cannot be applied; fail
        # closed by downgrading to a rejection instead of raising, so this
        # still reaches the reviewer's normal reject handling deterministically.
        value["decision"] = "reject"
        value.pop("candidate", None)
        value["message"] = (
            "The reviewer returned a repair decision without a usable "
            "candidate; the Hub downgraded this to a rejection."
        )
    if flat_repair and "candidate" not in value:
        candidate_fields = (
            "status",
            "target",
            "sql",
            "parameters",
            "expectedColumns",
        )
        if any(field in value for field in candidate_fields):
            flat_candidate = {field: value.get(field) for field in candidate_fields}
            flat_candidate, _ = _normalize_exact_duplicate_parameter_bindings(
                flat_candidate
            )
            flat_candidate, _ = _normalize_ordered_parameter_bindings(flat_candidate)
            flat_candidate = _normalize_grounded_parameter_names(
                flat_candidate, question, extension
            )
            value = {
                "decision": value.get("decision"),
                "checks": value.get("checks"),
                "candidate": flat_candidate,
            }
    candidate = value.get("candidate")
    if isinstance(candidate, Mapping) and candidate.get("status") == "ready":
        normalized_candidate = deepcopy(dict(candidate))
        normalized_candidate["target"] = _canonical_target(extension)
        value["candidate"] = normalized_candidate
    error = _validation_error(_REVIEW_VALIDATOR, value, label)
    if str(error):
        raise error
    return value


def _decode_exact_object(content: str, *, label: str) -> Dict[str, Any]:
    if not isinstance(content, str) or not content:
        raise QueryContractError(f"{label} was empty")

    def object_without_duplicates(
        pairs: list[tuple[str, Any]],
    ) -> Dict[str, Any]:
        value: Dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise QueryContractError(f"{label} repeated JSON key {key!r}")
            value[key] = item
        return value

    def reject_non_json_constant(value: str) -> None:
        raise QueryContractError(f"{label} used non-JSON numeric constant {value!r}")

    try:
        value = json.loads(
            content,
            object_pairs_hook=object_without_duplicates,
            parse_constant=reject_non_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise QueryContractError(f"{label} was not valid JSON") from exc
    if not isinstance(value, dict):
        raise QueryContractError(f"{label} was not a JSON object")
    return value


_NAMED_PARAMETER = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


def _normalize_single_date_binding(
    candidate: Mapping[str, Any], question: str
) -> tuple[Dict[str, Any], bool]:
    """Repair only one unambiguous date binding; never infer general parameters."""
    normalized = deepcopy(dict(candidate))
    if normalized.get("status") != "ready":
        return normalized, False
    placeholders = list(
        dict.fromkeys(_NAMED_PARAMETER.findall(str(normalized.get("sql", ""))))
    )
    dates = list(dict.fromkeys(_ISO_DATE_LITERAL.findall(question)))
    if len(placeholders) != 1 or len(dates) != 1:
        return normalized, False
    parameters = normalized.get("parameters")
    if (
        not isinstance(parameters, list)
        or len(parameters) != 1
        or not isinstance(parameters[0], Mapping)
    ):
        return normalized, False
    parameter = parameters[0]
    if parameter.get("type") != "date" or str(parameter.get("value", "")) != dates[0]:
        return normalized, False
    expected = {
        "name": placeholders[0],
        "type": "date",
        "source": "question",
        "value": dates[0],
    }
    if normalized.get("parameters") == [expected]:
        return normalized, False
    normalized["parameters"] = [expected]
    return normalized, True


def _normalize_ordered_parameter_bindings(
    candidate: Mapping[str, Any],
) -> tuple[Dict[str, Any], bool]:
    """Pair unnamed generated parameters with SQL placeholders in order."""
    normalized = deepcopy(dict(candidate))
    if normalized.get("status") != "ready":
        return normalized, False
    parameters = normalized.get("parameters")
    if not isinstance(parameters, list) or not all(
        isinstance(parameter, dict) for parameter in parameters
    ):
        return normalized, False
    placeholders = list(
        dict.fromkeys(_NAMED_PARAMETER.findall(str(normalized.get("sql", ""))))
    )
    if len(parameters) != len(placeholders):
        return normalized, False

    changed = False
    for placeholder, parameter in zip(placeholders, parameters):
        if not parameter.get("name"):
            parameter["name"] = placeholder
            changed = True
        if not parameter.get("source"):
            parameter["source"] = "question"
            changed = True
    return normalized, changed


def _normalize_exact_duplicate_parameter_bindings(
    candidate: Mapping[str, Any],
) -> tuple[Dict[str, Any], bool]:
    """Drop exact duplicate bindings only when SQL cardinality proves the result."""
    normalized = deepcopy(dict(candidate))
    if normalized.get("status") != "ready":
        return normalized, False
    parameters = normalized.get("parameters")
    if not isinstance(parameters, list) or not all(
        isinstance(parameter, dict) for parameter in parameters
    ):
        return normalized, False
    placeholders = list(
        dict.fromkeys(_NAMED_PARAMETER.findall(str(normalized.get("sql", ""))))
    )
    unique: list[dict[str, Any]] = []
    for parameter in parameters:
        if parameter not in unique:
            unique.append(parameter)
    if len(unique) != len(placeholders) or len(unique) == len(parameters):
        return normalized, False
    normalized["parameters"] = unique
    return normalized, True


def _normalize_candidate_draft(
    content: str,
    question: str,
    extension: Mapping[str, Any],
    *,
    label: str,
) -> tuple[Dict[str, Any], bool]:
    value = _decode_exact_object(content, label=label)
    deduplicated, duplicate_normalized = _normalize_exact_duplicate_parameter_bindings(
        value
    )
    ordered, binding_normalized = _normalize_ordered_parameter_bindings(deduplicated)
    binding_normalized = binding_normalized or duplicate_normalized
    normalized, date_normalized = _normalize_single_date_binding(ordered, question)
    binding_normalized = binding_normalized or date_normalized
    grounded = _normalize_grounded_parameter_names(normalized, question, extension)
    binding_normalized = binding_normalized or grounded != normalized
    return grounded, binding_normalized


def _parse_candidate(
    content: str,
    question: str,
    extension: Mapping[str, Any],
    *,
    label: str,
) -> tuple[Dict[str, Any], bool]:
    normalized, binding_normalized = _normalize_candidate_draft(
        content, question, extension, label=label
    )
    if normalized.get("status") == "ready":
        # Target metadata is supplied by Catalyst, not inferred by the model.
        # Canonicalize it deterministically so a typo in an opaque catalog
        # digest cannot discard otherwise valid SQL or skip independent review.
        normalized["target"] = _canonical_target(extension)
    error = _validation_error(_CANDIDATE_VALIDATOR, normalized, label)
    if str(error):
        raise error
    return normalized, binding_normalized


def _canonical_target(extension: Mapping[str, Any]) -> Dict[str, Any]:
    target = extension["target"]
    views = extension["catalog"]["views"]
    return {
        "dataSource": target["dataSource"],
        "catalogVersion": target["catalogVersion"],
        "dialect": target["dialect"],
        "approvedViews": [view["name"] for view in views],
    }


def _candidate_matches_catalog(
    candidate: Mapping[str, Any], canonical_target: Mapping[str, Any]
) -> bool:
    if candidate.get("status") != "ready":
        return True
    return candidate.get("target") == canonical_target


_ISO_DATE_LITERAL = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _bind_question_date_literals(
    candidate: Mapping[str, Any], question: str
) -> Dict[str, Any]:
    """Convert exact question dates into valid named PostgreSQL parameters."""
    normalized = deepcopy(dict(candidate))
    if normalized.get("status") != "ready":
        return normalized

    sql = str(normalized["sql"])
    parameters = list(normalized["parameters"])
    existing_names = {str(parameter["name"]) for parameter in parameters}
    existing_values = {
        str(parameter.get("value"))
        for parameter in parameters
        if parameter.get("type") == "date"
    }
    date_index = 1
    for value in dict.fromkeys(_ISO_DATE_LITERAL.findall(question)):
        quoted = f"'{value}'"
        if quoted not in sql or value in existing_values:
            continue
        while f"date_{date_index}" in existing_names:
            date_index += 1
        name = f"date_{date_index}"
        placeholder = f":{name}"
        typed_date = re.compile(rf"\bDATE\s+{re.escape(quoted)}", flags=re.IGNORECASE)
        sql, replacements = typed_date.subn(placeholder, sql)
        if not replacements:
            sql = sql.replace(quoted, placeholder)
        parameters.append(
            {
                "name": name,
                "type": "date",
                "source": "question",
                "value": value,
            }
        )
        existing_names.add(name)
        existing_values.add(value)
        date_index += 1

    normalized["sql"] = sql
    normalized["parameters"] = parameters
    return normalized


def _phrase_in_question(question: str, phrase: str) -> bool:
    pattern = rf"(?<!\w){re.escape(phrase.strip())}(?!\w)"
    return re.search(pattern, question, flags=re.IGNORECASE) is not None


def _named_semantic_values(
    question: str, extension: Mapping[str, Any]
) -> list[dict[str, str]]:
    """Resolve question terms only against catalog-supplied canonical values."""
    matches: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for view in extension["catalog"]["views"]:
        for dimension in view.get("semanticDimensions") or []:
            if dimension.get("semanticType") != "analyte":
                continue
            field = str(dimension["field"])
            for value in dimension["values"]:
                canonical = str(value["canonical"])
                phrases = [canonical, *value.get("aliases", [])]
                if not any(_phrase_in_question(question, phrase) for phrase in phrases):
                    continue
                key = (field.casefold(), canonical.casefold())
                if key not in seen:
                    matches.append({"field": field, "canonical": canonical})
                    seen.add(key)
    return matches


_RESULT_SUBJECT = re.compile(
    r"^\s*(?:show|list|find)\s+(?:the\s+)?(.+?)\s+results?\b",
    re.IGNORECASE,
)
_GENERIC_RESULT_SUBJECTS = {
    "all",
    "all lab",
    "all laboratory",
    "lab",
    "lab test",
    "lab tests",
    "laboratory",
    "laboratory test",
    "laboratory tests",
    "test",
    "tests",
}
_GENERIC_RESULT_WORDS = {
    "abnormal",
    "all",
    "available",
    "final",
    "flagged",
    "lab",
    "laboratory",
    "latest",
    "negative",
    "non-numeric",
    "normal",
    "numeric",
    "patient",
    "patients",
    "pending",
    "positive",
    "recent",
    "released",
    "test",
    "tests",
}
_RESULT_SUBJECT_MODIFIERS = re.compile(
    r"^(?:(?:top|first|last|most\s+recent|latest|recent|all)\s+|\d+\s+)+",
    re.IGNORECASE,
)


def _unknown_result_analyte(question: str, extension: Mapping[str, Any]) -> str | None:
    """Detect a narrow result-name request outside catalog terminology."""
    has_analyte_terminology = any(
        dimension.get("semanticType") == "analyte" and dimension.get("values")
        for view in extension["catalog"]["views"]
        for dimension in view.get("semanticDimensions") or []
    )
    if not has_analyte_terminology:
        return None
    if _named_semantic_values(question, extension):
        return None
    match = _RESULT_SUBJECT.search(question)
    if not match:
        return None
    subject = _RESULT_SUBJECT_MODIFIERS.sub("", match.group(1).strip()).strip()
    normalized_subject = subject.casefold()
    subject_words = set(re.findall(r"[a-z]+(?:-[a-z]+)?", normalized_subject))
    if (
        not subject
        or normalized_subject in _GENERIC_RESULT_SUBJECTS
        or (subject_words and subject_words <= _GENERIC_RESULT_WORDS)
    ):
        return None
    return subject


def _semantic_binding_failures(
    candidate: Mapping[str, Any],
    question: str,
    extension: Mapping[str, Any],
) -> list[str]:
    """Require named analytes to be bound in predicates on their catalog field."""
    if candidate.get("status") != "ready":
        return []
    requirements = _named_semantic_values(question, extension)
    if not requirements:
        return []

    sql = str(candidate.get("sql", ""))
    parameters = list(candidate.get("parameters", []))
    failures: list[str] = []
    for requirement in requirements:
        field = requirement["field"]
        canonical = requirement["canonical"]
        parameter_names = [
            str(parameter.get("name"))
            for parameter in parameters
            if str(parameter.get("value", "")).casefold() == canonical.casefold()
        ]
        if not parameter_names:
            failures.append(
                f"The named analyte {canonical!r} is not bound as a parameter."
            )
            continue

        qualified_field = rf'(?:\b[A-Za-z_][A-Za-z0-9_]*\.)?"?{re.escape(field)}"?'
        bound = False
        for name in parameter_names:
            placeholder = rf":{re.escape(name)}\b"
            direct = (
                rf"(?:{qualified_field}\s*=\s*{placeholder}|"
                rf"{placeholder}\s*=\s*{qualified_field})"
            )
            membership = rf"{qualified_field}\s+IN\s*\([^)]*{placeholder}[^)]*\)"
            if re.search(direct, sql, re.IGNORECASE) or re.search(
                membership, sql, re.IGNORECASE
            ):
                bound = True
                break
        if not bound:
            failures.append(
                f"The named analyte {canonical!r} is not constrained by {field}."
            )
    return failures


def _semantic_placeholder_names(sql: str, field: str) -> set[str]:
    qualified_field = rf'(?:\b[A-Za-z_][A-Za-z0-9_]*\.)?"?{re.escape(field)}"?'
    name = r"([A-Za-z_][A-Za-z0-9_]*)"
    names = set(re.findall(rf"{qualified_field}\s*=\s*:{name}\b", sql, re.IGNORECASE))
    names.update(re.findall(rf":{name}\b\s*=\s*{qualified_field}", sql, re.IGNORECASE))
    for membership in re.findall(
        rf"{qualified_field}\s+IN\s*\(([^)]*)\)", sql, re.IGNORECASE
    ):
        names.update(_NAMED_PARAMETER.findall(membership))
    return names


_QUESTION_NUMBER_LITERAL = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?(?![\w.])")


def _numeric_value_in_question(value: Any, question: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        expected = Decimal(str(value))
    except InvalidOperation:
        return False
    question_without_dates = _ISO_DATE_LITERAL.sub(" ", question)
    for token in _QUESTION_NUMBER_LITERAL.findall(question_without_dates):
        try:
            if Decimal(token.replace(",", "")) == expected:
                return True
        except InvalidOperation:
            continue
    return False


def _parameter_value_grounded_in_question(
    parameter: Mapping[str, Any], question: str
) -> bool:
    """Verify a complete typed parameter value is stated in the question."""
    if parameter.get("source") != "question" or "value" not in parameter:
        return False
    parameter_type = parameter.get("type")
    value = parameter.get("value")
    if parameter_type == "string":
        return (
            isinstance(value, str)
            and bool(value)
            and _phrase_in_question(question, value)
        )
    if parameter_type in {"integer", "number"}:
        return _numeric_value_in_question(value, question)
    if parameter_type == "boolean":
        return isinstance(value, bool) and _phrase_in_question(
            question, str(value).lower()
        )
    if parameter_type in {"date", "date-time"}:
        return isinstance(value, str) and bool(value) and value in question
    if parameter_type == "string-list":
        return (
            isinstance(value, list)
            and bool(value)
            and all(
                isinstance(item, str)
                and bool(item)
                and _phrase_in_question(question, item)
                for item in value
            )
        )
    if parameter_type == "integer-list":
        return (
            isinstance(value, list)
            and bool(value)
            and all(_numeric_value_in_question(item, question) for item in value)
        )
    return False


def _normalize_grounded_parameter_names(
    candidate: Mapping[str, Any],
    question: str,
    extension: Mapping[str, Any],
) -> Dict[str, Any]:
    """Fill only names uniquely grounded by SQL, catalog semantics, and question."""
    normalized = deepcopy(dict(candidate))
    if normalized.get("status") != "ready":
        return normalized
    sql = str(normalized.get("sql", ""))
    placeholders = set(_NAMED_PARAMETER.findall(sql))
    parameters = list(normalized.get("parameters") or [])
    assigned = {
        str(parameter["name"])
        for parameter in parameters
        if isinstance(parameter, Mapping) and parameter.get("name")
    }

    for requirement in _named_semantic_values(question, extension):
        semantic_names = _semantic_placeholder_names(sql, requirement["field"])
        named_parameters = [
            parameter
            for parameter in parameters
            if isinstance(parameter, dict) and parameter.get("name") in semantic_names
        ]
        if len(named_parameters) == 1:
            parameter = named_parameters[0]
            parameter["value"] = requirement["canonical"]
            parameter["type"] = "string"
            parameter["source"] = "question"
            assigned.add(str(parameter["name"]))
            continue

        matching = [
            parameter
            for parameter in parameters
            if isinstance(parameter, dict)
            and str(parameter.get("value", "")).casefold()
            == requirement["canonical"].casefold()
        ]
        available = semantic_names - assigned
        unnamed_strings = [
            parameter
            for parameter in parameters
            if isinstance(parameter, dict)
            and not parameter.get("name")
            and parameter.get("type") == "string"
        ]
        if len(unnamed_strings) == 1 and len(available) == 1:
            parameter = unnamed_strings[0]
            name = available.pop()
            parameter["name"] = name
            parameter["value"] = requirement["canonical"]
            parameter["type"] = "string"
            parameter["source"] = "question"
            assigned.add(name)
            continue
        if len(matching) == 1:
            parameter = matching[0]
            existing_name = parameter.get("name")
            if existing_name in semantic_names:
                assigned.add(str(existing_name))
            elif not existing_name and len(available) == 1:
                name = available.pop()
                parameter["name"] = name
                assigned.add(name)
            parameter.setdefault("type", "string")
            parameter.setdefault("source", "question")
        elif not matching and len(available) == 1:
            name = available.pop()
            parameters.append(
                {
                    "name": name,
                    "type": "string",
                    "source": "question",
                    "value": requirement["canonical"],
                }
            )
            assigned.add(name)

    question_dates = set(_ISO_DATE_LITERAL.findall(question))
    bound_question_dates = {
        str(parameter.get("value"))
        for parameter in parameters
        if isinstance(parameter, Mapping)
        and parameter.get("name")
        and parameter.get("type") == "date"
        and str(parameter.get("value", "")) in question_dates
    }
    unbound_question_dates = question_dates - bound_question_dates
    unnamed_dates = [
        parameter
        for parameter in parameters
        if isinstance(parameter, dict)
        and not parameter.get("name")
        and parameter.get("type") == "date"
        and str(parameter.get("value", "")) in unbound_question_dates
    ]
    available = placeholders - assigned
    if len(unnamed_dates) == 1 and len(available) == 1:
        unnamed_dates[0]["name"] = available.pop()
        unnamed_dates[0].setdefault("source", "question")
    elif not unnamed_dates and len(unbound_question_dates) == 1 and len(available) == 1:
        parameters.append(
            {
                "name": available.pop(),
                "type": "date",
                "source": "question",
                "value": next(iter(unbound_question_dates)),
            }
        )

    turnaround = turnaround_threshold(question)
    if turnaround:
        _operator, threshold_minutes = turnaround
        qualified_field = (
            r'(?:\b[A-Za-z_][A-Za-z0-9_]*\.)?"?receipt_to_release_minutes"?'
        )
        name = r"([A-Za-z_][A-Za-z0-9_]*)"
        threshold_names = set(
            re.findall(rf"{qualified_field}\s*(?:>=|>)\s*:{name}\b", sql, re.IGNORECASE)
        )
        threshold_names.update(
            re.findall(rf":{name}\b\s*(?:<=|<)\s*{qualified_field}", sql, re.IGNORECASE)
        )
        if len(threshold_names) == 1:
            threshold_name = next(iter(threshold_names))
            named = [
                parameter
                for parameter in parameters
                if isinstance(parameter, dict)
                and parameter.get("name") == threshold_name
            ]
            unnamed_numeric = [
                parameter
                for parameter in parameters
                if isinstance(parameter, dict)
                and not parameter.get("name")
                and parameter.get("type") in {"integer", "number"}
            ]
            target_parameter = None
            if len(named) == 1:
                target_parameter = named[0]
            elif len(unnamed_numeric) == 1:
                target_parameter = unnamed_numeric[0]
                target_parameter["name"] = threshold_name
            elif not named and not unnamed_numeric:
                target_parameter = {"name": threshold_name}
                parameters.append(target_parameter)
            if target_parameter is not None:
                target_parameter["type"] = (
                    "integer" if threshold_minutes.is_integer() else "number"
                )
                target_parameter["source"] = "question"
                target_parameter["value"] = (
                    int(threshold_minutes)
                    if threshold_minutes.is_integer()
                    else threshold_minutes
                )

    assigned = {
        str(parameter["name"])
        for parameter in parameters
        if isinstance(parameter, Mapping) and parameter.get("name")
    }
    available = placeholders - assigned
    unnamed = [
        parameter
        for parameter in parameters
        if isinstance(parameter, dict) and not parameter.get("name")
    ]
    if (
        len(available) == 1
        and len(unnamed) == 1
        and _parameter_value_grounded_in_question(unnamed[0], question)
    ):
        unnamed[0]["name"] = next(iter(available))
        unnamed[0].setdefault("source", "question")
        assigned.add(str(unnamed[0]["name"]))

    if not placeholders - assigned:
        parameters = [
            parameter
            for parameter in parameters
            if not (isinstance(parameter, Mapping) and not parameter.get("name"))
        ]

    normalized["parameters"] = parameters
    return normalized


def _semantic_checks(
    checks: list[dict[str, Any]],
    question: str,
    extension: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not _named_semantic_values(question, extension):
        return checks
    return [
        *checks,
        {
            "name": "named_analyte_constraint",
            "status": "passed",
            "message": (
                "Every analyte named in the question is constrained by its "
                "catalog semantic dimension and canonical bound value."
            ),
        },
    ]


def _lint_validation_checks(
    history: list[dict[str, Any]], checks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    process_checks = []
    for item in history:
        codes = list(item["finding_codes"])
        process_checks.append(
            {
                "name": f"query_lint_attempt_{item['attempt']}",
                "status": "warned" if codes else "passed",
                "message": (
                    f"Deterministic correction requested for: {', '.join(codes)}."
                    if codes
                    else "Candidate passed deterministic SQL lint."
                ),
            }
        )
    return [*process_checks, *checks]


def _semantic_lint_findings(
    candidate: Mapping[str, Any],
    question: str,
    extension: Mapping[str, Any],
) -> list[dict[str, Any]]:
    requirements = _named_semantic_values(question, extension)
    findings: list[dict[str, Any]] = []
    for message in _semantic_binding_failures(candidate, question, extension):
        requirement = next(
            (
                item
                for item in requirements
                if item["canonical"].casefold() in message.casefold()
            ),
            requirements[0],
        )
        field = requirement["field"]
        canonical = requirement["canonical"]
        findings.append(
            {
                "code": "semantic.named_analyte_constraint",
                "stage": "semantic_constraints",
                "severity": "error",
                "path": "sql",
                "message": message,
                "evidence": f"field={field}; canonical={canonical}",
                "suggestedAction": (
                    f"Add a predicate on {field} using a named parameter and bind its "
                    f"string value exactly as {canonical!r}."
                ),
            }
        )
    return findings


def _contract_lint_finding(error: QueryContractError) -> dict[str, Any]:
    return {
        "code": "contract.invalid_candidate",
        "stage": "output_contract",
        "severity": "error",
        "path": "$",
        "message": str(error),
        "evidence": "candidate failed the strict JSON Schema contract",
        "suggestedAction": (
            "Return exactly one complete JSON candidate matching the supplied schema."
        ),
    }


def _patch_lint_finding(error: QueryPatchError) -> dict[str, Any]:
    return {
        "code": error.code,
        "stage": "query_correct",
        "severity": "error",
        "path": "$",
        "message": str(error),
        "evidence": "generation correction patch was rejected",
        "suggestedAction": (
            "Return only permitted patch operations against the supplied base candidate."
        ),
    }


def _missing_parameter_name_paths(
    candidate: Mapping[str, Any], extension: Mapping[str, Any]
) -> list[str]:
    """Return exact missing-name leaves only when they are the sole schema errors."""
    if candidate.get("status") != "ready":
        return []
    if candidate.get("target") != _canonical_target(extension):
        return []
    parameters = candidate.get("parameters")
    if not isinstance(parameters, list):
        return []
    errors = list(_CANDIDATE_VALIDATOR.iter_errors(candidate))
    if not errors:
        return []

    paths: list[str] = []
    for error in errors:
        absolute_path = list(error.absolute_path)
        schema_path = list(error.absolute_schema_path)
        if (
            error.validator != "required"
            or len(absolute_path) != 2
            or absolute_path[0] != "parameters"
            or not isinstance(absolute_path[1], int)
            or absolute_path[1] < 0
            or absolute_path[1] >= len(parameters)
            or schema_path[-4:] != ["properties", "parameters", "items", "required"]
        ):
            return []
        parameter = parameters[absolute_path[1]]
        if not isinstance(parameter, Mapping):
            return []
        missing = set(error.validator_value) - set(parameter)
        if missing != {"name"}:
            return []
        paths.append(f"/parameters/{absolute_path[1]}/name")

    unique_paths = sorted(set(paths))
    placeholders = set(_NAMED_PARAMETER.findall(str(candidate.get("sql", ""))))
    assigned_names = [
        str(parameter.get("name"))
        for parameter in parameters
        if isinstance(parameter, Mapping) and parameter.get("name")
    ]
    assigned = set(assigned_names)
    if (
        len(unique_paths) != 1
        or len(assigned_names) != len(assigned)
        or not assigned.issubset(placeholders)
        or len(placeholders - assigned) != 1
    ):
        return []
    return unique_paths


def _missing_name_findings(paths: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "code": "contract.parameter_name_required",
            "stage": "output_contract",
            "severity": "error",
            "path": path,
            "message": "A bound parameter is missing its required SQL placeholder name.",
            "evidence": path,
            "suggestedAction": (
                "Add exactly the SQL placeholder name for this parameter without "
                "changing any other field."
            ),
        }
        for path in paths
    ]


def _candidate_parameter_name_paths(candidate: Mapping[str, Any]) -> list[str]:
    sql_names = set(_NAMED_PARAMETER.findall(str(candidate.get("sql", ""))))
    parameters = list(candidate.get("parameters") or [])
    assigned = {
        str(parameter.get("name"))
        for parameter in parameters
        if isinstance(parameter, Mapping) and parameter.get("name") in sql_names
    }
    unbound = sql_names - assigned
    repairable_indices = [
        index
        for index, parameter in enumerate(parameters)
        if isinstance(parameter, Mapping)
        and (not parameter.get("name") or str(parameter.get("name")) not in sql_names)
    ]
    if repairable_indices and len(repairable_indices) == len(unbound):
        return [f"/parameters/{index}/name" for index in repairable_indices]
    return []


def _allowed_patch_paths(
    candidate: Mapping[str, Any], findings: list[dict[str, Any]]
) -> list[str]:
    paths: set[str] = set()
    parameter_name_paths = _candidate_parameter_name_paths(candidate)
    for finding in findings:
        code = str(finding.get("code", ""))
        path = str(finding.get("path", "")).removeprefix("$.")
        if path == "sql" or code.startswith(("catalog.", "policy.", "semantic.")):
            paths.add("/sql")
        if path == "parameters" or code.startswith("binding."):
            paths.update(parameter_name_paths)
            if not parameter_name_paths:
                paths.add("/parameters/-")
        if code in {
            "policy.unbound_predicate_literal",
            "semantic.named_analyte_constraint",
            "semantic.turnaround_threshold",
        }:
            paths.add("/parameters/-")
        if path == "expectedColumns" or code == "output.projection_mismatch":
            paths.add("/sql")
            for index, column in enumerate(candidate.get("expectedColumns") or []):
                if isinstance(column, Mapping) and "name" in column:
                    paths.add(f"/expectedColumns/{index}/name")
    return sorted(paths)


def _decode_pointer(path: str) -> list[str]:
    if not path.startswith("/"):
        raise QueryPatchError(
            "generation.patch_out_of_scope", "Patch path is not a JSON Pointer."
        )
    return [
        segment.replace("~1", "/").replace("~0", "~") for segment in path[1:].split("/")
    ]


def _apply_leaf_patch(candidate: Dict[str, Any], operation: Mapping[str, Any]) -> None:
    path = str(operation["path"])
    segments = _decode_pointer(path)
    parent: Any = candidate
    for segment in segments[:-1]:
        if isinstance(parent, list):
            try:
                parent = parent[int(segment)]
            except (ValueError, IndexError) as error:
                raise QueryPatchError(
                    "generation.patch_out_of_scope",
                    f"Patch path {path!r} does not exist in the base candidate.",
                ) from error
        elif isinstance(parent, dict) and segment in parent:
            parent = parent[segment]
        else:
            raise QueryPatchError(
                "generation.patch_out_of_scope",
                f"Patch path {path!r} does not exist in the base candidate.",
            )

    leaf = segments[-1]
    op = str(operation["op"])
    value = deepcopy(operation.get("value"))
    if isinstance(parent, list):
        if leaf == "-" and op == "add":
            parent.append(value)
            return
        try:
            index = int(leaf)
        except ValueError as error:
            raise QueryPatchError(
                "generation.patch_out_of_scope",
                f"Patch path {path!r} is not a valid list index.",
            ) from error
        if index < 0 or index >= len(parent) or op != "replace":
            raise QueryPatchError(
                "generation.patch_out_of_scope",
                f"Patch operation {op!r} cannot target {path!r}.",
            )
        parent[index] = value
        return
    if not isinstance(parent, dict):
        raise QueryPatchError(
            "generation.patch_out_of_scope",
            f"Patch path {path!r} has no object parent.",
        )
    if op == "add":
        if leaf in parent:
            raise QueryPatchError(
                "generation.patch_out_of_scope",
                f"Patch add path {path!r} already exists.",
            )
        parent[leaf] = value
        return
    if op != "replace" or leaf not in parent:
        raise QueryPatchError(
            "generation.patch_out_of_scope",
            f"Patch replace path {path!r} does not exist.",
        )
    parent[leaf] = value


def _parse_and_apply_patch(
    content: str,
    base_candidate: Mapping[str, Any],
    findings: list[dict[str, Any]],
    allowed_paths: list[str],
    *,
    required_paths: Optional[set[str]] = None,
) -> Dict[str, Any]:
    try:
        value = _decode_exact_object(content, label="query generation patch")
        error = _validation_error(_PATCH_VALIDATOR, value, "query generation patch")
        if str(error):
            raise error
    except QueryContractError as error:
        raise QueryPatchError("contract.invalid_patch", str(error)) from error

    current_codes = {str(finding.get("code")) for finding in findings}
    allowed = set(allowed_paths)
    operations = list(value["patches"])
    for operation in operations:
        if operation["findingCode"] not in current_codes:
            raise QueryPatchError(
                "generation.patch_out_of_scope",
                "Patch findingCode does not match a current deterministic finding.",
            )
        if operation["path"] not in allowed:
            raise QueryPatchError(
                "generation.patch_out_of_scope",
                f"Patch path {operation['path']!r} is outside the permitted scope.",
            )

    leaf_paths = [
        str(operation["path"])
        for operation in operations
        if operation["op"] != "replace_text"
    ]
    if len(leaf_paths) != len(set(leaf_paths)):
        raise QueryPatchError(
            "generation.patch_ambiguous",
            "Patch contains duplicate or overlapping JSON Pointer paths.",
        )
    if required_paths is not None and set(leaf_paths) != required_paths:
        raise QueryPatchError(
            "generation.patch_out_of_scope",
            "Patch must address every and only the required missing-name path.",
        )

    patched = deepcopy(dict(base_candidate))
    sql = str(patched.get("sql", ""))
    text_edits: list[tuple[int, int, str]] = []
    for operation in operations:
        if operation["op"] != "replace_text":
            continue
        old_value = str(operation["oldValue"])
        starts = [match.start() for match in re.finditer(re.escape(old_value), sql)]
        if len(starts) != 1:
            raise QueryPatchError(
                "generation.patch_ambiguous",
                f"Anchored SQL text {old_value!r} must occur exactly once.",
            )
        start = starts[0]
        end = start + len(old_value)
        if any(
            start < other_end and other_start < end
            for other_start, other_end, _ in text_edits
        ):
            raise QueryPatchError(
                "generation.patch_ambiguous",
                "SQL text patches overlap in the frozen base candidate.",
            )
        text_edits.append((start, end, str(operation["replacement"])))

    for start, end, replacement in sorted(text_edits, reverse=True):
        sql = f"{sql[:start]}{replacement}{sql[end:]}"
    if text_edits:
        patched["sql"] = sql

    for operation in operations:
        if operation["op"] != "replace_text":
            _apply_leaf_patch(patched, operation)

    if required_paths:
        parameters = list(patched.get("parameters") or [])
        repaired_indices = {int(path.split("/")[2]) for path in required_paths}
        names = [
            str(parameters[int(path.split("/")[2])].get("name", ""))
            for path in sorted(required_paths)
        ]
        placeholders = set(_NAMED_PARAMETER.findall(str(patched.get("sql", ""))))
        already_assigned_names = [
            str(parameter.get("name"))
            for index, parameter in enumerate(parameters)
            if index not in repaired_indices
            and isinstance(parameter, Mapping)
            and parameter.get("name")
        ]
        already_assigned = set(already_assigned_names)
        if (
            len(names) != len(set(names))
            or len(already_assigned_names) != len(already_assigned)
            or not already_assigned.issubset(placeholders)
            or set(names) != placeholders - already_assigned
        ):
            raise QueryPatchError(
                "generation.patch_out_of_scope",
                "Missing-name patches must map bijectively to the SQL placeholders.",
            )

    return patched


def _initial_question(extension: Mapping[str, Any]) -> Optional[str]:
    revision = extension.get("revision")
    if not isinstance(revision, Mapping):
        return None
    for item in revision.get("instructionHistory") or []:
        if isinstance(item, Mapping) and item.get("kind") == "initial":
            return str(item["instruction"])
    return None
