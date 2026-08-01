"""FHIR-grounded question answering for the Catalyst sidecar POC (feature 011).

Replaces the M0.0 NL-to-SQL flow for the five canonical lab questions (see
specs/011-catalyst-fhir-sidecar-poc/spec.md). Every citation returned here
comes from an actually-fetched FHIR resource — the LLM only synthesizes
answer prose from already-verified facts, never invents resource IDs
(spec FR-002, FR-008; constitution Principle III: Record-Level Evidence).

Output shape matches contracts/sidecar_response.schema.json exactly (plain
dict, not a shared dataclass — catalyst-agents and catalyst-gateway are
separate Python packages/venvs, so the contract is the integration point,
not shared code).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from . import mcp_client
from .config import load_llm_config
from .llm_clients import create_llm_client

_FHIR_BASE_URL_FALLBACK = "https://localhost:18443/OpenELIS-Global/fhir"

_ANSWER_SYSTEM_PROMPT = (
    "You are a clinical lab assistant. You will be given a question and a list of "
    "verified facts extracted from FHIR resources. Answer the question using ONLY "
    "the facts provided — never state anything not directly supported by a fact. "
    "Reference facts with their citation index in square brackets, e.g. [1], [2]. "
    "Be concise and clinical. Do not invent resource IDs, values, or dates."
)


def _no_data_response(
    question: str,
    reason: str,
    tools_called: Optional[list[str]] = None,
    resource_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Abstention response (spec FR-008). Still carries real provenance —
    which tools were actually called and which resources were actually
    checked before concluding there's no data — so the debug drawer (spec
    Story 2) reflects what really happened, not a blanked-out no-op."""
    return {
        "answer": f"I don't have data to answer this: {reason}",
        "facts": [],
        "citations": [],
        "uiBlocks": [],
        "provenance": {
            "fhir_surface": "embedded",
            "fhir_base_url": _FHIR_BASE_URL_FALLBACK,
            "tools_called": tools_called or [],
            "resource_ids": resource_ids or [],
        },
    }


async def _resolve_patient(question: str) -> tuple[Optional[dict[str, Any]], list[str]]:
    """Returns (single matching Patient resource or None, tools_called).

    None with a non-empty candidate implication (ambiguous match) is
    signaled by returning None alongside tools_called that a caller can use
    to explain why — callers distinguish "no patient found" from "ambiguous"
    by re-inspecting the search result if needed; kept simple here since the
    canonical questions always name a specific patient.
    """
    tools_called: list[str] = []

    identifier_match = re.search(r"\b([A-Z0-9]+-PAT-\d+)\b", question, re.IGNORECASE)
    uuid_match = re.search(
        r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
        question,
        re.IGNORECASE,
    )

    if uuid_match:
        tools_called.append("get_patient_context")
        result = await mcp_client.call_tool(
            "get_patient_context", {"patient_id": uuid_match.group(1)}
        )
        if result.get("resourceType") == "Patient":
            return result, tools_called
        return None, tools_called

    query = identifier_match.group(1) if identifier_match else None
    if not query:
        return None, tools_called

    tools_called.append("search_patient")
    result = await mcp_client.call_tool("search_patient", {"query": query})
    entries = result.get("entry", []) if "error" not in result else []
    if len(entries) == 1:
        return entries[0]["resource"], tools_called
    return None, tools_called


def _patient_display(patient: dict[str, Any]) -> str:
    name = (patient.get("name") or [{}])[0]
    given = " ".join(name.get("given", []))
    family = name.get("family", "")
    return f"{given} {family}".strip() or patient.get("id", "unknown")


_QUESTION_INTENTS = (
    ("diagnostic report", "diagnostic_reports"),
    ("ordered", "service_requests"),
    ("what tests", "service_requests"),
    ("abnormal", "observations_abnormal"),
    ("linked to order", "order_linked"),
    ("recent lab result", "observations"),
    ("lab result", "observations"),
)


def _classify_intent(question: str) -> str:
    lowered = question.lower()
    for keyword, intent in _QUESTION_INTENTS:
        if keyword in lowered:
            return intent
    return "observations"


