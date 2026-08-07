from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load_importer_module():
    path = ROOT / "scripts/superset-import.py"
    spec = importlib.util.spec_from_file_location("catalyst_superset_importer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pointer(bundle_bytes: bytes, *, file_name: str | None = None) -> dict:
    digest = hashlib.sha256(bundle_bytes).hexdigest()
    return {
        "schemaVersion": "catalyst.superset.outbox.current.v1",
        "publicationId": "11111111-1111-4111-8111-111111111111",
        "bundleId": "22222222-2222-5222-8222-222222222222",
        "dashboard": {
            "id": "33333333-3333-4333-8333-333333333333",
            "versionId": "44444444-4444-4444-8444-444444444444",
            "configurationDigest": "c" * 64,
        },
        "targetSupersetVersion": "6.1.0",
        "bundle": {
            "fileName": file_name or f"{digest}.zip",
            "sha256": digest,
            "bytes": len(bundle_bytes),
        },
        "manifest": {
            "path": (
                "catalyst_dashboard_22222222-2222-5222-8222-222222222222/"
                "catalyst/manifest.json"
            ),
            "schemaVersion": "catalyst.superset.bundle.v1",
            "assetContentDigest": "d" * 64,
        },
        "publishedAt": "2026-08-05T12:00:00Z",
    }


def test_missing_and_malformed_pointer_fail_before_bundle_resolution(
    tmp_path: Path,
) -> None:
    importer = _load_importer_module()

    with pytest.raises(importer.ImportFailure) as missing:
        importer.load_current_pointer(tmp_path, tmp_path / "contracts")
    assert missing.value.stage == "pointer_read"

    (tmp_path / "current.json").write_text("{")
    with pytest.raises(importer.ImportFailure) as malformed:
        importer.load_current_pointer(tmp_path, tmp_path / "contracts")
    assert malformed.value.stage == "pointer_validation"


def test_bundle_digest_and_filename_must_match_pointer(tmp_path: Path) -> None:
    importer = _load_importer_module()
    bundle = b"not-a-zip"
    pointer = _pointer(bundle)
    pointer["bundle"]["sha256"] = "a" * 64
    pointer["bundle"]["fileName"] = f"{'a' * 64}.zip"
    bundle_path = tmp_path / pointer["bundle"]["fileName"]
    bundle_path.write_bytes(bundle)

    with pytest.raises(importer.ImportFailure) as failure:
        importer.resolve_bundle(tmp_path, pointer)
    assert failure.value.stage == "bundle_validation"
    assert failure.value.code == "bundle_digest_mismatch"


def test_bundle_reference_rejects_pointer_path_traversal(tmp_path: Path) -> None:
    importer = _load_importer_module()
    pointer = _pointer(b"bundle", file_name="../bundle.zip")

    with pytest.raises(importer.ImportFailure) as failure:
        importer.resolve_bundle(tmp_path, pointer)
    assert failure.value.stage == "bundle_validation"
    assert failure.value.code == "bundle_reference_invalid"


def test_corrupt_zip_and_wrong_superset_version_fail_before_cli(tmp_path: Path) -> None:
    importer = _load_importer_module()
    bundle = b"not-a-zip"
    pointer = _pointer(bundle)
    bundle_path = tmp_path / pointer["bundle"]["fileName"]
    bundle_path.write_bytes(bundle)

    assert importer.resolve_bundle(tmp_path, pointer) == bundle_path
    with pytest.raises(importer.ImportFailure) as corrupt:
        importer.inspect_bundle(bundle_path, pointer)
    assert corrupt.value.stage == "bundle_validation"
    assert corrupt.value.code == "bundle_zip_invalid"

    pointer["targetSupersetVersion"] = "6.0.0"
    (tmp_path / "current.json").write_text(json.dumps(pointer), encoding="utf-8")
    contracts = ROOT / "docs/contracts"
    with pytest.raises(importer.ImportFailure) as wrong_version:
        importer.load_current_pointer(tmp_path, contracts)
    assert wrong_version.value.stage == "pointer_validation"
    assert wrong_version.value.code == "current_pointer_invalid"


def test_zip_validation_rejects_traversal_and_symlinks(tmp_path: Path) -> None:
    importer = _load_importer_module()
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.yaml", "bad")

    with pytest.raises(importer.ImportFailure) as traversal:
        importer.inspect_bundle(archive, {})
    assert traversal.value.code == "unsafe_zip_member"

    archive = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("root/link")
    info.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(info, "target")

    with pytest.raises(importer.ImportFailure) as symlink:
        importer.inspect_bundle(archive, {})
    assert symlink.value.code == "unsafe_zip_member"


def test_diagnostic_redaction_is_bounded_and_hides_credentials() -> None:
    importer = _load_importer_module()
    diagnostic = importer.redacted_diagnostic(
        "postgresql://demo:very-secret@analytics-db/db " + ("x" * 20000),
        secrets=["very-secret"],
    )

    assert "very-secret" not in diagnostic["text"]
    assert "***" in diagnostic["text"]
    assert len(diagnostic["text"]) <= 16384
    assert diagnostic == {
        "text": diagnostic["text"],
        "truncated": True,
        "redacted": True,
    }


def test_successful_import_requires_an_exact_catalyst_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    importer = _load_importer_module()

    revision = "a" * 40
    monkeypatch.setenv("CATALYST_IMPORTER_REVISION", revision)
    assert importer.require_exact_importer_revision() == revision

    for invalid in ("", "worktree", "a" * 39, "A" * 40):
        monkeypatch.setenv("CATALYST_IMPORTER_REVISION", invalid)
        with pytest.raises(importer.ImportFailure) as failure:
            importer.require_exact_importer_revision()
        assert failure.value.stage == "provenance_resolution"
        assert failure.value.code == "importer_revision_invalid"


def test_relationship_verification_initializes_superset_before_model_imports() -> None:
    source = (ROOT / "scripts/superset-import.py").read_text(encoding="utf-8")
    verification = source[source.index("def _verify_superset") :]
    assert verification.index("app = create_app()") < verification.index(
        "from superset import db"
    )


def test_importer_is_standalone_and_uses_only_runtime_builtins() -> None:
    allowed_roots = {
        "__future__",
        "contextlib",
        "copy",
        "datetime",
        "fcntl",
        "hashlib",
        "importlib",
        "json",
        "jsonschema",
        "os",
        "re",
        "stat",
        "subprocess",
        "superset",
        "sys",
        "uuid",
        "zipfile",
        "pathlib",
        "typing",
    }
    for name in ("superset-import.py", "superset-import-state.py"):
        source = (ROOT / f"scripts/{name}").read_text(encoding="utf-8")
        tree = ast.parse(source)
        roots = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        roots.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        assert "catalyst" not in roots
        assert roots <= allowed_roots


def test_same_digest_recheck_inside_lock_skips_cli_after_concurrent_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    importer = _load_importer_module()
    pointer = _pointer(b"bundle")
    latest = {"latestReceipt": {"outcome": "imported", "stage": "complete"}}
    calls = iter([None, latest])

    monkeypatch.setattr(importer, "load_current_pointer", lambda *_: pointer)
    monkeypatch.setattr(importer, "_latest_for_pointer", lambda *_: next(calls))
    monkeypatch.setattr(
        importer,
        "resolve_bundle",
        lambda *_: pytest.fail("bundle must not be opened after concurrent success"),
    )
    monkeypatch.setenv("CATALYST_SUPERSET_OUTBOX", str(tmp_path / "outbox"))
    monkeypatch.setenv("CATALYST_SUPERSET_RECEIPTS", str(tmp_path / "receipts"))
    monkeypatch.setenv("CATALYST_CONTRACTS_DIR", str(ROOT / "docs/contracts"))

    assert importer.run_import() == 0


def test_latest_projection_is_ignored_when_foreign_or_receipt_is_corrupt(
    tmp_path: Path,
) -> None:
    importer = _load_importer_module()
    pointer = _pointer(b"bundle")
    digest = pointer["bundle"]["sha256"]
    receipt = {
        "schemaVersion": "catalyst.superset.import-receipt.v1",
        "receiptId": "11111111-1111-4111-8111-111111111111",
        "bundleId": pointer["bundleId"],
        "bundle": pointer["bundle"],
        "outcome": "imported",
        "stage": "complete",
        "finishedAt": "2026-08-05T12:00:00Z",
        "errorCode": None,
        "recovery": {"requiredAction": "none"},
    }
    receipt_path, _, latest_path = importer.state.record_receipt(
        tmp_path, digest, pointer["bundleId"], receipt
    )

    assert importer._latest_for_pointer(tmp_path, pointer) is not None

    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    latest["bundleDigest"] = "a" * 64
    latest_path.write_text(json.dumps(latest), encoding="utf-8")
    assert importer._latest_for_pointer(tmp_path, pointer) is None

    latest["bundleDigest"] = digest
    latest_path.write_text(json.dumps(latest), encoding="utf-8")
    stored_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    stored_receipt["finishedAt"] = "2026-08-05T12:01:00Z"
    receipt_path.write_text(json.dumps(stored_receipt), encoding="utf-8")
    assert importer._latest_for_pointer(tmp_path, pointer) is None


def test_busy_import_lock_does_not_replace_the_latest_projection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    importer = _load_importer_module()
    pointer = _pointer(b"bundle")

    monkeypatch.setattr(importer, "load_current_pointer", lambda *_: pointer)
    monkeypatch.setattr(importer, "_latest_for_pointer", lambda *_: None)
    monkeypatch.setattr(
        importer.state,
        "import_lock",
        lambda *_args, **_kwargs: _BusyContext(importer.state.ImportLockBusy),
    )
    monkeypatch.setattr(
        importer.state,
        "record_receipt",
        lambda *_args, **_kwargs: pytest.fail("busy is not a terminal import receipt"),
    )
    monkeypatch.setenv("CATALYST_SUPERSET_OUTBOX", str(tmp_path / "outbox"))
    monkeypatch.setenv("CATALYST_SUPERSET_RECEIPTS", str(tmp_path / "receipts"))
    monkeypatch.setenv("CATALYST_CONTRACTS_DIR", str(ROOT / "docs/contracts"))

    assert importer.run_import() == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "importing",
        "bundleDigest": pointer["bundle"]["sha256"],
    }


