"""Committed generated catalogs stay loadable and gateway-valid.

The catalogs are GENERATED artifacts (scripts/generate-catalyst-source-catalog.py
in the validation harness); these guards catch a hand edit or a generator
regression that would only surface at gateway boot or editor-catalog time.
The OpenMRS HIV source lives in the harness repo, so its checks skip when this
repo is checked out standalone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.catalyst.catalog import Catalog
from src.catalyst.contracts import ContractRegistry
from src.catalyst.policy import SqlPolicy
from src.catalyst.service import CatalystService, DataSourceBundle
from src.catalyst.storage import PreviewStore

from test_workbench_routes import CONTRACTS, FakeAnalytics, FakeHub, _ready_query

REPO_DIR = Path(__file__).resolve().parents[2]
OPENELIS_CATALOG = REPO_DIR / "analytics" / "catalog" / "analytics-catalog-v1.json"
HIV_SOURCE_DIR = REPO_DIR.parent.parent / "catalyst-sources" / "openmrs-hiv"
HIV_CATALOG = HIV_SOURCE_DIR / "catalog" / "openmrs-hiv-catalog.json"

CATALOG_PATHS = [
    pytest.param(OPENELIS_CATALOG, id="openelis"),
    pytest.param(
        HIV_CATALOG,
        id="openmrs-hiv",
        marks=pytest.mark.skipif(
            not HIV_CATALOG.is_file(),
            reason="harness layout (catalyst-sources/) not present",
        ),
    ),
]


@pytest.mark.skipif(
    not HIV_SOURCE_DIR.is_dir(),
    reason="harness layout (catalyst-sources/) not present",
)
def test_registry_catalog_path_matches_committed_catalog_name() -> None:
    """data-sources.json points at the catalog by its mounted container path;
    the basename must match the committed catalog file so the compose mount
    resolves it."""
    import json

    registry = json.loads((HIV_SOURCE_DIR / "data-sources.json").read_text())
    (entry,) = registry["dataSources"]
    assert Path(entry["catalogPath"]).name == HIV_CATALOG.name


OVERLAY_FOR_CATALOG = {
    "openelis": REPO_DIR / "analytics" / "catalog-overlay.json",
    "openmrs-hiv": HIV_SOURCE_DIR / "catalog-overlay.json",
}


@pytest.mark.parametrize("catalog_path", CATALOG_PATHS)
def test_overlay_and_generated_catalog_agree(catalog_path: Path, request) -> None:
    """The hand-maintained overlay and the generated catalog must describe the
    same catalog version, data source, and approved-view set — a mismatch means
    someone regenerated one side without the other."""
    import json

    overlay = json.loads(
        OVERLAY_FOR_CATALOG[request.node.callspec.id].read_text()
    )
    generated = json.loads(catalog_path.read_text())
    assert overlay["catalogVersion"] == generated["catalogVersion"]
    assert overlay["dataSource"] == generated["dataSource"]
    assert overlay["schemaVersion"] == generated["schemaVersion"]
    approved_in_catalog = {
        view["name"] for view in generated["views"] if view.get("approved") is True
    }
    assert {v["name"] for v in overlay["approvedViews"]} == approved_in_catalog


@pytest.mark.parametrize("catalog_path", CATALOG_PATHS)
@pytest.mark.asyncio
async def test_committed_catalog_passes_gateway_editor_validation(
    catalog_path: Path, tmp_path: Path
) -> None:
    catalog = Catalog.load(catalog_path)
    service = CatalystService(
        contracts=ContractRegistry.load(CONTRACTS),
        hub=FakeHub(_ready_query()),
        store=PreviewStore(tmp_path / "previews.sqlite3"),
        sql_policy=SqlPolicy(max_rows=2),
        max_rows=2,
        statement_timeout_ms=500,
        data_sources=(
            DataSourceBundle(
                source_id="under-test",
                label="Under test",
                catalog=catalog,
                analytics=FakeAnalytics(),
            ),
        ),
        default_data_source_id="under-test",
    )
    response = await service.workbench_editor_catalog()
    assert response.status_code == 200, response.body
    assert response.body["catalogVersion"] == catalog.catalog_version
