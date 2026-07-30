"""Tests for the in-process governed-query orchestrator (LocalHub)."""

from __future__ import annotations

import copy
import json
from unittest.mock import patch

import httpx
import pytest

from src.catalyst import query_engine
from src.catalyst.hub import HubError
from src.catalyst.local_hub import LocalHub
from src.catalyst.query_engine import query_profile_evidence
from src.catalyst.query_profiles import (
    BUNDLED_WRITER_MODEL,
    BUNDLED_WRITER_ONLY,
    DEFAULT_PROFILE_ID,
    PROFILES,
    WRITER_ONLY,
    WRITER_REVIEWED,
)
from src.catalyst.service import CatalystService
from src.catalyst.storage import WorkbenchStore

VIEW_NAME = "analytics.lab_result_fact_v1"
TARGET = {
    "dataSource": "openelis-demo-analytics",
    "catalogVersion": "analytics-catalog-v1",
    "dialect": "postgresql",
}


def _hub() -> LocalHub:
    advertised_models = sorted(
        {model for profile in PROFILES.values() for model in profile.models.values()}
    )

    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [],
                    "backend": {
                        "contract_version": (
                            "med-agent-hub.backend-model-inventory.v1"
                        ),
                        "catalog_reachable": True,
                        "advertised_model_ids": advertised_models,
                    },
                },
            )
        return httpx.Response(200, json={"status": "healthy"})

    return LocalHub(
        hub_base_url="http://hub",
        transport=httpx.MockTransport(transport),
    )


def _hub_with_inventory(
    advertised_models: set[str], *, backend_reachable: bool = True
) -> LocalHub:
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [],
                    "backend": {
                        "contract_version": (
                            "med-agent-hub.backend-model-inventory.v1"
                        ),
                        "catalog_reachable": backend_reachable,
                        "advertised_model_ids": sorted(advertised_models),
                    },
                },
            )
        return httpx.Response(200, json={"status": "healthy"})

    return LocalHub(
        hub_base_url="http://hub",
        transport=httpx.MockTransport(transport),
    )


def _request(profile_id: str) -> dict:
    return {
        "model": profile_id,
        "messages": [
            {"role": "user", "content": "Show viral load results since 2026-01-01"}
        ],
        "catalystQuery": {
            "contractVersion": "catalyst.query.request.v1",
            "requiredOutputContract": "catalyst.query.v1",
            "target": copy.deepcopy(TARGET),
            "catalog": {
                "contextSourceId": "catalog:analytics-catalog-v1",
                "views": [
                    {
                        "name": VIEW_NAME,
                        "version": "1",
                        "grain": "one row per finalized lab result",
                        "fields": [
                            {"name": "viral_load_value", "type": "decimal"},
                            {"name": "release_date", "type": "date"},
                        ],
                    }
                ],
            },
            "policy": {
                "allowedOperation": "select",
                "requirePreview": True,
                "maxRows": 100,
                "statementTimeoutMs": 5000,
            },
            "correlation": {"requestId": "r1", "traceId": "t1"},
        },
    }


def _ready_candidate() -> dict:
    return {
        "status": "ready",
        "target": {**TARGET, "approvedViews": [VIEW_NAME]},
        "sql": f"SELECT viral_load_value, release_date FROM {VIEW_NAME} WHERE release_date >= :since",
        "parameters": [
            {
                "name": "since",
                "type": "date",
                "source": "question",
                "value": "2026-01-01",
            }
        ],
        "expectedColumns": [
            {"name": "viral_load_value", "logicalType": "decimal", "nullable": False},
            {"name": "release_date", "logicalType": "date", "nullable": False},
        ],
    }


def _approve_review() -> dict:
    return {
        "decision": "approve",
        "checks": [{"name": "catalog_and_policy", "status": "passed", "message": "ok"}],
    }


def _queued(responses: list):
    queue = [r if isinstance(r, str) else json.dumps(r) for r in responses]

    async def fake_backend(client, model, messages, **kwargs) -> str:
        return queue.pop(0)

    return fake_backend


@pytest.mark.asyncio
async def test_discovery_lists_profiles_with_matching_evidence():
    hub = _hub()
    profiles = await hub.list_query_profiles()
    await hub.aclose()

    by_id = {p["id"]: p for p in profiles}
    assert set(by_id) == set(PROFILES)

    writer_only = by_id["catalyst-query-gemma-4-12b-q4"]
    reviewed = by_id["catalyst-query-gemma-4-12b-q4-checked"]

    # Writer-only advertises no reviewer role and no review stage.
    assert "reviewer" not in writer_only["profileEvidence"]
    assert "query_review" not in writer_only["stages"]
    assert writer_only["revisionCapable"] is True
    assert writer_only["profileEvidence"]["writer"]["config"]["maxTokens"] == 1024
    assert writer_only["profileEvidence"]["writer"]["systemPrompt"]["promptRef"] == (
        "catalyst-gateway:src/catalyst/prompts/catalyst-query-generate.txt"
    )

    # Reviewed advertises both roles and the review stage.
    assert reviewed["profileEvidence"]["reviewer"]["role"] == "reviewer"
    assert reviewed["profileEvidence"]["reviewer"]["config"]["maxTokens"] == 1024
    assert "query_review" in reviewed["stages"]
    assert reviewed["profileEvidence"]["reviewer"]["systemPrompt"]["promptRef"] == (
        "catalyst-gateway:src/catalyst/prompts/catalyst-query-review.txt"
    )


