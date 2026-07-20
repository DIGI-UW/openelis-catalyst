from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from src.catalyst.contracts import ContractRegistry
from src.catalyst.digest import canonical_sha256, utf8_sha256
from src.catalyst.storage import (
    ActiveTurnGenerationError,
    WorkbenchStore,
)
from src.catalyst.workbench import build_revision_context, workbench_query_digest


CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"


def _session(store: WorkbenchStore) -> dict:
    return store.create_session(
        question="Show recent viral load results",
        profile_id="catalyst-query-checked",
        dataset_id="openelis-demo",
        dataset_version="2026.07",
        catalog_version="2026.07",
    )


def _version(store: WorkbenchStore, session_id: str) -> dict:
    return store.append_version(
        session_id,
        sql="SELECT patient_id FROM analytics.lab_results LIMIT 25",
        parameters=[],
        expected_columns=[],
        author_type="model",
        provenance={"model": "gemma-4-12b"},
    )


def _snapshot(sql: str, parameters: list[dict] | None = None) -> dict:
    values = parameters or []
    return {
        "contractVersion": "catalyst.workbench.editor-snapshot.v1",
        "sql": sql,
        "parameters": values,
        "expectedColumns": [],
        "editorDigest": workbench_query_digest(sql, values, []),
    }


def _profile() -> dict:
    profile = {
        "profileId": "catalyst-query-checked",
        "profileName": "Catalyst checked",
        "profileDigest": "0" * 64,
        "writer": {
            "role": "writer",
            "providerId": "llama.cpp",
            "modelId": "gemma-4-12b",
            "modelClass": "gemma",
            "config": {"temperature": 0},
            "systemPrompt": {
                "promptId": "catalyst-query-writer",
                "version": "1",
                "promptRef": "med-agent-hub:prompts/catalyst-query-writer",
                "promptDigest": "1" * 64,
            },
        },
        "reviewer": {
            "role": "reviewer",
            "providerId": "llama.cpp",
            "modelId": "qwen2.5-14b",
            "modelClass": "qwen",
            "config": {"temperature": 0},
            "systemPrompt": {
                "promptId": "catalyst-query-reviewer",
                "version": "1",
                "promptRef": "med-agent-hub:prompts/catalyst-query-reviewer",
                "promptDigest": "2" * 64,
            },
        },
        "omissions": [],
    }
    profile["profileDigest"] = canonical_sha256(
        {key: value for key, value in profile.items() if key != "profileDigest"}
    )
    return profile


def _profile_evidence() -> dict:
    profile = _profile()
    detail = {
        "profileId": profile["profileId"],
        "profileName": profile["profileName"],
        "profileDigest": profile["profileDigest"],
        "writer": {
            **profile["writer"],
            "systemPrompt": {
                **profile["writer"]["systemPrompt"],
                "text": "Write one complete PostgreSQL query.",
            },
        },
        "reviewer": {
            **profile["reviewer"],
            "systemPrompt": {
                **profile["reviewer"]["systemPrompt"],
                "text": "Review the complete PostgreSQL query.",
            },
        },
    }
    return detail


def test_editor_digest_uses_the_normative_golden_vector() -> None:
    assert workbench_query_digest("SELECT 1", [], []) == (
        "82d9696f92e64acb0c4edba843633c97e" "b23fd3f22887d93755eb86971855105"
    )


