from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.catalyst.analytics import AnalyticsColumn, ManualAnalyticsResult
from src.catalyst.catalog import Catalog
from src.catalyst.contracts import ContractRegistry
from src.catalyst.digest import canonical_sha256, utf8_sha256
from src.catalyst.policy import SqlPolicy
from src.catalyst.service import CatalystService
from src.catalyst.storage import (
    PreviewStore,
    WorkbenchStorageError,
    WorkbenchStore,
)


CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"
PROFILE_ID = "catalyst-query-checked"


def _base_catalog() -> Catalog:
    return Catalog(
        data_source="openelis-demo",
        catalog_version="catalog-v1",
        schema_version="analytics-v1",
        dialect="postgresql",
        context_source_id="catalog:catalog-v1",
        views=[
            {
                "name": "analytics.lab_results",
                "version": "1",
                "grain": "one row per result",
                "fields": [
                    {
                        "name": "test_name",
                        "type": "string",
                        "description": "Test name",
                        "nullable": False,
                    }
                ],
            }
        ],
        freshness={},
    )


def _relations(*, drifted: bool = False) -> list[dict]:
    fields = [
        {
            "name": "test_name",
            "type": "string",
            "databaseType": "text",
            "description": "Test name",
            "nullable": False,
        }
    ]
    if drifted:
        fields.append(
            {
                "name": "new_column",
                "type": "string",
                "databaseType": "text",
                "description": "A newly discovered column",
                "nullable": True,
            }
        )
    return [
        {
            "name": "analytics.lab_results",
            "relationType": "view",
            "grain": "one row per result",
            "fields": fields,
        }
    ]


def _profile_evidence() -> dict:
    writer_prompt = "Write one complete PostgreSQL query."
    reviewer_prompt = "Review one complete PostgreSQL query."
    evidence = {
        "profileId": PROFILE_ID,
        "profileName": "Catalyst checked",
        "profileDigest": "0" * 64,
        "writer": {
            "role": "writer",
            "providerId": "llama.cpp",
            "modelClass": "gemma",
            "modelId": "gemma-4-12b",
            "config": {"temperature": 0},
            "systemPrompt": {
                "promptId": "catalyst-writer",
                "version": "1",
                "promptRef": "hub:prompt:catalyst-writer",
                "promptDigest": utf8_sha256(writer_prompt),
                "text": writer_prompt,
            },
        },
        "reviewer": {
            "role": "reviewer",
            "providerId": "llama.cpp",
            "modelClass": "qwen",
            "modelId": "qwen-2.5-14b",
            "config": {"temperature": 0},
            "systemPrompt": {
                "promptId": "catalyst-reviewer",
                "version": "1",
                "promptRef": "hub:prompt:catalyst-reviewer",
                "promptDigest": utf8_sha256(reviewer_prompt),
                "text": reviewer_prompt,
            },
        },
    }
    compact = deepcopy(evidence)
    compact.pop("profileDigest")
    compact["writer"]["systemPrompt"].pop("text")
    compact["reviewer"]["systemPrompt"].pop("text")
    evidence["profileDigest"] = canonical_sha256(compact)
    return evidence


