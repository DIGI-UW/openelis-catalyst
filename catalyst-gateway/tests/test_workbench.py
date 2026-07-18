from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.catalyst.policy import Violation
from src.catalyst.storage import (
    StaleWorkbenchVersionError,
    WorkbenchStore,
)
from src.catalyst.workbench import (
    VALIDATOR_REVISION,
    build_advisory_validation,
    normalize_findings,
    workbench_query_digest,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


def _session(store: WorkbenchStore) -> dict:
    return store.create_session(
        question="Count malaria results by month",
        profile_id="catalyst-query-checked",
        dataset_id="openelis-demo",
        dataset_version="2026.07",
        catalog_version="2026.07",
        browser_state={
            "expanded": False,
            "filters": {"testName": "Malaria", "patientId": None},
            "limit": 25,
            "offset": 0,
        },
        provenance={
            "traceId": "trace-1",
            "roleModels": {"query_generate": "qwen2.5-coder-14b"},
        },
    )


def _initial_version(store: WorkbenchStore, session_id: str) -> dict:
    return store.append_version(
        session_id,
        sql=(
            "SELECT result_date, COUNT(*) AS result_count "
            "FROM analytics.lab_results "
            "WHERE test_name = :test_name GROUP BY result_date"
        ),
        parameters=[
            {
                "name": "test_name",
                "type": "string",
                "source": "question",
                "value": "Malaria",
            }
        ],
        expected_columns=[
            {"name": "result_date", "logicalType": "date", "nullable": False},
            {
                "name": "result_count",
                "logicalType": "integer",
                "nullable": False,
            },
        ],
        author_type="model",
        provenance={"profileId": "catalyst-query-checked", "promptDigest": "p1"},
    )


def test_canonical_findings_are_deterministic_namespaced_and_advisory() -> None:
    digest = workbench_query_digest(
        "SELECT * FROM analytics.lab_results WHERE test_name = 'Malaria'",
        [],
    )
    raw_findings = [
        Violation("unbound_literal", "Predicate values must be bound."),
        {
            "code": "policy.unbound_predicate_literal",
            "stage": "query_lint",
            "severity": "error",
            "path": "$.sql",
            "message": "A predicate literal was not bound.",
            "suggested_action": "Replace the literal with a named parameter.",
            "repairability": "deterministic",
            "evidence": {"literal": "Malaria"},
        },
        # Duplicate input must not produce a duplicate canonical finding.
        Violation("unbound_literal", "Predicate values must be bound."),
    ]

    first = normalize_findings(
        raw_findings,
        query_digest=digest,
        default_stage="gateway_sql_policy",
    )
    second = normalize_findings(
        raw_findings,
        query_digest=digest,
        default_stage="gateway_sql_policy",
    )

    assert first == second
    assert len(first) == 2
    assert {finding["ruleCode"] for finding in first} == {
        "gateway_sql_policy.unbound_literal",
        "policy.unbound_predicate_literal",
    }
    assert all(
        finding["contractVersion"] == "catalyst.workbench.finding.v1"
        and finding["findingId"].startswith("finding-")
        and finding["validatorRevision"] == VALIDATOR_REVISION
        for finding in first
    )

    validation = build_advisory_validation(
        query_digest=digest,
        findings=first,
        duration_ms=3,
    )
    assert validation["contractVersion"] == "catalyst.workbench.validation.v1"
    assert validation["status"] == "invalid"
    assert validation["advisory"] is True
    assert validation["queryDigest"] == digest
    assert validation["checks"] == [
        {
            "name": "gateway_sql_policy",
            "status": "failed",
            "findingIds": [
                finding["findingId"]
                for finding in first
                if finding["stage"] == "gateway_sql_policy"
            ],
        },
        {
            "name": "query_lint",
            "status": "failed",
            "findingIds": [
                finding["findingId"]
                for finding in first
                if finding["stage"] == "query_lint"
            ],
        },
    ]


def test_session_versions_validation_execution_and_browser_state_restore(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workbench.sqlite3"
    clock = Clock()
    store = WorkbenchStore(path, now=clock)
    session = _session(store)
    version = _initial_version(store, session["sessionId"])

    findings = normalize_findings(
        [Violation("row_limit_exceeded", "The query has no bounded LIMIT.")],
        query_digest=version["queryDigest"],
        default_stage="gateway_sql_policy",
    )
    validation = store.append_validation(
        version["versionId"],
        build_advisory_validation(
            query_digest=version["queryDigest"],
            findings=findings,
            duration_ms=4,
        ),
    )
    execution = store.append_execution(
        version["versionId"],
        {
            "contractVersion": "catalyst.workbench.execution.v1",
            "queryDigest": version["queryDigest"],
            "idempotencyKey": "run-1",
            "status": "failed",
            "validationStatus": validation["status"],
            "databaseDiagnostic": {
                "sqlstate": "42703",
                "severity": "ERROR",
                "message": "column result_datee does not exist",
                "position": 8,
            },
            "durationMs": 11,
            "statementTimeoutMs": 500,
            "maxRows": 100,
        },
    )
    updated_browser_state = {
        "expanded": True,
        "filters": {"testName": None, "patientId": "PID-001"},
        "limit": 50,
        "offset": 50,
    }
    store.update_browser_state(session["sessionId"], updated_browser_state)
    store.close()

    restored_store = WorkbenchStore(path, now=clock)
    restored = restored_store.get_session(session["sessionId"])
    assert restored is not None
    assert restored["currentVersion"] == version
    assert restored["latestValidation"] == validation
    assert restored["executions"] == [execution]
    assert restored["browserState"] == updated_browser_state
    assert restored["versions"] == [version]

    events = restored_store.list_events(session["sessionId"])
    assert [event["sequence"] for event in events] == [1, 2, 3, 4, 5]
    assert [event["type"] for event in events] == [
        "session.created",
        "query_version.created",
        "validation.completed",
        "execution.completed",
        "browser_state.updated",
    ]
    assert events[1]["payload"]["version"]["sql"] == version["sql"]
    assert events[2]["payload"]["validation"]["findings"] == findings
    assert events[3]["payload"]["execution"]["databaseDiagnostic"]["sqlstate"] == (
        "42703"
    )
    restored_store.close()


def test_versions_are_immutable_and_stale_parent_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(path)
    session = _session(store)
    first = _initial_version(store, session["sessionId"])
    second = store.append_version(
        session["sessionId"],
        sql=first["sql"] + " LIMIT 25",
        parameters=first["parameters"],
        expected_columns=first["expectedColumns"],
        author_type="human",
        parent_version_id=first["versionId"],
        parent_query_digest=first["queryDigest"],
    )

    with pytest.raises(StaleWorkbenchVersionError):
        store.append_version(
            session["sessionId"],
            sql="SELECT 1",
            parameters=[],
            author_type="human",
            parent_version_id=first["versionId"],
            parent_query_digest=first["queryDigest"],
        )

    restored = store.get_session(session["sessionId"])
    assert restored is not None
    assert [version["versionId"] for version in restored["versions"]] == [
        first["versionId"],
        second["versionId"],
    ]
    assert restored["currentVersionId"] == second["versionId"]
    assert [event["type"] for event in store.list_events(session["sessionId"])] == [
        "session.created",
        "query_version.created",
        "query_version.created",
    ]
    store.close()

    connection = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute(
            "UPDATE catalyst_workbench_query_versions SET sql = 'SELECT 2' "
            "WHERE version_id = ?",
            (first["versionId"],),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "DELETE FROM catalyst_workbench_events WHERE session_id = ?",
            (session["sessionId"],),
        )
    connection.close()


def test_opening_existing_preview_database_adds_workbench_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "existing.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE catalyst_previews (preview_id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO catalyst_previews VALUES ('preview-1')")
    connection.commit()
    connection.close()

    store = WorkbenchStore(path)
    created = _session(store)
    assert store.get_session(created["sessionId"]) is not None
    store.close()

    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT preview_id FROM catalyst_previews"
    ).fetchall() == [("preview-1",)]
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "catalyst_workbench_sessions",
        "catalyst_workbench_query_versions",
        "catalyst_workbench_validations",
        "catalyst_workbench_findings",
        "catalyst_workbench_executions",
        "catalyst_workbench_events",
    }.issubset(tables)
    connection.close()
