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
from src.catalyst.query_engine import EngineProfile, EngineRequest, execute_query_profile

QUESTION = "Show viral load results since 2026-01-01 with value and release date"
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
            {"name": "since", "type": "date", "source": "question", "value": "2026-01-01"}
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
        prompts={"query_generate": "catalyst-query-generate"},
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
        prompts={
            "query_generate": "catalyst-query-generate",
            "query_review": "catalyst-query-review",
        },
        policies={"generation_attempts": 3},
    )


def _queued_backend(responses: list):
    queue = [
        r if isinstance(r, str) else json.dumps(r) for r in responses
    ]

    async def fake_backend(client, model, messages, **kwargs) -> str:
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