class LineageHub:
    def __init__(
        self,
        *,
        mismatched_response_profile: bool = False,
        omit_reviewer_invocation: bool = False,
        mismatched_reviewer_model: bool = False,
        writer_outcome: str = "succeeded",
        reviewer_outcome: str = "succeeded",
        repair_linted_writer: bool = False,
    ) -> None:
        self.requests: list[dict] = []
        self.mismatched_response_profile = mismatched_response_profile
        self.omit_reviewer_invocation = omit_reviewer_invocation
        self.mismatched_reviewer_model = mismatched_reviewer_model
        self.writer_outcome = writer_outcome
        self.reviewer_outcome = reviewer_outcome
        self.repair_linted_writer = repair_linted_writer

    async def list_query_profiles(self) -> list[dict]:
        evidence = _profile_evidence()
        return [
            {
                "id": PROFILE_ID,
                "label": "Catalyst checked",
                "available": True,
                "revisionCapable": True,
                "required_models": ["gemma-4-12b", "qwen-2.5-14b"],
                "role_models": {
                    "query_generate": "gemma-4-12b",
                    "query_review": "qwen-2.5-14b",
                },
                "stages": ["query_generate", "query_review"],
                "profileEvidence": evidence,
            }
        ]

    async def generate_query(self, request: dict) -> dict:
        self.requests.append(deepcopy(request))
        context = request["catalystQuery"]
        query = {
            "contractVersion": "catalyst.query.v1",
            "deploymentMode": "demo",
            "status": "ready",
            "question": request["messages"][0]["content"],
            "target": {
                **context["target"],
                "approvedViews": ["analytics.lab_results"],
            },
            "sql": "SELECT test_name FROM analytics.lab_results LIMIT 2",
            "parameters": [],
            "expectedColumns": [
                {"name": "test_name", "logicalType": "string", "nullable": False}
            ],
            "validation": {"status": "passed", "checks": []},
            "provenance": {
                "profileId": PROFILE_ID,
                "traceId": "hub-lineage-trace",
                "contextSourceIds": [context["catalog"]["contextSourceId"]],
            },
        }
        if self.repair_linted_writer:
            query["sql"] = query["sql"].replace(
                " LIMIT 2", " ORDER BY test_name LIMIT 2"
            )
            final_candidate = {
                key: deepcopy(query[key])
                for key in (
                    "status",
                    "target",
                    "sql",
                    "parameters",
                    "expectedColumns",
                )
            }
            writer_candidate = deepcopy(final_candidate)
            writer_candidate["sql"] = (
                "SELECT invented_writer_column FROM analytics.lab_results LIMIT 2"
            )
            writer_candidate["expectedColumns"] = [
                {
                    "name": "invented_writer_column",
                    "logicalType": "string",
                    "nullable": False,
                }
            ]
            query["modelCollaboration"] = {
                "writer": {
                    "model": "gemma-4-12b",
                    "candidate": writer_candidate,
                    "lintFindings": [
                        {
                            "code": "catalog.unknown_column",
                            "stage": "catalog_identifiers",
                            "severity": "error",
                            "path": "sql",
                            "message": "SQL references a field absent from the catalog.",
                        }
                    ],
                },
                "reviewer": {
                    "model": "qwen-2.5-14b",
                    "decision": "repair",
                    "candidate": final_candidate,
                    "checks": [{"name": "field-grounding", "status": "passed"}],
                },
                "finalLintFindings": [],
            }
            if context["contractVersion"] == "catalyst.query.request.v2":
                query["modelCollaboration"]["writer"]["disposition"] = "superseded"
                query["modelCollaboration"]["reviewer"]["disposition"] = "selected"
        response_profile = _profile_evidence()
        if self.mismatched_response_profile:
            response_profile["profileId"] = "catalyst-query-other"
        invocations = [
            {
                "invocationId": "00000000-0000-4000-8000-000000000101",
                "role": "writer",
                "stage": (
                    "followup_generation"
                    if context["contractVersion"] == "catalyst.query.request.v2"
                    else "initial_generation"
                ),
                "attempt": 1,
                "providerId": "llama.cpp",
                "modelId": "gemma-4-12b",
                "configuration": {
                    "temperature": 0,
                    "dryMultiplier": 0,
                    "maxTokens": None,
                    "responseFormat": "catalyst_query_candidate_v1",
                },
                "startedAt": "2026-07-20T12:00:00Z",
                "endedAt": "2026-07-20T12:00:01Z",
                "durationMs": 1000,
                "requestDigest": canonical_sha256(request),
                "responseDigest": canonical_sha256(query),
                "failureDigest": (
                    canonical_sha256(
                        {
                            "outcome": (
                                "validation_failed"
                                if self.repair_linted_writer
                                else self.writer_outcome
                            )
                        }
                    )
                    if self.repair_linted_writer or self.writer_outcome != "succeeded"
                    else None
                ),
                "outcome": (
                    "validation_failed"
                    if self.repair_linted_writer
                    else self.writer_outcome
                ),
            }
        ]
        if not self.omit_reviewer_invocation:
            invocations.append(
                {
                    "invocationId": "00000000-0000-4000-8000-000000000102",
                    "role": "reviewer",
                    "stage": "review",
                    "attempt": 1,
                    "providerId": "llama.cpp",
                    "modelId": (
                        "unexpected-reviewer"
                        if self.mismatched_reviewer_model
                        else "qwen-2.5-14b"
                    ),
                    "configuration": {
                        "temperature": 0,
                        "dryMultiplier": 0,
                        "maxTokens": None,
                        "responseFormat": "catalyst_query_review_v1",
                    },
                    "startedAt": "2026-07-20T12:00:01Z",
                    "endedAt": "2026-07-20T12:00:02Z",
                    "durationMs": 1000,
                    "requestDigest": canonical_sha256(
                        {"request": request, "role": "reviewer"}
                    ),
                    "responseDigest": canonical_sha256(
                        {"query": query, "role": "reviewer"}
                    ),
                    "failureDigest": (
                        canonical_sha256({"outcome": self.reviewer_outcome})
                        if self.reviewer_outcome != "succeeded"
                        else None
                    ),
                    "outcome": self.reviewer_outcome,
                }
            )
        query["_hubEvidence"] = {
            "profileEvidence": response_profile,
            "modelInvocations": invocations,
            "totalModelInvocationDurationMs": sum(
                invocation["durationMs"] for invocation in invocations
            ),
            "exactHubResponse": json.dumps(query, separators=(",", ":")),
            "hubResponseContentType": "application/json",
        }
        return query

    async def readiness(self) -> dict:
        return {
            "hub": {"ready": True},
            "queryProfile": {"ready": True},
            "modelRouter": {"ready": True},
        }

    async def aclose(self) -> None:
        return None


