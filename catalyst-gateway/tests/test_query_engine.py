"""End-to-end run tests for the relocated governed-query engine.

Drives ``execute_query_profile`` with the model-call seam (``_backend_chat``)
mocked, proving the relocated orchestration runs for both the writer-only default
and the writer+reviewer path and emits a valid catalyst.query.v1 result.
"""

from __future__ import annotations

import copy
import json
from unittest.mock import patch

import pytest

from src.catalyst import query_engine
from src.catalyst.query_engine import (
    EngineProfile,
    EngineRequest,
    execute_query_profile,
)

QUESTION = "Show viral load results since 2026-01-01 with value and release date"
FOLLOWUP = "Keep the current query and preserve its columns."
VIEW_NAME = "analytics.lab_result_fact_v1"
TARGET = {
    "dataSource": "openelis-demo-analytics",
    "catalogVersion": "analytics-catalog-v1",
    "dialect": "postgresql",
}
RESPONSE_TARGET = {**TARGET, "approvedViews": [VIEW_NAME]}


def _extension() -> dict:
    return {
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
        "correlation": {
            "requestId": "request-catalyst-query-001",
            "traceId": "trace-catalyst-query-001",
        },
    }


def _ready_candidate() -> dict:
    return {
        "status": "ready",
        "target": copy.deepcopy(RESPONSE_TARGET),
        "sql": (
            "SELECT viral_load_value, release_date "
            f"FROM {VIEW_NAME} WHERE release_date >= :since"
        ),
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
        "checks": [
            {
                "name": "catalog_and_policy",
                "status": "passed",
                "message": "Candidate matches the approved catalog and request policy.",
            }
        ],
    }


def _writer_only_profile() -> EngineProfile:
    return EngineProfile(
        id="catalyst-query-writer-only",
        label="Writer only",
        models={"query_generate": "gemma-4-12b-q4"},
        knobs={"query_generate": {"temperature": 0, "dry": 0}},
        policies={"generation_attempts": 3},
    )


def _reviewed_profile() -> EngineProfile:
    return EngineProfile(
        id="catalyst-query-reviewed",
        label="Writer + reviewer",
        models={"query_generate": "gemma-4-12b-q4", "query_review": "gemma-4-12b-q4"},
        knobs={
            "query_generate": {"temperature": 0, "dry": 0},
            "query_review": {"temperature": 0, "dry": 0},
        },
        policies={"generation_attempts": 3},
    )


def _collaborative_profile() -> EngineProfile:
    return EngineProfile(
        id="catalyst-query-collaborative",
        label="Cross-family writer + reviewer",
        models={"query_generate": "gemma-4-12b", "query_review": "qwen2.5-14b"},
        knobs={
            "query_generate": {"temperature": 0, "dry": 0},
            "query_review": {"temperature": 0, "dry": 0},
        },
        policies={
            "generation_attempts": 3,
            "collaborative_review": True,
            "model_classes": {
                "query_generate": "gemma-4",
                "query_review": "qwen2.5",
            },
        },
    )


def _queued_backend(responses: list, captured_messages: list | None = None):
    queue = [r if isinstance(r, str) else json.dumps(r) for r in responses]

    async def fake_backend(client, profile_id, role, model, messages, **kwargs) -> str:
        if captured_messages is not None:
            captured_messages.append(copy.deepcopy(messages))
        return queue.pop(0)

    return fake_backend


async def _run(profile: EngineProfile, responses: list) -> dict:
    request = EngineRequest(
        catalyst_query=_extension(),
        messages=[{"role": "user", "content": QUESTION}],
        profile=profile,
    )
    with patch.object(
        query_engine, "_backend_chat", side_effect=_queued_backend(responses)
    ):
        results = [
            payload
            async for kind, payload in execute_query_profile(request)
            if kind == "result"
        ]
    assert len(results) == 1
    return json.loads(results[0])


