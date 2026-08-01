"""Tests for the gateway-served sidecar report UI (feature 011, Story 2).

Renders src/sidecar_ui/render.py directly against fixture SidecarResponse
payloads — fast and deterministic, unlike the live full-stack contract test
in test_sidecar_response_contract.py. The real end-to-end render (through
GET /sidecar and POST /sidecar/ask against the live local stack) was also
manually verified in a browser during development; this file pins the
template's structural guarantees against regression.
"""

from __future__ import annotations

from src.sidecar_ui.render import render_ask_page

_GROUNDED_RESPONSE = {
    "answer": "Patient has one abnormal result [1].",
    "facts": [{"text": "Potassium 6.2 mmol/L (High)", "source_ref": "Observation/obs1"}],
    "citations": [
        {
            "index": 1,
            "resourceType": "Observation",
            "id": "obs1",
            "url": "https://localhost:18443/OpenELIS-Global/fhir/Observation/obs1",
            "display": "Potassium",
        },
        {
            "index": 2,
            "resourceType": "Patient",
            "id": "p1",
            "url": "https://localhost:18443/OpenELIS-Global/fhir/Patient/p1",
            "display": "John TEST-Smith",
        },
    ],
    "uiBlocks": [
        {
            "type": "lab_result_table",
            "rows": [
                {
                    "test": "Potassium",
                    "value": "6.2",
                    "unit": "mmol/L",
                    "refRange": "3.5-5.1",
                    "flag": "H",
                    "date": "2026-04-15",
                    "orderRef": "ServiceRequest/sr1",
                }
            ],
        },
        {
            "type": "lab_timeline",
            "events": [
                {
                    "date": "2026-04-15",
                    "resourceType": "Observation",
                    "id": "obs1",
                    "display": "Potassium",
                    "flag": "abnormal",
                }
            ],
        },
    ],
    "provenance": {
        "fhir_surface": "embedded",
        "fhir_base_url": "https://localhost:18443/OpenELIS-Global/fhir",
        "tools_called": ["search_patient", "get_observations"],
        "resource_ids": ["Patient/p1", "Observation/obs1"],
    },
}

_ABSTENTION_RESPONSE = {
    "answer": "I don't have data to answer this: no service requests data found for patient John TEST-Smith in OE2",
    "facts": [],
    "citations": [],
    "uiBlocks": [],
    "provenance": {
        "fhir_surface": "embedded",
        "fhir_base_url": "https://localhost:18443/OpenELIS-Global/fhir",
        "tools_called": [],
        "resource_ids": [],
    },
}


class TestGroundedResponseRendering:
    def test_renders_one_evidence_card_per_citation(self):
        page = render_ask_page(question="q", response=_GROUNDED_RESPONSE)
        assert page.count('class="evidence-card"') == 2
        assert "Potassium" in page
        assert "John TEST-Smith" in page

    def test_renders_lab_result_table_row(self):
        page = render_ask_page(question="q", response=_GROUNDED_RESPONSE)
        assert '<table class="lab-results">' in page
        assert "<td>6.2</td>" in page
        assert "3.5-5.1" in page

    def test_abnormal_flag_gets_highlight_class(self):
        page = render_ask_page(question="q", response=_GROUNDED_RESPONSE)
        assert 'class="flag-abnormal"' in page

    def test_renders_timeline_event_in_chronological_section(self):
        page = render_ask_page(question="q", response=_GROUNDED_RESPONSE)
        assert '<ul class="timeline">' in page
        assert 'class="abnormal"' in page

    def test_citation_markers_in_answer_text_are_linkified(self):
        page = render_ask_page(question="q", response=_GROUNDED_RESPONSE)
        assert 'class="citation-marker" data-citation="1"' in page

    def test_debug_drawer_shows_real_tool_calls(self):
        page = render_ask_page(question="q", response=_GROUNDED_RESPONSE)
        assert "search_patient" in page
        assert "get_observations" in page
        # raw JSON dump present, HTML-escaped by Jinja2 autoescape (correct:
        # this is untrusted-ish debug content, not markup we control)
        assert "&#34;answer&#34;" in page


class TestAbstentionResponseRendering:
    def test_shows_no_data_found_badge(self):
        page = render_ask_page(question="q", response=_ABSTENTION_RESPONSE)
        assert "No data found" in page

    def test_no_evidence_cards_or_ui_blocks_rendered(self):
        page = render_ask_page(question="q", response=_ABSTENTION_RESPONSE)
        assert 'class="evidence-card"' not in page
        assert '<table class="lab-results">' not in page
        assert '<ul class="timeline">' not in page

    def test_debug_drawer_does_not_error_on_zero_tool_calls(self):
        """Spec Edge Case: a pure-abstention answer (zero tool calls) must
        render the drawer plainly, not appear broken."""
        page = render_ask_page(question="q", response=_ABSTENTION_RESPONSE)
        assert "none (abstained before any tool call)" in page


class TestAskFormRendering:
    def test_bare_form_has_no_response_section(self):
        page = render_ask_page()
        assert "<form" in page
        # Check for the actual markup, not just the substring — the
        # stylesheet legitimately defines `.evidence-card`/`.debug-drawer`
        # selectors regardless of whether any element uses them.
        assert '<div class="evidence-card">' not in page
        assert "<details class=\"debug-drawer\">" not in page
