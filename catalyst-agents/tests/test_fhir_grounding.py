"""Unit tests for fhir_grounding.py (feature 011).

Mocks mcp_client.call_tool for speed/determinism — the real MCP protocol
round-trip against live OE2 FHIR data is proven separately in
catalyst-mcp/tests/test_fhir_tools.py (tool level) and
catalyst-gateway/tests/test_sidecar_response_contract.py (full HTTP path).
This file's job is fhir_grounding's own branching logic: patient resolution,
intent classification, citation/fact building, and abstention.
"""

from __future__ import annotations

import pytest

from src import fhir_grounding, mcp_client

_PATIENT_ID = "60d5288c-c7ff-4651-b5d0-eea03fb75090"
_PATIENT_BUNDLE_SINGLE = {
    "resourceType": "Bundle",
    "total": 1,
    "entry": [
        {
            "resource": {
                "resourceType": "Patient",
                "id": _PATIENT_ID,
                "name": [{"family": "TEST-Smith", "given": ["John"]}],
            }
        }
    ],
}
_PATIENT_BUNDLE_AMBIGUOUS = {
    "resourceType": "Bundle",
    "total": 3,
    "entry": [
        {"resource": {"resourceType": "Patient", "id": "p1", "name": [{"family": "TEST-Smith"}]}},
        {"resource": {"resourceType": "Patient", "id": "p2", "name": [{"family": "TEST-Jones"}]}},
        {"resource": {"resourceType": "Patient", "id": "p3", "name": [{"family": "TEST-Williams"}]}},
    ],
}
_EMPTY_BUNDLE = {"resourceType": "Bundle", "total": 0, "entry": []}


class _ToolRouter:
    """Routes mcp_client.call_tool calls to canned responses by tool name."""

    def __init__(self, responses: dict[str, object]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, tool_name: str, arguments: dict) -> dict:
        self.calls.append((tool_name, arguments))
        return self._responses.get(tool_name, _EMPTY_BUNDLE)


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch):
    """Every test mocks the LLM synthesis step — fhir_grounding's contract is
    that facts/citations are built deterministically before the LLM ever
    runs, so the LLM's exact prose is not this file's concern."""

    async def fake_synthesize(question: str, facts: list) -> str:
        return f"ANSWER grounded in {len(facts)} facts"

    monkeypatch.setattr(fhir_grounding, "_synthesize_answer", fake_synthesize)


class TestPatientResolution:
    @pytest.mark.asyncio
    async def test_no_identifier_in_question_abstains(self, monkeypatch):
        router = _ToolRouter({})
        monkeypatch.setattr(mcp_client, "call_tool", router)

        result = await fhir_grounding.answer_question("What tests were ordered?")

        assert result["citations"] == []
        assert "no single matching patient" in result["answer"]
        assert router.calls == []  # never even tried an MCP call

    @pytest.mark.asyncio
    async def test_ambiguous_identifier_does_not_silently_pick_one(self, monkeypatch):
        router = _ToolRouter({"search_patient": _PATIENT_BUNDLE_AMBIGUOUS})
        monkeypatch.setattr(mcp_client, "call_tool", router)

        result = await fhir_grounding.answer_question(
            "What tests were ordered for patient TEST-PAT-999?"
        )

        assert result["citations"] == []
        assert "no single matching patient" in result["answer"]

    @pytest.mark.asyncio
    async def test_known_identifier_resolves_patient(self, monkeypatch):
        router = _ToolRouter(
            {
                "search_patient": _PATIENT_BUNDLE_SINGLE,
                "get_service_requests": {
                    "resourceType": "Bundle",
                    "total": 1,
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "ServiceRequest",
                                "id": "sr1",
                                "code": {"text": "GPT/ALAT"},
                            }
                        }
                    ],
                },
            }
        )
        monkeypatch.setattr(mcp_client, "call_tool", router)

        result = await fhir_grounding.answer_question(
            "What tests were ordered for patient E2E-PAT-001?"
        )

        assert any(c["resourceType"] == "Patient" and c["id"] == _PATIENT_ID for c in result["citations"])
        assert ("search_patient", {"query": "E2E-PAT-001"}) in router.calls