@pytest.mark.parametrize("execution_status", ["failed", "timed_out", "cancelled"])
def test_revision_context_sanitizes_terminal_execution_context(
    execution_status: str,
) -> None:
    snapshot = _snapshot("SELECT 1")
    initial_turn = {
        "sessionId": "00000000-0000-0000-0000-000000000001",
        "turnId": "00000000-0000-0000-0000-000000000002",
        "ordinal": 1,
        "kind": "initial",
        "instruction": "Show one row",
        "instructionDigest": utf8_sha256("Show one row"),
    }
    context = build_revision_context(
        session={
            "sessionId": initial_turn["sessionId"],
            "validations": [],
            "executions": [
                {
                    "executionId": "00000000-0000-0000-0000-000000000003",
                    "versionId": "00000000-0000-0000-0000-000000000004",
                    "queryDigest": snapshot["editorDigest"],
                    "status": execution_status,
                    "validationStatus": "valid",
                    "durationMs": 8,
                    "result": {
                        "columns": [],
                        "rowCount": 1,
                        "rows": [[{"type": "string", "value": "sensitive"}]],
                    },
                    "databaseDiagnostic": {
                        "sqlstate": "08001",
                        "severity": "ERROR",
                        "message": (
                            'connection to server at "db.internal" (10.2.3.4), '
                            "port 5432 failed for user postgres"
                        ),
                        "detail": "host=db.internal user=postgres password=secret",
                        "hint": "Retry 10.2.3.4 on port 5432",
                        "position": None,
                    },
                }
            ],
        },
        prior_turns=[initial_turn],
        turn_id="00000000-0000-0000-0000-000000000005",
        instruction="Try again",
        base_classification="reused",
        observed_base={
            "versionId": "00000000-0000-0000-0000-000000000004",
            "queryDigest": snapshot["editorDigest"],
        },
        effective_base={
            "versionId": "00000000-0000-0000-0000-000000000004",
            "queryDigest": snapshot["editorDigest"],
        },
        editor_snapshot=snapshot,
    )

    execution_context = context["executionContext"]
    assert execution_context["status"] == execution_status
    assert execution_context["rowCount"] == 1
    assert {"rows", "resultRows", "result"}.isdisjoint(execution_context)
    diagnostic = execution_context["databaseDiagnostic"]
    rendered = " ".join(str(diagnostic[key]) for key in ("message", "detail", "hint"))
    for secret in ("db.internal", "10.2.3.4", "5432", "postgres", "secret"):
        assert secret not in rendered
    assert "[redacted]" in rendered


def test_revision_context_derives_blank_warning_for_matching_legacy_execution():
    snapshot = _snapshot("SELECT name_display FROM public.patient_flat_v1")
    initial_turn = {
        "sessionId": "00000000-0000-0000-0000-000000000011",
        "turnId": "00000000-0000-0000-0000-000000000012",
        "ordinal": 1,
        "kind": "initial",
        "instruction": "Show patient names",
        "instructionDigest": utf8_sha256("Show patient names"),
    }
    version_ref = {
        "versionId": "00000000-0000-0000-0000-000000000013",
        "queryDigest": snapshot["editorDigest"],
    }
    context = build_revision_context(
        session={
            "sessionId": initial_turn["sessionId"],
            "validations": [],
            "executions": [
                {
                    "executionId": "00000000-0000-0000-0000-000000000014",
                    "versionId": version_ref["versionId"],
                    "queryDigest": "f" * 64,
                    "status": "succeeded",
                    "validationStatus": "valid",
                    "durationMs": 4,
                    "result": {"warnings": ["stale warning"]},
                },
                {
                    "executionId": "00000000-0000-0000-0000-000000000015",
                    "versionId": version_ref["versionId"],
                    "queryDigest": snapshot["editorDigest"],
                    "status": "succeeded",
                    "validationStatus": "valid",
                    "durationMs": 5,
                    "result": {
                        "columns": [
                            {
                                "ordinal": 0,
                                "name": "name_display",
                                "databaseType": "text",
                                "logicalType": "string",
                            }
                        ],
                        "rows": [[{"type": "null"}], [{"type": "string", "value": ""}]],
                        "rowCount": {
                            "returned": 2,
                            "truncated": False,
                            "truncationReason": None,
                        },
                    },
                },
            ],
        },
        prior_turns=[initial_turn],
        turn_id="00000000-0000-0000-0000-000000000016",
        instruction="Use another patient name field",
        base_classification="reused",
        observed_base=version_ref,
        effective_base=version_ref,
        editor_snapshot=snapshot,
    )

    execution_context = context["executionContext"]
    assert execution_context["warnings"] == [
        "`name_display` was blank or NULL in all 2 returned rows. "
        "Select a populated column or revise the SQL expression."
    ]
    assert "stale warning" not in str(execution_context)
    assert {"rows", "resultRows", "result"}.isdisjoint(execution_context)
    ContractRegistry.load(CONTRACTS).validate(
        "catalyst-query-revision-context-v1.schema.json", context
    )


