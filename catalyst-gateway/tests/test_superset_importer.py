from __future__ import annotations

import hashlib
import importlib.util
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
