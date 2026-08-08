"""Two-source routing: a session is grounded in one source and stays there.

These tests exercise the registry/routing layer with in-memory fakes: they
prove requests reach the right bundle, not that either source's SQL or schema
discovery is correct. SQL semantics are guarded by tests/analytics/ (real
Postgres).
"""

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


class DriftingHivAnalytics(CountingAnalytics):
    """HIV adapter whose discovered schema can drift between turns."""

    def __init__(self) -> None:
        super().__init__()
        self.drifted = False

    async def discover_relations(self) -> list[dict]:
        fields = [
            {
                "name": "cd4_count",
                "type": "number",
                "databaseType": "numeric",
                "description": "CD4 cells/uL",
                "nullable": True,
            }
        ]
        if self.drifted:
            fields.append(
                {
                    "name": "regimen_line",
                    "type": "string",
                    "databaseType": "text",
                    "description": "A newly discovered column",
                    "nullable": True,
                }
            )
        return [
            {
                "name": "analytics.hiv_visit_fact",
                "relationType": "view",
                "grain": "one row per encounter",
                "fields": fields,
            }
        ]


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
                        "type": "decimal",
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
    analytics_b: CountingAnalytics | None = None,
) -> tuple[TestClient, FakeHub, CountingAnalytics, CountingAnalytics]:
    database = tmp_path / "gateway.sqlite3"
    analytics_a = CountingAnalytics()
    analytics_b = analytics_b or CountingAnalytics()
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
        hub=hub,
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


def test_followup_cannot_switch_source_mid_session(tmp_path: Path) -> None:
    """A session is grounded in one data source and cannot be retargeted.

    Query versions chain through parentVersionId and each follow-up is written
    relative to the previous query, so a version whose parent was written
    against a different schema would describe a lineage that never existed.
    Querying another source means starting another session.
    """
    client, hub, _, _ = _two_source_client(tmp_path)
    session = _create_session(client)  # starts on openelis
    hub_requests_before = len(hub.requests)

    response = _post_turn(
        client,
        session["sessionId"],
        session["currentVersion"],
        "Adapt this query to the HIV data source",
        dataSourceId="openmrs-hiv",
    )

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "data_source_immutable"
    assert error["details"] == {
        "sessionDataSourceId": "openelis",
        "requestedDataSourceId": "openmrs-hiv",
    }
    # Rejected before any generation: no model was asked to write the turn.
    assert len(hub.requests) == hub_requests_before

    # Naming the session's own source is not a switch, so it still works.
    accepted = _post_turn(
        client,
        session["sessionId"],
        session["currentVersion"],
        "Now filter to the last 90 days",
        dataSourceId="openelis",
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["dataSourceId"] == "openelis"


def test_version_cannot_switch_source_mid_session(tmp_path: Path) -> None:
    """The manual-edit path is bound by the same rule as the turn path."""
    client, _, _, _ = _two_source_client(tmp_path)
    session = _create_session(client)
    current = session["currentVersion"]

    response = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/versions",
        json={
            "contractVersion": "catalyst.workbench.version.request.v1",
            "parentVersionId": current["versionId"],
            "parentQueryDigest": current["queryDigest"],
            "sql": current["sql"],
            "parameters": current["parameters"],
            "dataSourceId": "openmrs-hiv",
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "data_source_immutable"


def test_session_reload_reports_its_creation_source(tmp_path: Path) -> None:
    """A session reports the source it was created against, for the life of
    the session."""
    client, _, _, _ = _two_source_client(tmp_path)
    session = _create_session(client, dataSourceId="openmrs-hiv")
    assert session["dataSourceId"] == "openmrs-hiv"

    _post_turn(
        client,
        session["sessionId"],
        session["currentVersion"],
        "Now filter to the last 90 days",
    )

    reloaded = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}"
    ).json()
    assert reloaded["dataSourceId"] == "openmrs-hiv"


def test_staleness_is_judged_against_the_session_catalog(tmp_path: Path) -> None:
    """Catalog drift on the session's own source trips the conflict, and the
    409 reports the baseline the session was created against."""
    drifting = DriftingHivAnalytics()
    client, _, _, _ = _two_source_client(tmp_path, analytics_b=drifting)
    session = _create_session(client, dataSourceId="openmrs-hiv")
    baseline = session["catalogVersion"]

    drifting.drifted = True
    conflicted = _post_turn(
        client,
        session["sessionId"],
        session["currentVersion"],
        "Now filter to the last 90 days",
    )

    assert conflicted.status_code == 409, conflicted.text
    error = conflicted.json()["error"]
    assert error["code"] == "stale_catalog_version"
    assert error["details"]["sessionCatalogVersion"] == baseline
    assert error["details"]["runtimeCatalogVersion"] != baseline


def test_dataset_and_editor_catalog_http_params_route_to_bundle(
    tmp_path: Path,
) -> None:
    """?dataSourceId= on the GET endpoints selects the bundle end to end."""

    class HivOverviewAnalytics(CountingAnalytics):
        async def dataset_overview(self) -> dict:
            body = await super().dataset_overview()
            return {**body, "dataSource": "openmrs-hiv-demo"}

    client, _, _, _ = _two_source_client(tmp_path, analytics_b=HivOverviewAnalytics())

    assert client.get("/v1/catalyst/dataset").json()["dataSource"] == "openelis-demo"
    assert (
        client.get("/v1/catalyst/dataset?dataSourceId=openmrs-hiv").json()["dataSource"]
        == "openmrs-hiv-demo"
    )

    default_catalog = client.get("/v1/catalyst/workbench/catalog").json()
    hiv_catalog = client.get(
        "/v1/catalyst/workbench/catalog?dataSourceId=openmrs-hiv"
    ).json()
    assert default_catalog["catalogVersion"] == "2026.07"
    assert hiv_catalog["catalogVersion"] == "2026.07-hiv"

    unknown = client.get("/v1/catalyst/dataset?dataSourceId=does-not-exist")
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "unknown_data_source"


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