def test_requested_evidence_immediately_projects_revision_history(
    tmp_path: Path,
) -> None:
    store = WorkbenchStore(tmp_path / "requested-history.sqlite3")
    session = _session(store)
    base = _version(store, session["sessionId"])
    snapshot = _snapshot(base["sql"])
    initial_instruction = session["question"]
    initial_turn = {
        "sessionId": session["sessionId"],
        "turnId": "00000000-0000-4000-8000-000000000011",
        "ordinal": 1,
        "kind": "initial",
        "instruction": initial_instruction,
        "instructionDigest": utf8_sha256(initial_instruction),
    }
    base_ref = {
        "versionId": base["versionId"],
        "queryDigest": base["queryDigest"],
    }
    revision = build_revision_context(
        session=store.get_session(session["sessionId"]),
        prior_turns=[initial_turn],
        turn_id="00000000-0000-4000-8000-000000000012",
        instruction="Only final results",
        base_classification="reused",
        observed_base=base_ref,
        effective_base=base_ref,
        editor_snapshot=snapshot,
    )

    turn = store.claim_turn(
        session["sessionId"],
        instruction="Only final results",
        instruction_digest=utf8_sha256("Only final results"),
        profile_snapshot=_profile(),
        observed_base=base_ref,
        editor_snapshot=snapshot,
        revision_context=revision,
        hub_request_digest="3" * 64,
        catalyst_trace_id="trace-history",
        profile_evidence=_profile_evidence(),
    )
    evidence = store.get_generation_evidence(session["sessionId"], turn["turnId"])

    assert evidence is not None
    assert evidence["status"] == "requested"
    assert evidence["history"] == {
        "included": [
            {
                "turnId": initial_turn["turnId"],
                "ordinal": 1,
                "kind": "initial",
                "instructionDigest": initial_turn["instructionDigest"],
            }
        ],
        "includedDigest": canonical_sha256(
            [
                {
                    "turnId": initial_turn["turnId"],
                    "ordinal": 1,
                    "kind": "initial",
                    "instructionDigest": initial_turn["instructionDigest"],
                }
            ]
        ),
        "omitted": [],
        "omittedDigest": canonical_sha256([]),
    }
    store.close()


