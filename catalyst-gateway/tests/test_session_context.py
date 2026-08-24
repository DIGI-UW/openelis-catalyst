"""The layered context the writer receives, and the order it arrives in.

Phase 1 adds three bounded layers to a request: session guidance, verified
examples from earlier in the session, and the one prior failure on this
revision line. The roadmap fixes their order and their precedence, because a
model reads position as authority: contract, catalog and policy outrank all
user context; the current instruction outranks retained guidance; later
guidance wins a guidance conflict; history, failures and examples are
evidence rather than commands.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.catalyst.session_context import (
    LAYER_ORDER,
    build_session_context,
    select_verified_examples,
)


def _entry(order: int, text: str, **extra: Any) -> dict[str, Any]:
    return {
        "contractVersion": "catalyst.workbench.guidance.v1",
        "entryId": f"guidance-{order}",
        "order": order,
        "text": text,
        "textDigest": "0" * 64,
        "source": "human",
        "originTurnId": None,
        "createdAt": "2026-08-24T00:00:00Z",
        "state": "active",
        **extra,
    }


def test_the_layers_arrive_in_the_order_the_roadmap_fixes() -> None:
    assert LAYER_ORDER == (
        "guidance",
        "verifiedExamples",
        "editorSnapshot",
        "instructionHistory",
        "relevantFailure",
        "currentValidation",
        "currentInstruction",
    )


def test_guidance_is_delivered_verbatim_in_pin_order() -> None:
    context = build_session_context(
        guidance=[_entry(1, "  Exclude do_not_perform.  "), _entry(2, "Use CIEL.")],
        omitted_guidance=[],
        verified_examples=[],
        relevant_failure=None,
    )

    assert [item["text"] for item in context["guidance"]["entries"]] == [
        "  Exclude do_not_perform.  ",
        "Use CIEL.",
    ]
    # The model-facing shape is exactly what the roadmap names.
    assert set(context["guidance"]["entries"][0]) == {
        "text",
        "source",
        "originTurnId",
        "createdAt",
    }


def test_guidance_says_it_is_standing_instruction_not_evidence() -> None:
    """Precedence is stated, because position alone is ambiguous."""
    context = build_session_context(
        guidance=[_entry(1, "Exclude do_not_perform.")],
        omitted_guidance=[],
        verified_examples=[],
        relevant_failure=None,
    )
    precedence = context["guidance"]["precedence"]

    assert "current instruction" in precedence
    assert "later" in precedence.lower()


def test_an_entry_the_cap_pushed_out_is_recorded_as_omitted() -> None:
    context = build_session_context(
        guidance=[_entry(2, "kept")],
        omitted_guidance=[_entry(1, "pushed out")],
        verified_examples=[],
        relevant_failure=None,
    )

    omissions = context["omissions"]
    assert omissions[0]["layer"] == "guidance"
    assert omissions[0]["itemIds"] == ["guidance-1"]
    assert omissions[0]["reason"] == "active_entry_cap"


def test_failures_and_examples_are_labelled_evidence_not_commands() -> None:
    context = build_session_context(
        guidance=[],
        omitted_guidance=[],
        verified_examples=[
            {
                "turnId": "t1",
                "instruction": "count results by status",
                "sql": "SELECT 1",
                "queryDigest": "a" * 64,
            }
        ],
        relevant_failure={
            "turnId": "t2",
            "code": "catalog.unknown_column",
            "message": "no such column",
        },
    )

    assert "evidence" in context["verifiedExamples"]["role"].lower()
    assert "evidence" in context["relevantFailure"]["role"].lower()


def test_a_request_with_nothing_pinned_carries_no_empty_layers() -> None:
    """Absent layers are absent, not empty objects the model must read past."""
    context = build_session_context(
        guidance=[],
        omitted_guidance=[],
        verified_examples=[],
        relevant_failure=None,
    )

    assert "guidance" not in context
    assert "verifiedExamples" not in context
    assert "relevantFailure" not in context
    assert context["omissions"] == []


# --- verified example selection --------------------------------------------


def _kept(turn: str, instruction: str, digest: str = "a" * 64) -> dict[str, Any]:
    return {
        "turnId": turn,
        "instruction": instruction,
        "sql": f"-- {turn}",
        "queryDigest": digest,
        "sourceId": "openmrs-hiv",
        "catalogVersion": "v6",
    }


def test_examples_rank_by_word_overlap_with_the_request() -> None:
    chosen = select_verified_examples(
        [
            _kept("t1", "count medication requests by name"),
            _kept("t2", "list visits by encounter type"),
            _kept("t3", "count medication requests by gender"),
        ],
        instruction="count medication requests by gender and name",
        source_id="openmrs-hiv",
        catalog_version="v6",
        exclude_turn_id=None,
    )

    assert [item["turnId"] for item in chosen][:2] == ["t3", "t1"]


def test_at_most_three_examples_travel() -> None:
    chosen = select_verified_examples(
        [_kept(f"t{index}", "count medication requests") for index in range(6)],
        instruction="count medication requests",
        source_id="openmrs-hiv",
        catalog_version="v6",
        exclude_turn_id=None,
    )

    assert len(chosen) == 3


def test_a_turn_never_receives_its_own_answer_as_an_example() -> None:
    chosen = select_verified_examples(
        [_kept("t1", "count medication requests")],
        instruction="count medication requests",
        source_id="openmrs-hiv",
        catalog_version="v6",
        exclude_turn_id="t1",
    )

    assert chosen == []


def test_examples_from_another_source_or_catalog_are_not_eligible() -> None:
    chosen = select_verified_examples(
        [
            _kept("t1", "count medication requests") | {"sourceId": "openelis"},
            _kept("t2", "count medication requests") | {"catalogVersion": "v5"},
        ],
        instruction="count medication requests",
        source_id="openmrs-hiv",
        catalog_version="v6",
        exclude_turn_id=None,
    )

    assert chosen == []


def test_selection_is_deterministic_when_overlap_ties() -> None:
    """Ties break on the newest turn, then the stable id -- never on chance."""
    candidates = [
        _kept("t1", "count medication requests"),
        _kept("t2", "count medication requests"),
        _kept("t3", "count medication requests"),
    ]
    first = select_verified_examples(
        candidates,
        instruction="count medication requests",
        source_id="openmrs-hiv",
        catalog_version="v6",
        exclude_turn_id=None,
    )
    second = select_verified_examples(
        list(reversed(candidates)),
        instruction="count medication requests",
        source_id="openmrs-hiv",
        catalog_version="v6",
        exclude_turn_id=None,
    )

    assert [item["turnId"] for item in first] == [item["turnId"] for item in second]


# --- token accounting ------------------------------------------------------
#
# A profile declares its window, its output reserve, and the exact tokenizer.
# The fully rendered messages are counted against them before the model is
# called, so overflow is a refusal rather than a silent truncation that
# quietly drops the guidance a person pinned.


def test_accounting_reports_the_counted_prompt_against_the_declared_window() -> None:
    from src.catalyst.session_context import account_for_tokens

    accounting = account_for_tokens(
        rendered="a b c d",
        profile={"contextWindow": 100, "outputReserve": 10, "tokenizer": "gemma-4"},
        included_item_ids=["guidance-1"],
        omissions=[],
        count_tokens=lambda text: len(text.split()),
    )

    assert accounting["promptTokens"] == 4
    assert accounting["contextWindow"] == 100
    assert accounting["outputReserve"] == 10
    assert accounting["tokenizer"] == "gemma-4"
    assert accounting["includedItemIds"] == ["guidance-1"]
    assert accounting["fits"] is True


def test_a_prompt_that_leaves_no_room_for_the_reply_does_not_fit() -> None:
    from src.catalyst.session_context import account_for_tokens

    accounting = account_for_tokens(
        rendered="a b c d e f g h i j",
        profile={"contextWindow": 12, "outputReserve": 5, "tokenizer": "gemma-4"},
        included_item_ids=[],
        omissions=[],
        count_tokens=lambda text: len(text.split()),
    )

    assert accounting["fits"] is False


def test_a_profile_without_an_exact_tokenizer_cannot_be_counted() -> None:
    """A character-count substitute is the thing the roadmap forbids."""
    from src.catalyst.session_context import TokenAccountingError, account_for_tokens

    with pytest.raises(TokenAccountingError, match="tokenizer"):
        account_for_tokens(
            rendered="a b",
            profile={"contextWindow": 100, "outputReserve": 10},
            included_item_ids=[],
            omissions=[],
            count_tokens=lambda text: len(text.split()),
        )


def test_every_omission_travels_with_its_reason() -> None:
    from src.catalyst.session_context import account_for_tokens

    accounting = account_for_tokens(
        rendered="a",
        profile={"contextWindow": 100, "outputReserve": 10, "tokenizer": "gemma-4"},
        included_item_ids=[],
        omissions=[
            {"layer": "guidance", "itemIds": ["g1"], "reason": "active_entry_cap"}
        ],
        count_tokens=lambda text: len(text.split()),
    )

    assert accounting["omittedItemIds"] == ["g1"]
    assert accounting["omissions"][0]["reason"] == "active_entry_cap"
