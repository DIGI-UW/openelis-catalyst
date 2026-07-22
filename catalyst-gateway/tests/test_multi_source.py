"""Two-source routing: turns target a data source; switching mid-session works."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src import gateway
from src.catalyst.catalog import Catalog
from src.catalyst.contracts import ContractRegistry
from src.catalyst.policy import SqlPolicy
from src.catalyst.service import CatalystService, DataSourceBundle
from src.catalyst.storage import PreviewStore, WorkbenchStore

from test_workbench_routes import (
    CONTRACTS,
    PROFILE_ID,
    QUESTION,
    FakeAnalytics,
    FakeHub,
    _catalog,
    _ready_query,
)


class CountingAnalytics(FakeAnalytics):
    def __init__(self) -> None:
        super().__init__()
        self.manual_executions = 0

    async def execute_manual(self, **kwargs):
        self.manual_executions += 1
        return await super().execute_manual(**kwargs)


def _hiv_catalog() -> Catalog:
    return Catalog(
        data_source="openmrs-hiv-demo",
        catalog_version="2026.07-hiv",
        schema_version="analytics-v1",
        dialect="postgresql",
        context_source_id="catalog:openmrs-hiv-demo:2026.07-hiv",
        views=[
            {
                "name": "analytics.hiv_visit_fact",
                "version": "1",
                "grain": "one row per encounter",
                "fields": [
                    {
                        "name": "cd4_count",
                        "type": "number",
                        "description": "CD4 cells/uL",
                    },
                    {
                        "name": "visit_date",
                        "type": "date",
                        "description": "Encounter date",
                    },
                ],
            }
        ],
        freshness={},
    )


def _two_source_client(
    tmp_path: Path,
) -> tuple[TestClient, FakeHub, CountingAnalytics, CountingAnalytics]:
    database = tmp_path / "gateway.sqlite3"
    analytics_a = CountingAnalytics()
    analytics_b = CountingAnalytics()
    catalog_a = _catalog()
    hub = FakeHub(_ready_query())
    bundles = (
        DataSourceBundle(
            source_id="openelis",
            label="OpenELIS Laboratory",
            catalog=catalog_a,
            analytics=analytics_a,
        ),
        DataSourceBundle(
            source_id="openmrs-hiv",
            label="OpenMRS HIV",
            catalog=_hiv_catalog(),
            analytics=analytics_b,
        ),
    )
    service = CatalystService(
        contracts=ContractRegistry.load(CONTRACTS),
        catalog=catalog_a,
        hub=hub,
        analytics=analytics_a,
        store=PreviewStore(database),
        workbench_store=WorkbenchStore(database),
        sql_policy=SqlPolicy(max_rows=2),
        max_rows=2,
        statement_timeout_ms=500,
        data_sources=bundles,
        default_data_source_id="openelis",
    )
    client = TestClient(gateway.create_app(catalyst_service=service))
    return client, hub, analytics_a, analytics_b


def _create_session(client: TestClient, **extra) -> dict:
    response = client.post(
        "/v1/catalyst/workbench/sessions",
        json={
            "contractVersion": "catalyst.workbench.session.request.v1",
            "deploymentMode": "demo",
            "question": QUESTION,
            "profileId": PROFILE_ID,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_data_sources_endpoint_lists_both(tmp_path: Path) -> None:
    client, _, _, _ = _two_source_client(tmp_path)
    body = client.get("/v1/catalyst/data-sources").json()
    assert body["defaultDataSourceId"] == "openelis"
    assert [s["id"] for s in body["dataSources"]] == ["openelis", "openmrs-hiv"]


def test_session_defaults_to_default_source(tmp_path: Path) -> None:
    client, hub, _, _ = _two_source_client(tmp_path)
    session = _create_session(client)
    assert session["dataSourceId"] == "openelis"
    request_blob = json.dumps(hub.requests[0])
    assert "openelis-demo" in request_blob
    assert "openmrs-hiv-demo" not in request_blob


def test_session_targets_requested_source(tmp_path: Path) -> None:
    client, hub, _, _ = _two_source_client(tmp_path)
    session = _create_session(client, dataSourceId="openmrs-hiv")
    assert session["dataSourceId"] == "openmrs-hiv"
    request_blob = json.dumps(hub.requests[0])
    assert "openmrs-hiv-demo" in request_blob
    assert "analytics.hiv_visit_fact" in request_blob
    assert "openelis-demo" not in request_blob


def test_unknown_source_is_rejected(tmp_path: Path) -> None:
    client, _, _, _ = _two_source_client(tmp_path)
    response = client.post(
        "/v1/catalyst/workbench/sessions",
        json={
            "contractVersion": "catalyst.workbench.session.request.v1",
            "deploymentMode": "demo",
            "question": QUESTION,
            "profileId": PROFILE_ID,
            "dataSourceId": "does-not-exist",
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "unknown_data_source"


def _post_turn(
    client: TestClient,
    session_id: str,
    base_version: dict,
    instruction: str,
    **extra,
):
    return client.post(
        f"/v1/catalyst/workbench/sessions/{session_id}/turns",
        json={
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": instruction,
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": base_version["versionId"],
                "queryDigest": base_version["queryDigest"],
            },
            "editorSnapshot": {
                "contractVersion": "catalyst.workbench.editor-snapshot.v1",
                "sql": base_version["sql"],
                "parameters": base_version["parameters"],
                "expectedColumns": base_version["expectedColumns"],
                "editorDigest": base_version["queryDigest"],
            },
            **extra,
        },
    )


def test_followup_switches_source_mid_session(tmp_path: Path) -> None:
    """The 'adapt this query to the other data source' flow."""
    client, hub, _, _ = _two_source_client(tmp_path)
    session = _create_session(client)  # starts on openelis
    current = session["currentVersion"]

    response = _post_turn(
        client,
        session["sessionId"],
        current,
        "Adapt this query to the HIV data source",
        dataSourceId="openmrs-hiv",
    )
    assert response.status_code == 201, response.text
    turn = response.json()
    assert turn["dataSourceId"] == "openmrs-hiv"

    # The generation request carries the NEW source's catalog while the
    # revision context still references the prior (openelis) query text —
    # exactly what "adapt to this source" needs. No stale-catalog 409.
    followup_request = json.dumps(hub.requests[-1])
    assert "openmrs-hiv-demo" in followup_request
    assert "analytics.hiv_visit_fact" in followup_request
    assert current["sql"] in followup_request

    # A third turn with no dataSourceId inherits the switched source.
    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()
    assert timeline["turns"][-1]["dataSourceId"] == "openmrs-hiv"


def test_session_reload_reports_current_source_after_switch(tmp_path: Path) -> None:
    """GET session reflects last-turn-wins, so a UI reload does not snap the
    switcher back to the session's initial source (and then silently target
    the wrong source on the next follow-up)."""
    client, _, _, _ = _two_source_client(tmp_path)
    session = _create_session(client)  # starts on openelis
    response = _post_turn(
        client,
        session["sessionId"],
        session["currentVersion"],
        "Adapt this query to the HIV data source",
        dataSourceId="openmrs-hiv",
    )
    assert response.status_code == 201, response.text

    reloaded = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}"
    ).json()
    assert reloaded["dataSourceId"] == "openmrs-hiv"


def test_execution_routes_to_version_source_adapter(tmp_path: Path) -> None:
    client, _, analytics_a, analytics_b = _two_source_client(tmp_path)
    session = _create_session(client, dataSourceId="openmrs-hiv")
    current = session["currentVersion"]

    response = client.post(
        f"/v1/catalyst/workbench/versions/{current['versionId']}/execute",
        json={
            "contractVersion": "catalyst.workbench.execute.request.v1",
            "versionId": current["versionId"],
            "queryDigest": current["queryDigest"],
            "idempotencyKey": "multi-source-exec-1",
        },
    )
    assert response.status_code in (200, 201), response.text
    assert analytics_b.manual_executions == 1
    assert analytics_a.manual_executions == 0