class DriftingAnalytics:
    def __init__(self) -> None:
        self.drifted = False
        self.manual_calls: list[dict] = []

    async def discover_relations(self) -> list[dict]:
        return _relations(drifted=self.drifted)

    async def execute_manual(self, **kwargs) -> ManualAnalyticsResult:
        self.manual_calls.append(deepcopy(kwargs))
        return ManualAnalyticsResult(
            columns=[AnalyticsColumn(0, "test_name", "text", 25, "string")],
            rows=[[{"type": "string", "value": "Malaria"}]],
            truncated=False,
        )

    async def readiness(self) -> dict:
        return {"ready": True, "dataSource": "openelis-demo"}

    async def dataset_overview(self) -> dict:
        return {
            "contractVersion": "catalyst.dataset-overview.v1",
            "datasetId": "lineage-dataset",
            "dataSource": "openelis-demo",
            "pipelineRunId": "lineage-run",
            "synthetic": True,
            "patients": 1,
            "results": 1,
            "testTypes": 1,
            "firstObservedAt": "2026-01-01T00:00:00Z",
            "lastObservedAt": "2026-01-01T00:00:00Z",
            "tests": [],
            "exampleQuestions": [],
        }


class CatalogSwappingHub(LineageHub):
    def __init__(self) -> None:
        super().__init__()
        self.service: CatalystService | None = None

    async def generate_query(self, request: dict) -> dict:
        query = await super().generate_query(request)
        assert self.service is not None
        default_bundle = self.service._bundles[self.service._default_data_source_id]
        default_bundle.runtime_snapshot = Catalog(
            data_source="openelis-demo",
            catalog_version="unrelated-catalog",
            schema_version="analytics-v1",
            dialect="postgresql",
            context_source_id="catalog:unrelated-catalog",
            views=[
                {
                    "name": "analytics.unrelated",
                    "version": "1",
                    "grain": "one row per unrelated record",
                    "fields": [
                        {
                            "name": "other_value",
                            "type": "string",
                            "description": "Unrelated value",
                            "nullable": True,
                        }
                    ],
                }
            ],
            freshness={},
        )
        return query


def _service(
    tmp_path: Path,
    *,
    hub: LineageHub | None = None,
    catalog: Catalog | None = None,
    default_query_profile_id: str | None = None,
) -> tuple[CatalystService, LineageHub, DriftingAnalytics]:
    actual_hub = hub or LineageHub()
    analytics = DriftingAnalytics()
    database = tmp_path / "lineage.sqlite3"
    service = CatalystService(
        contracts=ContractRegistry.load(CONTRACTS),
        catalog=catalog or _base_catalog(),
        hub=actual_hub,
        analytics=analytics,
        store=PreviewStore(database),
        workbench_store=WorkbenchStore(database),
        sql_policy=SqlPolicy(max_rows=2),
        max_rows=2,
        statement_timeout_ms=500,
        default_query_profile_id=default_query_profile_id,
    )
    return service, actual_hub, analytics


