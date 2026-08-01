"""HAPI/embedded FHIR parity probe (feature 011, Story 4).

Replays the underlying FHIR reads behind the five canonical questions
against OE2's HAPI FHIR sidecar and compares to the already-grounded
embedded-surface result (fhir_tools.py — the answer path, Story 1). Every
divergence, including the HAPI surface being wholesale unreachable, is
recorded as a non-blocking Gap-Log Entry (see
specs/011-catalyst-fhir-sidecar-poc/data-model.md) — it never invalidates or
hides the original embedded-grounded answer (spec FR-011).

This module reads HAPI directly (not through the MCP tool layer) since HAPI
is diagnostic-only here, not a tool the agent calls to answer questions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import httpx

from ..config import HapiConfig, load_hapi_config
from . import fhir_tools

# The resource types the five canonical questions actually require (spec
# "Canonical Question" table) — Specimen is deliberately excluded, per spec
# Assumptions: no canonical question requires it as a primary resource.
_PROBED_RESOURCE_TYPES = (
    "Patient",
    "ServiceRequest",
    "Observation",
    "DiagnosticReport",
)


def _hapi_get(config: HapiConfig, path: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        with httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_s,
            verify=config.verify_tls,
            headers={"Accept": "application/fhir+json"},
        ) as client:
            response = client.get(path, params=params)
    except httpx.HTTPError as exc:
        return {"error": "fhir_surface_unreachable", "detail": repr(exc)}

    if response.status_code >= 400:
        return {
            "error": "fhir_surface_unreachable",
            "detail": f"HTTP {response.status_code}: {response.text[:300]}",
        }
    return response.json()


def _embedded_read(resource_type: str, patient_id: str) -> dict[str, Any]:
    if resource_type == "Patient":
        return fhir_tools.get_patient_context(patient_id)
    if resource_type == "ServiceRequest":
        return fhir_tools.get_service_requests(patient_id)
    if resource_type == "Observation":
        return fhir_tools.get_observations(patient_id)
    if resource_type == "DiagnosticReport":
        return fhir_tools.get_diagnostic_reports(patient_id)
    raise ValueError(f"unprobed resource type: {resource_type}")


def _hapi_read(config: HapiConfig, resource_type: str, patient_id: str) -> dict[str, Any]:
    if resource_type == "Patient":
        return _hapi_get(config, f"/Patient/{patient_id}", {})
    param = "subject" if resource_type == "DiagnosticReport" else "patient"
    return _hapi_get(config, f"/{resource_type}", {param: patient_id})


def _status(result: dict[str, Any]) -> str:
    if result.get("error") == "fhir_surface_unreachable":
        return "error"
    if result.get("error") == "resource_not_found":
        return "absent"
    if "total" in result and result["total"] == 0:
        return "absent"
    return "present"


def _describe_divergence(hapi_status: str, embedded_status: str, hapi_result: dict[str, Any]) -> str:
    if hapi_status == "error":
        return f"HAPI surface unreachable: {hapi_result.get('detail', 'unknown error')}"
    return f"HAPI reports {hapi_status}, embedded reports {embedded_status}"


def probe_patient(patient_id: str) -> list[dict[str, Any]]:
    """Runs the parity probe for one patient across all probed resource
    types. Returns a list of Gap-Log Entry dicts — empty if every resource
    type matched (spec Story 4 Acceptance Scenario 1)."""
    config = load_hapi_config()
    gap_log: list[dict[str, Any]] = []

    for question_num, resource_type in enumerate(_PROBED_RESOURCE_TYPES, start=1):
        embedded_result = _embedded_read(resource_type, patient_id)
        hapi_result = _hapi_read(config, resource_type, patient_id)

        embedded_status = _status(embedded_result)
        hapi_status = _status(hapi_result)

        if embedded_status == hapi_status:
            continue  # identical on both surfaces — no gap entry (Acceptance Scenario 1)

        gap_log.append(
            {
                "question_num": question_num,
                "resource_type": resource_type,
                "resource_id": patient_id if resource_type == "Patient" else None,
                "hapi_status": hapi_status,
                "embedded_status": embedded_status,
                "divergence": _describe_divergence(hapi_status, embedded_status, hapi_result),
                "blocking": False,
            }
        )

    return gap_log


def write_gap_log(gap_log_path: Path, entries: list[dict[str, Any]]) -> None:
    """Appends Gap-Log Entries as JSONL, per data-model.md's
    artifacts/<run_id>/catalyst_gap_log.jsonl convention — mirrors the
    harness's own results.jsonl append-only shape."""
    gap_log_path.parent.mkdir(parents=True, exist_ok=True)
    with gap_log_path.open("a", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def probe_specimen_gap() -> dict[str, Any]:
    """Spec Story 4 Acceptance Scenario 3: Specimen has no dedicated embedded
    FHIR provider. Documented unconditionally (not read-dependent) since no
    canonical question requires it and this is a known structural gap, not
    a per-patient divergence."""
    return {
        "question_num": None,
        "resource_type": "Specimen",
        "resource_id": None,
        "hapi_status": "unknown",
        "embedded_status": "no_dedicated_provider",
        "divergence": (
            "OE2's embedded FHIR providers have no dedicated Specimen resource "
            "(available only partially via the subscription/transform pipeline). "
            "None of the five canonical questions require Specimen as a primary "
            "resource, so this does not block the POC (spec Assumptions)."
        ),
        "blocking": False,
    }