class TestIntentRouting:
    @pytest.mark.asyncio
    async def test_ordered_tests_question_calls_get_service_requests(self, monkeypatch):
        router = _ToolRouter(
            {
                "search_patient": _PATIENT_BUNDLE_SINGLE,
                "get_service_requests": {
                    "resourceType": "Bundle",
                    "total": 1,
                    "entry": [
                        {"resource": {"resourceType": "ServiceRequest", "id": "sr1", "code": {"text": "CBC"}}}
                    ],
                },
            }
        )
        monkeypatch.setattr(mcp_client, "call_tool", router)

        result = await fhir_grounding.answer_question(
            "What tests were ordered for patient E2E-PAT-001?"
        )

        tool_names = [name for name, _ in router.calls]
        assert "get_service_requests" in tool_names
        assert any(c["resourceType"] == "ServiceRequest" for c in result["citations"])

    @pytest.mark.asyncio
    async def test_recent_results_question_calls_get_observations_and_builds_table(self, monkeypatch):
        router = _ToolRouter(
            {
                "search_patient": _PATIENT_BUNDLE_SINGLE,
                "get_observations": {
                    "resourceType": "Bundle",
                    "total": 1,
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Observation",
                                "id": "obs1",
                                "code": {"text": "Hemoglobin"},
                                "valueQuantity": {"value": 10.2, "unit": "g/dL"},
                                "effectiveDateTime": "2026-04-15",
                                "interpretation": [{"coding": [{"code": "L"}]}],
                            }
                        }
                    ],
                },
            }
        )
        monkeypatch.setattr(mcp_client, "call_tool", router)

        result = await fhir_grounding.answer_question(
            "Show recent lab results for patient E2E-PAT-001."
        )

        assert "get_observations" in [name for name, _ in router.calls]
        assert result["uiBlocks"] == [
            {
                "type": "lab_result_table",
                "rows": [
                    {
                        "test": "Hemoglobin",
                        "value": "10.2",
                        "unit": "g/dL",
                        "refRange": "",
                        "flag": "L",
                        "date": "2026-04-15",
                        "orderRef": "",
                    }
                ],
            }
        ]

    @pytest.mark.asyncio
    async def test_abnormal_results_question_filters_out_normal_observations(self, monkeypatch):
        router = _ToolRouter(
            {
                "search_patient": _PATIENT_BUNDLE_SINGLE,
                "get_observations": {
                    "resourceType": "Bundle",
                    "total": 2,
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Observation",
                                "id": "obs-normal",
                                "code": {"text": "Sodium"},
                                "valueQuantity": {"value": 140, "unit": "mmol/L"},
                                "interpretation": [{"coding": [{"code": "N"}]}],
                            }
                        },
                        {
                            "resource": {
                                "resourceType": "Observation",
                                "id": "obs-abnormal",
                                "code": {"text": "Potassium"},
                                "valueQuantity": {"value": 6.2, "unit": "mmol/L"},
                                "interpretation": [{"coding": [{"code": "H"}]}],
                            }
                        },
                    ],
                },
            }
        )
        monkeypatch.setattr(mcp_client, "call_tool", router)

        result = await fhir_grounding.answer_question(
            "Summarize abnormal results for patient E2E-PAT-001."
        )

        cited_ids = {c["id"] for c in result["citations"] if c["resourceType"] == "Observation"}
        assert cited_ids == {"obs-abnormal"}

    @pytest.mark.asyncio
    async def test_diagnostic_reports_question_calls_get_diagnostic_reports(self, monkeypatch):
        router = _ToolRouter(
            {
                "search_patient": _PATIENT_BUNDLE_SINGLE,
                "get_diagnostic_reports": {
                    "resourceType": "Bundle",
                    "total": 1,
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "DiagnosticReport",
                                "id": "dr1",
                                "code": {"text": "COVID-19 PCR panel"},
                            }
                        }
                    ],
                },
            }
        )
        monkeypatch.setattr(mcp_client, "call_tool", router)

        result = await fhir_grounding.answer_question(
            "Which diagnostic reports are available for patient E2E-PAT-001?"
        )

        assert "get_diagnostic_reports" in [name for name, _ in router.calls]
        assert any(c["resourceType"] == "DiagnosticReport" for c in result["citations"])

    @pytest.mark.asyncio
    async def test_order_linked_question_resolves_order_and_its_linked_results(self, monkeypatch):
        order_id = "11111111-1111-1111-1111-111111111111"
        order_ref = f"ServiceRequest/{order_id}"
        router = _ToolRouter(
            {
                "search_patient": _PATIENT_BUNDLE_SINGLE,
                "get_resource_by_reference": {
                    "resourceType": "ServiceRequest",
                    "id": order_id,
                    "code": {"text": "Chem panel"},
                    "subject": {"reference": f"Patient/{_PATIENT_ID}"},
                },
                "get_observations": {
                    "resourceType": "Bundle",
                    "total": 2,
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Observation",
                                "id": "obs-linked",
                                "code": {"text": "Potassium"},
                                "valueQuantity": {"value": 4.1, "unit": "mmol/L"},
                                "basedOn": [{"reference": order_ref}],
                            }
                        },
                        {
                            "resource": {
                                "resourceType": "Observation",
                                "id": "obs-unrelated",
                                "code": {"text": "Sodium"},
                                "valueQuantity": {"value": 140, "unit": "mmol/L"},
                                "basedOn": [{"reference": "ServiceRequest/some-other-order"}],
                            }
                        },
                    ],
                },
            }
        )
        monkeypatch.setattr(mcp_client, "call_tool", router)

        result = await fhir_grounding.answer_question(
            f"What results are linked to order {order_id}?"
        )

        assert (
            "get_resource_by_reference",
            {"reference": order_ref},
        ) in router.calls
        assert any(c["id"] == order_id for c in result["citations"])
        # Only the Observation actually basedOn this order is cited — the
        # unrelated one (linked to a different order) must not leak in.
        observation_citation_ids = {
            c["id"] for c in result["citations"] if c["resourceType"] == "Observation"
        }
        assert observation_citation_ids == {"obs-linked"}

    @pytest.mark.asyncio
    async def test_order_linked_question_order_found_but_no_results_linked_abstains(self, monkeypatch):
        """An order that resolves but has zero linked results today is the
        real, current state of local demo data (spec.md Assumptions) — must
        abstain honestly rather than claim results exist."""
        order_id = "33333333-3333-3333-3333-333333333333"
        router = _ToolRouter(
            {
                "get_resource_by_reference": {
                    "resourceType": "ServiceRequest",
                    "id": order_id,
                    "code": {"text": "Chem panel"},
                    "subject": {"reference": f"Patient/{_PATIENT_ID}"},
                },
                "get_observations": _EMPTY_BUNDLE,
            }
        )
        monkeypatch.setattr(mcp_client, "call_tool", router)

        result = await fhir_grounding.answer_question(
            f"What results are linked to order {order_id}?"
        )

        assert result["citations"] == []
        assert "found, but no results are currently linked" in result["answer"]

    @pytest.mark.asyncio
    async def test_order_linked_question_with_unresolvable_order_abstains(self, monkeypatch):
        order_id = "22222222-2222-2222-2222-222222222222"
        router = _ToolRouter(
            {
                "search_patient": _PATIENT_BUNDLE_SINGLE,
                "get_resource_by_reference": {
                    "error": "resource_not_found",
                    "detail": f"ServiceRequest/{order_id} not found",
                },
            }
        )
        monkeypatch.setattr(mcp_client, "call_tool", router)

        result = await fhir_grounding.answer_question(
            f"What results are linked to order {order_id}?"
        )

        assert result["citations"] == []
        assert "was not found in OE2" in result["answer"]