@pytest.mark.asyncio
async def test_discovery_only_marks_profiles_with_all_role_models_available():
    hub = _hub_with_inventory({"gemma-4-12b", "qwen2.5-14b"})
    profiles = {profile["id"]: profile for profile in await hub.list_query_profiles()}
    readiness = await hub.readiness()
    await hub.aclose()

    assert {
        profile_id for profile_id, profile in profiles.items() if profile["available"]
    } == {
        "catalyst-query-gemma-4-12b",
        "catalyst-query-gemma-4-12b-qwen2.5-14b-checked",
    }
    assert profiles["catalyst-query-gemma-4-12b-q4"]["unavailable_reasons"] == [
        "model_not_advertised:gemma-4-12b-q4"
    ]
    assert profiles["catalyst-query-qwen-coder-1.5b"]["unavailable_reasons"] == [
        "model_not_advertised:qwen2.5-coder-1.5b-instruct-q4_k_m"
    ]
    assert (
        profiles["catalyst-query-gemma-4-12b-qwen2.5-14b-checked"]["revisionCapable"]
        is True
    )
    assert readiness["queryProfile"]["ready"] is True
    assert readiness["modelRouter"]["ready"] is True


@pytest.mark.asyncio
async def test_discovery_fails_closed_when_backend_inventory_is_unreachable():
    hub = _hub_with_inventory(set(), backend_reachable=False)
    profiles = await hub.list_query_profiles()
    readiness = await hub.readiness()
    await hub.aclose()

    assert profiles
    assert all(profile["available"] is False for profile in profiles)
    assert all(
        profile["unavailable_reasons"] == ["model_backend_unreachable"]
        for profile in profiles
    )
    assert readiness["hub"]["ready"] is True
    assert readiness["queryProfile"] == {
        "ready": False,
        "unavailableReasons": ["model_backend_unreachable"],
    }
    assert readiness["modelRouter"]["ready"] is False


@pytest.mark.asyncio
async def test_discovery_distinguishes_an_empty_reachable_router_catalog():
    hub = _hub_with_inventory(set())
    profiles = await hub.list_query_profiles()
    readiness = await hub.readiness()
    await hub.aclose()

    assert all(profile["available"] is False for profile in profiles)
    assert all(
        profile["unavailable_reasons"]
        and all(
            reason.startswith("model_not_advertised:")
            for reason in profile["unavailable_reasons"]
        )
        for profile in profiles
    )
    assert readiness["queryProfile"] == {
        "ready": False,
        "unavailableReasons": ["no_configured_profile_models_available"],
    }
    assert readiness["modelRouter"]["ready"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        (503, {"detail": "hub unavailable"}),
        (200, {"object": "list", "data": []}),
        (
            200,
            {
                "backend": {
                    "contract_version": ("med-agent-hub.backend-model-inventory.v1"),
                    "catalog_reachable": True,
                    "advertised_model_ids": ["", 42],
                }
            },
        ),
    ],
)
async def test_discovery_fails_closed_when_inventory_cannot_be_verified(
    status_code: int, payload: dict
):
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(status_code, json=payload)
        return httpx.Response(200, json={"status": "healthy"})

    hub = LocalHub(
        hub_base_url="http://hub",
        transport=httpx.MockTransport(transport),
    )
    profiles = await hub.list_query_profiles()
    await hub.aclose()

    assert all(profile["available"] is False for profile in profiles)
    assert all(
        profile["unavailable_reasons"] == ["model_inventory_unavailable"]
        for profile in profiles
    )


@pytest.mark.asyncio
async def test_generate_reviewed_profile_returns_ready_query():
    hub = _hub()
    calls = []

    async def backend(client, model, messages, **kwargs):
        calls.append({"model": model, **kwargs})
        return json.dumps(_ready_candidate() if len(calls) == 1 else _approve_review())

    with patch.object(
        query_engine,
        "_backend_chat",
        side_effect=backend,
    ):
        result = await hub.generate_query(
            _request("catalyst-query-gemma-4-12b-q4-checked")
        )
    await hub.aclose()

    assert result["status"] == "ready"
    assert result["provenance"]["profileId"] == "catalyst-query-gemma-4-12b-q4-checked"
    # Discovery evidence must equal generation evidence (the binding invariant).
    discovery = {p["id"]: p for p in await _hub().list_query_profiles()}
    assert (
        result["_hubEvidence"]["profileEvidence"]
        == discovery["catalyst-query-gemma-4-12b-q4-checked"]["profileEvidence"]
    )
    assert [call["max_tokens"] for call in calls] == [1024, 1024]


