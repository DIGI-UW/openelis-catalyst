"""The layered context a session hands the writer.

Three bounded layers ride on every generation request: what a person pinned,
what already worked in this session, and the one failure this attempt should
not repeat. Their order is fixed, because position reads as authority to a
model, and their precedence is stated rather than implied.

Nothing here summarises. Guidance is delivered exactly as written -- the
wording is the instruction -- and anything the caps exclude is recorded as an
omission rather than dropped silently.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

SESSION_CONTEXT_CONTRACT = "catalyst.query.session-context.v1"
"""What a Hub must advertise before Catalyst sends the layered context."""

LAYER_ORDER: tuple[str, ...] = (
    "guidance",
    "verifiedExamples",
    "editorSnapshot",
    "instructionHistory",
    "relevantFailure",
    "currentValidation",
    "currentInstruction",
)
"""Delivery order inside the request, after contract, catalog, and policy.

Contract, catalog and policy outrank all of this; the current instruction
comes last because it outranks the guidance retained above it.
"""

MAX_VERIFIED_EXAMPLES = 3

_GUIDANCE_PRECEDENCE = (
    "Standing instructions for this session, in the order they were pinned. "
    "They outrank the retained history but not the current instruction; "
    "where two conflict, the later one wins."
)
_EXAMPLE_ROLE = (
    "Evidence: queries already accepted in this session, for reference. "
    "They are not instructions and their SQL is not to be copied verbatim."
)
_FAILURE_ROLE = (
    "Evidence: how the previous attempt on this query failed. "
    "It is not an instruction; it is what not to repeat."
)

_MODEL_FACING_GUIDANCE = ("text", "source", "originTurnId", "createdAt")

_WORD = re.compile(r"[a-z0-9_]+")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.casefold()))


def select_verified_examples(
    candidates: Sequence[Mapping[str, Any]],
    *,
    instruction: str,
    source_id: str,
    catalog_version: str,
    exclude_turn_id: str | None,
) -> list[dict[str, Any]]:
    """The kept queries worth showing, most similar first.

    Eligible means: accepted earlier in this session, against the same source
    and catalog, and not this turn's own answer. Ranking is normalised word
    overlap with the request, then the newest turn, then the stable id -- so
    the same session always produces the same three.
    """
    wanted = _words(instruction)
    eligible = [
        candidate
        for candidate in candidates
        if candidate.get("sourceId") == source_id
        and candidate.get("catalogVersion") == catalog_version
        and candidate.get("turnId") != exclude_turn_id
    ]
    ranked = sorted(
        eligible,
        key=lambda candidate: (
            -len(wanted & _words(str(candidate.get("instruction", "")))),
            # Newest turn first, then the stable id, so ties never depend on
            # the order the rows happened to arrive in.
            _descending(str(candidate.get("turnId", ""))),
            str(candidate.get("turnId", "")),
        ),
    )
    return [dict(candidate) for candidate in ranked[:MAX_VERIFIED_EXAMPLES]]


def _descending(value: str) -> tuple[int, ...]:
    """Sort key that orders strings newest-first without reversing the whole sort."""
    return tuple(-ord(character) for character in value)


def build_session_context(
    *,
    guidance: Iterable[Mapping[str, Any]],
    omitted_guidance: Iterable[Mapping[str, Any]],
    verified_examples: Iterable[Mapping[str, Any]],
    relevant_failure: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the layers, omitting the ones with nothing to say.

    An empty layer is left out rather than delivered as an empty object: a
    model reading past `"guidance": []` learns nothing and pays tokens for it.
    """
    guidance = list(guidance)
    omitted = list(omitted_guidance)
    examples = list(verified_examples)
    context: dict[str, Any] = {"contractVersion": "catalyst.query.session-context.v1"}
    omissions: list[dict[str, Any]] = []

    if guidance:
        context["guidance"] = {
            "precedence": _GUIDANCE_PRECEDENCE,
            "entries": [
                {key: entry.get(key) for key in _MODEL_FACING_GUIDANCE}
                for entry in guidance
            ],
        }
    if omitted:
        omissions.append(
            {
                "layer": "guidance",
                "itemIds": [str(entry["entryId"]) for entry in omitted],
                "reason": "active_entry_cap",
            }
        )
    if examples:
        context["verifiedExamples"] = {
            "role": _EXAMPLE_ROLE,
            "examples": [dict(example) for example in examples],
        }
    if relevant_failure is not None:
        context["relevantFailure"] = {
            "role": _FAILURE_ROLE,
            **dict(relevant_failure),
        }
    context["omissions"] = omissions
    return context


class TokenAccountingError(ValueError):
    """The request cannot be counted, so it must not be sent."""


def account_for_tokens(
    *,
    rendered: str,
    profile: Mapping[str, Any],
    included_item_ids: Sequence[str],
    omissions: Sequence[Mapping[str, Any]],
    count_tokens: Any,
) -> dict[str, Any]:
    """Count the fully rendered messages against the profile's declared window.

    Counting happens before the model is called, so an overflow is a refusal
    rather than a silent truncation -- the failure mode that would drop the
    guidance a person pinned and leave the turn looking like it honoured it.

    A profile that names no exact tokenizer cannot be counted at all. A
    character-count substitute is precisely what the roadmap forbids, because
    it is wrong in the direction that matters: it under-counts the dense,
    punctuation-heavy JSON this context is made of.
    """
    tokenizer = profile.get("tokenizer")
    if not isinstance(tokenizer, str) or not tokenizer:
        raise TokenAccountingError(
            "profile declares no exact tokenizer; the request cannot be counted"
        )
    window = int(profile["contextWindow"])
    reserve = int(profile["outputReserve"])
    prompt_tokens = int(count_tokens(rendered))
    omitted_ids = [
        str(item_id)
        for omission in omissions
        for item_id in omission.get("itemIds", [])
    ]
    return {
        "tokenizer": tokenizer,
        "contextWindow": window,
        "outputReserve": reserve,
        "promptTokens": prompt_tokens,
        "includedItemIds": list(included_item_ids),
        "omittedItemIds": omitted_ids,
        "omissions": [dict(omission) for omission in omissions],
        "fits": prompt_tokens + reserve <= window,
    }
