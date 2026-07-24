"""Gateway-owned governed-query profiles.

These replace the med-agent-hub's catalyst-query-* profiles: the gateway now owns
which model/prompt/knobs fill each role and — the point of the whole refactor —
whether a reviewer role exists at all. Writer-only is the default; a self-checked
writer+reviewer variant is offered as an option. Adding a workflow is adding an
:class:`EngineProfile` here, not editing the hub.
"""

from __future__ import annotations

from .query_engine import EngineProfile

WRITER_MODEL = "gemma-4-12b-q4"
WRITER_PROMPT = "catalyst-query-generate"
REVIEW_PROMPT = "catalyst-query-review"
_ZERO_KNOBS = {"temperature": 0, "dry": 0}

# Default: writer-only. No independent review — the writer's candidate passes
# deterministic lint inside the engine and is finalized directly.
WRITER_ONLY = EngineProfile(
    id="catalyst-query-gemma-4-12b-q4",
    label="Catalyst governed query — Gemma 4 12B (Q4, writer only, CPU-only demo)",
    models={"query_generate": WRITER_MODEL},
    knobs={"query_generate": dict(_ZERO_KNOBS)},
    prompts={"query_generate": WRITER_PROMPT},
    policies={"generation_attempts": 3, "allowed_operation": "select"},
)

# Option: self-checked writer + reviewer (same model reviewing its own output).
# Non-collaborative (same model class), so the reviewer is an independent second
# pass rather than a cross-model collaboration.
WRITER_REVIEWED = EngineProfile(
    id="catalyst-query-gemma-4-12b-q4-checked",
    label="Catalyst governed query — Gemma 4 12B (Q4, self-checked, CPU-only demo)",
    models={"query_generate": WRITER_MODEL, "query_review": WRITER_MODEL},
    knobs={
        "query_generate": dict(_ZERO_KNOBS),
        "query_review": dict(_ZERO_KNOBS),
    },
    prompts={"query_generate": WRITER_PROMPT, "query_review": REVIEW_PROMPT},
    policies={
        "generation_attempts": 3,
        "allowed_operation": "select",
        "model_classes": {"query_generate": "gemma-4-q4", "query_review": "gemma-4-q4"},
    },
)

DEFAULT_PROFILE_ID = WRITER_ONLY.id

# Ordered registry: default first (drives the UI dropdown order).
PROFILES: dict[str, EngineProfile] = {
    WRITER_ONLY.id: WRITER_ONLY,
    WRITER_REVIEWED.id: WRITER_REVIEWED,
}