async def answer_question(question: str) -> dict[str, Any]:
    intent = _classify_intent(question)

    # Canonical question 5 ("What results are linked to order Y?") names an
    # order, not a patient — classify intent BEFORE resolving a patient so
    # the order's own UUID in the question text is never mistaken for a
    # patient id (a real bug caught by test_fhir_grounding.py: a naive
    # patient-first UUID regex grabbed the order's UUID and tried to resolve
    # it as a patient).
    if intent == "order_linked":
        return await _answer_order_linked(question)

    patient, patient_tools = await _resolve_patient(question)
    if patient is None:
        return _no_data_response(
            question,
            "no single matching patient identified in the question "
            "(specify a patient identifier such as E2E-PAT-001, or the exact "
            "patient's FHIR id)",
            tools_called=patient_tools,
        )

    patient_id = patient["id"]
    tools_called = list(patient_tools)
    resource_ids: list[str] = [f"Patient/{patient_id}"]
    citations: list[dict[str, Any]] = [
        {
            "index": 1,
            "resourceType": "Patient",
            "id": patient_id,
            "url": f"{_FHIR_BASE_URL_FALLBACK}/Patient/{patient_id}",
            "display": _patient_display(patient),
        }
    ]
    facts: list[dict[str, Any]] = [
        {"text": f"Patient: {_patient_display(patient)}", "source_ref": f"Patient/{patient_id}"}
    ]
    ui_blocks: list[dict[str, Any]] = []

    if intent == "service_requests":
        tools_called.append("get_service_requests")
        result = await mcp_client.call_tool(
            "get_service_requests", {"patient_id": patient_id}
        )
        entries = result.get("entry", []) if "error" not in result else []
        for entry in entries:
            _add_service_request(entry["resource"], citations, facts, resource_ids)

    elif intent in ("observations", "observations_abnormal"):
        tools_called.append("get_observations")
        result = await mcp_client.call_tool("get_observations", {"patient_id": patient_id})
        entries = result.get("entry", []) if "error" not in result else []
        rows = []
        for entry in entries:
            obs = entry["resource"]
            flag = _observation_flag(obs)
            if intent == "observations_abnormal" and flag in (None, "N"):
                continue
            _add_observation(obs, citations, facts, resource_ids)
            rows.append(_observation_to_row(obs, flag))
        if rows:
            ui_blocks.append({"type": "lab_result_table", "rows": rows})

    elif intent == "diagnostic_reports":
        tools_called.append("get_diagnostic_reports")
        result = await mcp_client.call_tool(
            "get_diagnostic_reports", {"patient_id": patient_id}
        )
        entries = result.get("entry", []) if "error" not in result else []
        for entry in entries:
            _add_diagnostic_report(entry["resource"], citations, facts, resource_ids)

    # Only Patient was resolved — no clinical resources found for this intent.
    if len(citations) == 1:
        return _no_data_response(
            question,
            f"no {intent.replace('_', ' ')} data found for patient "
            f"{_patient_display(patient)} in OE2 (see spec.md Assumptions: "
            "current fixture data does not sync all resource types)",
            tools_called=tools_called,
            resource_ids=resource_ids,
        )

    answer_text = await _synthesize_answer(question, facts)

    return {
        "answer": answer_text,
        "facts": facts,
        "citations": citations,
        "uiBlocks": ui_blocks,
        "provenance": {
            "fhir_surface": "embedded",
            "fhir_base_url": _FHIR_BASE_URL_FALLBACK,
            "tools_called": tools_called,
            "resource_ids": resource_ids,
        },
    }