@pytest.mark.asyncio
async def test_generate_writer_only_profile_returns_ready_query():
    hub = _hub()
    with patch.object(
        query_engine, "_backend_chat", side_effect=_queued([_ready_candidate()])
    ):
        result = await hub.generate_query(_request("catalyst-query-gemma-4-12b-q4"))
    await hub.aclose()

    assert result["status"] == "ready"
    assert result["provenance"]["profileId"] == "catalyst-query-gemma-4-12b-q4"
    assert "reviewer" not in result["_hubEvidence"]["profileEvidence"]


@pytest.mark.asyncio
async def test_generate_records_backend_rejection_as_transport_failure():
    request = httpx.Request("POST", "http://hub/v1/hub/generate")
    response = httpx.Response(502, request=request)
    backend_error = httpx.HTTPStatusError(
        "model backend returned 502",
        request=request,
        response=response,
    )
    hub = _hub()
    with patch.object(query_engine, "_backend_chat", side_effect=backend_error):
        result = await hub.generate_query(_request("catalyst-query-gemma-4-12b-q4"))
    await hub.aclose()

    assert result["status"] == "rejected"
    assert result["message"] == (
        "The model backend rejected the query-generation request (HTTP 502)."
    )
    invocations = result["_hubEvidence"]["modelInvocations"]
    assert len(invocations) == 1
    assert invocations[0]["role"] == "writer"
    assert invocations[0]["outcome"] == "transport_failed"
    assert invocations[0]["failureDigest"]
    assert invocations[0]["responseDigest"] is None


@pytest.mark.asyncio
async def test_bundled_profile_resolves_exact_model_without_reviewer():
    hub = _hub()
    profiles = {profile["id"]: profile for profile in await hub.list_query_profiles()}
    await hub.aclose()

    bundled = profiles["catalyst-query-qwen-coder-1.5b"]
    assert PROFILES[bundled["id"]] is BUNDLED_WRITER_ONLY
    assert BUNDLED_WRITER_ONLY.models == {"query_generate": BUNDLED_WRITER_MODEL}
    assert BUNDLED_WRITER_MODEL == "qwen2.5-coder-1.5b-instruct-q4_k_m"
    assert BUNDLED_WRITER_ONLY.knobs == {
        "query_generate": {"temperature": 0, "dry": 0, "maxTokens": 1024}
    }
    assert BUNDLED_WRITER_ONLY.prompts == {"query_generate": "catalyst-query-generate"}
    assert BUNDLED_WRITER_ONLY.policies["allowed_operation"] == "select"
    assert bundled["role_models"] == {
        "query_generate": "qwen2.5-coder-1.5b-instruct-q4_k_m"
    }
    assert bundled["required_models"] == ["qwen2.5-coder-1.5b-instruct-q4_k_m"]
    assert "reviewer" not in bundled["profileEvidence"]
    assert "query_review" not in bundled["stages"]
    assert bundled["revisionCapable"] is True
    assert DEFAULT_PROFILE_ID == WRITER_ONLY.id


@pytest.mark.asyncio
async def test_bundled_writer_only_profile_accepts_revision_context():
    request = _request(BUNDLED_WRITER_ONLY.id)
    base = _ready_candidate()
    base_digest = "a" * 64
    request["messages"] = [
        {"role": "user", "content": "Keep the current query and lower its limit"}
    ]
    request["catalystQuery"]["contractVersion"] = "catalyst.query.request.v2"
    request["catalystQuery"]["revision"] = {
        "contractVersion": "catalyst.query.revision-context.v1",
        "turnId": "turn-followup",
        "currentInstruction": request["messages"][0]["content"],
        "instructionDigest": "b" * 64,
        "baseClassification": "stored_version",
        "observedBase": {"versionId": "query-v1", "queryDigest": base_digest},
        "effectiveBaseVersion": {
            "versionId": "query-v1",
            "queryDigest": base_digest,
        },
        "editorSnapshot": {
            "contractVersion": "catalyst.workbench.editor-snapshot.v1",
            "sql": base["sql"],
            "parameters": base["parameters"],
            "expectedColumns": base["expectedColumns"],
            "editorDigest": base_digest,
        },
        "instructionHistory": [
            {
                "turnId": "turn-initial",
                "instruction": "Show viral load results since 2026-01-01",
            }
        ],
        "validationContext": None,
        "executionContext": None,
        "selection": {"omissions": {"prohibitedClasses": []}},
        "contextDigest": "c" * 64,
    }
    calls = []

    async def backend(client, model, messages, **kwargs):
        calls.append({"model": model, "payload": json.loads(messages[-1]["content"])})
        return json.dumps(base)

    hub = _hub()
    with patch.object(query_engine, "_backend_chat", side_effect=backend):
        result = await hub.generate_query(request)
    await hub.aclose()

    assert result["status"] == "ready"
    assert result["provenance"]["profileId"] == BUNDLED_WRITER_ONLY.id
    assert len(calls) == 1
    assert calls[0]["model"] == BUNDLED_WRITER_MODEL
    assert calls[0]["payload"]["instruction"] == request["messages"][0]["content"]
    assert calls[0]["payload"]["revision"] == request["catalystQuery"]["revision"]