class _BusyContext:
    def __init__(self, error_type: type[RuntimeError]):
        self.error_type = error_type

    def __enter__(self):
        raise self.error_type("another import is active")

    def __exit__(self, *_args):
        return False


@pytest.mark.parametrize(
    ("stage", "expected_disposition", "expected_preservation", "expected_action"),
    [
        ("bundle_validation", "not_started", "preserved", "retry_same_bundle"),
        ("credential_resolution", "not_started", "preserved", "retry_same_bundle"),
        ("cli_import", "transaction_rolled_back", "preserved", "retry_same_bundle"),
        (
            "post_import_verification",
            "committed_unverified",
            "not_guaranteed",
            "full_reset_then_reimport_last_verified_bundle",
        ),
    ],
)
def test_failure_receipt_preservation_is_stage_scoped(
    stage: str,
    expected_disposition: str,
    expected_preservation: str,
    expected_action: str,
    tmp_path: Path,
) -> None:
    importer = _load_importer_module()
    failure = importer.ImportFailure(
        stage,
        "bounded_failure",
        "safe diagnostic",
        exit_code=(
            7
            if stage == "cli_import"
            else (0 if stage == "post_import_verification" else None)
        ),
    )
    receipt = importer._failure_receipt(
        failure,
        receipt_id="11111111-1111-4111-8111-111111111111",
        started_at="2026-08-05T12:00:00Z",
        command=["superset", "import-dashboards"],
        receipts=tmp_path,
        pointer=None,
        manifest=None,
    )

    assert receipt["supersetMutationDisposition"] == expected_disposition
    assert receipt["recovery"]["priorVerifiedDashboardState"] == expected_preservation
    assert receipt["recovery"]["openSupersetEnabled"] is False
    assert receipt["recovery"]["currentSuccessClaimEnabled"] is False
    assert receipt["recovery"]["requiredAction"] == expected_action
    assert receipt["diagnostic"]["text"] == "safe diagnostic"
