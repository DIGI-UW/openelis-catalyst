"""Tests for CatalystAgentExecutor (feature 011).

Replaces the pre-feature-011 NL-to-SQL tests (generate_sql against a mocked
schema) — that whole flow was removed from catalyst_executor.py; the
single-agent fallback now answers the FHIR sidecar POC's canonical questions
via fhir_grounding.answer_question. That function's own branching logic
(patient resolution, intent classification, abstention, citation-building) is
unit-tested in test_fhir_grounding.py; this file verifies the executor
correctly wires fhir_grounding's output into the A2A artifact as JSON — the
one piece of logic this thin wrapper actually owns.

The full real path (HTTP -> A2A -> this executor -> MCP -> live OE2 FHIR) is
additionally verified live in catalyst-gateway's
tests/test_sidecar_response_contract.py.
"""

import asyncio
import json

import pytest
from a2a.server.events import EventQueue
from a2a.types import Message, MessageSendParams, Part, Role, TextPart

from src import fhir_grounding
from src.agents.catalyst_executor import CatalystAgentExecutor


def _make_context(question: str):
    from a2a.server.agent_execution import RequestContext

    message = Message(
        messageId="test-msg-1",
        role=Role.user,
        parts=[Part(root=TextPart(text=question))],
    )
    return RequestContext(request=MessageSendParams(message=message))


@pytest.mark.asyncio
async def test_executor_serializes_fhir_grounding_response_as_json_artifact(monkeypatch):
    fake_response = {
        "answer": "Patient X has one order on file [1].",
        "facts": [{"text": "Ordered: GPT/ALAT", "source_ref": "ServiceRequest/2"}],
        "citations": [
            {
                "index": 1,
                "resourceType": "ServiceRequest",
                "id": "2",
                "url": "https://localhost:18443/OpenELIS-Global/fhir/ServiceRequest/2",
                "display": "GPT/ALAT",
            }
        ],
        "uiBlocks": [],
        "provenance": {
            "fhir_surface": "embedded",
            "fhir_base_url": "https://localhost:18443/OpenELIS-Global/fhir",
            "tools_called": ["search_patient", "get_service_requests"],
            "resource_ids": ["Patient/1", "ServiceRequest/2"],
        },
    }

    async def fake_answer_question(question: str) -> dict:
        assert question == "What tests were ordered for patient E2E-PAT-001?"
        return fake_response

    monkeypatch.setattr(fhir_grounding, "answer_question", fake_answer_question)

    context = _make_context("What tests were ordered for patient E2E-PAT-001?")
    queue = EventQueue()
    executor = CatalystAgentExecutor()

    await executor.execute(context, queue)

    artifact_text = None
    # execute() enqueues at most a couple of events (a status update, then the
    # artifact, then completion) — bound the drain so a queue-API mismatch
    # fails fast as an assertion instead of hanging the test suite.
    for _ in range(10):
        try:
            event = await asyncio.wait_for(queue.dequeue_event(no_wait=True), timeout=1.0)
        except (asyncio.QueueEmpty, asyncio.TimeoutError):
            break
        artifact = getattr(event, "artifact", None)
        if artifact is not None and getattr(artifact, "parts", None):
            artifact_text = artifact.parts[0].root.text

    assert artifact_text is not None, "expected an artifact event carrying the JSON response"
    assert json.loads(artifact_text) == fake_response
