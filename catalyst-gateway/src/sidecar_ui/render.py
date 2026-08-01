"""Rendering for the Catalyst sidecar report UI (feature 011, Story 2).

Gateway-served, server-rendered HTML (Jinja2) — no separate frontend build,
per specs/011-catalyst-fhir-sidecar-poc/research.md item 4 (the brief's own
stated POC-simplicity preference).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")


def _linkify_citation_markers(answer: str) -> str:
    """Turn inline '[1]', '[2]' markers (see fhir_grounding._ANSWER_SYSTEM_PROMPT)
    into styled spans. The answer text itself is escaped first since this
    function's output is marked |safe in the template — it is the one place
    in the response responsible for its own escaping."""
    escaped = html.escape(answer)

    def _replace(match: re.Match) -> str:
        index = match.group(1)
        return f'<sup class="citation-marker" data-citation="{index}">[{index}]</sup>'

    return _CITATION_MARKER_RE.sub(_replace, escaped)


def render_ask_page(question: Optional[str] = None, response: Optional[dict[str, Any]] = None) -> str:
    template = _env.get_template("ask.html")
    context: dict[str, Any] = {"question": question, "response": response}
    if response is not None:
        context["answer_html"] = _linkify_citation_markers(response.get("answer", ""))
        context["response_json"] = json.dumps(response, indent=2)
    return template.render(**context)