def test_terminal_evidence_ref_is_final_in_immutable_version_and_created_event(
    tmp_path: Path,
) -> None:
    store = WorkbenchStore(tmp_path / "terminal-evidence-link.sqlite3")
    session = _session(store)
    base = _version(store, session["sessionId"])
    base_ref = {
        "versionId": base["versionId"],
        "queryDigest": base["queryDigest"],
    }
    snapshot = _snapshot(base["sql"])
    turn = store.claim_turn(
        session["sessionId"],
        instruction="Use ten rows",
        instruction_digest=utf8_sha256("Use ten rows"),
        profile_snapshot=_profile(),
        observed_base=base_ref,
        editor_snapshot=snapshot,
        revision_context={"contractVersion": "catalyst.query.revision-context.v1"},
        hub_request_digest="4" * 64,
        catalyst_trace_id="trace-complete",
        profile_evidence=_profile_evidence(),
    )
    revision = build_revision_context(
        session=store.get_session(session["sessionId"]),
        prior_turns=[
            {
                "sessionId": session["sessionId"],
                "turnId": "00000000-0000-4000-8000-000000000021",
                "ordinal": 1,
                "kind": "initial",
                "instruction": session["question"],
                "instructionDigest": utf8_sha256(session["question"]),
            }
        ],
        turn_id=turn["turnId"],
        instruction="Use ten rows",
        base_classification="reused",
        observed_base=base_ref,
        effective_base=base_ref,
        editor_snapshot=snapshot,
    )
    store.freeze_turn_request(
        turn["turnId"],
        revision_context=revision,
        hub_request={"model": "catalyst-query-checked"},
    )
    completed = store.complete_turn(
        turn["turnId"],
        outputs=[
            {
                "sql": base["sql"].replace("LIMIT 25", "LIMIT 10"),
                "parameters": [],
                "expectedColumns": [],
                "authorType": "model",
                "provenance": {"model": "gemma-4-12b"},
            }
        ],
        selected_index=0,
        hub_trace_id="hub-trace-complete",
        hub_response={"exactHubResponse": "{}"},
        invocations=[
            {
                "invocationId": "00000000-0000-4000-8000-000000000022",
                "role": "writer",
                "stage": "followup_generation",
                "attempt": 1,
                "providerId": "llama.cpp",
                "modelId": "gemma-4-12b",
                "startedAt": "2026-07-18T12:00:00Z",
                "endedAt": "2026-07-18T12:00:01Z",
                "durationMs": 1000,
                "requestDigest": "5" * 64,
                "responseDigest": "6" * 64,
                "failureDigest": None,
                "outcome": "succeeded",
            }
        ],
    )
    evidence = store.get_generation_evidence(session["sessionId"], turn["turnId"])
    restored = store.get_session(session["sessionId"])

    assert evidence is not None
    assert restored is not None
    assert "configuration" not in evidence["invocations"][0]
    final_ref = completed["generationEvidenceRef"]
    assert final_ref["evidenceDigest"] == evidence["evidenceDigest"]
    version = restored["currentVersion"]
    assert version["provenance"]["generationEvidenceRef"] == final_ref
    assert completed["outputVersions"][0]["generationEvidenceRef"] == final_ref
    created_event = next(
        event
        for event in store.list_events(session["sessionId"])
        if event["type"] == "query_version.created"
        and event["entityRefs"].get("versionId") == version["versionId"]
    )
    assert (
        created_event["payload"]["version"]["provenance"]["generationEvidenceRef"]
        == final_ref
    )
    assert evidence["candidates"][0]["rawEvidence"]["omissionReason"] == (
        "No separate raw candidate payload was recorded."
    )
    assert "raw_model_outputs" in evidence["prohibitedClasses"]
    assert "unrelated_historical_sql" in evidence["prohibitedClasses"]
    ContractRegistry.load(CONTRACTS).validate(
        "catalyst-workbench-generation-evidence-v1.schema.json", evidence
    )
    store.close()


def test_failed_retained_writer_uses_final_evidence_ref_and_hub_trace(
    tmp_path: Path,
) -> None:
    store = WorkbenchStore(tmp_path / "failed-evidence-link.sqlite3")
    session = _session(store)
    base = _version(store, session["sessionId"])
    base_ref = {
        "versionId": base["versionId"],
        "queryDigest": base["queryDigest"],
    }
    turn = store.claim_turn(
        session["sessionId"],
        instruction="Try a reviewer correction",
        instruction_digest=utf8_sha256("Try a reviewer correction"),
        profile_snapshot=_profile(),
        observed_base=base_ref,
        editor_snapshot=_snapshot(base["sql"]),
        revision_context={"contractVersion": "catalyst.query.revision-context.v1"},
        hub_request_digest="7" * 64,
        catalyst_trace_id="trace-failed",
        profile_evidence=_profile_evidence(),
    )
    failed = store.fail_turn(
        turn["turnId"],
        stage="reviewer_validation",
        code="reviewer_validation_failed",
        message="Reviewer correction did not pass validation.",
        raw_evidence='{"status":"rejected"}',
        hub_trace_id="hub-trace-failed",
        hub_response={"exactHubResponse": "{}"},
        retained_writer={
            "sql": base["sql"].replace("LIMIT 25", "LIMIT 10"),
            "parameters": [],
            "expectedColumns": [],
            "provenance": {"model": "gemma-4-12b"},
        },
    )
    evidence = store.get_generation_evidence(session["sessionId"], turn["turnId"])
    restored = store.get_session(session["sessionId"])

    assert evidence is not None
    assert restored is not None
    retained = restored["versions"][-1]
    final_ref = failed["generationEvidenceRef"]
    assert failed["hubTraceId"] == "hub-trace-failed"
    assert evidence["correlation"]["hubTraceId"] == "hub-trace-failed"
    assert retained["provenance"]["generationEvidenceRef"] == final_ref
    assert failed["outputVersions"][0]["generationEvidenceRef"] == final_ref
    assert final_ref["evidenceDigest"] == evidence["evidenceDigest"]
    created_event = next(
        event
        for event in store.list_events(session["sessionId"])
        if event["type"] == "query_version.created"
        and event["entityRefs"].get("versionId") == retained["versionId"]
    )
    assert (
        created_event["payload"]["version"]["provenance"]["generationEvidenceRef"]
        == final_ref
    )
    store.close()


