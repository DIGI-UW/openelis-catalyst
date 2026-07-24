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
from src.catalyst.query_profiles import PROFILES, WRITER_ONLY, WRITER_REVIEWED
from src.catalyst.service import CatalystService
from src.catalyst.storage import WorkbenchStore

VIEW_NAME = "analytics.lab_result_fact_v1"
TARGET = {
    "dataSource": "openelis-demo-analytics",
    "catalogVersion": "analytics-catalog-v1",
    "dialect": "postgresql",
}


def _hub() -> LocalHub:
    return LocalHub(
        hub_base_url="http://hub",
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
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
async def test_discovery_lists_both_profiles_with_matching_evidence():
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
    assert writer_only["revisionCapable"] is False

    # Reviewed advertises both roles and the review stage.
    assert reviewed["profileEvidence"]["reviewer"]["role"] == "reviewer"
    assert "query_review" in reviewed["stages"]


@pytest.mark.asyncio
async def test_generate_reviewed_profile_returns_ready_query():
    hub = _hub()
    with patch.object(
        query_engine,
        "_backend_chat",
        side_effect=_queued([_ready_candidate(), _approve_review()]),
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


@pytest.mark.asyncio
async def test_unknown_profile_raises():
    hub = _hub()
    with pytest.raises(HubError) as excinfo:
        await hub.generate_query(_request("nope"))
    await hub.aclose()
    assert excinfo.value.code == "profile_unavailable"
