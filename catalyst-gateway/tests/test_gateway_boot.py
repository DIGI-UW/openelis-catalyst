"""Boot-time data-source registry behavior through the real gateway wiring."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src import gateway


def test_unprovisioned_source_is_listed_unavailable_and_untargetable(
    monkeypatch, tmp_path: Path
) -> None:
    """A registered source whose catalog file does not exist yet boots as
    available=False (visible in /data-sources) and cannot be targeted."""
    registry = tmp_path / "data-sources.json"
    registry.write_text(
        json.dumps(
            {
                "dataSources": [
                    {
                        "id": "openmrs-hiv",
                        "label": "OpenMRS HIV/ART program",
                        "analyticsDsn": "postgresql://u:p@localhost:5/hiv",
                        "catalogPath": str(tmp_path / "not-provisioned-yet.json"),
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("CATALYST_DATA_SOURCES_PATH", str(registry))
    monkeypatch.setenv(
        "CATALYST_PREVIEW_STORE_PATH", str(tmp_path / "previews.sqlite3")
    )

    client = TestClient(gateway.create_app())
    body = client.get("/v1/catalyst/data-sources").json()
    assert [(s["id"], s["available"]) for s in body["dataSources"]] == [
        ("openelis", True),
        ("openmrs-hiv", False),
    ]

    response = client.post(
        "/v1/catalyst/workbench/sessions",
        json={
            "contractVersion": "catalyst.workbench.session.request.v1",
            "deploymentMode": "demo",
            "question": "How many viral load results were reported this month?",
            "profileId": "catalyst-query-checked",
            "dataSourceId": "openmrs-hiv",
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "unknown_data_source"
