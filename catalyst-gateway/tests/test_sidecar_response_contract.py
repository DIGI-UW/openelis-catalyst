"""Contract test: catalyst-gateway's /v1/chat/completions response for a
FHIR sidecar question validates against
specs/011-catalyst-fhir-sidecar-poc/contracts/sidecar_response.schema.json.

Exercises the real running local stack (gateway -> A2A -> catalyst-agents ->
MCP -> OE2 embedded FHIR), the same way the rest of this submodule's tests
verify real behavior rather than mocks. Requires the harness's Catalyst +
OpenELIS-Global-2 quickstart to be running locally (gateway on :8000).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import jsonschema
import pytest

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[4]
    / "specs"
    / "011-catalyst-fhir-sidecar-poc"
    / "contracts"
    / "sidecar_response.schema.json"
)
_GATEWAY_URL = "http://localhost:8000/v1/chat/completions"


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _ask(question: str) -> dict:
    try:
        response = httpx.post(
            _GATEWAY_URL,
            json={"model": "catalyst", "messages": [{"role": "user", "content": question}]},
            timeout=90.0,
        )
    except httpx.HTTPError as exc:
        pytest.skip(f"catalyst-gateway not reachable at {_GATEWAY_URL}: {exc!r}")
    response.raise_for_status()
    return response.json()


def test_schema_file_is_valid_json_schema():
    schema = _load_schema()
    jsonschema.Draft202012Validator.check_schema(schema)


def test_abstention_response_validates_against_contract():
    """A question with no matching FHIR data (the currently-real state for
    ServiceRequest — see spec.md Assumptions) is the easiest real response to
    get deterministically, so it's the primary contract-conformance check."""
    schema = _load_schema()
    payload = _ask("What tests were ordered for patient E2E-PAT-001?")

    jsonschema.validate(instance=payload, schema=schema)
    assert payload["citations"] == []
    assert payload["choices"][0]["message"]["content"] == payload["answer"]


def test_abstention_provenance_still_records_patient_resolved_before_abstaining():
    """Provenance must show what was actually checked, even on abstention —
    not a blanked-out no-op (see fhir_grounding._no_data_response docstring)."""
    schema = _load_schema()
    payload = _ask("What tests were ordered for patient E2E-PAT-001?")
    jsonschema.validate(instance=payload, schema=schema)

    assert "Patient/60d5288c-c7ff-4651-b5d0-eea03fb75090" in payload["provenance"]["resource_ids"]
    assert "search_patient" in payload["provenance"]["tools_called"]
