"""FHIR-backed MCP tools for the Catalyst FHIR sidecar POC (feature 011).

Reads OE2's embedded FHIR provider (see ../config.py:FhirConfig) — the
verified-working primary surface for this POC, not OE2's separate HAPI FHIR
sidecar container (see specs/011-catalyst-fhir-sidecar-poc/research.md items
3 and 5 for why).

Contract: specs/011-catalyst-fhir-sidecar-poc/contracts/catalyst_mcp_tools.schema.yaml

Every tool returns a plain dict shaped as either:
  - a real FHIR resource / Bundle (success), or
  - {"error": "<code>", "detail": "<human-readable>"} (failure)
so the calling agent can convert a failure into an honest abstention
(spec FR-008) instead of raising an unstructured exception up to the
reviewer.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from ..config import FhirConfig, load_fhir_config

_ERROR_UNREACHABLE = "fhir_surface_unreachable"
_ERROR_NOT_FOUND = "resource_not_found"
_ERROR_BAD_REFERENCE = "invalid_reference"


def _client(config: FhirConfig) -> httpx.Client:
    return httpx.Client(
        base_url=config.base_url,
        auth=(config.username, config.password),
        timeout=config.timeout_s,
        verify=config.verify_tls,
        headers={"Accept": "application/fhir+json"},
    )


def _get(config: FhirConfig, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    try:
        with _client(config) as client:
            response = client.get(path, params=params or {})
    except httpx.HTTPError as exc:
        return {
            "error": _ERROR_UNREACHABLE,
            "detail": f"OE2 embedded FHIR endpoint unreachable: {exc!r}",
        }

    if response.status_code == 404:
        return {"error": _ERROR_NOT_FOUND, "detail": f"{path} not found"}
    if response.status_code >= 400:
        return {
            "error": _ERROR_UNREACHABLE,
            "detail": f"HTTP {response.status_code} from {path}: {response.text[:500]}",
        }

    return response.json()


def search_patient(query: str) -> dict[str, Any]:
    """Search for patients by name or identifier. Returns a FHIR searchset Bundle.

    Tries `identifier=` first (matches OE2's national-ID-style identifiers
    like "E2E-PAT-001", which never appear in `name`), falling back to
    `name=` (matches family/given text) when the identifier search finds
    nothing — a single FHIR search call cannot OR across both parameters.
    """
    config = load_fhir_config()
    by_identifier = _get(config, "/Patient", params={"identifier": query})
    if by_identifier.get("total"):
        return by_identifier
    return _get(config, "/Patient", params={"name": query})


def get_patient_context(patient_id: str) -> dict[str, Any]:
    """Demographic + identifier summary for a single patient."""
    config = load_fhir_config()
    return _get(config, f"/Patient/{patient_id}")


def get_service_requests(
    patient_id: str,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
) -> dict[str, Any]:
    """Lab orders (ServiceRequest) for a patient; optional date range."""
    config = load_fhir_config()
    params: dict[str, Any] = {"patient": patient_id}
    if date_start:
        params["date"] = [f"ge{date_start}"] + ([f"le{date_end}"] if date_end else [])
    return _get(config, "/ServiceRequest", params=params)


def get_observations(patient_id: str, test_code: Optional[str] = None) -> dict[str, Any]:
    """Lab result Observation resources for a patient; optional test-code filter."""
    config = load_fhir_config()
    params: dict[str, Any] = {"patient": patient_id}
    if test_code:
        params["code"] = test_code
    return _get(config, "/Observation", params=params)


def get_diagnostic_reports(patient_id: str) -> dict[str, Any]:
    """DiagnosticReport resources for a patient.

    Uses the `subject` search parameter, not `patient` — OE2's embedded
    DiagnosticReport provider's CapabilityStatement (verified locally) only
    supports {_id, _lastUpdated, date, status, subject}, unlike
    Observation/ServiceRequest which both accept `patient` as an alias.
    """
    config = load_fhir_config()
    return _get(config, "/DiagnosticReport", params={"subject": patient_id})


def get_resource_by_reference(reference: str) -> dict[str, Any]:
    """Resolve an arbitrary FHIR reference, e.g. 'Observation/12345'."""
    if "/" not in reference or reference.count("/") != 1:
        return {
            "error": _ERROR_BAD_REFERENCE,
            "detail": f"expected 'ResourceType/id', got {reference!r}",
        }
    config = load_fhir_config()
    return _get(config, f"/{reference}")


def build_patient_lab_timeline(patient_id: str) -> dict[str, Any]:
    """Chronological merge of Observation + DiagnosticReport for a patient.

    Returns {"events": [...]} on success — each event shaped per
    data-model.md's LabTimelineEvent — or {"error": ...} if either read fails
    outright (a resource type simply having zero entries is not an error;
    an unreachable surface is).
    """
    obs = get_observations(patient_id)
    reports = get_diagnostic_reports(patient_id)

    if "error" in obs and obs["error"] == _ERROR_UNREACHABLE:
        return obs
    if "error" in reports and reports["error"] == _ERROR_UNREACHABLE:
        return reports

    events: list[dict[str, Any]] = []
    for entry in obs.get("entry", []) if "error" not in obs else []:
        resource = entry.get("resource", {})
        events.append(_observation_to_event(resource))
    for entry in reports.get("entry", []) if "error" not in reports else []:
        resource = entry.get("resource", {})
        events.append(_diagnostic_report_to_event(resource))

    events.sort(key=lambda e: e.get("date") or "")
    return {"events": events}


def _observation_to_event(resource: dict[str, Any]) -> dict[str, Any]:
    interpretation = (resource.get("interpretation") or [{}])[0]
    coding = (interpretation.get("coding") or [{}])[0]
    flag_code = coding.get("code")
    flag = "abnormal" if flag_code and flag_code not in ("N", None) else ("normal" if flag_code == "N" else None)
    return {
        "date": resource.get("effectiveDateTime"),
        "resourceType": "Observation",
        "id": resource.get("id"),
        "display": (resource.get("code") or {}).get("text")
        or ((resource.get("code") or {}).get("coding") or [{}])[0].get("display")
        or "Observation",
        "flag": flag,
    }


def _diagnostic_report_to_event(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": resource.get("effectiveDateTime") or resource.get("issued"),
        "resourceType": "DiagnosticReport",
        "id": resource.get("id"),
        "display": (resource.get("code") or {}).get("text")
        or ((resource.get("code") or {}).get("coding") or [{}])[0].get("display")
        or "DiagnosticReport",
        "flag": None,
    }
