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
        models={"query_generate": "google/gemma-4-e4b"},
        knobs={"query_generate": {"temperature": 0, "dry": 0}},
        policies={"generation_attempts": 3},
    )


def _reviewed_profile() -> EngineProfile:
    return EngineProfile(
        id="catalyst-query-reviewed",
        label="Writer + reviewer",
        models={
            "query_generate": "google/gemma-4-e4b",
            "query_review": "qwen2.5-14b-instruct-mlx",
        },
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
        models={
            "query_generate": "google/gemma-4-e4b",
            "query_review": "qwen2.5-14b-instruct-mlx",
        },
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
    assert evidence["writer"]["modelId"] == "google/gemma-4-e4b"


@pytest.mark.asyncio
async def test_reviewed_path_runs_writer_then_reviewer():
    result = await _run(_reviewed_profile(), [_ready_candidate(), _approve_review()])

    assert result["status"] == "ready"
    assert result["provenance"]["profileId"] == "catalyst-query-reviewed"
    evidence = result["_hubEvidence"]["profileEvidence"]
    # Reviewed profile carries both role legs.
    assert evidence["writer"]["modelId"] == "google/gemma-4-e4b"
    assert evidence["reviewer"]["modelId"] == "qwen2.5-14b-instruct-mlx"
    # Two model invocations: writer + reviewer.
    assert len(result["_hubEvidence"]["modelInvocations"]) == 2


@pytest.mark.asyncio
async def test_a_malformed_review_gets_one_corrective_attempt_and_recovers():
    """The case that cost a real turn.

    A reviewer failed field-grounding, announced in its message that it was
    repairing the candidate, and then emitted ``{"status": "ready"}`` with no SQL.
    The corrective re-ask and the instruction written for exactly this case
    already existed; an early raise made them unreachable, so the turn ended on a
    jsonschema string instead of a second attempt.
    """
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
        [_ready_candidate(), malformed_review, _approve_review()],
        captured_messages=captured_messages,
    )

    # The corrected review lands and the turn completes.
    assert result["status"] != "rejected"
    # Three model calls: writer, malformed review, corrected review.
    assert len(captured_messages) == 3
    # The correction names the contract rather than restating the question.
    correction = captured_messages[2][-1]["content"]
    assert "failed the strict output contract" in correction


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

    # Two malformed reviews: the reviewer is asked once to correct the shape of
    # its output and fails again. One corrective attempt, then the turn stops.
    result = await _run_revision(
        _collaborative_profile(),
        [_ready_candidate(), malformed_review, malformed_review],
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


# --- the writer's non-ready outcomes ---------------------------------------
#
# Asking and declining are terminal answers, not failures. They cost one writer
# call: no lint, no repair loop, no reviewer, no SQL. The prompt has always
# asked for this; until the wire carried the branches, the only legal move on
# an ungroundable request was to invent an identifier.


@pytest.mark.asyncio
async def test_a_clarification_is_one_call_that_carries_the_question():
    question = "Which date window and which result types did you mean?"
    result = await _run(
        _reviewed_profile(),
        [{"status": "needs_clarification", "clarification": question}],
    )

    assert result["status"] == "needs_clarification"
    assert result["clarification"] == question
    assert "sql" not in result
    # One writer call and nothing else: no reviewer, no repair attempt.
    assert len(result["_hubEvidence"]["modelInvocations"]) == 1
    assert result["_hubEvidence"]["modelInvocations"][0]["role"] == "writer"


@pytest.mark.asyncio
async def test_an_unsupported_answer_is_one_call_that_carries_the_reason():
    reason = "This data holds no home address for a patient."
    result = await _run(
        _reviewed_profile(),
        [{"status": "unsupported", "message": reason}],
    )

    assert result["status"] == "unsupported"
    assert result["message"] == reason
    assert "sql" not in result
    assert len(result["_hubEvidence"]["modelInvocations"]) == 1


@pytest.mark.asyncio
async def test_a_non_ready_answer_is_never_linted():
    """Lint judges SQL; there is none, so it must not run at all.

    The contract requires an unsupported answer to carry
    validation.status "rejected" -- it produced no executable query -- so the
    proof that lint stayed out is that no lint check was recorded, and that
    the outcome is the writer's `unsupported` rather than the Gateway's
    `rejected`.
    """
    result = await _run(
        _writer_only_profile(),
        [{"status": "unsupported", "message": "No such data."}],
    )

    assert result["status"] == "unsupported"
    names = [check["name"] for check in result["validation"]["checks"]]
    assert not [name for name in names if name.startswith("query_lint")], names


@pytest.mark.asyncio
async def test_lint_is_never_reached_for_a_terminal_writer_answer():
    """Not merely that lint found nothing -- that it was never asked.

    Today lint returns no findings for a candidate with no SQL, so the run
    would end on one call either way. Depending on that is fragile: a future
    rule that errors on missing SQL would turn every clarification into three
    retries and a failed turn.
    """
    with patch.object(
        query_engine, "lint_candidate", side_effect=AssertionError("lint ran")
    ) as lint:
        result = await _run(
            _writer_only_profile(),
            [{"status": "needs_clarification", "clarification": "Which window?"}],
        )

    assert result["status"] == "needs_clarification"
    lint.assert_not_called()


async def _run_scope(question: str, values: list[str]) -> dict:
    """Run the catalog-scope preflight alone: no model response is queued."""
    extension = _extension()
    extension["catalog"]["views"][0]["semanticDimensions"] = [
        {
            "field": "test_name",
            "semanticType": "analyte",
            "values": [{"canonical": value, "aliases": []} for value in values],
        }
    ]
    request = EngineRequest(
        catalyst_query=extension,
        messages=[{"role": "user", "content": question}],
        profile=_reviewed_profile(),
    )
    with patch.object(query_engine, "_backend_chat", side_effect=_queued_backend([])):
        results = [
            payload
            async for kind, payload in execute_query_profile(request)
            if kind == "result"
        ]
    assert len(results) == 1
    return json.loads(results[0])


@pytest.mark.asyncio
async def test_a_result_name_nothing_resembles_is_still_unsupported():
    result = await _run_scope("Show dengue results", ["Malaria", "Haemoglobin"])

    assert result["status"] == "unsupported"
    assert "dengue" in result["message"]
    assert not result["_hubEvidence"]["modelInvocations"]


@pytest.mark.asyncio
async def test_a_result_name_the_catalog_answers_under_other_names_asks():
    """Refusing here would be a claim about the data the check cannot support.

    Nothing is called 'HIV', but two recorded results are HIV results. The
    honest deterministic answer names them and asks, and it must stay one
    preflight decision with no model call behind it.
    """
    result = await _run_scope(
        "Show recent HIV results",
        ["CD4 count", "HIV viral load", "Current WHO HIV stage", "Malaria"],
    )

    assert result["status"] == "needs_clarification"
    assert "HIV viral load" in result["clarification"]
    assert "Current WHO HIV stage" in result["clarification"]
    assert "Malaria" not in result["clarification"]
    assert "sql" not in result
    assert not result["_hubEvidence"]["modelInvocations"]


@pytest.mark.asyncio
async def test_the_hubs_token_count_travels_with_the_invocation():
    """Exact token evidence rides from the Hub role call into the evidence.

    The Hub counts the fully rendered request with the model's own tokenizer;
    the engine's job is not to lose it: each invocation keeps its own
    accounting and the result surfaces the writer's, which is the request
    whose budget the turn lives or dies by.
    """
    accounting = {
        "tokenizer": "gemma-4-12b",
        "contextWindow": 24576,
        "outputReserve": 1024,
        "promptTokens": 2345,
    }
    responses = [
        (json.dumps(_ready_candidate()), accounting),
        (json.dumps(_approve_review()), {**accounting, "promptTokens": 999}),
    ]

    async def backend(client, profile_id, role, model, messages, **kwargs):
        return responses.pop(0)

    request = EngineRequest(
        catalyst_query=_extension(),
        messages=[{"role": "user", "content": QUESTION}],
        profile=_reviewed_profile(),
    )
    with patch.object(query_engine, "_backend_chat", side_effect=backend):
        results = [
            payload
            async for kind, payload in execute_query_profile(request)
            if kind == "result"
        ]
    result = json.loads(results[0])

    invocations = result["_hubEvidence"]["modelInvocations"]
    assert invocations[0]["tokenAccounting"] == accounting
    assert invocations[1]["tokenAccounting"]["promptTokens"] == 999
    assert result["_hubEvidence"]["tokenAccounting"] == accounting


@pytest.mark.asyncio
async def test_a_hub_that_counted_nothing_is_reported_as_nothing():
    """No accounting is absence in the evidence, never an invented shape."""

    async def backend(client, profile_id, role, model, messages, **kwargs):
        return json.dumps(_ready_candidate())

    request = EngineRequest(
        catalyst_query=_extension(),
        messages=[{"role": "user", "content": QUESTION}],
        profile=_writer_only_profile(),
    )
    with patch.object(query_engine, "_backend_chat", side_effect=backend):
        results = [
            payload
            async for kind, payload in execute_query_profile(request)
            if kind == "result"
        ]
    result = json.loads(results[0])

    assert result["_hubEvidence"]["modelInvocations"][0]["tokenAccounting"] is None
    assert result["_hubEvidence"]["tokenAccounting"] is None
