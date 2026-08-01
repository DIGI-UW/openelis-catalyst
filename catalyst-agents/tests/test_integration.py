"""Router -> Catalyst delegation integration test (feature 011).

Verifies RouterAgentExecutor.delegate_query_single_agent correctly forwards
a question and returns whatever text artifact the downstream agent produced
— now a JSON sidecar_response payload (feature 011) rather than raw SQL text.
This test only exercises the router's delegation plumbing (via a fake A2A
client, as before); it does not re-test fhir_grounding's own logic (see
test_fhir_grounding.py) or the executor's JSON-artifact wiring (see
test_catalyst_agent.py).
"""

import json

import pytest
from a2a.types import Part, TextPart

from src.agents.router_executor import RouterAgentExecutor


class _FakeArtifact:
    def __init__(self, text: str) -> None:
        self.parts = [Part(root=TextPart(text=text))]
        self.name = "sidecar_response"


class _FakeTask:
    def __init__(self, text: str) -> None:
        self.artifacts = [_FakeArtifact(text)]


class _FakeClient:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    async def send_message(self, message):
        yield _FakeTask(self._response_text)


@pytest.mark.asyncio
async def test_router_delegates_to_catalyst_and_returns_its_artifact():
    fake_sidecar_response = {
        "answer": "No service requests found for this patient.",
        "facts": [],
        "citations": [],
        "uiBlocks": [],
        "provenance": {
            "fhir_surface": "embedded",
            "fhir_base_url": "https://localhost:18443/OpenELIS-Global/fhir",
            "tools_called": ["search_patient", "get_service_requests"],
            "resource_ids": ["Patient/1"],
        },
    }
    response_text = json.dumps(fake_sidecar_response)

    executor = RouterAgentExecutor()
    executor.mode = "single"  # Use single-agent mode (CatalystAgent) for this test

    async def _fake_create_client(agent_url: str):
        return _FakeClient(response_text)

    executor._create_client = _fake_create_client  # type: ignore[method-assign]

    parts = await executor.delegate_query_single_agent(
        "What tests were ordered for patient E2E-PAT-001?"
    )

    assert len(parts) == 1
    assert json.loads(parts[0].root.text) == fake_sidecar_response
