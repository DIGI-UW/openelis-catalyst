from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
import re
from typing import Any

from .analytics import manual_result_warnings
from .digest import canonical_sha256, utf8_sha256
from .policy import Violation


FINDING_CONTRACT_VERSION = "catalyst.workbench.finding.v1"
VALIDATION_CONTRACT_VERSION = "catalyst.workbench.validation.v1"
VALIDATOR_REVISION = "catalyst.workbench.validator.v1"

_VALIDATOR_DEFINITION = {
    "contractVersion": VALIDATION_CONTRACT_VERSION,
    "revision": VALIDATOR_REVISION,
    "sources": [
        "med_agent_hub",
        "gateway_question_policy",
        "gateway_invariant",
        "gateway_sql_policy",
    ],
    "severityOrder": ["error", "warning", "info"],
    "statusOrder": ["invalid", "warning", "valid"],
}
VALIDATOR_DIGEST = canonical_sha256(_VALIDATOR_DEFINITION)

_SEVERITY_ALIASES = {
    "error": "error",
    "failed": "error",
    "failure": "error",
    "invalid": "error",
    "rejected": "error",
    "warning": "warning",
    "warn": "warning",
    "warned": "warning",
    "info": "info",
    "informational": "info",
    "passed": "info",
}
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_REPAIRABILITY = {"deterministic", "model", "manual", "none"}
_SENSITIVE_DIAGNOSTIC = re.compile(
    r"(?ix)"
    r"(?:postgres(?:ql)?|mysql)://\S+|"
    r"\bconnection\s+to\s+server\s+at\s+(?:\"[^\"]+\"|[^\s,(]+)"
    r"(?:\s+\([^)]*\))?|"
    r"\bport\s+(?:\"[^\"]+\"|\d+)|"
    r"\b(?:for\s+)?user(?:name)?\s+(?:\"[^\"]+\"|[^\s,;]+)|"
    r"\b(?:password|passwd|pwd|user|username|host|port|dbname|database)"
    r"\s*=\s*[^\s,;]+|"
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
)


def workbench_query_digest(
    sql: str,
    parameters: list[dict[str, Any]],
    expected_columns: list[dict[str, Any]] | None = None,
) -> str:
    """Digest the exact editable query payload, independent of mutable session state."""

    return canonical_sha256(
        {
            "sql": sql,
            "parameters": parameters,
            "expectedColumns": expected_columns or [],
        }
    )


