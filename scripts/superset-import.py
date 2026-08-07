#!/usr/bin/env python3
"""Validate and import one Catalyst native bundle into pinned Superset 6.1.0."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn


SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "superset-import-state.py"
_state_spec = importlib.util.spec_from_file_location(
    "catalyst_superset_import_state", STATE_PATH
)
if _state_spec is None or _state_spec.loader is None:
    raise RuntimeError(f"unable to load importer state module: {STATE_PATH}")
state = importlib.util.module_from_spec(_state_spec)
sys.modules[_state_spec.name] = state
_state_spec.loader.exec_module(state)


SUPERSET_VERSION = "6.1.0"
DEFAULT_IMAGE_DIGEST = (
    "sha256:5822dff49c41fd745ce33e38af502f9c64df30d133aeba148c5d89b35a1004ef"
)
MAX_ZIP_MEMBERS = 512
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_DIAGNOSTIC_CHARS = 16384
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[a-f0-9]{40}$")


class ImportFailure(RuntimeError):
    def __init__(
        self, stage: str, code: str, message: str, *, exit_code: int | None = None
    ):
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.exit_code = exit_code


def _fail(
    stage: str,
    code: str,
    message: str,
    *,
    exit_code: int | None = None,
) -> NoReturn:
    raise ImportFailure(stage, code, message, exit_code=exit_code)


def _read_json(path: Path, *, stage: str, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail(stage, code, f"required file does not exist: {path.name}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(stage, code, f"unable to parse {path.name}: {exc}")
    if not isinstance(value, dict):
        _fail(stage, code, f"{path.name} must contain one JSON object")
    return value


def _validate_contract(
    instance: dict[str, Any],
    contract_path: Path,
    *,
    stage: str,
    code: str,
) -> None:
    try:
        from jsonschema import Draft202012Validator, FormatChecker

        schema = json.loads(contract_path.read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(instance)
    except ImportFailure:
        raise
    except Exception as exc:
        _fail(stage, code, f"contract validation failed: {exc}")


def load_current_pointer(outbox: Path, contracts: Path) -> dict[str, Any]:
    pointer_path = outbox / "current.json"
    if not pointer_path.is_file():
        _fail(
            "pointer_read", "current_pointer_missing", "current.json is not available"
        )
    pointer = _read_json(
        pointer_path,
        stage="pointer_validation",
        code="current_pointer_invalid",
    )
    _validate_contract(
        pointer,
        contracts / "catalyst-superset-outbox-current-v1.schema.json",
        stage="pointer_validation",
        code="current_pointer_invalid",
    )
    return pointer


def resolve_bundle(outbox: Path, pointer: dict[str, Any]) -> Path:
    bundle = pointer.get("bundle")
    if not isinstance(bundle, dict):
        _fail(
            "bundle_validation",
            "bundle_reference_invalid",
            "bundle reference is missing",
        )
    filename = bundle.get("fileName")
    expected_digest = bundle.get("sha256")
    expected_bytes = bundle.get("bytes")
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or not isinstance(expected_digest, str)
        or not SHA256_PATTERN.fullmatch(expected_digest)
        or filename != f"{expected_digest}.zip"
    ):
        _fail(
            "bundle_validation",
            "bundle_reference_invalid",
            "bundle file name and digest are inconsistent",
        )
    path = outbox / filename
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        _fail("bundle_read", "bundle_missing", f"bundle does not exist: {filename}")
    except OSError as exc:
        _fail("bundle_read", "bundle_unreadable", f"unable to read bundle: {exc}")
    actual_digest = hashlib.sha256(data).hexdigest()
    if actual_digest != expected_digest:
        _fail(
            "bundle_validation",
            "bundle_digest_mismatch",
            f"bundle digest {actual_digest} does not match current.json",
        )
    if not isinstance(expected_bytes, int) or expected_bytes != len(data):
        _fail(
            "bundle_validation",
            "bundle_size_mismatch",
            f"bundle size {len(data)} does not match current.json",
        )
    return path


def _safe_member(info: zipfile.ZipInfo) -> bool:
    path = PurePosixPath(info.filename)
    mode = info.external_attr >> 16
    return (
        bool(info.filename)
        and not info.filename.startswith("/")
        and ".." not in path.parts
        and not stat.S_ISLNK(mode)
        and "\\" not in info.filename
    )


def inspect_bundle(bundle_path: Path, pointer: dict[str, Any]) -> dict[str, Any]:
    try:
        archive = zipfile.ZipFile(bundle_path)
    except (OSError, zipfile.BadZipFile) as exc:
        _fail("bundle_validation", "bundle_zip_invalid", f"invalid ZIP: {exc}")
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_MEMBERS:
            _fail("bundle_validation", "bundle_too_large", "ZIP has too many members")
        if sum(item.file_size for item in infos) > MAX_UNCOMPRESSED_BYTES:
            _fail(
                "bundle_validation", "bundle_too_large", "ZIP expands beyond the limit"
            )
        names: set[str] = set()
        for info in infos:
            if not _safe_member(info) or info.filename in names:
                _fail(
                    "bundle_validation",
                    "unsafe_zip_member",
                    f"unsafe or duplicate ZIP member: {info.filename}",
                )
            names.add(info.filename)

        manifest_ref = pointer.get("manifest")
        if not isinstance(manifest_ref, dict) or not isinstance(
            manifest_ref.get("path"), str
        ):
            _fail(
                "manifest_validation",
                "manifest_reference_invalid",
                "manifest path missing",
            )
        manifest_path = manifest_ref["path"]
        if manifest_path not in names:
            _fail(
                "manifest_validation", "manifest_missing", "bundle manifest is missing"
            )
        try:
            manifest = json.loads(archive.read(manifest_path).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            _fail("manifest_validation", "manifest_invalid", f"invalid manifest: {exc}")
        if not isinstance(manifest, dict):
            _fail(
                "manifest_validation", "manifest_invalid", "manifest must be an object"
            )
        return manifest


def validate_manifest(
    manifest: dict[str, Any],
    pointer: dict[str, Any],
    contracts: Path,
) -> None:
    _validate_contract(
        manifest,
        contracts / "catalyst-superset-bundle-v1.schema.json",
        stage="manifest_validation",
        code="manifest_invalid",
    )
    expected = {
        "bundleId": pointer["bundleId"],
        "targetSupersetVersion": pointer["targetSupersetVersion"],
        "dashboardVersionId": pointer["dashboard"]["versionId"],
        "configurationDigest": pointer["dashboard"]["configurationDigest"],
        "assetContentDigest": pointer["manifest"]["assetContentDigest"],
    }
    actual = {
        "bundleId": manifest.get("bundleId"),
        "targetSupersetVersion": manifest.get("targetSupersetVersion"),
        "dashboardVersionId": (manifest.get("dashboard") or {}).get("versionId"),
        "configurationDigest": (manifest.get("dashboard") or {}).get(
            "configurationDigest"
        ),
        "assetContentDigest": manifest.get("assetContentDigest"),
    }
    if actual != expected:
        _fail(
            "manifest_validation",
            "manifest_pointer_mismatch",
            "manifest identity does not match current.json",
        )


def redacted_diagnostic(message: str, *, secrets: list[str]) -> dict[str, Any]:
    text = message
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    text = re.sub(r"(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)", r"\1***\2", text)
    truncated = len(text) > MAX_DIAGNOSTIC_CHARS
    return {
        "text": text[:MAX_DIAGNOSTIC_CHARS],
        "truncated": truncated,
        "redacted": True,
    }


def require_exact_importer_revision() -> str:
    revision = os.environ.get("CATALYST_IMPORTER_REVISION", "")
    if not GIT_REVISION_PATTERN.fullmatch(revision):
        _fail(
            "provenance_resolution",
            "importer_revision_invalid",
            "CATALYST_IMPORTER_REVISION must be the exact 40-character Catalyst commit",
        )
    return revision


def _importer_metadata(
    command: list[str], *, revision: str | None = None
) -> dict[str, Any]:
    return {
        # A failed attempt can be recorded before the operator provenance is
        # available. Successful imports pass a validated exact revision.
        "revision": revision
        or os.environ.get("CATALYST_IMPORTER_REVISION")
        or "unresolved",
        "supersetVersion": SUPERSET_VERSION,
        "imageDigest": os.environ.get(
            "CATALYST_SUPERSET_IMAGE_DIGEST", DEFAULT_IMAGE_DIGEST
        ),
        "platform": os.environ.get("CATALYST_SUPERSET_PLATFORM", "linux/unknown"),
        "driverRevision": os.environ.get(
            "CATALYST_SUPERSET_DRIVER_REVISION", "psycopg2-binary==2.9.9"
        ),
        "commandDigest": hashlib.sha256(
            state.canonical_json_bytes(command)
        ).hexdigest(),
    }


def _dashboard_ref(pointer: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        **pointer["dashboard"],
        "supersetUuid": manifest["assetUuids"]["dashboard"],
        "supersetSlug": manifest["dashboardSlug"],
    }


def _last_verified_availability(
    receipts: Path, logical_dashboard_id: str | None
) -> dict[str, Any]:
    if logical_dashboard_id:
        path = receipts / "last-verified" / f"{logical_dashboard_id}.json"
        if path.is_file():
            try:
                projection = json.loads(path.read_text(encoding="utf-8"))
                return {
                    "status": "available",
                    "logicalDashboardId": logical_dashboard_id,
                    "generation": projection["generation"],
                    "path": f"receipts/last-verified/{logical_dashboard_id}.json",
                    "projectionDigest": projection["projectionDigest"],
                    "publicationId": projection["publicationId"],
                    "bundleId": projection["bundleId"],
                    "bundleDigest": projection["bundleDigest"],
                    "receiptId": projection["importReceipt"]["receiptId"],
                    "receiptDigest": projection["importReceipt"]["receiptDigest"],
                }
            except (OSError, KeyError, TypeError, ValueError):
                pass
    return {"status": "unavailable", "omissionReason": "no_verified_import"}


def _base_receipt(
    *,
    receipt_id: str,
    started_at: str,
    finished_at: str,
    command: list[str],
    importer_revision: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": "catalyst.superset.import-receipt.v1",
        "receiptId": receipt_id,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "importer": _importer_metadata(command, revision=importer_revision),
    }


def _failure_receipt(
    failure: ImportFailure,
    *,
    receipt_id: str,
    started_at: str,
    command: list[str],
    receipts: Path,
    pointer: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    post_verification = failure.stage == "post_import_verification"
    cli_failure = failure.stage == "cli_import"
    logical_id = None
    if pointer and isinstance(pointer.get("dashboard"), dict):
        logical_id = pointer["dashboard"].get("id")
    receipt = {
        **_base_receipt(
            receipt_id=receipt_id,
            started_at=started_at,
            finished_at=state.utc_now(),
            command=command,
        ),
        "outcome": "import_failed",
        "stage": failure.stage,
        "exitCode": failure.exit_code
        if cli_failure
        else (0 if post_verification else None),
        "supersetMutationDisposition": (
            "committed_unverified"
            if post_verification
            else ("transaction_rolled_back" if cli_failure else "not_started")
        ),
        "verification": {
            "status": "failed" if post_verification else "not_run",
            "omissionReason": failure.code,
        },
        "errorCode": failure.code,
        "diagnostic": redacted_diagnostic(
            str(failure),
            secrets=[
                os.environ.get("SUPERSET_ADMIN_PASSWORD", ""),
                os.environ.get("SUPERSET_METADATA_PASSWORD", ""),
            ],
        ),
        "recovery": {
            "priorVerifiedDashboardState": (
                "not_guaranteed" if post_verification else "preserved"
            ),
            "openSupersetEnabled": False,
            "currentSuccessClaimEnabled": False,
            "requiredAction": (
                "full_reset_then_reimport_last_verified_bundle"
                if post_verification
                else "retry_same_bundle"
            ),
            "lastVerifiedProjection": _last_verified_availability(receipts, logical_id),
        },
    }
    if (
        pointer
        and manifest
        and failure.stage
        in {
            "credential_resolution",
            "cli_import",
            "post_import_verification",
        }
    ):
        receipt.update(
            {
                "publicationId": pointer["publicationId"],
                "bundleId": pointer["bundleId"],
                "bundle": pointer["bundle"],
                "dashboard": _dashboard_ref(pointer, manifest),
                "targetSupersetVersion": pointer["targetSupersetVersion"],
            }
        )
    receipt["receiptDigest"] = state.digest_document(receipt, "receiptDigest")
    return receipt


def _verify_superset(manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        from superset.app import create_app

        app = create_app()
        with app.app_context():
            # These model imports initialize encrypted fields; Superset requires
            # the application to exist before they are imported.
            from superset import db
            from superset.connectors.sqla.models import SqlaTable
            from superset.models.dashboard import Dashboard
            from superset.models.slice import Slice

            dashboard_uuid = uuid.UUID(manifest["assetUuids"]["dashboard"])
            dashboard = db.session.query(Dashboard).filter_by(uuid=dashboard_uuid).one()
            if dashboard.slug != manifest["dashboardSlug"]:
                raise ValueError("dashboard slug differs from manifest")
            expected_datasets = sorted(
                manifest["assetUuids"]["datasetsByVersion"].values()
            )
            expected_charts = sorted(manifest["assetUuids"]["chartsByVersion"].values())
            observed_datasets = {
                str(item.uuid)
                for item in db.session.query(SqlaTable)
                .filter(
                    SqlaTable.uuid.in_([uuid.UUID(item) for item in expected_datasets])
                )
                .all()
            }
            observed_charts = {
                str(item.uuid)
                for item in db.session.query(Slice)
                .filter(Slice.uuid.in_([uuid.UUID(item) for item in expected_charts]))
                .all()
            }
            dashboard_charts = {str(item.uuid) for item in dashboard.slices}
            if observed_datasets != set(expected_datasets):
                raise ValueError("one or more expected datasets are missing")
            if observed_charts != set(expected_charts):
                raise ValueError("one or more expected charts are missing")
            if not set(expected_charts).issubset(dashboard_charts):
                raise ValueError("dashboard/chart relationships do not match")
            return {
                "status": "passed",
                "dashboardUuid": str(dashboard.uuid),
                "dashboardSlug": dashboard.slug,
                "expectedDatasetUuids": expected_datasets,
                "expectedChartUuids": expected_charts,
                "relationshipsMatch": True,
            }
    except Exception as exc:
        _fail(
            "post_import_verification",
            "superset_relationship_verification_failed",
            str(exc),
            exit_code=0,
        )


def _successful_receipt(
    *,
    receipt_id: str,
    started_at: str,
    command: list[str],
    pointer: dict[str, Any],
    manifest: dict[str, Any],
    verification: dict[str, Any],
    importer_revision: str,
) -> dict[str, Any]:
    receipt = {
        **_base_receipt(
            receipt_id=receipt_id,
            started_at=started_at,
            finished_at=state.utc_now(),
            command=command,
            importer_revision=importer_revision,
        ),
        "publicationId": pointer["publicationId"],
        "bundleId": pointer["bundleId"],
        "bundle": pointer["bundle"],
        "dashboard": _dashboard_ref(pointer, manifest),
        "targetSupersetVersion": pointer["targetSupersetVersion"],
        "outcome": "imported",
        "stage": "complete",
        "exitCode": 0,
        "supersetMutationDisposition": "verified",
        "verification": verification,
        "errorCode": None,
        "diagnostic": {"text": "", "truncated": False, "redacted": True},
        "recovery": {
            "priorVerifiedDashboardState": "not_applicable",
            "openSupersetEnabled": True,
            "currentSuccessClaimEnabled": True,
            "requiredAction": "none",
            "lastVerifiedProjection": {
                "status": "unavailable",
                "omissionReason": "created_after_receipt",
            },
        },
    }
    receipt["receiptDigest"] = state.digest_document(receipt, "receiptDigest")
    return receipt


def _record_unresolved(receipts: Path, receipt: dict[str, Any]) -> Path:
    path = receipts / "attempts" / "unresolved" / f"{receipt['receiptId']}.json"
    state.atomic_write_json(path, receipt)
    return path


def _last_verified_payload(
    pointer: dict[str, Any],
    manifest: dict[str, Any],
    receipt: dict[str, Any],
    receipt_digest: str,
    latest_path: Path,
    importer_revision: str,
) -> dict[str, Any]:
    latest_digest = hashlib.sha256(latest_path.read_bytes()).hexdigest()
    logical_id = pointer["dashboard"]["id"]
    return {
        "schemaVersion": "catalyst.superset.last-verified.v1",
        "logicalDashboardId": logical_id,
        "dashboard": pointer["dashboard"],
        "publicationId": pointer["publicationId"],
        "bundleContractVersion": manifest["schemaVersion"],
        "bundleId": pointer["bundleId"],
        "bundleDigest": pointer["bundle"]["sha256"],
        "bundle": {
            "path": f"outbox/{pointer['bundle']['fileName']}",
            "sha256": pointer["bundle"]["sha256"],
            "bytes": pointer["bundle"]["bytes"],
        },
        "importLatest": {
            "path": f"receipts/latest/{pointer['bundle']['sha256']}.json",
            "sha256": latest_digest,
            "schemaVersion": "catalyst.superset.import-latest.v1",
        },
        "importReceipt": {
            "receiptId": receipt["receiptId"],
            "path": (
                f"receipts/attempts/{pointer['bundle']['sha256']}/"
                f"{receipt['receiptId']}.json"
            ),
            "receiptDigest": receipt_digest,
            "schemaVersion": receipt["schemaVersion"],
            "outcome": "imported",
            "stage": "complete",
        },
        "supersetRuntime": {
            "version": SUPERSET_VERSION,
            "imageDigest": os.environ.get(
                "CATALYST_SUPERSET_IMAGE_DIGEST", DEFAULT_IMAGE_DIGEST
            ),
            "metadataDatabaseImageDigest": os.environ.get(
                "CATALYST_SUPERSET_METADATA_IMAGE_DIGEST",
                "sha256:7c688148e5e156d0e86df7ba8ae5a05a2386aaec1e2ad8e6d11bdf10504b1fb7",
            ),
            "platform": os.environ.get("CATALYST_SUPERSET_PLATFORM", "linux/unknown"),
            "driverRevision": os.environ.get(
                "CATALYST_SUPERSET_DRIVER_REVISION", "psycopg2-binary==2.9.9"
            ),
            "importerRevision": importer_revision,
        },
        "supersetDashboard": {
            "logicalDashboardId": logical_id,
            "uuid": manifest["assetUuids"]["dashboard"],
            "slug": manifest["dashboardSlug"],
            "url": (
                f"{os.environ.get('CATALYST_SUPERSET_PUBLIC_URL', 'http://localhost:8088').rstrip('/')}"
                f"/superset/dashboard/{manifest['dashboardSlug']}/"
            ),
        },
        "updatedAt": receipt["finishedAt"],
    }


def _latest_for_pointer(
    receipts: Path, pointer: dict[str, Any]
) -> dict[str, Any] | None:
    bundle_digest = pointer["bundle"]["sha256"]
    path = receipts / "latest" / f"{bundle_digest}.json"
    if not path.is_file():
        return None
    try:
        latest = json.loads(path.read_text(encoding="utf-8"))
        latest_receipt = latest["latestReceipt"]
        receipt_id = latest_receipt["receiptId"]
        expected_receipt_path = f"receipts/attempts/{bundle_digest}/{receipt_id}.json"
        if (
            latest.get("schemaVersion") != "catalyst.superset.import-latest.v1"
            or latest.get("bundleId") != pointer["bundleId"]
            or latest.get("bundleDigest") != bundle_digest
            or latest_receipt.get("path") != expected_receipt_path
        ):
            return None
        receipt_path = receipts / "attempts" / bundle_digest / f"{receipt_id}.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            receipt.get("receiptId") != receipt_id
            or receipt.get("receiptDigest")
            != state.digest_document(receipt, "receiptDigest")
            or receipt.get("receiptDigest") != latest_receipt.get("receiptDigest")
            or receipt.get("outcome") != latest_receipt.get("outcome")
            or receipt.get("stage") != latest_receipt.get("stage")
            or receipt.get("finishedAt") != latest_receipt.get("finishedAt")
            or receipt.get("errorCode") != latest_receipt.get("errorCode")
            or (receipt.get("recovery") or {}).get("requiredAction")
            != latest_receipt.get("recoveryAction")
        ):
            return None
        if receipt.get("outcome") == "imported" and (
            receipt.get("bundleId") != pointer["bundleId"]
            or (receipt.get("bundle") or {}).get("sha256") != bundle_digest
        ):
            return None
    except (KeyError, OSError, TypeError, ValueError):
        return None
    return latest if isinstance(latest, dict) else None


def run_import(*, bootstrap: bool = False) -> int:
    outbox = Path(os.environ.get("CATALYST_SUPERSET_OUTBOX", "/opt/catalyst/outbox"))
    receipts = Path(
        os.environ.get("CATALYST_SUPERSET_RECEIPTS", "/opt/catalyst/receipts")
    )
    contracts = Path(os.environ.get("CATALYST_CONTRACTS_DIR", "/docs/contracts"))
    command = [
        "superset",
        "import-dashboards",
        "-p",
        "<bundle>",
        "-u",
        os.environ.get("SUPERSET_ADMIN_USERNAME", "admin"),
    ]
    receipt_id = str(uuid.uuid4())
    started_at = state.utc_now()
    pointer: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None
    bundle_digest: str | None = None
    try:
        pointer = load_current_pointer(outbox, contracts)
        bundle_digest = pointer["bundle"]["sha256"]
        latest = _latest_for_pointer(receipts, pointer)
        if latest and latest.get("latestReceipt", {}).get("outcome") == "imported":
            print(
                json.dumps(
                    {"status": "already_imported", "bundleDigest": bundle_digest}
                )
            )
            return 0
        if (
            bootstrap
            and latest
            and latest.get("latestReceipt", {}).get("stage")
            == "post_import_verification"
        ):
            print(
                json.dumps(
                    {
                        "status": "automatic_retry_suppressed",
                        "bundleDigest": bundle_digest,
                    }
                )
            )
            return 0
        with state.import_lock(
            receipts / "import.lock",
            publication_id=pointer["publicationId"],
            bundle_id=pointer["bundleId"],
            bundle_digest=bundle_digest,
            receipt_id=receipt_id,
        ):
            # Another process may have completed this exact import after our
            # initial fast-path read but before we acquired the lock.
            latest = _latest_for_pointer(receipts, pointer)
            if latest and latest.get("latestReceipt", {}).get("outcome") == "imported":
                print(
                    json.dumps(
                        {"status": "already_imported", "bundleDigest": bundle_digest}
                    )
                )
                return 0
            if (
                bootstrap
                and latest
                and latest.get("latestReceipt", {}).get("stage")
                == "post_import_verification"
            ):
                print(
                    json.dumps(
                        {
                            "status": "automatic_retry_suppressed",
                            "bundleDigest": bundle_digest,
                        }
                    )
                )
                return 0
            bundle_path = resolve_bundle(outbox, pointer)
            manifest = inspect_bundle(bundle_path, pointer)
            validate_manifest(manifest, pointer, contracts)
            importer_revision = require_exact_importer_revision()
            if not os.environ.get("CATALYST_ANALYTICS_DATABASE_URI"):
                _fail(
                    "credential_resolution",
                    "analytics_credential_missing",
                    "CATALYST_ANALYTICS_DATABASE_URI is required",
                )
            actual_command = [
                "superset",
                "import-dashboards",
                "-p",
                str(bundle_path),
                "-u",
                os.environ.get("SUPERSET_ADMIN_USERNAME", "admin"),
            ]
            completed = subprocess.run(
                actual_command,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                _fail(
                    "cli_import",
                    "superset_cli_import_failed",
                    completed.stderr
                    or completed.stdout
                    or "Superset CLI import failed",
                    exit_code=completed.returncode,
                )
            verification = _verify_superset(manifest)
            receipt = _successful_receipt(
                receipt_id=receipt_id,
                started_at=started_at,
                command=command,
                pointer=pointer,
                manifest=manifest,
                verification=verification,
                importer_revision=importer_revision,
            )
            _validate_contract(
                receipt,
                contracts / "catalyst-superset-import-receipt-v1.schema.json",
                stage="post_import_verification",
                code="receipt_contract_invalid",
            )
            _, receipt_digest, latest_path = state.record_receipt(
                receipts, bundle_digest, pointer["bundleId"], receipt
            )
            projection_payload = _last_verified_payload(
                pointer,
                manifest,
                receipt,
                receipt_digest,
                latest_path,
                importer_revision,
            )
            projection = state.update_last_verified(
                receipts,
                logical_dashboard_id=pointer["dashboard"]["id"],
                payload=projection_payload,
            )
            _validate_contract(
                projection,
                contracts / "catalyst-superset-last-verified-v1.schema.json",
                stage="post_import_verification",
                code="last_verified_contract_invalid",
            )
            print(
                json.dumps(
                    {
                        "status": "imported",
                        "bundleDigest": bundle_digest,
                        "dashboardUrl": projection["supersetDashboard"]["url"],
                    }
                )
            )
            return 0
    except state.ImportLockBusy:
        print(
            json.dumps(
                {
                    "status": "importing",
                    "bundleDigest": bundle_digest,
                }
            )
        )
        return 2
    except ImportFailure as exc:
        failure = exc

    receipt = _failure_receipt(
        failure,
        receipt_id=receipt_id,
        started_at=started_at,
        command=command,
        receipts=receipts,
        pointer=pointer,
        manifest=manifest,
    )
    try:
        _validate_contract(
            receipt,
            contracts / "catalyst-superset-import-receipt-v1.schema.json",
            stage=failure.stage,
            code="receipt_contract_invalid",
        )
    except ImportFailure:
        # Contract-loading failure is still recorded as bounded raw evidence.
        pass
    if bundle_digest and pointer and pointer.get("bundleId"):
        state.record_receipt(receipts, bundle_digest, pointer["bundleId"], receipt)
    else:
        _record_unresolved(receipts, receipt)
    print(
        json.dumps(
            {
                "status": "import_failed",
                "stage": failure.stage,
                "errorCode": failure.code,
            }
        )
    )
    return 1


def show_status() -> int:
    outbox = Path(os.environ.get("CATALYST_SUPERSET_OUTBOX", "/opt/catalyst/outbox"))
    receipts = Path(
        os.environ.get("CATALYST_SUPERSET_RECEIPTS", "/opt/catalyst/receipts")
    )
    contracts = Path(os.environ.get("CATALYST_CONTRACTS_DIR", "/docs/contracts"))
    try:
        pointer = load_current_pointer(outbox, contracts)
    except ImportFailure as failure:
        print(json.dumps({"status": "draft", "errorCode": failure.code}))
        return 0
    bundle_digest = pointer["bundle"]["sha256"]
    activity = state.read_import_activity(receipts / "import.lock", bundle_digest)
    if activity["status"] == "importing":
        print(
            json.dumps(
                {
                    "status": "importing",
                    "bundleDigest": bundle_digest,
                    "import": activity["descriptor"],
                }
            )
        )
        return 0
    latest = _latest_for_pointer(receipts, pointer)
    payload = {
        "status": (
            latest.get("latestReceipt", {}).get("outcome") if latest else "bundle_ready"
        ),
        "bundleDigest": bundle_digest,
        "latest": latest,
    }
    if activity["status"] == "diagnostic":
        payload["importerDiagnostic"] = activity["code"]
    print(json.dumps(payload))
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    if command == "status":
        return show_status()
    if command == "import":
        return run_import(bootstrap=False)
    if command == "bootstrap":
        return run_import(bootstrap=True)
    print("usage: superset-import.py {status|import|bootstrap}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