_ORDER_UUID_RE = re.compile(
    r"\b(order\s+)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)


async def _answer_order_linked(question: str) -> dict[str, Any]:
    """Canonical question 5: 'What results are linked to order Y?' — resolves
    the order directly (no patient pre-resolution; the order's UUID is not a
    patient id) and filters that patient's Observations to only those whose
    basedOn references the order, since no MCP tool filters Observations by
    order directly."""
    order_match = _ORDER_UUID_RE.search(question)
    if not order_match:
        return _no_data_response(question, "no order identifier found in the question")

    order_id = order_match.group(2)
    tools_called = ["get_resource_by_reference"]
    order = await mcp_client.call_tool(
        "get_resource_by_reference", {"reference": f"ServiceRequest/{order_id}"}
    )
    if order.get("resourceType") != "ServiceRequest":
        return _no_data_response(
            question,
            f"order {order_id} was not found in OE2",
            tools_called=tools_called,
        )

    resource_ids = [f"ServiceRequest/{order_id}"]
    citations: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    _add_service_request(order, citations, facts, resource_ids)

    order_ref = f"ServiceRequest/{order_id}"
    subject_ref = (order.get("subject") or {}).get("reference", "")
    linked_rows: list[dict[str, Any]] = []
    if subject_ref.startswith("Patient/"):
        subject_patient_id = subject_ref.split("/", 1)[1]
        tools_called.append("get_observations")
        obs_result = await mcp_client.call_tool(
            "get_observations", {"patient_id": subject_patient_id}
        )
        for entry in obs_result.get("entry", []) if "error" not in obs_result else []:
            obs = entry["resource"]
            based_on_refs = {b.get("reference", "") for b in obs.get("basedOn", [])}
            if order_ref not in based_on_refs:
                continue
            flag = _observation_flag(obs)
            _add_observation(obs, citations, facts, resource_ids)
            linked_rows.append(_observation_to_row(obs, flag))

    ui_blocks = [{"type": "lab_result_table", "rows": linked_rows}] if linked_rows else []

    if not linked_rows:
        return _no_data_response(
            question,
            f"order {order_id} was found, but no results are currently linked to it in OE2 "
            "(see spec.md Assumptions: current fixture data does not sync all resource types)",
            tools_called=tools_called,
            resource_ids=resource_ids,
        )

    answer_text = await _synthesize_answer(question, facts)
    return {
        "answer": answer_text,
        "facts": facts,
        "citations": citations,
        "uiBlocks": ui_blocks,
        "provenance": {
            "fhir_surface": "embedded",
            "fhir_base_url": _FHIR_BASE_URL_FALLBACK,
            "tools_called": tools_called,
            "resource_ids": resource_ids,
        },
    }


def _observation_flag(obs: dict[str, Any]) -> Optional[str]:
    interpretation = (obs.get("interpretation") or [{}])[0]
    coding = (interpretation.get("coding") or [{}])[0]
    return coding.get("code")


def _observation_display(obs: dict[str, Any]) -> str:
    code = obs.get("code") or {}
    return code.get("text") or (code.get("coding") or [{}])[0].get("display") or "Observation"


def _observation_to_row(obs: dict[str, Any], flag: Optional[str]) -> dict[str, Any]:
    value = obs.get("valueQuantity") or {}
    ref_range = (obs.get("referenceRange") or [{}])[0]
    low = ref_range.get("low", {}).get("value")
    high = ref_range.get("high", {}).get("value")
    return {
        "test": _observation_display(obs),
        "value": str(value.get("value", "")),
        "unit": value.get("unit", ""),
        "refRange": f"{low}-{high}" if low is not None and high is not None else "",
        "flag": flag,
        "date": obs.get("effectiveDateTime", ""),
        "orderRef": next(
            (b.get("reference", "") for b in obs.get("basedOn", [])), ""
        ),
    }


def _add_observation(
    obs: dict[str, Any],
    citations: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    resource_ids: list[str],
) -> None:
    rid = obs.get("id", "")
    ref = f"Observation/{rid}"
    resource_ids.append(ref)
    idx = len(citations) + 1
    citations.append(
        {
            "index": idx,
            "resourceType": "Observation",
            "id": rid,
            "url": f"{_FHIR_BASE_URL_FALLBACK}/{ref}",
            "display": _observation_display(obs),
        }
    )
    value = obs.get("valueQuantity") or {}
    facts.append(
        {
            "text": f"{_observation_display(obs)}: {value.get('value', '')} {value.get('unit', '')}".strip(),
            "source_ref": ref,
        }
    )


def _add_service_request(
    sr: dict[str, Any],
    citations: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    resource_ids: list[str],
) -> None:
    rid = sr.get("id", "")
    ref = f"ServiceRequest/{rid}"
    resource_ids.append(ref)
    idx = len(citations) + 1
    code = sr.get("code") or {}
    display = code.get("text") or (code.get("coding") or [{}])[0].get("display") or "ServiceRequest"
    citations.append(
        {
            "index": idx,
            "resourceType": "ServiceRequest",
            "id": rid,
            "url": f"{_FHIR_BASE_URL_FALLBACK}/{ref}",
            "display": display,
        }
    )
    facts.append({"text": f"Ordered: {display}", "source_ref": ref})


def _add_diagnostic_report(
    report: dict[str, Any],
    citations: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    resource_ids: list[str],
) -> None:
    rid = report.get("id", "")
    ref = f"DiagnosticReport/{rid}"
    resource_ids.append(ref)
    idx = len(citations) + 1
    code = report.get("code") or {}
    display = code.get("text") or (code.get("coding") or [{}])[0].get("display") or "DiagnosticReport"
    citations.append(
        {
            "index": idx,
            "resourceType": "DiagnosticReport",
            "id": rid,
            "url": f"{_FHIR_BASE_URL_FALLBACK}/{ref}",
            "display": display,
        }
    )
    facts.append({"text": f"Diagnostic report: {display}", "source_ref": ref})


async def _synthesize_answer(question: str, facts: list[dict[str, Any]]) -> str:
    facts_text = "\n".join(f"[{i + 1}] {f['text']}" for i, f in enumerate(facts))
    prompt = f"Question: {question}\n\nVerified facts:\n{facts_text}\n\nAnswer:"
    config = load_llm_config()
    client = create_llm_client(config)
    return client.complete(prompt, system=_ANSWER_SYSTEM_PROMPT)