def build_revision_context(
    *,
    session: Mapping[str, Any],
    prior_turns: Iterable[Mapping[str, Any]],
    turn_id: str,
    instruction: str,
    base_classification: str,
    observed_base: Mapping[str, Any] | None,
    effective_base: Mapping[str, Any] | None,
    editor_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Build bounded, digest-bound context without rows or historical SQL copies."""

    ordered = sorted(prior_turns, key=lambda item: int(item["ordinal"]))
    session_id = str(session["sessionId"])
    if any(str(turn.get("sessionId")) != session_id for turn in ordered):
        raise ValueError("Revision history contains an unrelated session turn.")
    initial = next((turn for turn in ordered if turn["kind"] == "initial"), None)
    followups = [turn for turn in ordered if turn["kind"] == "followup"][-5:]
    included = ([initial] if initial is not None else []) + followups
    included_ids = {str(turn["turnId"]) for turn in included}
    omitted = [turn for turn in ordered if str(turn["turnId"]) not in included_ids]
    if len(omitted) > 1000:
        raise ValueError("Revision history exceeds the deterministic omission bound.")

    history = [
        {
            "turnId": str(turn["turnId"]),
            "ordinal": int(turn["ordinal"]),
            "kind": str(turn["kind"]),
            "instruction": str(turn["instruction"]),
            "instructionDigest": str(turn["instructionDigest"]),
        }
        for turn in included
    ]
    omitted_refs = [
        {
            "turnId": str(turn["turnId"]),
            "ordinal": int(turn["ordinal"]),
            "kind": str(turn["kind"]),
            "instructionDigest": str(turn["instructionDigest"]),
        }
        for turn in omitted
    ]
    editor_digest = str(editor_snapshot["editorDigest"])
    matching_validations = [
        validation
        for validation in session.get("validations", [])
        if validation.get("queryDigest") == editor_digest
    ]
    validation_context = None
    validation_ref = None
    validation_omitted = 0
    if matching_validations:
        validation = matching_validations[-1]
        findings = [
            {
                key: finding[key]
                for key in (
                    "findingId",
                    "ruleCode",
                    "severity",
                    "stage",
                    "path",
                    "message",
                )
            }
            for finding in validation.get("findings", [])[:50]
        ]
        validation_omitted = max(0, len(validation.get("findings", [])) - 50)
        validation_context = {
            "validationId": validation["validationId"],
            "versionId": validation["versionId"],
            "queryDigest": validation["queryDigest"],
            "status": validation["status"],
            "validatorRevision": validation["validatorRevision"],
            "validatorDigest": validation["validatorDigest"],
            "findings": findings,
        }
        validation_ref = {
            key: validation[key] for key in ("validationId", "versionId", "queryDigest")
        }

    matching_executions = [
        execution
        for execution in session.get("executions", [])
        if execution.get("queryDigest") == editor_digest
    ]
    execution_context = None
    execution_ref = None
    execution_columns_omitted = 0
    diagnostic_truncated = False
    if matching_executions:
        execution = matching_executions[-1]
        result = (
            execution.get("result") if isinstance(execution.get("result"), dict) else {}
        )
        raw_columns = result.get("columns") if isinstance(result, dict) else []
        raw_columns = raw_columns if isinstance(raw_columns, list) else []
        execution_columns_omitted = max(0, len(raw_columns) - 128)
        columns = [
            {
                "ordinal": int(column["ordinal"]),
                "name": str(column["name"]),
                "databaseType": str(column["databaseType"]),
                "logicalType": str(column["logicalType"]),
            }
            for column in raw_columns[:128]
            if isinstance(column, Mapping)
            and all(
                key in column
                for key in ("ordinal", "name", "databaseType", "logicalType")
            )
        ]
        if "warnings" in result:
            raw_warnings = result.get("warnings")
            raw_warnings = raw_warnings if isinstance(raw_warnings, list) else []
        else:
            raw_rows = result.get("rows")
            rows = (
                [
                    row
                    for row in raw_rows
                    if isinstance(row, (list, tuple))
                    and all(isinstance(cell, Mapping) for cell in row)
                ]
                if isinstance(raw_rows, list)
                else []
            )
            row_count = result.get("rowCount")
            raw_warnings = manual_result_warnings(
                raw_columns,
                rows,
                truncated=(
                    bool(row_count.get("truncated"))
                    if isinstance(row_count, Mapping)
                    else False
                ),
            )
        execution_warnings = [
            _SENSITIVE_DIAGNOSTIC.sub("[redacted]", warning)[:2000]
            for warning in raw_warnings[:8]
            if isinstance(warning, str) and warning.strip()
        ]
        diagnostic = execution.get("databaseDiagnostic")
        if isinstance(diagnostic, Mapping):
            bounded_diagnostic: dict[str, Any] = {}
            for key in (
                "sqlstate",
                "severity",
                "message",
                "detail",
                "hint",
                "position",
            ):
                value = diagnostic.get(key)
                if key in {"message", "detail", "hint"} and isinstance(value, str):
                    value = _SENSITIVE_DIAGNOSTIC.sub("[redacted]", value)
                    if len(value) > 4000:
                        diagnostic_truncated = True
                    value = value[:4000]
                bounded_diagnostic[key] = value
            diagnostic = bounded_diagnostic
        else:
            diagnostic = None
        execution_context = {
            "executionId": execution["executionId"],
            "versionId": execution["versionId"],
            "queryDigest": execution["queryDigest"],
            "status": execution["status"],
            "validationStatus": execution.get("validationStatus", "not_run"),
            "rowCount": result.get("rowCount") if isinstance(result, dict) else None,
            "columns": columns,
            "warnings": execution_warnings,
            "databaseDiagnostic": diagnostic,
            "durationMs": int(execution.get("durationMs") or 0),
        }
        execution_ref = {
            key: execution[key] for key in ("executionId", "versionId", "queryDigest")
        }

    selection = {
        "includedHistoryTurnIds": [item["turnId"] for item in history],
        "validationRef": validation_ref,
        "executionRef": execution_ref,
        "omissions": {
            "historyInstructionsOmitted": len(omitted_refs),
            "validationFindingsOmitted": validation_omitted,
            "executionColumnsOmitted": execution_columns_omitted,
            "diagnosticTextTruncated": diagnostic_truncated,
            "prohibitedClasses": [
                "database_credentials",
                "database_connection_details",
                "database_dsn",
                "execution_result_rows",
                "hidden_reasoning",
                "historical_sql_copies",
                "raw_chat_transcript",
                "raw_model_outputs",
                "raw_reasoning_traces",
                "unrelated_session_history",
                "unrelated_historical_sql",
            ],
            "omittedHistory": omitted_refs,
            "omittedHistoryDigest": canonical_sha256(omitted_refs),
        },
    }
    context = {
        "contractVersion": "catalyst.query.revision-context.v1",
        "turnId": turn_id,
        "currentInstruction": instruction,
        "instructionDigest": utf8_sha256(instruction),
        "baseClassification": base_classification,
        "observedBase": dict(observed_base) if observed_base is not None else None,
        "effectiveBaseVersion": (
            dict(effective_base) if effective_base is not None else None
        ),
        "editorSnapshot": dict(editor_snapshot),
        "instructionHistory": history,
        "validationContext": validation_context,
        "executionContext": execution_context,
        "selection": selection,
        "contextDigest": "0" * 64,
    }
    context["contextDigest"] = canonical_sha256(
        {key: value for key, value in context.items() if key != "contextDigest"}
    )
    return context


def normalize_findings(
    findings: Iterable[Violation | Mapping[str, Any]],
    *,
    query_digest: str,
    default_stage: str = "gateway",
    default_severity: str = "error",
    validator_revision: str = VALIDATOR_REVISION,
) -> list[dict[str, Any]]:
    """Normalize Hub and Gateway findings into one deterministic advisory shape.

    The result is deliberately presentation- and transport-independent. Finding IDs
    are content-addressed to the exact query so a repeated validation of the same
    draft produces the same IDs.
    """

    canonical: dict[str, dict[str, Any]] = {}
    for raw in findings:
        source = _finding_mapping(raw)
        stage = str(source.get("stage") or default_stage).strip() or default_stage
        raw_code = str(
            source.get("ruleCode") or source.get("code") or "unknown"
        ).strip()
        rule_code = raw_code if "." in raw_code else f"{stage}.{raw_code}"
        severity = _normalize_severity(
            source.get("severity") or source.get("status") or default_severity
        )
        message = str(source.get("message") or "Validation finding.").strip()
        path = str(source.get("path") or "$.sql")
        suggested_action = source.get("suggestedAction")
        if suggested_action is None:
            suggested_action = source.get("suggested_action")
        repairability = _normalize_repairability(source.get("repairability"))
        evidence = _bounded_json(source.get("evidence"))
        ast_unit = _bounded_json(source.get("astUnit", source.get("ast_unit")))
        span = _bounded_json(source.get("span"))

        identity = {
            "queryDigest": query_digest,
            "ruleCode": rule_code,
            "severity": severity,
            "stage": stage,
            "message": message,
            "path": path,
            "astUnit": ast_unit,
            "span": span,
            "evidence": evidence,
        }
        fingerprint = canonical_sha256(identity)
        normalized = {
            "contractVersion": FINDING_CONTRACT_VERSION,
            "findingId": f"finding-{fingerprint[:24]}",
            "ruleCode": rule_code,
            "severity": severity,
            "stage": stage,
            "message": message,
            "path": path,
            "astUnit": ast_unit,
            "span": span,
            "evidence": evidence,
            "suggestedAction": (
                str(suggested_action).strip() if suggested_action is not None else None
            ),
            "repairability": repairability,
            "validatorRevision": validator_revision,
        }
        canonical[fingerprint] = normalized

    return sorted(
        canonical.values(),
        key=lambda item: (
            _SEVERITY_ORDER[item["severity"]],
            item["stage"],
            item["ruleCode"],
            item["path"],
            item["message"],
        ),
    )


def build_advisory_validation(
    *,
    query_digest: str,
    findings: Iterable[Mapping[str, Any]],
    duration_ms: int = 0,
    validator_revision: str = VALIDATOR_REVISION,
    validator_digest: str = VALIDATOR_DIGEST,
) -> dict[str, Any]:
    """Build the immutable validation payload stored alongside a query version."""

    canonical_findings = [dict(finding) for finding in findings]
    by_stage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in canonical_findings:
        by_stage[str(finding["stage"])].append(finding)

    checks: list[dict[str, Any]] = []
    for stage in sorted(by_stage):
        stage_findings = by_stage[stage]
        severities = {finding["severity"] for finding in stage_findings}
        status = (
            "failed"
            if "error" in severities
            else "warned"
            if "warning" in severities
            else "passed"
        )
        checks.append(
            {
                "name": stage,
                "status": status,
                "findingIds": [finding["findingId"] for finding in stage_findings],
            }
        )

    severities = {finding["severity"] for finding in canonical_findings}
    aggregate_status = (
        "invalid"
        if "error" in severities
        else "warning"
        if "warning" in severities
        else "valid"
    )
    return {
        "contractVersion": VALIDATION_CONTRACT_VERSION,
        "queryDigest": query_digest,
        "validatorRevision": validator_revision,
        "validatorDigest": validator_digest,
        "status": aggregate_status,
        "advisory": True,
        "checks": checks,
        "findings": canonical_findings,
        "durationMs": max(0, int(duration_ms)),
    }


def _finding_mapping(raw: Violation | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(raw, Violation):
        return raw.as_dict()
    return raw


def _normalize_severity(value: Any) -> str:
    return _SEVERITY_ALIASES.get(str(value).casefold(), "error")


def _normalize_repairability(value: Any) -> str:
    if isinstance(value, bool):
        return "model" if value else "none"
    normalized = str(value or "manual").casefold()
    return normalized if normalized in _REPAIRABILITY else "manual"


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    """Keep evidence useful without allowing unbounded model/database payloads."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:1000]
    if depth >= 4:
        return str(value)[:1000]
    if isinstance(value, Mapping):
        items = list(value.items())[:25]
        return {
            str(key)[:200]: _bounded_json(item, depth=depth + 1) for key, item in items
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_json(item, depth=depth + 1) for item in value[:25]]
    return str(value)[:1000]
