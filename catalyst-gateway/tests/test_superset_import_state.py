from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load_state_module():
    path = ROOT / "scripts/superset-import-state.py"
    spec = importlib.util.spec_from_file_location(
        "catalyst_superset_import_state", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_json_is_stable_and_rejects_floats() -> None:
    state = _load_state_module()

    assert state.canonical_json_bytes({"z": 2, "a": [True, None, "é"]}) == (
        b'{"a":[true,null,"\xc3\xa9"],"z":2}'
    )
    with pytest.raises(ValueError, match="floating-point"):
        state.canonical_json_bytes({"unsafe": 1.25})


def test_atomic_json_write_and_digest_are_reproducible(tmp_path: Path) -> None:
    state = _load_state_module()
    path = tmp_path / "nested/state.json"
    payload = {"schemaVersion": "example.v1", "value": "same"}

    first = state.atomic_write_json(path, payload)
    second = state.atomic_write_json(path, payload)

    assert first == second == state.sha256_hex(state.canonical_json_bytes(payload))
    assert json.loads(path.read_text()) == payload
    assert not list(path.parent.glob("*.tmp-*"))


def test_import_lock_is_nonblocking_and_carries_bounded_descriptor(
    tmp_path: Path,
) -> None:
    state = _load_state_module()
    lock_path = tmp_path / "receipts/import.lock"

    with state.import_lock(lock_path, operation="import", bundle_digest="a" * 64):
        descriptor = json.loads(lock_path.read_text())
        assert descriptor["operation"] == "import"
        assert descriptor["bundleDigest"] == "a" * 64
        with pytest.raises(state.ImportLockBusy):
            with state.import_lock(lock_path, operation="import"):
                pass


def test_receipt_latest_and_last_verified_are_distinct_atomic_projections(
    tmp_path: Path,
) -> None:
    state = _load_state_module()
    receipt_id = "11111111-1111-4111-8111-111111111111"
    dashboard_id = "22222222-2222-4222-8222-222222222222"
    bundle_id = "33333333-3333-5333-8333-333333333333"
    digest = "a" * 64
    receipt = {
        "schemaVersion": "catalyst.superset.import-receipt.v1",
        "receiptId": receipt_id,
        "outcome": "imported",
        "stage": "complete",
        "finishedAt": "2026-08-05T12:00:00Z",
        "errorCode": None,
        "recovery": {"requiredAction": "none"},
    }

    receipt_path, receipt_digest, latest_path = state.record_receipt(
        tmp_path, digest, bundle_id, receipt
    )
    assert receipt_path.name == f"{receipt_id}.json"
    assert latest_path == tmp_path / "latest" / f"{digest}.json"
    latest = json.loads(latest_path.read_text())
    assert latest["bundleDigest"] == digest
    assert latest["latestReceipt"]["receiptDigest"] == receipt_digest

    projection = state.update_last_verified(
        tmp_path,
        logical_dashboard_id=dashboard_id,
        payload={
            "schemaVersion": "catalyst.superset.last-verified.v1",
            "logicalDashboardId": dashboard_id,
            "bundleId": bundle_id,
            "bundleDigest": digest,
        },
    )
    assert projection["generation"] == 1
    assert projection["projectionDigest"] == state.digest_document(
        projection, "projectionDigest"
    )

    projection = state.update_last_verified(
        tmp_path,
        logical_dashboard_id=dashboard_id,
        payload={
            **projection,
            "bundleDigest": "b" * 64,
        },
    )
    assert projection["generation"] == 2
