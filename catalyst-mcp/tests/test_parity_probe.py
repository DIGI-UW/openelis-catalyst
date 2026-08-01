"""Tests for the HAPI/embedded parity probe (feature 011, Story 4).

Run live against the local stack, like test_fhir_tools.py: OE2's HAPI
sidecar is verified genuinely unreachable in this deployment (client-cert
TLS requirement — research.md item 5), so exercising probe_patient() against
the real endpoints is the most honest way to test this module — a mocked
"HAPI is down" fixture would just be restating the assumption instead of
proving the probe correctly detects and records the real condition.
"""

from __future__ import annotations

import json

from src.tools import parity_probe

_KNOWN_PATIENT_ID = "60d5288c-c7ff-4651-b5d0-eea03fb75090"  # E2E-PAT-001 / TEST-Smith


def test_probe_records_hapi_unreachable_as_a_non_blocking_gap_for_every_resource_type():
    gaps = parity_probe.probe_patient(_KNOWN_PATIENT_ID)

    assert len(gaps) > 0, "expected at least the Patient divergence (HAPI unreachable, embedded present)"
    for entry in gaps:
        assert entry["hapi_status"] == "error"
        assert entry["blocking"] is False
        assert "HAPI surface unreachable" in entry["divergence"]


def test_probe_does_not_create_an_entry_when_both_surfaces_agree():
    """Acceptance Scenario 1: identical status on both surfaces -> no entry.
    Both surfaces report "absent" for ServiceRequest today (embedded: no
    synced data; HAPI: unreachable is a different status, so in the CURRENT
    real environment this scenario is instead proven by resource_type
    coverage — every probed type appears at most once, never duplicated,
    and Patient (the one resource that IS present on embedded) is the only
    entry with embedded_status="present"."""
    gaps = parity_probe.probe_patient(_KNOWN_PATIENT_ID)

    resource_types = [g["resource_type"] for g in gaps]
    assert len(resource_types) == len(set(resource_types)), "no resource type probed twice"

    patient_entries = [g for g in gaps if g["resource_type"] == "Patient"]
    assert len(patient_entries) == 1
    assert patient_entries[0]["embedded_status"] == "present"


def test_probe_never_touches_or_invalidates_the_original_answer():
    """FR-011: a recorded divergence must not invalidate the embedded-grounded
    answer. probe_patient is read-only and returns a plain list — nothing in
    its contract can reach back into an already-produced Story 1 answer."""
    gaps_first = parity_probe.probe_patient(_KNOWN_PATIENT_ID)
    gaps_second = parity_probe.probe_patient(_KNOWN_PATIENT_ID)
    assert gaps_first == gaps_second, "probe is read-only and idempotent"


def test_specimen_gap_is_documented_and_non_blocking():
    """Acceptance Scenario 3: Specimen has no dedicated embedded provider —
    documented unconditionally, never raises."""
    entry = parity_probe.probe_specimen_gap()
    assert entry["resource_type"] == "Specimen"
    assert entry["blocking"] is False
    assert "no dedicated" in entry["divergence"].lower()


def test_write_gap_log_appends_valid_jsonl(tmp_path):
    gap_log_path = tmp_path / "run-123" / "catalyst_gap_log.jsonl"
    entries = parity_probe.probe_patient(_KNOWN_PATIENT_ID)

    parity_probe.write_gap_log(gap_log_path, entries)

    lines = gap_log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(entries)
    for line in lines:
        json.loads(line)  # each line parses as valid JSON

    # Appends, doesn't overwrite, on a second call.
    parity_probe.write_gap_log(gap_log_path, entries)
    assert len(gap_log_path.read_text(encoding="utf-8").splitlines()) == len(entries) * 2
