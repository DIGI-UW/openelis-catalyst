"""The complete eligible session context the writer receives."""

from __future__ import annotations

from typing import Any

from src.catalyst.session_context import (
    build_session_context,
    select_verified_examples,
)
from src.catalyst.service import CatalystService


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


def test_guidance_is_labelled_as_optional_experimental_context() -> None:
    context = build_session_context(
        guidance=[_entry(1, "Exclude do_not_perform.")],
        omitted_guidance=[],
        verified_examples=[],
        relevant_failure=None,
    )
    assert "experiment" in context["guidance"]["role"]
    assert "provenance" in context["guidance"]["role"]


def test_an_explicitly_omitted_entry_keeps_its_reason() -> None:
    context = build_session_context(
        guidance=[_entry(2, "kept")],
        omitted_guidance=[
            _entry(1, "not supplied", omissionReason="operator_excluded")
        ],
        verified_examples=[],
        relevant_failure=None,
    )

    omissions = context["omissions"]
    assert omissions[0]["layer"] == "guidance"
    assert omissions[0]["itemIds"] == ["guidance-1"]
    assert omissions[0]["reason"] == "operator_excluded"


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


def test_examples_remain_in_recorded_session_order() -> None:
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

    assert [item["turnId"] for item in chosen] == ["t1", "t2", "t3"]


def test_every_eligible_example_travels() -> None:
    chosen = select_verified_examples(
        [_kept(f"t{index}", "count medication requests") for index in range(6)],
        instruction="count medication requests",
        source_id="openmrs-hiv",
        catalog_version="v6",
        exclude_turn_id=None,
    )

    assert len(chosen) == 6


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


def test_selection_does_not_invent_a_relevance_ranking() -> None:
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

    assert [item["turnId"] for item in first] == ["t1", "t2", "t3"]
    assert [item["turnId"] for item in second] == ["t3", "t2", "t1"]


def test_only_validated_and_successfully_executed_kept_queries_become_examples() -> (
    None
):
    version = {
        "versionId": "version-1",
        "sql": "SELECT 1",
        "queryDigest": "a" * 64,
    }
    turn = {
        "turnId": "turn-1",
        "instruction": "Show one row",
        "selectedVersionId": version["versionId"],
        "dataSourceId": "openmrs-hiv",
        "catalogVersion": "runtime-catalog",
    }
    base_session = {
        "sessionId": "session-1",
        "versions": [version],
        "validations": [],
        "executions": [],
    }
    validation = {
        "validationId": "validation-1",
        "versionId": version["versionId"],
        "queryDigest": version["queryDigest"],
        "status": "invalid",
        "validatorRevision": "validator-1",
        "validatorDigest": "b" * 64,
        "findings": [{"ruleCode": "advisory.warning", "severity": "warning"}],
        "createdAt": "2026-08-25T00:00:00Z",
    }
    failed_execution = {
        "executionId": "execution-failed",
        "sessionId": base_session["sessionId"],
        "versionId": version["versionId"],
        "queryDigest": version["queryDigest"],
        "status": "failed",
    }
    successful_execution = {
        "executionId": "execution-succeeded",
        "sessionId": base_session["sessionId"],
        "versionId": version["versionId"],
        "queryDigest": version["queryDigest"],
        "status": "succeeded",
        "completedAt": "2026-08-25T00:01:00Z",
        "durationMs": 8,
        "result": {"rows": [[{"type": "string", "value": "not context"}]]},
    }

    assert CatalystService._verified_examples(base_session, [turn]) == []
    assert (
        CatalystService._verified_examples(
            base_session | {"validations": [validation]}, [turn]
        )
        == []
    )
    assert (
        CatalystService._verified_examples(
            base_session
            | {"validations": [validation], "executions": [failed_execution]},
            [turn],
        )
        == []
    )

    examples = CatalystService._verified_examples(
        base_session
        | {
            "validations": [validation],
            "executions": [failed_execution, successful_execution],
        },
        [turn],
    )

    assert len(examples) == 1
    assert examples[0]["advisoryValidations"][0]["status"] == "invalid"
    assert examples[0]["advisoryValidations"][0]["findings"] == validation["findings"]
    assert examples[0]["successfulExecutions"] == [
        {
            "executionId": "execution-succeeded",
            "status": "succeeded",
            "completedAt": "2026-08-25T00:01:00Z",
            "durationMs": 8,
        }
    ]
    assert "result" not in examples[0]