@pytest.mark.asyncio
async def test_query_options_exposes_compact_profile_provenance(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)

    response = await service.query_options()

    assert response.status_code == 200
    profile = response.body["profiles"][0]
    assert (
        profile["provenance"]["profileConfigurationDigest"]
        == (_profile_evidence()["profileDigest"])
    )
    assert profile["provenance"]["rolePromptDigests"] == {
        "query_generate": _profile_evidence()["writer"]["systemPrompt"]["promptDigest"],
        "query_review": _profile_evidence()["reviewer"]["systemPrompt"]["promptDigest"],
    }


@pytest.mark.asyncio
async def test_query_options_uses_runtime_default_profile(tmp_path: Path) -> None:
    service, _, _ = _service(
        tmp_path,
        default_query_profile_id="catalyst-query-gemma-4-12b-qwen2.5-14b-checked",
    )

    response = await service.query_options()

    assert (
        response.body["defaultProfileId"]
        == "catalyst-query-gemma-4-12b-qwen2.5-14b-checked"
    )


async def _create_session(service: CatalystService) -> dict:
    response = await service.create_workbench_session(
        {
            "contractVersion": "catalyst.workbench.session.request.v1",
            "deploymentMode": "demo",
            "question": "Show laboratory results",
            "profileId": PROFILE_ID,
        }
    )
    assert response.status_code == 201
    return response.body


def _snapshot(version: dict) -> dict:
    return {
        "contractVersion": "catalyst.workbench.editor-snapshot.v1",
        "sql": version["sql"],
        "parameters": version["parameters"],
        "expectedColumns": version["expectedColumns"],
        "editorDigest": version["queryDigest"],
    }


def _catalog_with_analyte_dimension() -> Catalog:
    """_base_catalog() plus a semanticDimensions entry naming one analyte, so a
    question/instruction mentioning it must bind it as a query parameter."""
    base = _base_catalog()
    views = deepcopy(base.views)
    views[0]["semanticDimensions"] = [
        {
            "field": "test_name",
            "semanticType": "analyte",
            "values": [{"canonical": "Malaria", "aliases": ["malaria"]}],
        }
    ]
    return Catalog(
        data_source=base.data_source,
        catalog_version=base.catalog_version,
        schema_version=base.schema_version,
        dialect=base.dialect,
        context_source_id=base.context_source_id,
        views=views,
        freshness=base.freshness,
    )


@pytest.mark.asyncio
async def test_manual_validation_uses_latest_turn_instruction(tmp_path: Path) -> None:
    """A human SQL edit is validated against the LATEST turn's instruction, not
    the session's original question: the original question names no analyte
    (no violation), but the turn's instruction names one the SQL never binds."""
    service, _, _ = _service(tmp_path, catalog=_catalog_with_analyte_dimension())
    session = await _create_session(service)
    assert session["latestValidation"]["findings"] == []
    base = session["currentVersion"]
    instruction = "Now just show malaria results"
    followup = await service.create_workbench_turn(
        session["sessionId"],
        {
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": instruction,
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": base["versionId"],
                "queryDigest": base["queryDigest"],
            },
            "editorSnapshot": _snapshot(base),
        },
    )
    assert followup.status_code == 201
    current = service.get_workbench_session(session["sessionId"]).body["currentVersion"]

    saved = await service.create_workbench_version(
        session["sessionId"],
        {
            "contractVersion": "catalyst.workbench.version.request.v1",
            "parentVersionId": current["versionId"],
            "parentQueryDigest": current["queryDigest"],
            "sql": current["sql"].replace("LIMIT 2", "LIMIT 1"),
            "parameters": current["parameters"],
            "expectedColumns": current["expectedColumns"],
        },
    )
    assert saved.status_code == 201
    manual = saved.body["currentVersion"]
    assert any(
        finding["ruleCode"] == "gateway_invariant.missing_semantic_filter"
        for finding in saved.body["latestValidation"]["findings"]
    )

    validated = await service.validate_workbench_version(manual["versionId"])
    assert validated.status_code == 201
    assert any(
        finding["ruleCode"] == "gateway_invariant.missing_semantic_filter"
        for finding in validated.body["findings"]
    )
    await service.aclose()