class TestAbstention:
    @pytest.mark.asyncio
    async def test_no_data_for_resource_type_abstains_with_explanation(self, monkeypatch):
        router = _ToolRouter(
            {"search_patient": _PATIENT_BUNDLE_SINGLE, "get_service_requests": _EMPTY_BUNDLE}
        )
        monkeypatch.setattr(mcp_client, "call_tool", router)

        result = await fhir_grounding.answer_question(
            "What tests were ordered for patient E2E-PAT-001?"
        )

        assert result["citations"] == []
        assert result["uiBlocks"] == []
        assert "no service requests data found" in result["answer"]

    @pytest.mark.asyncio
    async def test_abstention_still_records_real_provenance_of_what_was_checked(self, monkeypatch):
        """Regression guard: abstention must not silently blank out
        provenance — a reviewer needs to see what was actually tried."""
        router = _ToolRouter(
            {"search_patient": _PATIENT_BUNDLE_SINGLE, "get_service_requests": _EMPTY_BUNDLE}
        )
        monkeypatch.setattr(mcp_client, "call_tool", router)

        result = await fhir_grounding.answer_question(
            "What tests were ordered for patient E2E-PAT-001?"
        )

        assert result["provenance"]["tools_called"] == ["search_patient", "get_service_requests"]
        assert result["provenance"]["resource_ids"] == [f"Patient/{_PATIENT_ID}"]


class TestCitationResolvability:
    @pytest.mark.asyncio
    async def test_every_citation_comes_from_an_actually_fetched_resource(self, monkeypatch):
        """No citation may be fabricated — each one traces back to a resource
        id that was actually returned by an MCP tool call, never invented by
        the LLM (which only sees already-built fact strings, not raw FHIR)."""
        router = _ToolRouter(
            {
                "search_patient": _PATIENT_BUNDLE_SINGLE,
                "get_diagnostic_reports": {
                    "resourceType": "Bundle",
                    "total": 2,
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "DiagnosticReport",
                                "id": "dr-a",
                                "code": {"text": "Panel A"},
                            }
                        },
                        {
                            "resource": {
                                "resourceType": "DiagnosticReport",
                                "id": "dr-b",
                                "code": {"text": "Panel B"},
                            }
                        },
                    ],
                },
            }
        )
        monkeypatch.setattr(mcp_client, "call_tool", router)

        result = await fhir_grounding.answer_question(
            "Which diagnostic reports are available for patient E2E-PAT-001?"
        )

        fetched_ids = {"dr-a", "dr-b"}
        cited_ids = {c["id"] for c in result["citations"] if c["resourceType"] == "DiagnosticReport"}
        assert cited_ids <= fetched_ids
        assert cited_ids == fetched_ids  # both fetched resources were cited, none dropped or invented
