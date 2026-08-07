from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import uuid

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


def test_constrained_canonical_json_matches_rfc8785() -> None:
    state = _load_state_module()
    rfc8785 = pytest.importorskip("rfc8785")
    fixtures = [
        {"z": 2, "a": [True, None, "é"]},
        {"nested": {"receiptId": str(uuid.uuid4()), "count": 0}},
        {"unicode": "\u20ac\r\n", "safeInteger": 9007199254740991},
    ]

    for fixture in fixtures:
        assert state.canonical_json_bytes(fixture) == rfc8785.dumps(fixture)


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
    publication_id = "11111111-1111-4111-8111-111111111111"
    bundle_id = "22222222-2222-5222-8222-222222222222"
    receipt_id = "33333333-3333-4333-8333-333333333333"
    digest = "a" * 64

    with state.import_lock(
        lock_path,
        publication_id=publication_id,
        bundle_id=bundle_id,
        bundle_digest=digest,
        receipt_id=receipt_id,
    ):
        descriptor = json.loads(lock_path.read_text())
        assert set(descriptor) == {
            "schemaVersion",
            "publicationId",
            "bundleId",
            "bundleDigest",
            "receiptId",
            "startedAt",
        }
        assert descriptor["publicationId"] == publication_id
        assert descriptor["bundleId"] == bundle_id
        assert descriptor["bundleDigest"] == digest
        assert descriptor["receiptId"] == receipt_id
        assert state.read_import_activity(lock_path, digest) == {
            "status": "importing",
            "descriptor": descriptor,
        }
        with pytest.raises(state.ImportLockBusy):
            with state.import_lock(
                lock_path,
                publication_id=None,
                bundle_id=None,
                bundle_digest=None,
                receipt_id="44444444-4444-4444-8444-444444444444",
            ):
                pass

    # Marker bytes may remain after the lock is released, but they are not
    # process state and therefore cannot claim that an import is live.
    assert state.read_import_activity(lock_path, digest) == {"status": "idle"}


def test_import_activity_rejects_a_held_foreign_digest_marker(tmp_path: Path) -> None:
    state = _load_state_module()
    lock_path = tmp_path / "receipts/import.lock"

    with state.import_lock(
        lock_path,
        publication_id=None,
        bundle_id=None,
        bundle_digest="a" * 64,
        receipt_id="11111111-1111-4111-8111-111111111111",
    ):
        assert state.read_import_activity(lock_path, "b" * 64) == {
            "status": "diagnostic",
            "code": "import_lock_digest_mismatch",
        }


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


@pytest.mark.parametrize("contents", ["{", "[]", '{"generation": 1}'])
def test_corrupt_last_verified_projection_is_not_replaced(
    tmp_path: Path, contents: str
) -> None:
    state = _load_state_module()
    dashboard_id = "22222222-2222-4222-8222-222222222222"
    path = tmp_path / "last-verified" / f"{dashboard_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text(contents, encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(state.LastVerifiedProjectionInvalid):
        state.update_last_verified(
            tmp_path,
            logical_dashboard_id=dashboard_id,
            payload={
                "schemaVersion": "catalyst.superset.last-verified.v1",
                "logicalDashboardId": dashboard_id,
                "bundleId": "33333333-3333-5333-8333-333333333333",
                "bundleDigest": "a" * 64,
            },
        )

    assert path.read_bytes() == before


def test_recovery_projection_loader_fails_closed_for_missing_or_bad_digest(
    tmp_path: Path,
) -> None:
    state = _load_state_module()
    dashboard_id = "22222222-2222-4222-8222-222222222222"
    path = tmp_path / "last-verified" / f"{dashboard_id}.json"

    with pytest.raises(state.LastVerifiedProjectionMissing):
        state.load_last_verified(path, dashboard_id)

    path.parent.mkdir(parents=True)
    projection = {
        "schemaVersion": "catalyst.superset.last-verified.v1",
        "logicalDashboardId": dashboard_id,
        "generation": 1,
        "projectionDigest": "a" * 64,
    }
    path.write_text(json.dumps(projection), encoding="utf-8")
    with pytest.raises(state.LastVerifiedProjectionInvalid):
        state.load_last_verified(path, dashboard_id)
