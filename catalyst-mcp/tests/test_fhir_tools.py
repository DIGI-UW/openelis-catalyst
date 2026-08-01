"""Tests for the FHIR-backed MCP tools (feature 011).

Run live against the local OE2 embedded FHIR provider (the primary surface —
see specs/011-catalyst-fhir-sidecar-poc/research.md items 3 and 5), the same
way test_mcp_tools.py exercises schema_tools against real state rather than
mocks. Requires the harness's OpenELIS-Global-2 sibling-checkout quickstart
to have been run (docker compose up, fixtures loaded, FHIR backfill
triggered) and OE2_FHIR_* configured in .env — both already true when tests
run via ./tests/run_tests.sh, which sources .env before invoking pytest.

Fixture patients used below (E2E-PAT-001/002/003, family names all prefixed
"TEST-") come from OE2's own --profile=harness E2E fixtures, not data this
feature seeds.
"""

from __future__ import annotations

import pytest

from src.tools import fhir_tools

_KNOWN_PATIENT_ID = "60d5288c-c7ff-4651-b5d0-eea03fb75090"  # E2E-PAT-001 / TEST-Smith
_KNOWN_PATIENT_IDENTIFIER = "E2E-PAT-001"
_VALID_FORMAT_UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"


def _skip_if_fhir_unreachable(result: dict) -> None:
    if result.get("error") == "fhir_surface_unreachable":
        pytest.skip(
            f"OE2 embedded FHIR endpoint unreachable in this environment: {result.get('detail')}"
        )


class TestSearchPatient:
    def test_finds_fixture_patient_by_identifier(self):
        result = fhir_tools.search_patient(_KNOWN_PATIENT_IDENTIFIER)
        _skip_if_fhir_unreachable(result)
        assert result["resourceType"] == "Bundle"
        assert result["total"] == 1
        assert result["entry"][0]["resource"]["id"] == _KNOWN_PATIENT_ID

    def test_finds_fixture_patient_by_name(self):
        result = fhir_tools.search_patient("TEST-Smith")
        _skip_if_fhir_unreachable(result)
        assert result["total"] >= 1

    def test_ambiguous_query_returns_all_matches_not_one(self):
        """A query matching multiple patients must return all of them — the
        system must not silently pick one (spec Edge Cases)."""
        result = fhir_tools.search_patient("TEST")
        _skip_if_fhir_unreachable(result)
        assert result["total"] >= 3, "expected all 3 fixture patients (shared TEST- family prefix)"

    def test_no_match_returns_empty_bundle_not_error(self):
        result = fhir_tools.search_patient("no-such-patient-exists-zzz")
        _skip_if_fhir_unreachable(result)
        assert result["resourceType"] == "Bundle"
        assert result["total"] == 0


class TestGetPatientContext:
    def test_returns_patient_resource_for_known_id(self):
        result = fhir_tools.get_patient_context(_KNOWN_PATIENT_ID)
        _skip_if_fhir_unreachable(result)
        assert result["resourceType"] == "Patient"
        assert result["id"] == _KNOWN_PATIENT_ID

    def test_valid_format_unknown_id_returns_explicit_not_found(self):
        """Must not raise or return a fabricated/default resource."""
        result = fhir_tools.get_patient_context(_VALID_FORMAT_UNKNOWN_ID)
        if result.get("error") == "fhir_surface_unreachable":
            pytest.skip(f"OE2 unreachable: {result.get('detail')}")
        assert result.get("error") == "resource_not_found"


class TestGetObservations:
    def test_returns_bundle_shape_for_known_patient(self):
        """Fixture data currently has zero synced Observations for any patient
        (documented data gap, spec.md Assumptions) — asserting bundle shape,
        not a nonzero count, is the correct test today."""
        result = fhir_tools.get_observations(_KNOWN_PATIENT_ID)
        _skip_if_fhir_unreachable(result)
        assert result["resourceType"] == "Bundle"
        assert "total" in result

    def test_test_code_filter_does_not_error(self):
        result = fhir_tools.get_observations(_KNOWN_PATIENT_ID, test_code="GLUC")
        _skip_if_fhir_unreachable(result)
        assert result["resourceType"] == "Bundle"


class TestGetServiceRequests:
    def test_returns_bundle_shape_for_known_patient(self):
        result = fhir_tools.get_service_requests(_KNOWN_PATIENT_ID)
        _skip_if_fhir_unreachable(result)
        assert result["resourceType"] == "Bundle"
        assert "total" in result


class TestGetDiagnosticReports:
    def test_returns_bundle_shape_for_known_patient(self):
        """Uses `subject=`, not `patient=` — see fhir_tools.get_diagnostic_reports
        docstring for why (verified via OE2's own CapabilityStatement)."""
        result = fhir_tools.get_diagnostic_reports(_KNOWN_PATIENT_ID)
        _skip_if_fhir_unreachable(result)
        assert result["resourceType"] == "Bundle"
        assert "total" in result


class TestGetResourceByReference:
    def test_resolves_known_resource(self):
        result = fhir_tools.get_resource_by_reference(f"Patient/{_KNOWN_PATIENT_ID}")
        _skip_if_fhir_unreachable(result)
        assert result["resourceType"] == "Patient"
        assert result["id"] == _KNOWN_PATIENT_ID

    def test_unresolvable_reference_returns_explicit_not_found(self):
        result = fhir_tools.get_resource_by_reference(f"Patient/{_VALID_FORMAT_UNKNOWN_ID}")
        if result.get("error") == "fhir_surface_unreachable":
            pytest.skip(f"OE2 unreachable: {result.get('detail')}")
        assert result.get("error") == "resource_not_found"

    def test_malformed_reference_returns_invalid_reference_error(self):
        result = fhir_tools.get_resource_by_reference("not-a-valid-reference")
        assert result == {
            "error": "invalid_reference",
            "detail": "expected 'ResourceType/id', got 'not-a-valid-reference'",
        }


class TestBuildPatientLabTimeline:
    def test_returns_events_list_shape(self):
        result = fhir_tools.build_patient_lab_timeline(_KNOWN_PATIENT_ID)
        if result.get("error") == "fhir_surface_unreachable":
            pytest.skip(f"OE2 unreachable: {result.get('detail')}")
        assert "events" in result
        assert isinstance(result["events"], list)

    def test_events_are_chronologically_sorted(self):
        result = fhir_tools.build_patient_lab_timeline(_KNOWN_PATIENT_ID)
        if result.get("error") == "fhir_surface_unreachable":
            pytest.skip(f"OE2 unreachable: {result.get('detail')}")
        dates = [e["date"] for e in result["events"] if e.get("date")]
        assert dates == sorted(dates)
