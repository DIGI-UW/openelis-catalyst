#!/usr/bin/env python3
"""Deterministic, standalone state primitives for the Superset importer."""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator


class ImportLockBusy(RuntimeError):
    """Raised when another importer owns the non-blocking OS lock."""


def _reject_floats(value: Any) -> None:
    if isinstance(value, float):
        raise ValueError(
            "floating-point values are outside the constrained canonical JSON profile"
        )
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("canonical JSON object keys must be strings")
        for item in value.values():
            _reject_floats(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_floats(item)
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise ValueError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return the RFC-8785-compatible subset used by importer state.

    Importer documents intentionally contain no floating-point numbers. Within
    that constrained domain, sorted UTF-8 JSON with minimal separators is the
    same byte representation used by the full RFC 8785 implementation.
    """

    _reject_floats(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_document(document: dict[str, Any], digest_field: str) -> str:
    unsigned = copy.deepcopy(document)
    unsigned.pop(digest_field, None)
    return sha256_hex(canonical_json_bytes(unsigned))


def utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def atomic_write_json(path: Path, payload: dict[str, Any]) -> str:
    data = canonical_json_bytes(payload)
    digest = sha256_hex(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{digest[:12]}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()
    return digest


@contextlib.contextmanager
def import_lock(
    path: Path,
    *,
    operation: str,
    bundle_digest: str | None = None,
) -> Iterator[dict[str, Any]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ImportLockBusy("another Superset import operation is active") from exc
        descriptor: dict[str, Any] = {
            "schemaVersion": "catalyst.superset.import-lock.v1",
            "operation": operation,
            "pid": os.getpid(),
            "startedAt": utc_now(),
        }
        if bundle_digest is not None:
            descriptor["bundleDigest"] = bundle_digest
        stream.seek(0)
        stream.truncate()
        stream.write(canonical_json_bytes(descriptor).decode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
        yield descriptor
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def record_receipt(
    receipts_root: Path,
    bundle_digest: str,
    bundle_id: str,
    receipt: dict[str, Any],
) -> tuple[Path, str, Path]:
    stored = copy.deepcopy(receipt)
    stored["receiptDigest"] = digest_document(stored, "receiptDigest")
    receipt_digest = stored["receiptDigest"]
    receipt_id = stored["receiptId"]
    relative_attempt = Path("attempts") / bundle_digest / f"{receipt_id}.json"
    receipt_path = receipts_root / relative_attempt
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    if receipt_path.exists():
        raise FileExistsError(f"immutable receipt already exists: {receipt_path}")
    atomic_write_json(receipt_path, stored)

    latest = {
        "schemaVersion": "catalyst.superset.import-latest.v1",
        "bundleId": bundle_id,
        "bundleDigest": bundle_digest,
        "latestReceipt": {
            "receiptId": receipt_id,
            "path": f"receipts/{relative_attempt.as_posix()}",
            "receiptDigest": receipt_digest,
            "outcome": stored["outcome"],
            "stage": stored["stage"],
            "finishedAt": stored["finishedAt"],
            "errorCode": stored.get("errorCode"),
            "recoveryAction": stored["recovery"]["requiredAction"],
        },
        "updatedAt": stored["finishedAt"],
    }
    latest_path = receipts_root / "latest" / f"{bundle_digest}.json"
    atomic_write_json(latest_path, latest)
    return receipt_path, receipt_digest, latest_path


def update_last_verified(
    receipts_root: Path,
    *,
    logical_dashboard_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    path = receipts_root / "last-verified" / f"{logical_dashboard_id}.json"
    generation = 1
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("logicalDashboardId") == logical_dashboard_id:
                generation = int(current.get("generation", 0)) + 1
        except (OSError, ValueError, TypeError):
            # Recovery validation rejects corrupt projections. A successful new
            # verified import may replace one only by explicit importer action.
            generation = 1

    projection = copy.deepcopy(payload)
    projection["logicalDashboardId"] = logical_dashboard_id
    projection["generation"] = generation
    projection.pop("projectionDigest", None)
    projection["projectionDigest"] = digest_document(projection, "projectionDigest")
    atomic_write_json(path, projection)
    return projection