def test_turn_claim_reuses_or_promotes_exact_editor_once(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "turns.sqlite3", owner_instance_id="boot-a")
    session = _session(store)
    base = _version(store, session["sessionId"])
    observed = {
        "versionId": base["versionId"],
        "queryDigest": base["queryDigest"],
    }

    reused = store.claim_turn(
        session["sessionId"],
        instruction="Only finalized results",
        instruction_digest=workbench_query_digest("Only finalized results", [], []),
        profile_snapshot=_profile(),
        observed_base=observed,
        editor_snapshot=_snapshot(base["sql"]),
        revision_context={"contractVersion": "catalyst.query.revision-context.v1"},
        hub_request_digest="1" * 64,
        catalyst_trace_id="trace-1",
    )
    assert reused["snapshotClassification"] == "reused"
    assert reused["effectiveBaseVersion"] == observed
    assert reused["manualVersion"] is None

    store.fail_turn(
        reused["turnId"],
        stage="hub",
        code="test_failure",
        message="release claim",
        raw_evidence=None,
    )
    dirty_sql = base["sql"].replace("LIMIT 25", "LIMIT 10")
    promoted = store.claim_turn(
        session["sessionId"],
        instruction="Limit it to ten",
        instruction_digest=workbench_query_digest("Limit it to ten", [], []),
        profile_snapshot=_profile(),
        observed_base=observed,
        editor_snapshot=_snapshot(dirty_sql),
        revision_context={"contractVersion": "catalyst.query.revision-context.v1"},
        hub_request_digest="2" * 64,
        catalyst_trace_id="trace-2",
    )
    assert promoted["snapshotClassification"] == "promoted_human"
    assert (
        promoted["manualVersion"]["queryDigest"] == _snapshot(dirty_sql)["editorDigest"]
    )
    restored = store.get_session(session["sessionId"])
    assert restored is not None
    assert [version["authorType"] for version in restored["versions"]] == [
        "model",
        "human",
    ]
    store.close()


def test_manual_save_reuses_unchanged_current_and_promotes_dirty_once(
    tmp_path: Path,
) -> None:
    store = WorkbenchStore(tmp_path / "shared-resolver.sqlite3")
    session = _session(store)
    base = _version(store, session["sessionId"])
    base_ref = {"versionId": base["versionId"], "queryDigest": base["queryDigest"]}
    before = store.get_session(session["sessionId"])
    assert before is not None
    events_before = store.list_events(session["sessionId"])

    unchanged = store.append_version(
        session["sessionId"],
        sql=base["sql"],
        parameters=base["parameters"],
        expected_columns=base["expectedColumns"],
        author_type="human",
        parent_version_id=base_ref["versionId"],
        parent_query_digest=base_ref["queryDigest"],
    )

    assert unchanged == base
    assert store.get_session(session["sessionId"])["versions"] == [base]
    assert store.list_events(session["sessionId"]) == events_before

    dirty_sql = base["sql"].replace("LIMIT 25", "LIMIT 10")
    dirty = store.append_version(
        session["sessionId"],
        sql=dirty_sql,
        parameters=[],
        expected_columns=[],
        author_type="human",
        parent_version_id=base["versionId"],
        parent_query_digest=base["queryDigest"],
    )
    reused_dirty = store.append_version(
        session["sessionId"],
        sql=dirty_sql,
        parameters=[],
        expected_columns=[],
        author_type="human",
        parent_version_id=dirty["versionId"],
        parent_query_digest=dirty["queryDigest"],
    )

    assert reused_dirty == dirty
    assert len(store.get_session(session["sessionId"])["versions"]) == 2
    store.close()


