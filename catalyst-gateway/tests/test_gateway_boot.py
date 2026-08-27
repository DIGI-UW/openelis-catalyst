"""Boot-time data-source registry behavior through the real gateway wiring."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src import gateway


def test_an_unreachable_source_does_not_prevent_startup(
    monkeypatch, tmp_path: Path
) -> None:
    """A source whose connection cannot be reached still boots.

    Availability used to be decided at boot by whether a generated catalog
    file existed. Live discovery removes that file, and nothing connects at
    startup, so a registered source is listed and only fails when it is
    actually used -- which is a stronger form of the same guarantee: one
    unavailable source never stops the application or another source."""
    registry = tmp_path / "data-sources.json"
    registry.write_text(
        json.dumps(
            {
                "dataSources": [
                    {
                        "id": "openmrs-hiv",
                        "label": "OpenMRS HIV/ART program",
                        "connectionUri": "hive2://u:p@unreachable-host:10000/hiv",
                        "dialect": "spark",
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
    assert [s["id"] for s in body["dataSources"]] == ["openelis", "openmrs-hiv"]

    # Targeting the unreachable source fails on its own terms rather than
    # taking the application down or silently falling back to another source.
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
    assert response.status_code >= 400, response.text

    # The other source is untouched by its neighbour being unreachable.
    assert client.get("/v1/catalyst/data-sources").status_code == 200
