"""Optional session guidance retained verbatim for experiments.

Guidance is durable and append-only. Delivery does not impose a fixed item
limit or claim a precedence that the planned research has not established.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.catalyst.storage import WorkbenchStore


def _store(tmp_path: Path) -> WorkbenchStore:
    return WorkbenchStore(tmp_path / "gateway.sqlite3")


def _session(store: WorkbenchStore) -> str:
    session = store.create_session(
        question="Show recent results",
        profile_id="catalyst-query-checked",
        dataset_id="openelis-demo",
        dataset_version="1",
        catalog_version="2026.07",
    )
    return str(session["sessionId"])


def test_a_pin_is_stored_verbatim_with_its_provenance(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session_id = _session(store)

    entry = store.pin_guidance(
        session_id,
        text="  Exclude do_not_perform rows.  ",
        source="human",
    )

    # Verbatim: the gateway does not tidy what a person wrote.
    assert entry["text"] == "  Exclude do_not_perform rows.  "
    assert entry["contractVersion"] == "catalyst.workbench.guidance.v1"
    assert entry["state"] == "active"
    assert entry["order"] == 1
    assert entry["source"] == "human"
    assert entry["originTurnId"] is None
    assert len(entry["textDigest"]) == 64
    assert [event["action"] for event in entry["events"]] == ["pinned"]


def test_entries_keep_the_order_they_were_recorded_in(tmp_path: Path) -> None:
    """Recorded sequence is preserved without defining conflict precedence."""
    store = _store(tmp_path)
    session_id = _session(store)
    for text in ("first", "second", "third"):
        store.pin_guidance(session_id, text=text, source="human")

    active = store.active_guidance(session_id)

    assert [entry["text"] for entry in active] == ["first", "second", "third"]
    assert [entry["order"] for entry in active] == [1, 2, 3]


def test_unpinning_appends_an_event_and_never_edits_history(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    session_id = _session(store)
    entry = store.pin_guidance(session_id, text="temporary", source="human")

    store.unpin_guidance(session_id, entry["entryId"])

    assert store.active_guidance(session_id) == []
    stored = store.guidance_history(session_id)
    assert len(stored) == 1
    assert stored[0]["state"] == "unpinned"
    assert stored[0]["text"] == "temporary"
    assert [event["action"] for event in stored[0]["events"]] == [
        "pinned",
        "unpinned",
    ]


def test_replacing_an_entry_supersedes_it_and_keeps_both(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session_id = _session(store)
    first = store.pin_guidance(session_id, text="old wording", source="human")

    second = store.pin_guidance(
        session_id, text="new wording", source="human", supersedes=first["entryId"]
    )

    assert [entry["text"] for entry in store.active_guidance(session_id)] == [
        "new wording"
    ]
    history = {entry["entryId"]: entry for entry in store.guidance_history(session_id)}
    assert history[first["entryId"]]["state"] == "superseded"
    assert history[first["entryId"]]["supersededBy"] == second["entryId"]


def test_more_than_twenty_active_entries_are_delivered_without_a_fixed_cap(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    session_id = _session(store)
    for index in range(25):
        store.pin_guidance(session_id, text=f"entry {index}", source="human")

    active = store.active_guidance(session_id)

    assert len(active) == 25
    assert active[0]["text"] == "entry 0"
    assert active[-1]["text"] == "entry 24"


def test_guidance_never_leaks_between_sessions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first_session = _session(store)
    second_session = _session(store)
    store.pin_guidance(first_session, text="only mine", source="human")

    assert store.active_guidance(second_session) == []


def test_a_finding_accepted_from_a_failed_turn_records_where_it_came_from(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    session_id = _session(store)

    entry = store.pin_guidance(
        session_id,
        text="There is no patient_last_name; the name is on the dimension.",
        source="system",
        origin_turn_id="1f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f",
    )

    assert entry["source"] == "system"
    assert entry["originTurnId"] == "1f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f"


def test_empty_guidance_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session_id = _session(store)
    with pytest.raises(ValueError, match="guidance text"):
        store.pin_guidance(session_id, text="   ", source="human")
