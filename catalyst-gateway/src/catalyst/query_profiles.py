"""Gateway-owned governed-query profiles.

These replace the med-agent-hub's catalyst-query-* profiles: the gateway now owns
which model/prompt/knobs fill each role and — the point of the whole refactor —
whether a reviewer role exists at all. Writer-only is the default; a self-checked
writer+reviewer variant is offered as an option. Adding a workflow is adding an
:class:`EngineProfile` here, not editing the hub.
"""

from __future__ import annotations

from .query_engine import EngineProfile

# Q4 writer for the CPU-only demo lane; the full-weight writer and a
# different-family reviewer for hosts with a GPU to spend.
WRITER_MODEL = "gemma-4-12b-q4"
BUNDLED_WRITER_MODEL = "qwen2.5-coder-1.5b-instruct-q4_k_m"
GPU_WRITER_MODEL = "gemma-4-12b"
GPU_REVIEWER_MODEL = "qwen2.5-14b"
WRITER_PROMPT = "catalyst-query-generate"
REVIEW_PROMPT = "catalyst-query-review"
MAX_OUTPUT_TOKENS = 1024
_ZERO_KNOBS = {
    "temperature": 0,
    "dry": 0,
    "maxTokens": MAX_OUTPUT_TOKENS,
}

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

# Optional bundled lane: the small local GGUF writes once, then deterministic
# lint decides whether the candidate can be finalized. It has no reviewer role.
BUNDLED_WRITER_ONLY = EngineProfile(
    id="catalyst-query-qwen-coder-1.5b",
    label="Catalyst governed query — Qwen 2.5 Coder 1.5B (Q4, writer only, bundled demo)",
    models={"query_generate": BUNDLED_WRITER_MODEL},
    knobs={"query_generate": dict(_ZERO_KNOBS)},
    prompts={"query_generate": WRITER_PROMPT},
    policies={"generation_attempts": 3, "allowed_operation": "select"},
)

# Full-weight writer, no review. The Q4 profiles exist so the demo runs on a
# host with no GPU to spend; where there is one, quantisation is a cost nothing
# is asking us to pay.
GPU_WRITER_ONLY = EngineProfile(
    id="catalyst-query-gemma-4-12b",
    label="Catalyst governed query — Gemma 4 12B (writer only)",
    models={"query_generate": GPU_WRITER_MODEL},
    knobs={"query_generate": dict(_ZERO_KNOBS)},
    prompts={"query_generate": WRITER_PROMPT},
    policies={"generation_attempts": 3, "allowed_operation": "select"},
)

# Writer and reviewer from different model families, which is the only variant
# here where review is genuinely independent: a model re-reading its own output
# shares the blind spot that produced it. Gemma writes the query, Qwen corrects
# it. Both stay resident on a 24G-class GPU (~12G + ~8.4G of weights), so the
# role switch costs nothing once warm.
GPU_WRITER_REVIEWED_TEAM = EngineProfile(
    id="catalyst-query-gemma-4-12b-qwen2.5-14b-checked",
    label="Catalyst governed query — Gemma 4 12B writer, Qwen 2.5 14B reviewer",
    models={
        "query_generate": GPU_WRITER_MODEL,
        "query_review": GPU_REVIEWER_MODEL,
    },
    knobs={
        "query_generate": dict(_ZERO_KNOBS),
        "query_review": dict(_ZERO_KNOBS),
    },
    prompts={"query_generate": WRITER_PROMPT, "query_review": REVIEW_PROMPT},
    policies={
        "generation_attempts": 3,
        "allowed_operation": "select",
        # Distinct classes: this is the cross-family pairing, not a self-check.
        "model_classes": {
            "query_generate": "gemma-4",
            "query_review": "qwen2.5",
        },
    },
)

DEFAULT_PROFILE_ID = WRITER_ONLY.id

# Ordered registry: default first (drives the UI dropdown order), then the
# remaining CPU-only variant, then the GPU lane.
PROFILES: dict[str, EngineProfile] = {
    WRITER_ONLY.id: WRITER_ONLY,
    WRITER_REVIEWED.id: WRITER_REVIEWED,
    BUNDLED_WRITER_ONLY.id: BUNDLED_WRITER_ONLY,
    GPU_WRITER_ONLY.id: GPU_WRITER_ONLY,
    GPU_WRITER_REVIEWED_TEAM.id: GPU_WRITER_REVIEWED_TEAM,
}
