"""Sidecar response contract (feature 011).

Mirrors specs/011-catalyst-fhir-sidecar-poc/contracts/sidecar_response.schema.json
field-for-field. Plain dataclasses (not pydantic — catalyst-gateway has no
pydantic dependency today and this is a thin, already-validated-at-the-edge
shape) with a to_dict() that produces exactly the wire shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

FhirSurface = Literal["hapi", "embedded", "hybrid"]
ObservationFlag = Literal["N", "L", "H", "LL", "HH"]
TimelineFlag = Literal["abnormal", "normal"]


@dataclass(frozen=True)
class Fact:
    text: str
    source_ref: str  # "ResourceType/id"

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "source_ref": self.source_ref}


@dataclass(frozen=True)
class Citation:
    index: int
    resource_type: str
    id: str
    url: str
    display: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "resourceType": self.resource_type,
            "id": self.id,
            "url": self.url,
            "display": self.display,
        }


@dataclass(frozen=True)
class LabResultRow:
    test: str
    value: str
    unit: str
    ref_range: str
    flag: Optional[ObservationFlag]
    date: str
    order_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "test": self.test,
            "value": self.value,
            "unit": self.unit,
            "refRange": self.ref_range,
            "flag": self.flag,
            "date": self.date,
            "orderRef": self.order_ref,
        }


@dataclass(frozen=True)
class LabTimelineEvent:
    date: str
    resource_type: str  # "Observation" | "DiagnosticReport"
    id: str
    display: str
    flag: Optional[TimelineFlag]

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "resourceType": self.resource_type,
            "id": self.id,
            "display": self.display,
            "flag": self.flag,
        }


@dataclass(frozen=True)
class LabResultTableBlock:
    rows: list[LabResultRow]

    def to_dict(self) -> dict[str, Any]:
        return {"type": "lab_result_table", "rows": [r.to_dict() for r in self.rows]}


@dataclass(frozen=True)
class LabTimelineBlock:
    events: list[LabTimelineEvent]

    def to_dict(self) -> dict[str, Any]:
        return {"type": "lab_timeline", "events": [e.to_dict() for e in self.events]}


UiBlock = LabResultTableBlock | LabTimelineBlock


@dataclass(frozen=True)
class Provenance:
    fhir_surface: FhirSurface
    fhir_base_url: str
    tools_called: list[str]
    resource_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fhir_surface": self.fhir_surface,
            "fhir_base_url": self.fhir_base_url,
            "tools_called": self.tools_called,
            "resource_ids": self.resource_ids,
        }


@dataclass(frozen=True)
class SidecarResponse:
    answer: str
    citations: list[Citation]
    provenance: Provenance
    facts: list[Fact] = field(default_factory=list)
    ui_blocks: list[UiBlock] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "facts": [f.to_dict() for f in self.facts],
            "citations": [c.to_dict() for c in self.citations],
            "uiBlocks": [b.to_dict() for b in self.ui_blocks],
            "provenance": self.provenance.to_dict(),
        }

    def to_chat_completion(self, completion_id: str) -> dict[str, Any]:
        """OpenAI-chat-completion-shaped envelope with the sidecar fields
        merged in as additive top-level keys (contract's additionalProperties:
        true) — `answer` doubles as choices[0].message.content so generic
        OpenAI clients keep working unmodified."""
        payload = self.to_dict()
        payload["id"] = completion_id
        payload["object"] = "chat.completion"
        payload["choices"] = [
            {
                "index": 0,
                "message": {"role": "assistant", "content": self.answer},
                "finish_reason": "stop",
            }
        ]
        return payload