@pytest.mark.asyncio
async def test_catalog_drift_blocks_all_session_bound_operations_without_mutation(
    tmp_path: Path,
) -> None:
    service, hub, analytics = _service(tmp_path)
    session = await _create_session(service)
    version = session["currentVersion"]
    requests_before = len(hub.requests)
    versions_before = len(session["versions"])
    validations_before = len(session["validations"])
    analytics.drifted = True

    followup = await service.create_workbench_turn(
        session["sessionId"],
        {
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "Only final results",
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": version["versionId"],
                "queryDigest": version["queryDigest"],
            },
            "editorSnapshot": _snapshot(version),
        },
    )
    saved = await service.create_workbench_version(
        session["sessionId"],
        {
            "contractVersion": "catalyst.workbench.version.request.v1",
            "parentVersionId": version["versionId"],
            "parentQueryDigest": version["queryDigest"],
            "sql": version["sql"].replace("LIMIT 2", "LIMIT 1"),
            "parameters": version["parameters"],
            "expectedColumns": version["expectedColumns"],
        },
    )
    validated = await service.validate_workbench_version(version["versionId"])
    executed = await service.execute_workbench_version(
        version["versionId"],
        {
            "contractVersion": "catalyst.workbench.execute.request.v1",
            "versionId": version["versionId"],
            "queryDigest": version["queryDigest"],
            "idempotencyKey": "drifted-run",
        },
    )

    for response in (followup, saved, validated, executed):
        assert response.status_code == 409
        assert response.body["error"]["code"] == "stale_catalog_version"
        assert (
            response.body["error"]["details"]["sessionCatalogVersion"]
            == (session["catalogVersion"])
        )
        assert (
            response.body["error"]["details"]["runtimeCatalogVersion"]
            != (session["catalogVersion"])
        )
    restored = service.get_workbench_session(session["sessionId"]).body
    assert len(restored["versions"]) == versions_before
    assert len(restored["validations"]) == validations_before
    assert restored["executions"] == []
    assert len(service.get_workbench_turns(session["sessionId"]).body["turns"]) == 1
    assert len(hub.requests) == requests_before
    assert analytics.manual_calls == []
    await service.aclose()


@pytest.mark.asyncio
async def test_mismatched_response_profile_is_not_persisted(tmp_path: Path) -> None:
    service, _, _ = _service(
        tmp_path,
        hub=LineageHub(mismatched_response_profile=True),
    )
    session = await _create_session(service)

    assert session["currentVersion"] is None
    turn = service.get_workbench_turns(session["sessionId"]).body["turns"][0]
    assert turn["status"] == "failed"
    assert turn["failure"]["code"] == "hub_invalid_response"
    evidence = service.get_workbench_generation_evidence(
        session["sessionId"], turn["turnId"]
    ).body
    assert evidence["profile"]["profileId"] == PROFILE_ID
    await service.aclose()


@pytest.mark.asyncio
async def test_validation_keeps_request_catalog_when_hub_await_changes_snapshot(
    tmp_path: Path,
) -> None:
    hub = CatalogSwappingHub()
    service, _, _ = _service(tmp_path, hub=hub)
    hub.service = service

    session = await _create_session(service)

    assert session["currentVersion"] is not None
    assert session["latestValidation"]["status"] == "valid"
    assert not any(
        finding["ruleCode"].endswith("relation_not_found")
        for finding in session["latestValidation"]["findings"]
    )
    await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hub",
    [
        LineageHub(omit_reviewer_invocation=True),
        LineageHub(mismatched_reviewer_model=True),
        LineageHub(writer_outcome="validation_failed"),
        LineageHub(reviewer_outcome="validation_failed"),
    ],
    ids=[
        "missing-reviewer",
        "mismatched-reviewer",
        "failed-writer-without-repair",
        "failed-reviewer",
    ],
)
async def test_ready_query_requires_profile_bound_writer_and_reviewer_evidence(
    tmp_path: Path,
    hub: LineageHub,
) -> None:
    service, _, _ = _service(tmp_path, hub=hub)

    session = await _create_session(service)

    assert session["currentVersion"] is None
    turn = service.get_workbench_turns(session["sessionId"]).body["turns"][0]
    assert turn["status"] == "failed"
    assert turn["failure"]["code"] == "hub_invalid_response"
    evidence = service.get_workbench_generation_evidence(
        session["sessionId"], turn["turnId"]
    ).body
    assert evidence["invocations"] == []
    await service.aclose()


