from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from .digest import canonical_sha256
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