async def _run_revision(
    profile: EngineProfile,
    responses: list,
    *,
    captured_messages: list | None = None,
) -> dict:
    extension = _extension()
    extension["contractVersion"] = "catalyst.query.request.v2"
    extension["revision"] = {
        "baseClassification": "reused",
        "observedBase": {
            "versionId": "00000000-0000-4000-8000-000000000001",
            "queryDigest": "a" * 64,
        },
        "effectiveBaseVersion": {
            "versionId": "00000000-0000-4000-8000-000000000001",
            "queryDigest": "a" * 64,
        },
        "editorSnapshot": {
            "sql": _ready_candidate()["sql"],
            "parameters": _ready_candidate()["parameters"],
            "expectedColumns": _ready_candidate()["expectedColumns"],
            "editorDigest": "a" * 64,
        },
        "currentInstruction": FOLLOWUP,
        "instructionHistory": [{"kind": "initial", "instruction": QUESTION}],
    }
    request = EngineRequest(
        catalyst_query=extension,
        messages=[{"role": "user", "content": FOLLOWUP}],
        profile=profile,
    )
    with patch.object(
        query_engine,
        "_backend_chat",
        side_effect=_queued_backend(responses, captured_messages),
    ):
        results = [
            payload
            async for kind, payload in execute_query_profile(request)
            if kind == "result"
        ]
    assert len(results) == 1
    return json.loads(results[0])


@pytest.mark.asyncio
async def test_writer_only_finalizes_without_review():
    result = await _run(_writer_only_profile(), [_ready_candidate()])

    assert result["contractVersion"] == "catalyst.query.v1"
    assert result["status"] == "ready"
    assert result["sql"].startswith("SELECT viral_load_value")
    assert result["target"] == RESPONSE_TARGET
    assert result["provenance"]["profileId"] == "catalyst-query-writer-only"
    assert result["validation"]["status"] in {"passed", "warned"}
    # Writer-only emits no reviewer evidence.
    evidence = result["_hubEvidence"]["profileEvidence"]
    assert "reviewer" not in evidence
    assert evidence["writer"]["modelId"] == "gemma-4-12b-q4"


@pytest.mark.asyncio
async def test_reviewed_path_runs_writer_then_reviewer():
    result = await _run(_reviewed_profile(), [_ready_candidate(), _approve_review()])

    assert result["status"] == "ready"
    assert result["provenance"]["profileId"] == "catalyst-query-reviewed"
    evidence = result["_hubEvidence"]["profileEvidence"]
    # Reviewed profile carries both role legs.
    assert evidence["writer"]["modelId"] == "gemma-4-12b-q4"
    assert evidence["reviewer"]["modelId"] == "gemma-4-12b-q4"
    # Two model invocations: writer + reviewer.
    assert len(result["_hubEvidence"]["modelInvocations"]) == 2


@pytest.mark.asyncio
async def test_collaborative_review_contract_failure_preserves_raw_and_writer():
    captured_messages = []
    malformed_review = {
        "decision": "repair",
        "checks": _approve_review()["checks"],
        "candidate": {
            "status": "ready",
            "target": copy.deepcopy(RESPONSE_TARGET),
        },
    }

    result = await _run_revision(
        _collaborative_profile(),
        [_ready_candidate(), malformed_review],
        captured_messages=captured_messages,
    )

    assert result["status"] == "rejected"
    reviewer_request = json.loads(captured_messages[1][-1]["content"])
    assert reviewer_request["question"] == FOLLOWUP
    assert reviewer_request["instruction"] == FOLLOWUP
    assert reviewer_request["revision"]["currentInstruction"] == FOLLOWUP
    assert result["diagnosticCandidate"]["rawOutput"] == json.dumps(malformed_review)
    collaboration = result["modelCollaboration"]
    assert collaboration["writer"]["candidate"] == _ready_candidate()
    assert collaboration["writer"]["disposition"] == "retained_unselected"
    assert collaboration["reviewer"]["decision"] == "failed"
    assert collaboration["reviewer"]["disposition"] == "diagnostic_only"