@pytest.mark.asyncio
async def test_ready_query_accepts_reviewer_repair_of_linted_writer(
    tmp_path: Path,
) -> None:
    hub = LineageHub()
    service, _, _ = _service(tmp_path, hub=hub)

    session = await _create_session(service)
    base = session["currentVersion"]
    hub.repair_linted_writer = True
    response = await service.create_workbench_turn(
        session["sessionId"],
        {
            "contractVersion": "catalyst.workbench.turn.request.v1",
            "instruction": "Sort these results by test name.",
            "profileId": PROFILE_ID,
            "observedBase": {
                "versionId": base["versionId"],
                "queryDigest": base["queryDigest"],
            },
            "editorSnapshot": {
                "contractVersion": "catalyst.workbench.editor-snapshot.v1",
                "sql": base["sql"],
                "parameters": base["parameters"],
                "expectedColumns": base["expectedColumns"],
                "editorDigest": base["queryDigest"],
            },
        },
    )

    assert response.status_code == 201
    turn = response.body
    assert turn["status"] == "completed"
    assert len(turn["outputVersions"]) == 2
    writer, reviewer = turn["outputVersions"]
    assert reviewer["parentVersionId"] == writer["versionId"]

    restored = service.get_workbench_session(session["sessionId"]).body
    assert restored["currentVersion"]["authorType"] == "model_repair"
    assert "ORDER BY test_name" in restored["currentVersion"]["sql"]
    evidence = service.get_workbench_generation_evidence(
        session["sessionId"], turn["turnId"]
    ).body
    assert [item["outcome"] for item in evidence["invocations"]] == [
        "validation_failed",
        "succeeded",
    ]
    assert [item["configuration"] for item in evidence["invocations"]] == [
        {
            "temperature": 0,
            "dryMultiplier": 0,
            "maxTokens": None,
            "responseFormat": "catalyst_query_candidate_v1",
        },
        {
            "temperature": 0,
            "dryMultiplier": 0,
            "maxTokens": None,
            "responseFormat": "catalyst_query_review_v1",
        },
    ]
    ContractRegistry.load(CONTRACTS).validate(
        "catalyst-workbench-generation-evidence-v1.schema.json", evidence
    )
    await service.aclose()


def test_store_refuses_to_replace_requested_profile_with_response_mismatch(
    tmp_path: Path,
) -> None:
    store = WorkbenchStore(tmp_path / "profile-storage.sqlite3")
    profile_evidence = _profile_evidence()
    profile = {
        "id": PROFILE_ID,
        "profileEvidence": profile_evidence,
    }
    profile_snapshot = CatalystService._turn_profile_snapshot(profile)
    session = store.create_session(
        question="Show laboratory results",
        profile_id=PROFILE_ID,
        dataset_id="lineage-dataset",
        dataset_version="lineage-run",
        catalog_version="catalog-v1",
    )
    turn = store.claim_initial_turn(
        session["sessionId"],
        instruction=session["question"],
        instruction_digest=utf8_sha256(session["question"]),
        profile_snapshot=profile_snapshot,
        catalyst_trace_id="catalyst-profile-storage",
        hub_request={"model": PROFILE_ID},
        profile_evidence=profile_evidence,
    )
    mismatched = deepcopy(profile_evidence)
    mismatched["profileId"] = "catalyst-query-other"

    with pytest.raises(WorkbenchStorageError, match="does not match"):
        store.complete_turn(
            turn["turnId"],
            outputs=[
                {
                    "sql": "SELECT test_name FROM analytics.lab_results LIMIT 2",
                    "parameters": [],
                    "expectedColumns": [
                        {
                            "name": "test_name",
                            "logicalType": "string",
                            "nullable": False,
                        }
                    ],
                    "authorType": "model",
                    "provenance": {"profileId": PROFILE_ID},
                }
            ],
            selected_index=0,
            hub_trace_id="hub-profile-storage",
            hub_response={"profileEvidence": mismatched},
            invocations=[],
        )

    restored = store.list_turns(session["sessionId"])["turns"][0]
    evidence = store.get_generation_evidence(session["sessionId"], turn["turnId"])
    assert restored["status"] == "requested"
    assert evidence is not None
    assert evidence["profile"]["profileId"] == PROFILE_ID
    store.close()