def test_unresolved_snapshot_is_durable_but_not_a_version(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "unresolved.sqlite3", owner_instance_id="boot-a")
    session = _session(store)
    base = _version(store, session["sessionId"])
    observed = {"versionId": base["versionId"], "queryDigest": base["queryDigest"]}
    unresolved = _snapshot(
        "SELECT patient_id FROM analytics.lab_results WHERE test_name = :test_name",
        [{"name": "", "type": "string", "source": "human", "value": "VL"}],
    )

    turn = store.claim_turn(
        session["sessionId"],
        instruction="Fix the parameter",
        instruction_digest=workbench_query_digest("Fix the parameter", [], []),
        profile_snapshot=_profile(),
        observed_base=observed,
        editor_snapshot=unresolved,
        revision_context={"contractVersion": "catalyst.query.revision-context.v1"},
        hub_request_digest="3" * 64,
        catalyst_trace_id="trace-3",
    )

    assert turn["snapshotClassification"] == "unresolved"
    assert turn["effectiveBaseVersion"] is None
    assert turn["unresolvedPaths"] == ["$.parameters[0].name"]
    assert store.get_session(session["sessionId"])["versions"] == [base]
    store.close()


def test_active_claim_precedes_stale_and_loser_has_no_side_effects(
    tmp_path: Path,
) -> None:
    store = WorkbenchStore(tmp_path / "concurrency.sqlite3", owner_instance_id="boot-a")
    session = _session(store)
    base = _version(store, session["sessionId"])
    observed = {"versionId": base["versionId"], "queryDigest": base["queryDigest"]}
    arguments = {
        "instruction_digest": workbench_query_digest("Refine", [], []),
        "profile_snapshot": _profile(),
        "editor_snapshot": _snapshot(base["sql"]),
        "revision_context": {"contractVersion": "catalyst.query.revision-context.v1"},
        "hub_request_digest": "4" * 64,
        "catalyst_trace_id": "trace-4",
    }
    store.claim_turn(
        session["sessionId"],
        instruction="Refine",
        observed_base=observed,
        **arguments,
    )
    events_before = deepcopy(store.list_events(session["sessionId"]))

    with pytest.raises(ActiveTurnGenerationError):
        store.claim_turn(
            session["sessionId"],
            instruction="Concurrent",
            observed_base=None,
            **arguments,
        )
    assert store.list_events(session["sessionId"]) == events_before

    store.close()


def test_prior_owner_requested_turn_is_recovered_without_time_expiry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "orphan.sqlite3"
    first = WorkbenchStore(path, owner_instance_id="boot-a")
    session = _session(first)
    base = _version(first, session["sessionId"])
    first.claim_turn(
        session["sessionId"],
        instruction="Refine",
        instruction_digest=workbench_query_digest("Refine", [], []),
        profile_snapshot=_profile(),
        observed_base={
            "versionId": base["versionId"],
            "queryDigest": base["queryDigest"],
        },
        editor_snapshot=_snapshot(base["sql"]),
        revision_context={"contractVersion": "catalyst.query.revision-context.v1"},
        hub_request_digest="5" * 64,
        catalyst_trace_id="trace-5",
    )
    first.close()

    second = WorkbenchStore(path, owner_instance_id="boot-b")
    timeline = second.list_turns(session["sessionId"])
    assert timeline["turns"][-1]["status"] == "failed"
    assert timeline["turns"][-1]["failure"]["stage"] == "orphan_recovery"
    assert timeline["turns"][-1]["failure"]["code"] == "generation_interrupted"
    assert second.list_turns(session["sessionId"]) == timeline
    second.close()