def test_sessions_are_named_and_listed_for_the_rail(tmp_path: Path) -> None:
    """The rail's session control needs a name to show and a list to pick from.

    A name is what an analyst calls the thread; the question is immutable
    evidence of what was asked. They are stored separately so renaming a
    session never rewrites what it asked.
    """
    client, _, _, _ = _two_source_client(tmp_path)
    named = _create_session(client, name="Monthly viral load, 2026")
    unnamed = _create_session(client, dataSourceId="openmrs-hiv")

    assert named["name"] == "Monthly viral load, 2026"
    assert named["question"] == QUESTION
    # A session created without a name is called by the question that opened
    # it, which is what the UI displayed before naming existed.
    assert unnamed["name"] == QUESTION

    listing = client.get("/v1/catalyst/workbench/sessions")
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["contractVersion"] == "catalyst.workbench.session-list.v1"

    by_id = {row["sessionId"]: row for row in body["sessions"]}
    assert by_id[named["sessionId"]]["name"] == "Monthly viral load, 2026"
    # Each row carries the source it is grounded in, so the menu can say
    # which catalog a thread belongs to without opening it.
    assert by_id[named["sessionId"]]["dataSourceId"] == "openelis"
    assert by_id[unnamed["sessionId"]]["dataSourceId"] == "openmrs-hiv"
    assert by_id[named["sessionId"]]["turnCount"] == 1

    # Newest first, so the menu opens on what was worked on last.
    assert [row["sessionId"] for row in body["sessions"]][:2] == [
        unnamed["sessionId"],
        named["sessionId"],
    ]


def test_session_name_survives_reload(tmp_path: Path) -> None:
    client, _, _, _ = _two_source_client(tmp_path)
    session = _create_session(client, name="Turnaround time, Q3")

    reloaded = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}"
    ).json()
    assert reloaded["name"] == "Turnaround time, Q3"
    assert reloaded["question"] == QUESTION


def test_session_opens_empty_without_asking_a_model(tmp_path: Path) -> None:
    """Choosing where to work must not require knowing what to ask yet."""
    client, hub, _, _ = _two_source_client(tmp_path)

    response = client.post(
        "/v1/catalyst/workbench/sessions",
        json={
            "contractVersion": "catalyst.workbench.session.request.v1",
            "deploymentMode": "demo",
            "name": "CD4 cohort review",
            "profileId": PROFILE_ID,
            "dataSourceId": "openmrs-hiv",
        },
    )

    assert response.status_code == 201, response.text
    session = response.json()
    assert session["name"] == "CD4 cohort review"
    assert session["question"] == ""
    assert session["dataSourceId"] == "openmrs-hiv"
    assert session["currentVersion"] is None
    assert session["versions"] == []
    # Opening a session is not a generation.
    assert hub.requests == []

    timeline = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()
    assert timeline["turns"] == []
    assert timeline["currentTurnId"] is None

    listed = client.get("/v1/catalyst/workbench/sessions").json()["sessions"]
    assert listed[0]["sessionId"] == session["sessionId"]
    assert listed[0]["turnCount"] == 0


def _open_empty_session(client: TestClient, **extra) -> dict:
    response = client.post(
        "/v1/catalyst/workbench/sessions",
        json={
            "contractVersion": "catalyst.workbench.session.request.v1",
            "deploymentMode": "demo",
            "profileId": PROFILE_ID,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_first_question_seeds_an_empty_session_as_its_initial_turn(
    tmp_path: Path,
) -> None:
    """The first question runs the same initial generation a session created
    with a question runs — an initial turn, not a revision of nothing."""
    client, hub, _, _ = _two_source_client(tmp_path)
    session = _open_empty_session(client, name="CD4", dataSourceId="openmrs-hiv")

    response = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/question",
        json={"question": QUESTION, "profileId": PROFILE_ID},
    )

    assert response.status_code == 201, response.text
    seeded = response.json()
    assert seeded["question"] == QUESTION
    assert seeded["currentVersion"] is not None
    # Generated against the source the session is grounded in.
    assert "openmrs-hiv-demo" in json.dumps(hub.requests[-1])

    turns = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()["turns"]
    assert len(turns) == 1
    # kind: initial keeps its meaning — nothing observed, nothing revised.
    assert turns[0]["kind"] == "initial"
    assert turns[0]["observedBase"] is None
    assert turns[0]["editorSnapshot"] is None
    assert turns[0]["revisionContext"] is None


def test_a_session_is_only_asked_its_first_question_once(tmp_path: Path) -> None:
    client, _, _, _ = _two_source_client(tmp_path)
    session = _open_empty_session(client)
    first = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/question",
        json={"question": QUESTION},
    )
    assert first.status_code == 201, first.text

    again = client.post(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/question",
        json={"question": "Something else entirely"},
    )

    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "session_already_started"


def test_creating_a_session_with_a_question_is_unchanged(tmp_path: Path) -> None:
    """The existing one-step flow keeps generating on creation."""
    client, hub, _, _ = _two_source_client(tmp_path)

    session = _create_session(client)

    assert session["question"] == QUESTION
    assert session["currentVersion"] is not None
    assert len(hub.requests) == 1
    turns = client.get(
        f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns"
    ).json()["turns"]
    assert [turn["kind"] for turn in turns] == ["initial"]