def _discovery(profile) -> dict:
    return {
        "id": profile.id,
        "label": profile.label,
        "profileEvidence": query_profile_evidence(profile),
    }


def test_writer_only_turn_snapshot_omits_reviewer_with_empty_omissions():
    # Regression: recorded turns must keep omissions empty, and a writer-only
    # profile has no reviewer leg. (This only failed against the live contract;
    # the unit suite never drove a writer-only turn through the snapshot builder.)
    snap = CatalystService._turn_profile_snapshot(_discovery(WRITER_ONLY))
    assert snap["writer"]["role"] == "writer"
    assert "reviewer" not in snap
    assert snap["omissions"] == []


def test_reviewed_turn_snapshot_keeps_reviewer():
    snap = CatalystService._turn_profile_snapshot(_discovery(WRITER_REVIEWED))
    assert snap["reviewer"]["role"] == "reviewer"
    assert snap["omissions"] == []


def test_writer_only_generation_evidence_detail_has_writer_no_reviewer():
    # Regression: the generation-evidence profileDetail must be a real object for
    # recorded turns; writer-only builds it from the writer alone.
    descriptor = WorkbenchStore._hub_profile_descriptor(
        query_profile_evidence(WRITER_ONLY), compact_digest="0" * 64
    )
    detail = descriptor["detail"]
    assert isinstance(detail, dict)
    assert detail["writer"]["role"] == "writer"
    assert "reviewer" not in detail
    assert descriptor["profileRef"] == (
        "catalyst-gateway:/v1/catalyst/query-options/" "catalyst-query-gemma-4-12b-q4"
    )

    current = query_profile_evidence(WRITER_ONLY)
    current_descriptor = WorkbenchStore._evidence_profile_descriptor(
        current, profile_evidence=current
    )
    assert current_descriptor["profileRef"] == descriptor["profileRef"]


@pytest.mark.asyncio
async def test_unknown_profile_raises():
    hub = _hub()
    with pytest.raises(HubError) as excinfo:
        await hub.generate_query(_request("nope"))
    await hub.aclose()
    assert excinfo.value.code == "profile_unavailable"


@pytest.mark.asyncio
async def test_gpu_lane_offers_a_full_weight_writer_and_a_cross_family_reviewer():
    """The Q4 profiles exist for hosts with no GPU; a host that has one should
    not be stuck paying for quantisation, and should be able to pick a reviewer
    that is not the writer re-reading its own output."""

    hub = _hub()
    profiles = await hub.list_query_profiles()
    await hub.aclose()
    by_id = {p["id"]: p for p in profiles}

    team = PROFILES["catalyst-query-gemma-4-12b-qwen2.5-14b-checked"]
    writer = PROFILES["catalyst-query-gemma-4-12b"]

    # Full-weight writer, not the Q4 demo build.
    assert writer.models["query_generate"] == "gemma-4-12b"
    assert not writer.has_review

    # The team's reviewer is a different model AND a different family, which is
    # what makes its review independent rather than a self-check.
    assert team.models["query_generate"] == "gemma-4-12b"
    assert team.models["query_review"] == "qwen2.5-14b"
    assert team.models["query_generate"] != team.models["query_review"]
    classes = team.policies["model_classes"]
    assert classes["query_generate"] != classes["query_review"]
    assert team.policies["collaborative_review"] is True

    # Contrast: the CPU-only "checked" profile is the same model twice.
    assert (
        WRITER_REVIEWED.models["query_generate"]
        == WRITER_REVIEWED.models["query_review"]
    )

    # Both new profiles are discoverable, and the team advertises its reviewer.
    assert {writer.id, team.id} <= set(by_id)
    assert by_id[team.id]["profileEvidence"]["reviewer"]["role"] == "reviewer"
    assert "query_review" in by_id[team.id]["stages"]
    assert "query_review" not in by_id[writer.id]["stages"]