def test_recorded_orphan_evidence_allows_no_invented_model_invocation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "initial-orphan.sqlite3"
    first = WorkbenchStore(path, owner_instance_id="boot-a")
    session = _session(first)
    turn = first.claim_initial_turn(
        session["sessionId"],
        instruction=session["question"],
        instruction_digest=utf8_sha256(session["question"]),
        profile_snapshot=_profile(),
        catalyst_trace_id="trace-initial",
        hub_request={"model": "catalyst-query-checked"},
        profile_evidence=_profile_evidence(),
    )
    first.close()

    second = WorkbenchStore(path, owner_instance_id="boot-b")
    timeline = second.list_turns(session["sessionId"])
    evidence = second.get_generation_evidence(session["sessionId"], turn["turnId"])
    assert evidence is not None
    assert evidence["invocations"] == []
    assert evidence["totalInvocationDurationMs"] == 0
    assert evidence["omissions"] == []
    registry = ContractRegistry.load(CONTRACTS)
    registry.validate("catalyst-workbench-turn-timeline-v1.schema.json", timeline)
    registry.validate("catalyst-workbench-generation-evidence-v1.schema.json", evidence)
    second.close()


@pytest.mark.parametrize("fixture", ["model", "later_human", "draft", "raw"])
def test_legacy_projection_is_stable_schema_valid_and_never_invents_provenance(
    tmp_path: Path,
    fixture: str,
) -> None:
    provenance = {
        "profileSnapshot": {
            "profileId": "catalyst-query-checked",
            "profileLabel": "Catalyst checked",
        }
    }
    if fixture == "draft":
        provenance["generationOutcome"] = {
            "status": "rejected",
            "createdAt": "2026-07-18T01:02:03Z",
        }
    if fixture == "raw":
        provenance["generationRawOutput"] = "{not valid json"
        provenance["generationRawOutputCreatedAt"] = "2026-07-18T02:03:04Z"
    store = WorkbenchStore(tmp_path / f"legacy-{fixture}.sqlite3")
    session = store.create_session(
        question="Show recent viral load results",
        profile_id="catalyst-query-checked",
        dataset_id="openelis-demo",
        dataset_version="2026.07",
        catalog_version="2026.07",
        provenance=provenance,
    )
    model = None
    human = None
    if fixture in {"model", "later_human"}:
        model = _version(store, session["sessionId"])
    if fixture == "later_human":
        assert model is not None
        human = store.append_version(
            session["sessionId"],
            sql=model["sql"].replace("LIMIT 25", "LIMIT 10"),
            parameters=[],
            expected_columns=[],
            author_type="human",
            parent_version_id=model["versionId"],
            parent_query_digest=model["queryDigest"],
        )
    events_before = store.list_events(session["sessionId"])

    timeline = store.list_turns(session["sessionId"])
    repeated = store.list_turns(session["sessionId"])
    turn = timeline["turns"][0]
    evidence = store.get_generation_evidence(session["sessionId"], turn["turnId"])

    assert timeline == repeated
    assert store.list_events(session["sessionId"]) == events_before
    assert evidence is not None
    assert turn["origin"] == "synthesized_legacy"
    assert turn["catalystTraceId"] is None
    assert evidence["correlation"] is None
    assert "correlation_unavailable" in evidence["omissions"]
    assert turn["generationEvidenceRef"]["evidenceDigest"] == evidence["evidenceDigest"]
    if model is not None:
        assert turn["selectedVersionId"] == model["versionId"]
    else:
        assert turn["status"] == "failed"
        assert turn["failure"]["stage"] == "legacy_generation"
    if human is not None:
        assert timeline["currentVersion"]["versionId"] == human["versionId"]
        assert turn["selectedVersionId"] != human["versionId"]
    if fixture == "raw":
        raw_candidate = evidence["candidates"][-1]
        assert raw_candidate["rawEvidence"]["exactPayload"] == "{not valid json"

    registry = ContractRegistry.load(CONTRACTS)
    registry.validate("catalyst-workbench-turn-timeline-v1.schema.json", timeline)
    registry.validate("catalyst-workbench-turn-v1.schema.json", turn)
    registry.validate("catalyst-workbench-generation-evidence-v1.schema.json", evidence)
    store.close()
