from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .digest import canonical_sha256, query_digest, utf8_sha256
from .workbench import workbench_query_digest


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ExecutionDecision:
    action: str
    status_code: int
    body: dict[str, Any] | None = None
    preview: dict[str, Any] | None = None
    query: dict[str, Any] | None = None
    accepted_at: str | None = None
    catalyst_trace_id: str | None = None


@dataclass(frozen=True)
class WorkbenchExecutionDecision:
    """Atomic disposition for a manual workbench execution request."""

    action: str
    execution: dict[str, Any] | None = None
    claimed_version_id: str | None = None


class WorkbenchStorageError(RuntimeError):
    """Base class for persistent workbench state errors."""


class WorkbenchNotFoundError(WorkbenchStorageError):
    """The requested workbench session or query version does not exist."""


class StaleWorkbenchVersionError(WorkbenchStorageError):
    def __init__(
        self,
        *,
        current_version_id: str | None,
        current_query_digest: str | None,
    ) -> None:
        self.current_version_id = current_version_id
        self.current_query_digest = current_query_digest
        super().__init__("The current query version changed before this operation.")


class ActiveTurnGenerationError(WorkbenchStorageError):
    """A session already has one requested generation owned by this boot."""


class EditorSnapshotDigestError(WorkbenchStorageError):
    """The supplied editor digest does not identify the supplied content."""


_PARAMETER_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LEGACY_TURN_NAMESPACE = "https://openelis-global.org/catalyst/workbench/sessions"


class PreviewStore:
    def __init__(
        self,
        path: str | Path,
        *,
        now: Callable[[], datetime] | None = None,
        execution_lease_seconds: int = 60,
    ) -> None:
        self.path = str(path)
        self._now = now or _utc_now
        self.execution_lease_seconds = execution_lease_seconds
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=5,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS catalyst_previews (
                    preview_id TEXT PRIMARY KEY,
                    query_digest TEXT NOT NULL,
                    preview_json TEXT NOT NULL,
                    query_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    accepted_at TEXT,
                    execution_started_at TEXT,
                    idempotency_key TEXT,
                    outcome_json TEXT,
                    outcome_status INTEGER,
                    catalyst_trace_id TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(catalyst_previews)"
                ).fetchall()
            }
            if "execution_started_at" not in columns:
                self._connection.execute(
                    "ALTER TABLE catalyst_previews "
                    "ADD COLUMN execution_started_at TEXT"
                )
            if "expires_at" in columns:
                self._remove_legacy_expiration_column()

    def _remove_legacy_expiration_column(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                "DROP TABLE IF EXISTS catalyst_previews_without_expiration"
            )
            self._connection.execute(
                """
                CREATE TABLE catalyst_previews_without_expiration (
                    preview_id TEXT PRIMARY KEY,
                    query_digest TEXT NOT NULL,
                    preview_json TEXT NOT NULL,
                    query_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    accepted_at TEXT,
                    execution_started_at TEXT,
                    idempotency_key TEXT,
                    outcome_json TEXT,
                    outcome_status INTEGER,
                    catalyst_trace_id TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                INSERT INTO catalyst_previews_without_expiration (
                    preview_id, query_digest, preview_json, query_json, state,
                    accepted_at, execution_started_at, idempotency_key,
                    outcome_json, outcome_status, catalyst_trace_id
                )
                SELECT
                    preview_id, query_digest, preview_json, query_json,
                    CASE WHEN state = 'expired' THEN 'awaiting_acceptance' ELSE state END,
                    accepted_at, execution_started_at, idempotency_key,
                    outcome_json, outcome_status, catalyst_trace_id
                FROM catalyst_previews
                """
            )
            self._connection.execute("DROP TABLE catalyst_previews")
            self._connection.execute(
                "ALTER TABLE catalyst_previews_without_expiration "
                "RENAME TO catalyst_previews"
            )
            for row in self._connection.execute(
                "SELECT preview_id, preview_json FROM catalyst_previews"
            ).fetchall():
                preview = json.loads(row["preview_json"])
                if preview.pop("expiresAt", None) is not None:
                    self._connection.execute(
                        "UPDATE catalyst_previews SET preview_json = ? "
                        "WHERE preview_id = ?",
                        (
                            json.dumps(preview, separators=(",", ":")),
                            row["preview_id"],
                        ),
                    )
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise

    def create_preview(
        self,
        query: dict[str, Any],
        *,
        catalyst_trace_id: str | None = None,
        profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = self._now()
        preview_id = str(uuid.uuid4())
        digest = query_digest(query)
        trace_id = catalyst_trace_id or str(uuid.uuid4())
        preview = {
            "contractVersion": "catalyst.preview.v1",
            "deploymentMode": "demo",
            "previewId": preview_id,
            "queryDigest": digest,
            "question": query["question"],
            "target": query["target"],
            "sql": query["sql"],
            "parameters": query["parameters"],
            "expectedColumns": query["expectedColumns"],
            "reasoningTrace": {
                "traceId": query["provenance"]["traceId"],
                "profileId": query["provenance"]["profileId"],
                "status": query["validation"]["status"],
                "stages": list((profile or {}).get("stages", [])),
                "roleModels": dict((profile or {}).get("role_models", {})),
                "checks": query["validation"]["checks"],
            },
            "createdAt": _timestamp(now),
            "state": "awaiting_acceptance",
        }
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO catalyst_previews (
                    preview_id, query_digest, preview_json, query_json, state,
                    catalyst_trace_id
                ) VALUES (?, ?, ?, ?, 'awaiting_acceptance', ?)
                """,
                (
                    preview_id,
                    digest,
                    json.dumps(preview, separators=(",", ":")),
                    json.dumps(query, separators=(",", ":")),
                    trace_id,
                ),
            )
        return preview

    def begin_execution(
        self,
        preview_id: str,
        digest: str,
        idempotency_key: str,
    ) -> ExecutionDecision:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM catalyst_previews WHERE preview_id = ?",
                (preview_id,),
            ).fetchone()
            if row is None:
                return self._outcome_decision(
                    404,
                    preview_id,
                    idempotency_key,
                    status="not_found",
                    error_code="execution_not_found",
                    message="Preview was not found.",
                )

            if digest != row["query_digest"]:
                return self._outcome_decision(
                    409,
                    preview_id,
                    idempotency_key,
                    status="conflict",
                    error_code="preview_consumed",
                    message="Query digest does not match the stored preview.",
                )

            state = row["state"]
            stored_key = row["idempotency_key"]
            if state in {"awaiting_acceptance", "expired"}:
                accepted_at = _timestamp(self._now())
                connection.execute(
                    """
                    UPDATE catalyst_previews
                    SET state = 'consuming', idempotency_key = ?,
                        accepted_at = ?, execution_started_at = ?
                    WHERE preview_id = ? AND state IN ('awaiting_acceptance', 'expired')
                    """,
                    (idempotency_key, accepted_at, accepted_at, preview_id),
                )
                return ExecutionDecision(
                    action="execute",
                    status_code=0,
                    preview=json.loads(row["preview_json"]),
                    query=json.loads(row["query_json"]),
                    accepted_at=accepted_at,
                    catalyst_trace_id=row["catalyst_trace_id"],
                )

            if stored_key != idempotency_key:
                return self._outcome_decision(
                    409,
                    preview_id,
                    idempotency_key,
                    status="conflict",
                    error_code="idempotency_conflict",
                    message="A different idempotency key consumed this preview.",
                )

            if state == "consuming":
                stale = self._stale_execution_decision(
                    connection,
                    row,
                    idempotency_key,
                )
                if stale is not None:
                    return stale
                return self._outcome_decision(
                    202,
                    preview_id,
                    idempotency_key,
                    status="in_progress",
                    error_code="execution_in_progress",
                    message="Execution is in progress.",
                    replayed=True,
                    retryable=True,
                )
            return self._stored_decision(row, replayed=True)

    def finish_success(
        self,
        preview_id: str,
        idempotency_key: str,
        table: dict[str, Any],
    ) -> None:
        self._finish(
            preview_id,
            idempotency_key,
            state="succeeded",
            body=table,
            status_code=200,
        )

    def finish_failure(
        self,
        preview_id: str,
        idempotency_key: str,
        message: str,
    ) -> dict[str, Any]:
        body = self._outcome(
            preview_id,
            idempotency_key,
            status="failed",
            error_code="execution_failed",
            message=message,
        )
        self._finish(
            preview_id,
            idempotency_key,
            state="failed",
            body=body,
            status_code=502,
        )
        return body

    def poll(self, preview_id: str, idempotency_key: str) -> ExecutionDecision:
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM catalyst_previews
                WHERE preview_id = ? AND idempotency_key = ?
                """,
                (preview_id, idempotency_key),
            ).fetchone()
            if row is None or row["state"] in {"awaiting_acceptance", "expired"}:
                return self._outcome_decision(
                    404,
                    preview_id,
                    idempotency_key,
                    status="not_found",
                    error_code="execution_not_found",
                    message="Execution was not found.",
                )
            if row["state"] == "consuming":
                stale = self._stale_execution_decision(
                    connection,
                    row,
                    idempotency_key,
                )
                if stale is not None:
                    return stale
                return self._outcome_decision(
                    202,
                    preview_id,
                    idempotency_key,
                    status="in_progress",
                    error_code="execution_in_progress",
                    message="Execution is in progress.",
                    replayed=True,
                    retryable=True,
                )
            return self._stored_decision(row, replayed=True)

    def readiness(self) -> dict[str, bool]:
        try:
            with self._lock:
                self._connection.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            return {"ready": False}
        return {"ready": True}

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _finish(
        self,
        preview_id: str,
        idempotency_key: str,
        *,
        state: str,
        body: dict[str, Any],
        status_code: int,
    ) -> None:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE catalyst_previews
                SET state = ?, outcome_json = ?, outcome_status = ?
                WHERE preview_id = ? AND state = 'consuming'
                    AND idempotency_key = ?
                """,
                (
                    state,
                    json.dumps(body, separators=(",", ":")),
                    status_code,
                    preview_id,
                    idempotency_key,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Execution claim is no longer active.")

    def _stored_decision(
        self,
        row: sqlite3.Row,
        *,
        replayed: bool,
    ) -> ExecutionDecision:
        if row["outcome_json"] is None or row["outcome_status"] is None:
            return self._outcome_decision(
                409,
                row["preview_id"],
                row["idempotency_key"],
                status="conflict",
                error_code="preview_consumed",
                message="Preview was consumed without a stored outcome.",
            )
        body = json.loads(row["outcome_json"])
        if body.get("contractVersion") == "catalyst.execution.outcome.v1":
            body["replayed"] = replayed
        return ExecutionDecision(
            action="return",
            status_code=row["outcome_status"],
            body=body,
        )

    def _stale_execution_decision(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        idempotency_key: str,
    ) -> ExecutionDecision | None:
        started_at_value = row["execution_started_at"] or row["accepted_at"]
        if started_at_value is None:
            lease_expired = True
        else:
            started_at = datetime.fromisoformat(started_at_value.replace("Z", "+00:00"))
            lease_expired = (
                self._now() - started_at
            ).total_seconds() >= self.execution_lease_seconds
        if not lease_expired:
            return None

        body = self._outcome(
            row["preview_id"],
            idempotency_key,
            status="failed",
            error_code="execution_failed",
            message="Execution lease expired before an outcome was stored.",
            replayed=True,
        )
        connection.execute(
            """
            UPDATE catalyst_previews
            SET state = 'failed', outcome_json = ?, outcome_status = 502
            WHERE preview_id = ? AND state = 'consuming'
                AND idempotency_key = ?
            """,
            (
                json.dumps(body, separators=(",", ":")),
                row["preview_id"],
                idempotency_key,
            ),
        )
        return ExecutionDecision(action="return", status_code=502, body=body)

    @staticmethod
    def _outcome(
        preview_id: str,
        idempotency_key: str,
        *,
        status: str,
        error_code: str,
        message: str,
        replayed: bool = False,
        retryable: bool = False,
    ) -> dict[str, Any]:
        return {
            "contractVersion": "catalyst.execution.outcome.v1",
            "deploymentMode": "demo",
            "previewId": preview_id,
            "idempotencyKey": idempotency_key,
            "status": status,
            "errorCode": error_code,
            "message": message,
            "replayed": replayed,
            "retryable": retryable,
        }

    @classmethod
    def _outcome_decision(
        cls,
        status_code: int,
        preview_id: str,
        idempotency_key: str,
        **kwargs: Any,
    ) -> ExecutionDecision:
        return ExecutionDecision(
            action="return",
            status_code=status_code,
            body=cls._outcome(preview_id, idempotency_key, **kwargs),
        )

    class _Transaction:
        def __init__(self, store: PreviewStore) -> None:
            self.store = store

        def __enter__(self) -> sqlite3.Connection:
            self.store._lock.acquire()
            self.store._connection.execute("BEGIN IMMEDIATE")
            return self.store._connection

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            try:
                if exc_type is None:
                    self.store._connection.execute("COMMIT")
                else:
                    self.store._connection.execute("ROLLBACK")
            finally:
                self.store._lock.release()

    def _transaction(self) -> _Transaction:
        return self._Transaction(self)


class WorkbenchStore:
    """Append-only workbench lineage stored alongside, but separate from, previews."""

    def __init__(
        self,
        path: str | Path,
        *,
        now: Callable[[], datetime] | None = None,
        owner_instance_id: str | None = None,
        execution_lease_seconds: int = 60,
    ) -> None:
        self.path = str(path)
        self._now = now or _utc_now
        self.owner_instance_id = owner_instance_id or str(uuid.uuid4())
        self.execution_lease_seconds = execution_lease_seconds
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=5,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalyst_workbench_schema_migrations (
                    migration_version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS catalyst_workbench_sessions (
                    session_id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    dataset_version TEXT NOT NULL,
                    catalog_version TEXT NOT NULL,
                    current_version_id TEXT,
                    browser_state_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS catalyst_workbench_query_versions (
                    version_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    parent_version_id TEXT,
                    ordinal INTEGER NOT NULL,
                    author_type TEXT NOT NULL,
                    sql TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    expected_columns_json TEXT NOT NULL,
                    query_digest TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    source_finding_ids_json TEXT NOT NULL,
                    repair_proposal_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (session_id, ordinal),
                    FOREIGN KEY (session_id)
                        REFERENCES catalyst_workbench_sessions(session_id),
                    FOREIGN KEY (parent_version_id)
                        REFERENCES catalyst_workbench_query_versions(version_id)
                );

                CREATE TABLE IF NOT EXISTS catalyst_workbench_validations (
                    validation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    validation_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (version_id, ordinal),
                    FOREIGN KEY (session_id)
                        REFERENCES catalyst_workbench_sessions(session_id),
                    FOREIGN KEY (version_id)
                        REFERENCES catalyst_workbench_query_versions(version_id)
                );

                CREATE TABLE IF NOT EXISTS catalyst_workbench_findings (
                    validation_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    rule_code TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    finding_json TEXT NOT NULL,
                    PRIMARY KEY (validation_id, finding_id),
                    UNIQUE (validation_id, ordinal),
                    FOREIGN KEY (validation_id)
                        REFERENCES catalyst_workbench_validations(validation_id)
                );

                CREATE TABLE IF NOT EXISTS catalyst_workbench_executions (
                    execution_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    execution_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (session_id, ordinal),
                    FOREIGN KEY (session_id)
                        REFERENCES catalyst_workbench_sessions(session_id),
                    FOREIGN KEY (version_id)
                        REFERENCES catalyst_workbench_query_versions(version_id)
                );

                CREATE TABLE IF NOT EXISTS catalyst_workbench_execution_claims (
                    session_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, idempotency_key),
                    FOREIGN KEY (session_id)
                        REFERENCES catalyst_workbench_sessions(session_id),
                    FOREIGN KEY (version_id)
                        REFERENCES catalyst_workbench_query_versions(version_id)
                );

                CREATE TABLE IF NOT EXISTS catalyst_workbench_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    contract_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    entity_refs_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE (session_id, sequence),
                    FOREIGN KEY (session_id)
                        REFERENCES catalyst_workbench_sessions(session_id)
                );

                CREATE TABLE IF NOT EXISTS catalyst_workbench_editor_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL UNIQUE,
                    editor_digest TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id)
                        REFERENCES catalyst_workbench_sessions(session_id)
                );

                CREATE TABLE IF NOT EXISTS catalyst_workbench_turns (
                    turn_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    generation_run_id TEXT NOT NULL,
                    owner_instance_id TEXT NOT NULL,
                    turn_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (session_id, ordinal),
                    FOREIGN KEY (session_id)
                        REFERENCES catalyst_workbench_sessions(session_id)
                );

                CREATE TABLE IF NOT EXISTS catalyst_workbench_generation_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL UNIQUE,
                    evidence_digest TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (session_id)
                        REFERENCES catalyst_workbench_sessions(session_id),
                    FOREIGN KEY (turn_id)
                        REFERENCES catalyst_workbench_turns(turn_id)
                );

                CREATE INDEX IF NOT EXISTS catalyst_workbench_versions_session_idx
                    ON catalyst_workbench_query_versions(session_id, ordinal);
                CREATE INDEX IF NOT EXISTS catalyst_workbench_validations_version_idx
                    ON catalyst_workbench_validations(version_id, ordinal);
                CREATE INDEX IF NOT EXISTS catalyst_workbench_executions_version_idx
                    ON catalyst_workbench_executions(version_id, ordinal);
                CREATE INDEX IF NOT EXISTS catalyst_workbench_events_session_idx
                    ON catalyst_workbench_events(session_id, sequence);
                CREATE INDEX IF NOT EXISTS catalyst_workbench_turns_session_idx
                    ON catalyst_workbench_turns(session_id, ordinal);
                CREATE UNIQUE INDEX IF NOT EXISTS catalyst_workbench_one_active_turn_idx
                    ON catalyst_workbench_turns(session_id)
                    WHERE status = 'requested';

                CREATE TRIGGER IF NOT EXISTS catalyst_workbench_versions_no_update
                BEFORE UPDATE ON catalyst_workbench_query_versions
                BEGIN
                    SELECT RAISE(ABORT, 'workbench query versions are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS catalyst_workbench_versions_no_delete
                BEFORE DELETE ON catalyst_workbench_query_versions
                BEGIN
                    SELECT RAISE(ABORT, 'workbench query versions are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS catalyst_workbench_validations_no_update
                BEFORE UPDATE ON catalyst_workbench_validations
                BEGIN
                    SELECT RAISE(ABORT, 'workbench validations are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS catalyst_workbench_validations_no_delete
                BEFORE DELETE ON catalyst_workbench_validations
                BEGIN
                    SELECT RAISE(ABORT, 'workbench validations are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS catalyst_workbench_findings_no_update
                BEFORE UPDATE ON catalyst_workbench_findings
                BEGIN
                    SELECT RAISE(ABORT, 'workbench findings are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS catalyst_workbench_findings_no_delete
                BEFORE DELETE ON catalyst_workbench_findings
                BEGIN
                    SELECT RAISE(ABORT, 'workbench findings are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS catalyst_workbench_executions_no_update
                BEFORE UPDATE ON catalyst_workbench_executions
                BEGIN
                    SELECT RAISE(ABORT, 'workbench executions are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS catalyst_workbench_executions_no_delete
                BEFORE DELETE ON catalyst_workbench_executions
                BEGIN
                    SELECT RAISE(ABORT, 'workbench executions are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS catalyst_workbench_events_no_update
                BEFORE UPDATE ON catalyst_workbench_events
                BEGIN
                    SELECT RAISE(ABORT, 'workbench events are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS catalyst_workbench_events_no_delete
                BEFORE DELETE ON catalyst_workbench_events
                BEGIN
                    SELECT RAISE(ABORT, 'workbench events are append-only');
                END;
                """
            )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO catalyst_workbench_schema_migrations (
                    migration_version, applied_at
                ) VALUES (1, ?)
                """,
                (_timestamp(self._now()),),
            )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO catalyst_workbench_schema_migrations (
                    migration_version, applied_at
                ) VALUES (2, ?)
                """,
                (_timestamp(self._now()),),
            )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO catalyst_workbench_schema_migrations (
                    migration_version, applied_at
                ) VALUES (3, ?)
                """,
                (_timestamp(self._now()),),
            )
            self._recover_orphaned_turns()

    def create_session(
        self,
        *,
        question: str,
        profile_id: str,
        dataset_id: str,
        dataset_version: str,
        catalog_version: str,
        browser_state: dict[str, Any] | None = None,
        status: str = "active",
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        timestamp = _timestamp(self._now())
        state = browser_state or {}
        lineage = provenance or {}
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO catalyst_workbench_sessions (
                    session_id, question, profile_id, dataset_id,
                    dataset_version, catalog_version, current_version_id,
                    browser_state_json, provenance_json, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    question,
                    profile_id,
                    dataset_id,
                    dataset_version,
                    catalog_version,
                    _json(state),
                    _json(lineage),
                    status,
                    timestamp,
                    timestamp,
                ),
            )
            session = {
                "contractVersion": "catalyst.workbench.session.v1",
                "sessionId": session_id,
                "question": question,
                "profileId": profile_id,
                "datasetId": dataset_id,
                "datasetVersion": dataset_version,
                "catalogVersion": catalog_version,
                "currentVersionId": None,
                "browserState": state,
                "provenance": lineage,
                "status": status,
                "createdAt": timestamp,
                "updatedAt": timestamp,
            }
            self._append_event(
                connection,
                session_id=session_id,
                event_type="session.created",
                actor="system",
                entity_refs={"sessionId": session_id},
                payload={"session": session},
                timestamp=timestamp,
            )
        return session

    def claim_initial_turn(
        self,
        session_id: str,
        *,
        instruction: str,
        instruction_digest: str,
        profile_snapshot: dict[str, Any],
        catalyst_trace_id: str,
        hub_request: dict[str, Any] | None = None,
        profile_evidence: dict[str, Any] | None = None,
        data_source_id: str | None = None,
        catalog_version: str | None = None,
    ) -> dict[str, Any]:
        """Record the initial request before the first model call."""

        return self._claim_turn(
            session_id,
            kind="initial",
            instruction=instruction,
            instruction_digest=instruction_digest,
            profile_snapshot=profile_snapshot,
            observed_base=None,
            editor_snapshot=None,
            revision_context=None,
            hub_request_digest=(
                canonical_sha256(hub_request or {}) if hub_request is not None else None
            ),
            catalyst_trace_id=catalyst_trace_id,
            hub_request=hub_request,
            profile_evidence=profile_evidence,
            data_source_id=data_source_id,
            catalog_version=catalog_version,
        )

    def claim_turn(
        self,
        session_id: str,
        *,
        instruction: str,
        instruction_digest: str,
        profile_snapshot: dict[str, Any],
        observed_base: dict[str, str] | None,
        editor_snapshot: dict[str, Any],
        revision_context: dict[str, Any],
        hub_request_digest: str,
        catalyst_trace_id: str,
        hub_request: dict[str, Any] | None = None,
        request_factory: Callable[
            [dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]
        ]
        | None = None,
        profile_evidence: dict[str, Any] | None = None,
        data_source_id: str | None = None,
        catalog_version: str | None = None,
    ) -> dict[str, Any]:
        return self._claim_turn(
            session_id,
            kind="followup",
            instruction=instruction,
            instruction_digest=instruction_digest,
            profile_snapshot=profile_snapshot,
            observed_base=observed_base,
            editor_snapshot=editor_snapshot,
            revision_context=revision_context,
            hub_request_digest=hub_request_digest,
            catalyst_trace_id=catalyst_trace_id,
            hub_request=hub_request,
            request_factory=request_factory,
            profile_evidence=profile_evidence,
            data_source_id=data_source_id,
            catalog_version=catalog_version,
        )

    def _claim_turn(
        self,
        session_id: str,
        *,
        kind: str,
        instruction: str,
        instruction_digest: str,
        profile_snapshot: dict[str, Any],
        observed_base: dict[str, str] | None,
        editor_snapshot: dict[str, Any] | None,
        revision_context: dict[str, Any] | None,
        hub_request_digest: str | None,
        catalyst_trace_id: str,
        hub_request: dict[str, Any] | None,
        request_factory: Callable[
            [dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]
        ]
        | None = None,
        profile_evidence: dict[str, Any] | None = None,
        data_source_id: str | None = None,
        catalog_version: str | None = None,
    ) -> dict[str, Any]:
        turn_id = str(uuid.uuid4())
        generation_run_id = str(uuid.uuid4())
        evidence_id = str(uuid.uuid4())
        snapshot_id = str(uuid.uuid4()) if editor_snapshot is not None else None
        timestamp = _timestamp(self._now())
        with self._transaction() as connection:
            session = self._require_session(connection, session_id)
            active = connection.execute(
                """
                SELECT turn_id FROM catalyst_workbench_turns
                WHERE session_id = ? AND status = 'requested'
                """,
                (session_id,),
            ).fetchone()
            if active is not None:
                raise ActiveTurnGenerationError(
                    "A query generation is already in progress for this session."
                )

            current = self._current_version(connection, session)
            current_ref = self._version_ref_row(current)
            if kind == "followup" and current_ref != observed_base:
                raise StaleWorkbenchVersionError(
                    current_version_id=(
                        current_ref["versionId"] if current_ref is not None else None
                    ),
                    current_query_digest=(
                        current_ref["queryDigest"] if current_ref is not None else None
                    ),
                )

            ordinal = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal
                    FROM catalyst_workbench_turns WHERE session_id = ?
                    """,
                    (session_id,),
                ).fetchone()["next_ordinal"]
            )
            classification = "not_applicable"
            unresolved_paths: list[str] = []
            effective_base = None
            manual_version = None
            snapshot_record = None
            if editor_snapshot is not None:
                computed_digest = workbench_query_digest(
                    str(editor_snapshot.get("sql") or ""),
                    list(editor_snapshot.get("parameters") or []),
                    list(editor_snapshot.get("expectedColumns") or []),
                )
                if editor_snapshot.get("editorDigest") != computed_digest:
                    raise EditorSnapshotDigestError(
                        "editorDigest does not match the exact editor snapshot."
                    )
                unresolved_paths = self._snapshot_unresolved_paths(editor_snapshot)
                if (
                    current_ref is not None
                    and computed_digest == current_ref["queryDigest"]
                ):
                    classification = "reused"
                    effective_base = current_ref
                elif unresolved_paths:
                    classification = "unresolved"
                else:
                    classification = "promoted_human"
                    manual = self._insert_version(
                        connection,
                        session_id=session_id,
                        sql=editor_snapshot["sql"],
                        parameters=list(editor_snapshot["parameters"]),
                        expected_columns=list(editor_snapshot["expectedColumns"]),
                        author_type="human",
                        parent=current,
                        provenance={
                            "turnId": turn_id,
                            "editorSnapshotDigest": computed_digest,
                            "profileId": profile_snapshot.get("profileId"),
                            "dataSourceId": data_source_id,
                        },
                        timestamp=timestamp,
                    )
                    manual_version = self._version_ref(manual)
                    effective_base = manual_version
                    current = self._version_row(connection, manual["versionId"])
                findings = [
                    {
                        "findingId": f"snapshot-{index + 1}",
                        "code": "immutable_version_contract",
                        "severity": "error",
                        "path": path,
                        "message": "Editor content cannot yet become an immutable query version.",
                    }
                    for index, path in enumerate(unresolved_paths)
                ]
                snapshot_record = {
                    "contractVersion": "catalyst.workbench.editor-snapshot-record.v1",
                    "snapshotId": snapshot_id,
                    "sessionId": session_id,
                    "turnId": turn_id,
                    "capturedAt": timestamp,
                    "actor": {"type": "human", "actorId": None},
                    "sourceObservedBase": observed_base,
                    "classification": classification,
                    "effectiveBaseVersion": effective_base,
                    "content": editor_snapshot,
                    "unresolvedPaths": unresolved_paths,
                    "findings": findings,
                    "sourceEvidenceRef": None,
                }
                connection.execute(
                    """
                    INSERT INTO catalyst_workbench_editor_snapshots (
                        snapshot_id, session_id, turn_id, editor_digest,
                        snapshot_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        session_id,
                        turn_id,
                        computed_digest,
                        _json(snapshot_record),
                        timestamp,
                    ),
                )

            if request_factory is not None:
                revision_context, hub_request = request_factory(
                    {
                        "turnId": turn_id,
                        "snapshotClassification": classification,
                        "observedBase": observed_base,
                        "effectiveBaseVersion": effective_base,
                        "manualVersion": manual_version,
                        "editorSnapshot": editor_snapshot,
                    }
                )
                hub_request_digest = canonical_sha256(hub_request)

            evidence_ref = {
                "contractVersion": "catalyst.workbench.generation-evidence-ref.v1",
                "evidenceId": evidence_id,
                "evidenceDigest": "0" * 64,
                "detailPath": (
                    f"/v1/catalyst/workbench/sessions/{session_id}/turns/"
                    f"{turn_id}/generation-evidence"
                ),
            }
            requested_event = {
                "eventId": str(uuid.uuid4()),
                "status": "requested",
                "generationRunId": generation_run_id,
                "ownerInstanceId": self.owner_instance_id,
                "occurredAt": timestamp,
            }
            turn = {
                "contractVersion": "catalyst.workbench.turn.v1",
                "sessionId": session_id,
                "turnId": turn_id,
                "ordinal": ordinal,
                "kind": kind,
                "origin": "recorded",
                "dataSourceId": data_source_id,
                "catalogVersion": catalog_version,
                "instruction": instruction,
                "instructionDigest": instruction_digest,
                "profileSnapshot": profile_snapshot,
                "observedBase": observed_base,
                "editorSnapshot": snapshot_record,
                "snapshotClassification": classification,
                "unresolvedPaths": unresolved_paths,
                "effectiveBaseVersion": effective_base,
                "manualVersion": manual_version,
                "revisionContext": revision_context,
                "hubRequestDigest": hub_request_digest,
                "catalystTraceId": catalyst_trace_id,
                "hubTraceId": None,
                "generationEvidenceRef": evidence_ref,
                "recoveryReferences": None,
                "status": "requested",
                "outputVersions": [],
                "selectedVersionId": None,
                "resultingCurrentVersion": effective_base or current_ref,
                "events": [requested_event],
                "failure": None,
                "createdAt": timestamp,
                "updatedAt": timestamp,
            }
            evidence = self._requested_evidence(
                evidence_id=evidence_id,
                turn=turn,
                session=session,
                profile_evidence=profile_evidence,
                hub_request=hub_request,
                timestamp=timestamp,
            )
            evidence_ref["evidenceDigest"] = evidence["evidenceDigest"]
            connection.execute(
                """
                INSERT INTO catalyst_workbench_turns (
                    turn_id, session_id, ordinal, kind, status,
                    generation_run_id, owner_instance_id, turn_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'requested', ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    session_id,
                    ordinal,
                    kind,
                    generation_run_id,
                    self.owner_instance_id,
                    _json(turn),
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO catalyst_workbench_generation_evidence (
                    evidence_id, session_id, turn_id, evidence_digest,
                    evidence_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    session_id,
                    turn_id,
                    evidence["evidenceDigest"],
                    _json(evidence),
                    timestamp,
                    timestamp,
                ),
            )
            self._append_event(
                connection,
                session_id=session_id,
                event_type="query_turn.requested",
                actor="human" if kind == "followup" else "system",
                entity_refs={"sessionId": session_id, "turnId": turn_id},
                payload={"turn": turn},
                timestamp=timestamp,
            )
        return turn

    def append_version(
        self,
        session_id: str,
        *,
        sql: str,
        parameters: list[dict[str, Any]],
        author_type: str,
        expected_columns: list[dict[str, Any]] | None = None,
        parent_version_id: str | None = None,
        parent_query_digest: str | None = None,
        provenance: dict[str, Any] | None = None,
        source_finding_ids: list[str] | None = None,
        repair_proposal_id: str | None = None,
    ) -> dict[str, Any]:
        if author_type not in {
            "model",
            "human",
            "deterministic_repair",
            "model_repair",
        }:
            raise ValueError(f"Unsupported workbench author type: {author_type}")
        columns = expected_columns or []
        lineage = provenance or {}
        finding_ids = source_finding_ids or []
        digest = workbench_query_digest(sql, parameters, columns)
        version_id = str(uuid.uuid4())
        timestamp = _timestamp(self._now())

        with self._transaction() as connection:
            session = self._require_session(connection, session_id)
            active = connection.execute(
                """
                SELECT turn_id FROM catalyst_workbench_turns
                WHERE session_id = ? AND status = 'requested'
                """,
                (session_id,),
            ).fetchone()
            if active is not None:
                raise ActiveTurnGenerationError(
                    "A query generation is already in progress for this session."
                )
            current = self._current_version(connection, session)
            current_id = current["version_id"] if current is not None else None
            current_digest = current["query_digest"] if current is not None else None
            if (current is None and (parent_version_id or parent_query_digest)) or (
                current is not None
                and (
                    parent_version_id != current_id
                    or parent_query_digest != current_digest
                )
            ):
                raise StaleWorkbenchVersionError(
                    current_version_id=current_id,
                    current_query_digest=current_digest,
                )

            if (
                current is not None
                and digest == current_digest
                and sql == current["sql"]
                and parameters == json.loads(current["parameters_json"])
                and columns == json.loads(current["expected_columns_json"])
            ):
                # Validate, Run, and follow-up all resolve the exact visible
                # editor buffer against the same immutable content identity.
                # Saving an unchanged buffer is therefore a read-only reuse,
                # not a duplicate human version or event.
                return self._version_from_row(current)

            ordinal = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal
                    FROM catalyst_workbench_query_versions
                    WHERE session_id = ?
                    """,
                    (session_id,),
                ).fetchone()["next_ordinal"]
            )
            connection.execute(
                """
                INSERT INTO catalyst_workbench_query_versions (
                    version_id, session_id, parent_version_id, ordinal,
                    author_type, sql, parameters_json, expected_columns_json,
                    query_digest, provenance_json, source_finding_ids_json,
                    repair_proposal_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    session_id,
                    parent_version_id,
                    ordinal,
                    author_type,
                    sql,
                    _json(parameters),
                    _json(columns),
                    digest,
                    _json(lineage),
                    _json(finding_ids),
                    repair_proposal_id,
                    timestamp,
                ),
            )
            connection.execute(
                """
                UPDATE catalyst_workbench_sessions
                SET current_version_id = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (version_id, timestamp, session_id),
            )
            version = {
                "contractVersion": "catalyst.workbench.query-version.v1",
                "versionId": version_id,
                "sessionId": session_id,
                "parentVersionId": parent_version_id,
                "ordinal": ordinal,
                "authorType": author_type,
                "sql": sql,
                "parameters": parameters,
                "expectedColumns": columns,
                "queryDigest": digest,
                "provenance": lineage,
                "sourceFindingIds": finding_ids,
                "repairProposalId": repair_proposal_id,
                "createdAt": timestamp,
            }
            self._append_event(
                connection,
                session_id=session_id,
                event_type="query_version.created",
                actor=author_type,
                entity_refs={"sessionId": session_id, "versionId": version_id},
                payload={"version": version},
                timestamp=timestamp,
            )
        return version

    def complete_turn(
        self,
        turn_id: str,
        *,
        outputs: list[dict[str, Any]],
        selected_index: int,
        hub_trace_id: str | None,
        hub_response: dict[str, Any] | None,
        invocations: list[dict[str, Any]] | None = None,
        raw_evidence: str | None = None,
        validations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not outputs or selected_index < 0 or selected_index >= len(outputs):
            raise ValueError("A completed turn must select one complete output.")
        timestamp = _timestamp(self._now())
        with self._transaction() as connection:
            row = self._require_requested_turn(connection, turn_id)
            turn = json.loads(row["turn_json"])
            self._require_session(connection, row["session_id"])
            anchor_ref = turn.get("effectiveBaseVersion") or turn.get("observedBase")
            parent = (
                self._version_row(connection, anchor_ref["versionId"])
                if anchor_ref is not None
                else None
            )
            supplied_validations = validations or []
            if supplied_validations and len(supplied_validations) != len(outputs):
                raise ValueError(
                    "Each completed output must have one validation payload."
                )
            planned: list[dict[str, Any]] = []
            stored_outputs: list[dict[str, Any]] = []
            parent_version_id = parent["version_id"] if parent is not None else None
            for index, output in enumerate(outputs):
                sql = str(output["sql"])
                parameters = list(output.get("parameters") or [])
                expected_columns = list(output.get("expectedColumns") or [])
                version_id = str(uuid.uuid4())
                validation_id = str(uuid.uuid4()) if supplied_validations else None
                query_digest = workbench_query_digest(sql, parameters, expected_columns)
                planned.append(
                    {
                        "versionId": version_id,
                        "validationId": validation_id,
                        "output": output,
                        "sql": sql,
                        "parameters": parameters,
                        "expectedColumns": expected_columns,
                    }
                )
                stored_outputs.append(
                    {
                        "versionId": version_id,
                        "queryDigest": query_digest,
                        "parentVersionId": parent_version_id,
                        "role": "writer" if index == 0 else "reviewer",
                        "authorType": str(output.get("authorType") or "model"),
                        "contractValid": True,
                        "validationId": validation_id,
                        "selected": index == selected_index,
                        "generationEvidenceRef": turn["generationEvidenceRef"],
                    }
                )
                parent_version_id = version_id
            selected = stored_outputs[selected_index]
            terminal_event = {
                "eventId": str(uuid.uuid4()),
                "status": "completed",
                "generationRunId": row["generation_run_id"],
                "occurredAt": timestamp,
            }
            turn.update(
                status="completed",
                hubTraceId=hub_trace_id,
                outputVersions=stored_outputs,
                selectedVersionId=selected["versionId"],
                resultingCurrentVersion={
                    "versionId": selected["versionId"],
                    "queryDigest": selected["queryDigest"],
                },
                events=[*turn["events"], terminal_event],
                failure=None,
                updatedAt=timestamp,
            )
            self._finish_turn(
                connection,
                row=row,
                turn=turn,
                hub_response=hub_response,
                invocations=invocations or [],
                raw_evidence=raw_evidence,
                timestamp=timestamp,
                candidate_ids=[
                    str(uuid.uuid4())
                    for _ in range(len(stored_outputs) + (raw_evidence is not None))
                ],
            )
            for index, plan in enumerate(planned):
                output = plan["output"]
                provenance = {
                    **dict(output.get("provenance") or {}),
                    "turnId": turn_id,
                    "dataSourceId": turn.get("dataSourceId"),
                    "observedBase": turn.get("observedBase"),
                    "effectiveBaseVersion": turn.get("effectiveBaseVersion"),
                    "manualVersion": turn.get("manualVersion"),
                    "editorSnapshotDigest": (
                        (turn.get("editorSnapshot") or {})
                        .get("content", {})
                        .get("editorDigest")
                    ),
                    "generationEvidenceRef": turn["generationEvidenceRef"],
                    "profileSnapshot": turn["profileSnapshot"],
                    "hubRequestDigest": turn.get("hubRequestDigest"),
                    "catalystTraceId": turn["catalystTraceId"],
                    "hubTraceId": hub_trace_id,
                }
                version = self._insert_version(
                    connection,
                    session_id=row["session_id"],
                    sql=plan["sql"],
                    parameters=plan["parameters"],
                    expected_columns=plan["expectedColumns"],
                    author_type=stored_outputs[index]["authorType"],
                    parent=parent,
                    provenance=provenance,
                    timestamp=timestamp,
                    version_id=plan["versionId"],
                )
                parent = self._version_row(connection, version["versionId"])
                if supplied_validations:
                    self._insert_validation(
                        connection,
                        version=version,
                        validation=supplied_validations[index],
                        timestamp=timestamp,
                        validation_id=plan["validationId"],
                    )
            connection.execute(
                """
                UPDATE catalyst_workbench_sessions
                SET current_version_id = ?, updated_at = ? WHERE session_id = ?
                """,
                (selected["versionId"], timestamp, row["session_id"]),
            )
            self._append_event(
                connection,
                session_id=row["session_id"],
                event_type="query_turn.completed",
                actor="med_agent_hub",
                entity_refs={
                    "sessionId": row["session_id"],
                    "turnId": turn_id,
                    "versionId": selected["versionId"],
                },
                payload={"turn": turn},
                timestamp=timestamp,
            )
        return turn

    def freeze_turn_request(
        self,
        turn_id: str,
        *,
        revision_context: dict[str, Any],
        hub_request: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind the accepted claim to the exact v2 request before inference."""

        timestamp = _timestamp(self._now())
        with self._transaction() as connection:
            row = self._require_requested_turn(connection, turn_id)
            turn = json.loads(row["turn_json"])
            turn["revisionContext"] = revision_context
            turn["hubRequestDigest"] = canonical_sha256(hub_request)
            turn["updatedAt"] = timestamp
            evidence_row = connection.execute(
                """
                SELECT * FROM catalyst_workbench_generation_evidence WHERE turn_id = ?
                """,
                (turn_id,),
            ).fetchone()
            assert evidence_row is not None
            evidence = json.loads(evidence_row["evidence_json"])
            evidence["revisionContext"] = revision_context
            evidence["history"] = self._history_evidence(revision_context)
            evidence["hubRequest"] = self._raw_artifact(
                hub_request,
                ref=f"generation-evidence:{evidence['evidenceId']}:hub-request",
            )
            evidence["updatedAt"] = timestamp
            evidence["evidenceDigest"] = self._evidence_digest(evidence)
            turn["generationEvidenceRef"]["evidenceDigest"] = evidence["evidenceDigest"]
            connection.execute(
                """
                UPDATE catalyst_workbench_turns SET turn_json = ?, updated_at = ?
                WHERE turn_id = ?
                """,
                (_json(turn), timestamp, turn_id),
            )
            connection.execute(
                """
                UPDATE catalyst_workbench_generation_evidence
                SET evidence_digest = ?, evidence_json = ?, updated_at = ?
                WHERE turn_id = ?
                """,
                (
                    evidence["evidenceDigest"],
                    _json(evidence),
                    timestamp,
                    turn_id,
                ),
            )
        return turn

    def fail_turn(
        self,
        turn_id: str,
        *,
        stage: str,
        code: str,
        message: str,
        raw_evidence: str | None,
        hub_trace_id: str | None = None,
        hub_response: dict[str, Any] | None = None,
        invocations: list[dict[str, Any]] | None = None,
        retained_writer: dict[str, Any] | None = None,
        retained_writer_validation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        timestamp = _timestamp(self._now())
        with self._transaction() as connection:
            row = self._require_requested_turn(connection, turn_id)
            turn = json.loads(row["turn_json"])
            turn["hubTraceId"] = hub_trace_id
            anchor = turn.get("effectiveBaseVersion") or turn.get("observedBase")
            stored_outputs: list[dict[str, Any]] = []
            writer_plan: dict[str, Any] | None = None
            if retained_writer is not None:
                sql = str(retained_writer["sql"])
                parameters = list(retained_writer.get("parameters") or [])
                expected_columns = list(retained_writer.get("expectedColumns") or [])
                version_id = str(uuid.uuid4())
                validation_id = (
                    str(uuid.uuid4())
                    if retained_writer_validation is not None
                    else None
                )
                writer_plan = {
                    "versionId": version_id,
                    "validationId": validation_id,
                    "sql": sql,
                    "parameters": parameters,
                    "expectedColumns": expected_columns,
                }
                stored_outputs.append(
                    {
                        "versionId": version_id,
                        "queryDigest": workbench_query_digest(
                            sql, parameters, expected_columns
                        ),
                        "parentVersionId": (
                            anchor["versionId"] if anchor is not None else None
                        ),
                        "role": "writer",
                        "authorType": "model",
                        "contractValid": True,
                        "validationId": validation_id,
                        "selected": False,
                        "generationEvidenceRef": turn["generationEvidenceRef"],
                    }
                )
            evidence_available = raw_evidence is not None
            raw_ref = (
                f"generation-evidence:{turn['generationEvidenceRef']['evidenceId']}:raw"
                if evidence_available
                else None
            )
            failure = {
                "stage": stage,
                "code": code,
                "message": message[:4000],
                "evidenceAvailable": evidence_available,
                "rawEvidenceRef": raw_ref,
                "diagnostic": {
                    "contractVersion": "catalyst.workbench.turn-failure-diagnostic.v1",
                    "retryable": stage not in {"orphan_recovery", "legacy_generation"},
                    "details": [],
                },
            }
            terminal_event = {
                "eventId": str(uuid.uuid4()),
                "status": "failed",
                "generationRunId": row["generation_run_id"],
                "occurredAt": timestamp,
            }
            turn.update(
                status="failed",
                outputVersions=stored_outputs,
                selectedVersionId=None,
                resultingCurrentVersion=anchor,
                events=[*turn["events"], terminal_event],
                failure=failure,
                updatedAt=timestamp,
            )
            self._finish_turn(
                connection,
                row=row,
                turn=turn,
                hub_response=hub_response,
                invocations=invocations or [],
                raw_evidence=raw_evidence,
                timestamp=timestamp,
                candidate_ids=[
                    str(uuid.uuid4())
                    for _ in range(len(stored_outputs) + (raw_evidence is not None))
                ],
            )
            if writer_plan is not None and retained_writer is not None:
                parent = (
                    self._version_row(connection, anchor["versionId"])
                    if anchor is not None
                    else None
                )
                writer = self._insert_version(
                    connection,
                    session_id=row["session_id"],
                    sql=writer_plan["sql"],
                    parameters=writer_plan["parameters"],
                    expected_columns=writer_plan["expectedColumns"],
                    author_type="model",
                    parent=parent,
                    provenance={
                        **dict(retained_writer.get("provenance") or {}),
                        "turnId": turn_id,
                        "dataSourceId": turn.get("dataSourceId"),
                        "selected": False,
                        "generationEvidenceRef": turn["generationEvidenceRef"],
                        "observedBase": turn.get("observedBase"),
                        "effectiveBaseVersion": turn.get("effectiveBaseVersion"),
                        "manualVersion": turn.get("manualVersion"),
                        "editorSnapshotDigest": (
                            (turn.get("editorSnapshot") or {})
                            .get("content", {})
                            .get("editorDigest")
                        ),
                        "profileSnapshot": turn.get("profileSnapshot"),
                        "hubRequestDigest": turn.get("hubRequestDigest"),
                        "catalystTraceId": turn.get("catalystTraceId"),
                        "hubTraceId": turn.get("hubTraceId"),
                    },
                    timestamp=timestamp,
                    version_id=writer_plan["versionId"],
                )
                if retained_writer_validation is not None:
                    self._insert_validation(
                        connection,
                        version=writer,
                        validation=retained_writer_validation,
                        timestamp=timestamp,
                        validation_id=writer_plan["validationId"],
                    )
            connection.execute(
                """
                UPDATE catalyst_workbench_sessions
                SET current_version_id = ?, updated_at = ? WHERE session_id = ?
                """,
                (
                    anchor["versionId"] if anchor is not None else None,
                    timestamp,
                    row["session_id"],
                ),
            )
            self._append_event(
                connection,
                session_id=row["session_id"],
                event_type="query_turn.failed",
                actor="system",
                entity_refs={"sessionId": row["session_id"], "turnId": turn_id},
                payload={"turn": turn},
                timestamp=timestamp,
            )
        return turn

    def append_validation(
        self,
        version_id: str,
        validation: dict[str, Any],
    ) -> dict[str, Any]:
        validation_id = str(uuid.uuid4())
        timestamp = _timestamp(self._now())
        with self._transaction() as connection:
            version_row = self._require_version(connection, version_id)
            version = self._version_from_row(version_row)
            stored = self._insert_validation(
                connection,
                version=version,
                validation=validation,
                timestamp=timestamp,
                validation_id=validation_id,
            )
        return stored

    def _insert_validation(
        self,
        connection: sqlite3.Connection,
        *,
        version: dict[str, Any],
        validation: dict[str, Any],
        timestamp: str,
        validation_id: str | None = None,
    ) -> dict[str, Any]:
        validation_id = validation_id or str(uuid.uuid4())
        version_id = version["versionId"]
        session_id = version["sessionId"]
        if validation.get("queryDigest") != version["queryDigest"]:
            raise ValueError("Validation digest does not match the query version.")
        ordinal = int(
            connection.execute(
                """
                SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal
                FROM catalyst_workbench_validations WHERE version_id = ?
                """,
                (version_id,),
            ).fetchone()["next_ordinal"]
        )
        stored = {
            **validation,
            "validationId": validation_id,
            "sessionId": session_id,
            "versionId": version_id,
            "ordinal": ordinal,
            "createdAt": timestamp,
        }
        connection.execute(
            """
            INSERT INTO catalyst_workbench_validations (
                validation_id, session_id, version_id, ordinal,
                validation_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (validation_id, session_id, version_id, ordinal, _json(stored), timestamp),
        )
        for finding_ordinal, finding in enumerate(stored.get("findings", []), 1):
            connection.execute(
                """
                INSERT INTO catalyst_workbench_findings (
                    validation_id, finding_id, ordinal, rule_code,
                    severity, stage, finding_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    validation_id,
                    finding["findingId"],
                    finding_ordinal,
                    finding["ruleCode"],
                    finding["severity"],
                    finding["stage"],
                    _json(finding),
                ),
            )
        connection.execute(
            "UPDATE catalyst_workbench_sessions SET updated_at = ? WHERE session_id = ?",
            (timestamp, session_id),
        )
        self._append_event(
            connection,
            session_id=session_id,
            event_type="validation.completed",
            actor="validator",
            entity_refs={
                "sessionId": session_id,
                "versionId": version_id,
                "validationId": validation_id,
            },
            payload={"validation": stored},
            timestamp=timestamp,
        )
        return stored

    def begin_execution(
        self,
        version_id: str,
        idempotency_key: str,
    ) -> WorkbenchExecutionDecision:
        """Claim one session-scoped idempotency key before touching the database.

        The claim is written in the same immediate transaction that checks prior
        executions. Two gateway requests, including requests handled by separate
        store instances, therefore cannot both receive permission to execute.
        """
        if not idempotency_key:
            raise ValueError("idempotency_key must not be empty.")
        with self._transaction() as connection:
            version = self._require_version(connection, version_id)
            session_id = str(version["session_id"])

            for row in connection.execute(
                """
                SELECT version_id, execution_json
                FROM catalyst_workbench_executions
                WHERE session_id = ? ORDER BY ordinal
                """,
                (session_id,),
            ).fetchall():
                execution = json.loads(row["execution_json"])
                if execution.get("idempotencyKey") != idempotency_key:
                    continue
                if row["version_id"] != version_id:
                    return WorkbenchExecutionDecision(
                        action="conflict",
                        claimed_version_id=str(row["version_id"]),
                    )
                return WorkbenchExecutionDecision(
                    action="replay",
                    execution={**execution, "replayed": True},
                    claimed_version_id=version_id,
                )

            claim = connection.execute(
                """
                SELECT version_id, started_at
                FROM catalyst_workbench_execution_claims
                WHERE session_id = ? AND idempotency_key = ?
                """,
                (session_id, idempotency_key),
            ).fetchone()
            if claim is not None:
                claimed_version_id = str(claim["version_id"])
                if claimed_version_id != version_id:
                    return WorkbenchExecutionDecision(
                        action="conflict",
                        claimed_version_id=claimed_version_id,
                    )
                started_at = datetime.fromisoformat(
                    str(claim["started_at"]).replace("Z", "+00:00")
                )
                lease_expired = (
                    self._now() - started_at
                ).total_seconds() >= self.execution_lease_seconds
                if lease_expired:
                    connection.execute(
                        """
                        UPDATE catalyst_workbench_execution_claims
                        SET started_at = ?
                        WHERE session_id = ? AND idempotency_key = ?
                            AND version_id = ?
                        """,
                        (
                            _timestamp(self._now()),
                            session_id,
                            idempotency_key,
                            version_id,
                        ),
                    )
                    return WorkbenchExecutionDecision(
                        action="execute",
                        claimed_version_id=version_id,
                    )
                return WorkbenchExecutionDecision(
                    action="in_progress",
                    claimed_version_id=claimed_version_id,
                )

            connection.execute(
                """
                INSERT INTO catalyst_workbench_execution_claims (
                    session_id, idempotency_key, version_id, started_at
                ) VALUES (?, ?, ?, ?)
                """,
                (session_id, idempotency_key, version_id, _timestamp(self._now())),
            )
            return WorkbenchExecutionDecision(
                action="execute",
                claimed_version_id=version_id,
            )

    def append_execution(
        self,
        version_id: str,
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        if execution.get("status") == "running":
            raise ValueError("WorkbenchStore accepts completed execution records only.")
        execution_id = str(execution.get("executionId") or uuid.uuid4())
        timestamp = _timestamp(self._now())
        with self._transaction() as connection:
            version = self._require_version(connection, version_id)
            supplied_digest = execution.get("queryDigest")
            if (
                supplied_digest is not None
                and supplied_digest != version["query_digest"]
            ):
                raise ValueError("Execution digest does not match the query version.")
            ordinal = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal
                    FROM catalyst_workbench_executions
                    WHERE session_id = ?
                    """,
                    (version["session_id"],),
                ).fetchone()["next_ordinal"]
            )
            stored = {
                **execution,
                "executionId": execution_id,
                "sessionId": version["session_id"],
                "versionId": version_id,
                "queryDigest": version["query_digest"],
                "ordinal": ordinal,
                "completedAt": execution.get("completedAt") or timestamp,
            }
            connection.execute(
                """
                INSERT INTO catalyst_workbench_executions (
                    execution_id, session_id, version_id, ordinal,
                    execution_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    version["session_id"],
                    version_id,
                    ordinal,
                    _json(stored),
                    timestamp,
                ),
            )
            connection.execute(
                """
                UPDATE catalyst_workbench_sessions SET updated_at = ?
                WHERE session_id = ?
                """,
                (timestamp, version["session_id"]),
            )
            idempotency_key = stored.get("idempotencyKey")
            if isinstance(idempotency_key, str) and idempotency_key:
                connection.execute(
                    """
                    DELETE FROM catalyst_workbench_execution_claims
                    WHERE session_id = ? AND idempotency_key = ? AND version_id = ?
                    """,
                    (version["session_id"], idempotency_key, version_id),
                )
            self._append_event(
                connection,
                session_id=version["session_id"],
                event_type="execution.completed",
                actor="database",
                entity_refs={
                    "sessionId": version["session_id"],
                    "versionId": version_id,
                    "executionId": execution_id,
                },
                payload={"execution": stored},
                timestamp=timestamp,
            )
        return stored

    def update_browser_state(
        self,
        session_id: str,
        browser_state: dict[str, Any],
    ) -> dict[str, Any]:
        timestamp = _timestamp(self._now())
        with self._transaction() as connection:
            self._require_session(connection, session_id)
            connection.execute(
                """
                UPDATE catalyst_workbench_sessions
                SET browser_state_json = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (_json(browser_state), timestamp, session_id),
            )
            self._append_event(
                connection,
                session_id=session_id,
                event_type="browser_state.updated",
                actor="human",
                entity_refs={"sessionId": session_id},
                payload={"browserState": browser_state},
                timestamp=timestamp,
            )
        restored = self.get_session(session_id)
        assert restored is not None
        return restored

    def update_session_provenance(
        self,
        session_id: str,
        provenance: dict[str, Any],
    ) -> None:
        timestamp = _timestamp(self._now())
        with self._transaction() as connection:
            self._require_session(connection, session_id)
            connection.execute(
                """
                UPDATE catalyst_workbench_sessions
                SET provenance_json = ?, updated_at = ? WHERE session_id = ?
                """,
                (_json(provenance), timestamp, session_id),
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM catalyst_workbench_sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            versions = [
                self._version_from_row(version)
                for version in self._connection.execute(
                    """
                    SELECT * FROM catalyst_workbench_query_versions
                    WHERE session_id = ? ORDER BY ordinal
                    """,
                    (session_id,),
                ).fetchall()
            ]
            validations = [
                json.loads(validation["validation_json"])
                for validation in self._connection.execute(
                    """
                    SELECT validation_json FROM catalyst_workbench_validations
                    WHERE session_id = ? ORDER BY created_at, ordinal
                    """,
                    (session_id,),
                ).fetchall()
            ]
            executions = [
                json.loads(execution["execution_json"])
                for execution in self._connection.execute(
                    """
                    SELECT execution_json FROM catalyst_workbench_executions
                    WHERE session_id = ? ORDER BY ordinal
                    """,
                    (session_id,),
                ).fetchall()
            ]

        session = self._session_from_row(row)
        current_version_id = row["current_version_id"]
        session.update(
            {
                "versions": versions,
                "currentVersion": next(
                    (
                        version
                        for version in versions
                        if version["versionId"] == current_version_id
                    ),
                    None,
                ),
                "validations": validations,
                "latestValidation": next(
                    (
                        validation
                        for validation in reversed(validations)
                        if validation["versionId"] == current_version_id
                    ),
                    None,
                ),
                "executions": executions,
            }
        )
        return session

    def get_version(self, version_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM catalyst_workbench_query_versions
                WHERE version_id = ?
                """,
                (version_id,),
            ).fetchone()
        return self._version_from_row(row) if row is not None else None

    def list_turns(self, session_id: str) -> dict[str, Any]:
        # Access is also a recovery boundary for a store kept open across a
        # process-owner change in tests or embedded runtimes.
        with self._lock:
            self._recover_orphaned_turns(session_id=session_id)
            session_row = self._connection.execute(
                "SELECT * FROM catalyst_workbench_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session_row is None:
                raise WorkbenchNotFoundError("Workbench session was not found.")
            rows = self._connection.execute(
                """
                SELECT turn_json FROM catalyst_workbench_turns
                WHERE session_id = ? ORDER BY ordinal
                """,
                (session_id,),
            ).fetchall()
        if rows:
            turns = [json.loads(row["turn_json"]) for row in rows]
        else:
            session = self.get_session(session_id)
            assert session is not None
            turns = [self._synthesize_legacy_turn(session)]
        current = self.get_session(session_id)
        assert current is not None
        return {
            "contractVersion": "catalyst.workbench.turn.timeline.v1",
            "sessionId": session_id,
            "currentTurnId": turns[-1]["turnId"],
            "currentVersion": (
                self._version_ref(current["currentVersion"])
                if current["currentVersion"] is not None
                else None
            ),
            "turns": turns,
        }

    def get_generation_evidence(
        self,
        session_id: str,
        turn_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT evidence_json FROM catalyst_workbench_generation_evidence
                WHERE session_id = ? AND turn_id = ?
                """,
                (session_id, turn_id),
            ).fetchone()
        if row is not None:
            return json.loads(row["evidence_json"])
        session = self.get_session(session_id)
        if session is None:
            return None
        legacy = self._synthesize_legacy_turn(session)
        if legacy["turnId"] != turn_id:
            return None
        return self._legacy_generation_evidence(session, legacy)

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM catalyst_workbench_events
                WHERE session_id = ? ORDER BY sequence
                """,
                (session_id,),
            ).fetchall()
        return [
            {
                "contractVersion": row["contract_version"],
                "eventId": row["event_id"],
                "sessionId": row["session_id"],
                "sequence": row["sequence"],
                "type": row["event_type"],
                "timestamp": row["created_at"],
                "actor": row["actor"],
                "entityRefs": json.loads(row["entity_refs_json"]),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def readiness(self) -> dict[str, bool]:
        try:
            with self._lock:
                self._connection.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            return {"ready": False}
        return {"ready": True}

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        event_type: str,
        actor: str,
        entity_refs: dict[str, Any],
        payload: dict[str, Any],
        timestamp: str,
    ) -> None:
        sequence = int(
            connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM catalyst_workbench_events WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()["next_sequence"]
        )
        connection.execute(
            """
            INSERT INTO catalyst_workbench_events (
                event_id, session_id, sequence, event_type,
                contract_version, created_at, actor,
                entity_refs_json, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                session_id,
                sequence,
                event_type,
                "catalyst.workbench.event.v1",
                timestamp,
                actor,
                _json(entity_refs),
                _json(payload),
            ),
        )

    def _insert_version(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        sql: str,
        parameters: list[dict[str, Any]],
        expected_columns: list[dict[str, Any]],
        author_type: str,
        parent: sqlite3.Row | None,
        provenance: dict[str, Any],
        timestamp: str,
        version_id: str | None = None,
    ) -> dict[str, Any]:
        if author_type not in {
            "model",
            "human",
            "deterministic_repair",
            "model_repair",
        }:
            raise ValueError(f"Unsupported workbench author type: {author_type}")
        version_id = version_id or str(uuid.uuid4())
        digest = workbench_query_digest(sql, parameters, expected_columns)
        ordinal = int(
            connection.execute(
                """
                SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal
                FROM catalyst_workbench_query_versions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()["next_ordinal"]
        )
        parent_id = parent["version_id"] if parent is not None else None
        connection.execute(
            """
            INSERT INTO catalyst_workbench_query_versions (
                version_id, session_id, parent_version_id, ordinal,
                author_type, sql, parameters_json, expected_columns_json,
                query_digest, provenance_json, source_finding_ids_json,
                repair_proposal_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', NULL, ?)
            """,
            (
                version_id,
                session_id,
                parent_id,
                ordinal,
                author_type,
                sql,
                _json(parameters),
                _json(expected_columns),
                digest,
                _json(provenance),
                timestamp,
            ),
        )
        connection.execute(
            """
            UPDATE catalyst_workbench_sessions
            SET current_version_id = ?, updated_at = ? WHERE session_id = ?
            """,
            (version_id, timestamp, session_id),
        )
        version = {
            "contractVersion": "catalyst.workbench.query-version.v1",
            "versionId": version_id,
            "sessionId": session_id,
            "parentVersionId": parent_id,
            "ordinal": ordinal,
            "authorType": author_type,
            "sql": sql,
            "parameters": parameters,
            "expectedColumns": expected_columns,
            "queryDigest": digest,
            "provenance": provenance,
            "sourceFindingIds": [],
            "repairProposalId": None,
            "createdAt": timestamp,
        }
        self._append_event(
            connection,
            session_id=session_id,
            event_type="query_version.created",
            actor=author_type,
            entity_refs={"sessionId": session_id, "versionId": version_id},
            payload={"version": version},
            timestamp=timestamp,
        )
        return version

    def _finish_turn(
        self,
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        turn: dict[str, Any],
        hub_response: dict[str, Any] | None,
        invocations: list[dict[str, Any]],
        raw_evidence: str | None,
        timestamp: str,
        candidate_ids: list[str] | None = None,
    ) -> None:
        evidence_row = connection.execute(
            """
            SELECT * FROM catalyst_workbench_generation_evidence WHERE turn_id = ?
            """,
            (turn["turnId"],),
        ).fetchone()
        if evidence_row is None:
            raise WorkbenchNotFoundError("Generation evidence was not found.")
        evidence = json.loads(evidence_row["evidence_json"])
        evidence.update(
            status=turn["status"],
            hubResponse=self._raw_artifact(
                (
                    hub_response.get("exactHubResponse")
                    if isinstance(hub_response, dict)
                    and isinstance(hub_response.get("exactHubResponse"), str)
                    else hub_response
                ),
                ref=f"generation-evidence:{evidence['evidenceId']}:hub-response",
            ),
            invocations=invocations,
            totalInvocationDurationMs=sum(
                int(invocation.get("durationMs") or 0) for invocation in invocations
            ),
            finalSelection={
                "status": turn["status"],
                "selectedVersion": turn["resultingCurrentVersion"]
                if turn["status"] == "completed"
                else None,
                "failure": (
                    {
                        "stage": turn["failure"]["stage"],
                        "code": turn["failure"]["code"],
                        "evidenceRef": turn["failure"]["rawEvidenceRef"],
                    }
                    if turn["failure"] is not None
                    else None
                ),
            },
            updatedAt=timestamp,
        )
        profile_evidence = (
            hub_response.get("profileEvidence")
            if isinstance(hub_response, dict)
            else None
        )
        if isinstance(profile_evidence, dict):
            response_profile = self._hub_profile_descriptor(
                profile_evidence,
                compact_digest=turn["profileSnapshot"]["profileDigest"],
            )
            if response_profile != evidence.get("profile"):
                raise WorkbenchStorageError(
                    "Hub response profile evidence does not match the profile "
                    "recorded when the turn was requested."
                )
            evidence["profile"] = response_profile
        if isinstance(evidence.get("correlation"), dict):
            correlation = dict(evidence["correlation"])
            correlation["hubTraceId"] = turn.get("hubTraceId")
            digestable_correlation = {
                key: value
                for key, value in correlation.items()
                if key != "correlationDigest"
            }
            correlation["correlationDigest"] = canonical_sha256(digestable_correlation)
            evidence["correlation"] = correlation
        expected_candidate_count = len(turn["outputVersions"]) + (
            1 if raw_evidence is not None else 0
        )
        candidate_ids = candidate_ids or [
            str(uuid.uuid4()) for _ in range(expected_candidate_count)
        ]
        if len(candidate_ids) != expected_candidate_count:
            raise ValueError(
                "Terminal candidate identity count does not match output evidence."
            )
        evidence["candidates"] = [
            {
                "candidateId": candidate_ids[index],
                "attemptOrdinal": index + 1,
                "role": output["role"],
                "candidateDigest": output["queryDigest"],
                "disposition": (
                    "selected"
                    if output["selected"]
                    else "retained_unselected"
                    if turn["status"] == "failed"
                    else "superseded"
                ),
                "versionRef": {
                    "versionId": output["versionId"],
                    "queryDigest": output["queryDigest"],
                },
                "validationRef": (
                    {
                        "validationId": output["validationId"],
                        "versionId": output["versionId"],
                        "queryDigest": output["queryDigest"],
                    }
                    if output.get("validationId") is not None
                    else None
                ),
                "rawEvidence": self._raw_artifact(
                    None,
                    ref=f"generation-evidence:{evidence['evidenceId']}:candidate:{index + 1}",
                    omission_reason=("No separate raw candidate payload was recorded."),
                ),
            }
            for index, output in enumerate(turn["outputVersions"])
        ]
        if raw_evidence is not None:
            terminal_role = (
                invocations[-1].get("role")
                if invocations and isinstance(invocations[-1], dict)
                else None
            )
            if terminal_role not in {"writer", "reviewer"}:
                failure_stage = str((turn.get("failure") or {}).get("stage") or "")
                terminal_role = (
                    "reviewer" if failure_stage.startswith("reviewer") else "writer"
                )
            evidence["candidates"].append(
                {
                    "candidateId": candidate_ids[len(turn["outputVersions"])],
                    "attemptOrdinal": len(evidence["candidates"]) + 1,
                    "role": terminal_role,
                    "candidateDigest": None,
                    "disposition": "diagnostic_only",
                    "versionRef": None,
                    "validationRef": None,
                    "rawEvidence": self._raw_artifact(
                        raw_evidence,
                        ref=f"generation-evidence:{evidence['evidenceId']}:raw",
                    ),
                }
            )
        evidence["evidenceDigest"] = self._evidence_digest(evidence)
        turn["generationEvidenceRef"]["evidenceDigest"] = evidence["evidenceDigest"]
        connection.execute(
            """
            UPDATE catalyst_workbench_turns
            SET status = ?, turn_json = ?, updated_at = ? WHERE turn_id = ?
            """,
            (turn["status"], _json(turn), timestamp, turn["turnId"]),
        )
        connection.execute(
            """
            UPDATE catalyst_workbench_generation_evidence
            SET evidence_digest = ?, evidence_json = ?, updated_at = ?
            WHERE turn_id = ?
            """,
            (
                evidence["evidenceDigest"],
                _json(evidence),
                timestamp,
                turn["turnId"],
            ),
        )

    def _requested_evidence(
        self,
        *,
        evidence_id: str,
        turn: dict[str, Any],
        session: sqlite3.Row,
        profile_evidence: dict[str, Any] | None,
        hub_request: dict[str, Any] | None,
        timestamp: str,
    ) -> dict[str, Any]:
        dataset_descriptor = self._artifact_descriptor(
            artifact_id=str(session["dataset_id"]),
            artifact_version=str(session["dataset_version"]),
            artifact_ref=f"workbench-session:{turn['sessionId']}:dataset",
        )
        catalog_descriptor = self._artifact_descriptor(
            artifact_id="catalyst-approved-catalog",
            artifact_version=str(session["catalog_version"]),
            artifact_ref="/v1/catalyst/workbench/catalog",
        )
        policy_descriptor = self._artifact_descriptor(
            artifact_id="catalyst-workbench-read-only-policy",
            artifact_version="1",
            artifact_ref="catalyst-gateway:sql-policy",
        )
        output_descriptor = self._artifact_descriptor(
            artifact_id="catalyst.query.v1",
            artifact_version="1",
            artifact_ref="contracts/catalyst-query-v1.schema.json",
        )
        profile_descriptor = self._evidence_profile_descriptor(
            turn["profileSnapshot"],
            profile_evidence=profile_evidence,
        )
        request_correlation = (
            hub_request.get("catalystQuery", {}).get("correlation", {})
            if isinstance(hub_request, dict)
            else {}
        )
        correlation_value = {
            "requestId": str(request_correlation.get("requestId") or "unavailable"),
            "catalystTraceId": turn["catalystTraceId"],
            "hubTraceId": None,
            "correlationRef": f"generation-run:{turn['turnId']}",
        }
        correlation_value["correlationDigest"] = canonical_sha256(correlation_value)
        selection_policy = {
            "revision": "writer-reviewer-complete-query.v1",
            "policyRef": "catalyst-gateway:query-selection-policy",
        }
        selection_policy["policyDigest"] = canonical_sha256(selection_policy)
        writer = turn.get("profileSnapshot", {}).get("writer") or {}
        request_digest = canonical_sha256(hub_request or {})
        evidence = {
            "contractVersion": "catalyst.workbench.generation-evidence.v1",
            "evidenceId": evidence_id,
            "evidenceDigest": "0" * 64,
            "sessionId": turn["sessionId"],
            "turnId": turn["turnId"],
            "turnKind": turn["kind"],
            "origin": "recorded",
            "status": "requested",
            "instruction": turn["instruction"],
            "instructionDigest": turn["instructionDigest"],
            "editorSnapshot": turn["editorSnapshot"],
            "observedBase": turn["observedBase"],
            "effectiveBaseVersion": turn["effectiveBaseVersion"],
            "manualVersion": turn["manualVersion"],
            "revisionContext": turn["revisionContext"],
            "dataset": dataset_descriptor,
            "catalog": catalog_descriptor,
            "policy": policy_descriptor,
            "outputSchema": output_descriptor,
            "profile": profile_descriptor,
            "correlation": correlation_value,
            "selectionPolicy": selection_policy,
            "history": self._history_evidence(turn.get("revisionContext") or {}),
            "hubRequest": self._raw_artifact(
                hub_request,
                ref=f"generation-evidence:{evidence_id}:hub-request",
            ),
            "hubResponse": self._raw_artifact(
                None,
                ref=f"generation-evidence:{evidence_id}:hub-response",
                omission_reason="The Hub response is pending.",
            ),
            "invocations": [
                {
                    "invocationId": str(uuid.uuid4()),
                    "role": "writer",
                    "stage": (
                        "initial_generation"
                        if turn["kind"] == "initial"
                        else "followup_generation"
                    ),
                    "attempt": 1,
                    "providerId": str(writer.get("providerId") or "med-agent-hub"),
                    "modelId": str(
                        writer.get("modelId") or turn["profileSnapshot"]["profileId"]
                    ),
                    "startedAt": timestamp,
                    "endedAt": None,
                    "durationMs": None,
                    "requestDigest": request_digest,
                    "responseDigest": None,
                    "failureDigest": None,
                    "outcome": "in_progress",
                }
            ],
            "totalInvocationDurationMs": None,
            "candidates": [],
            "finalSelection": {
                "status": "requested",
                "selectedVersion": None,
                "failure": None,
            },
            "omissions": [],
            "prohibitedClasses": self._prohibited_evidence_classes(),
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        evidence["evidenceDigest"] = self._evidence_digest(evidence)
        return evidence

    @staticmethod
    def _artifact_descriptor(
        *,
        artifact_id: str,
        artifact_version: str,
        artifact_ref: str,
    ) -> dict[str, Any]:
        descriptor = {
            "artifactId": artifact_id,
            "artifactVersion": artifact_version,
            "artifactRef": artifact_ref,
        }
        descriptor["artifactDigest"] = canonical_sha256(descriptor)
        return descriptor

    @staticmethod
    def _evidence_profile_descriptor(
        profile: dict[str, Any],
        *,
        profile_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        detail = None
        if isinstance(profile_evidence, dict):
            writer = profile_evidence.get("writer")
            if isinstance(writer, dict):
                # Writer is always present; reviewer only for reviewed profiles.
                detail = {
                    "profileName": str(profile_evidence.get("profileName")),
                    "writer": writer,
                }
                reviewer = profile_evidence.get("reviewer")
                if isinstance(reviewer, dict):
                    detail["reviewer"] = reviewer
        digest = profile.get("profileDigest")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            digest = canonical_sha256(
                {key: value for key, value in profile.items() if key != "profileDigest"}
            )
        descriptor = {
            "profileId": profile["profileId"],
            "profileRef": (
                f"catalyst-gateway:/v1/catalyst/query-options/{profile['profileId']}"
            ),
            "profileDigest": digest,
            "detail": detail if isinstance(detail, dict) else None,
        }
        return descriptor

    @staticmethod
    def _hub_profile_descriptor(
        profile: dict[str, Any],
        *,
        compact_digest: str,
    ) -> dict[str, Any]:
        writer = profile.get("writer")
        detail = None
        if isinstance(writer, dict):
            detail = {
                "profileName": str(
                    profile.get("profileName") or profile.get("profileId")
                ),
                "writer": writer,
            }
            reviewer = profile.get("reviewer")
            if isinstance(reviewer, dict):
                detail["reviewer"] = reviewer
        return {
            "profileId": str(profile.get("profileId") or "unknown-profile"),
            "profileRef": str(
                profile.get("profileRef")
                or "catalyst-gateway:/v1/catalyst/query-options/"
                f"{profile.get('profileId') or 'unknown'}"
            ),
            "profileDigest": compact_digest,
            "detail": detail,
        }

    @staticmethod
    def _raw_artifact(
        value: Any,
        *,
        ref: str,
        omission_reason: str = "No payload was recorded for this artifact.",
    ) -> dict[str, Any]:
        if value is None:
            return {
                "available": False,
                "inspectable": False,
                "evidenceRef": None,
                "payloadDigest": None,
                "contentType": None,
                "exactPayload": None,
                "omissionReason": omission_reason,
            }
        exact = value
        return {
            "available": True,
            "inspectable": True,
            "evidenceRef": ref,
            "payloadDigest": (
                utf8_sha256(exact)
                if isinstance(exact, str)
                else canonical_sha256(exact)
            ),
            "contentType": "application/json"
            if not isinstance(value, str)
            else "text/plain",
            "exactPayload": exact,
            "omissionReason": None,
        }

    @staticmethod
    def _evidence_digest(evidence: dict[str, Any]) -> str:
        digestable = dict(evidence)
        digestable.pop("evidenceDigest", None)
        return canonical_sha256(digestable)

    @staticmethod
    def _history_evidence(revision_context: dict[str, Any]) -> dict[str, Any]:
        included = [
            {
                key: item[key]
                for key in ("turnId", "ordinal", "kind", "instructionDigest")
            }
            for item in revision_context.get("instructionHistory", [])
        ]
        omitted = list(
            revision_context.get("selection", {})
            .get("omissions", {})
            .get("omittedHistory", [])
        )
        return {
            "included": included,
            "includedDigest": canonical_sha256(included),
            "omitted": omitted,
            "omittedDigest": canonical_sha256(omitted),
        }

    @staticmethod
    def _prohibited_evidence_classes() -> list[str]:
        return [
            "database_credentials",
            "database_connection_details",
            "database_dsn",
            "execution_result_rows",
            "hidden_reasoning",
            "historical_sql_copies",
            "raw_chat_transcript",
            "raw_model_outputs",
            "raw_reasoning_traces",
            "unrelated_session_history",
            "unrelated_historical_sql",
        ]

    @staticmethod
    def _snapshot_unresolved_paths(snapshot: dict[str, Any]) -> list[str]:
        paths: list[str] = []
        sql = snapshot.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            paths.append("$.sql")
        seen: set[str] = set()
        for index, parameter in enumerate(snapshot.get("parameters") or []):
            name = parameter.get("name") if isinstance(parameter, dict) else None
            if not isinstance(name, str) or not _PARAMETER_NAME.fullmatch(name):
                paths.append(f"$.parameters[{index}].name")
            elif name in seen:
                paths.append(f"$.parameters[{index}].name")
            else:
                seen.add(name)
        return paths

    @staticmethod
    def _version_ref(version: dict[str, Any]) -> dict[str, str]:
        return {
            "versionId": version["versionId"],
            "queryDigest": version["queryDigest"],
        }

    @staticmethod
    def _version_ref_row(row: sqlite3.Row | None) -> dict[str, str] | None:
        if row is None:
            return None
        return {"versionId": row["version_id"], "queryDigest": row["query_digest"]}

    @staticmethod
    def _version_row(
        connection: sqlite3.Connection,
        version_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM catalyst_workbench_query_versions WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            raise WorkbenchNotFoundError("Workbench query version was not found.")
        return row

    @staticmethod
    def _require_requested_turn(
        connection: sqlite3.Connection,
        turn_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM catalyst_workbench_turns WHERE turn_id = ?",
            (turn_id,),
        ).fetchone()
        if row is None:
            raise WorkbenchNotFoundError("Workbench turn was not found.")
        if row["status"] != "requested":
            raise WorkbenchStorageError("Workbench turn is already terminal.")
        return row

    def _recover_orphaned_turns(self, *, session_id: str | None = None) -> None:
        query = (
            "SELECT turn_id FROM catalyst_workbench_turns "
            "WHERE status = 'requested' AND owner_instance_id <> ?"
        )
        parameters: list[Any] = [self.owner_instance_id]
        if session_id is not None:
            query += " AND session_id = ?"
            parameters.append(session_id)
        rows = self._connection.execute(query, parameters).fetchall()
        for row in rows:
            self.fail_turn(
                row["turn_id"],
                stage="orphan_recovery",
                code="generation_interrupted",
                message=(
                    "The gateway process that owned this generation is no longer "
                    "present. The prior query remains current."
                ),
                raw_evidence=None,
            )

    def _synthesize_legacy_turn(self, session: dict[str, Any]) -> dict[str, Any]:
        base = f"{_LEGACY_TURN_NAMESPACE}/{session['sessionId']}/turns/initial"
        turn_id = str(uuid.uuid5(uuid.NAMESPACE_URL, base))
        generation_run_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, base + "/runs/generation")
        )
        evidence_id = str(uuid.uuid5(uuid.NAMESPACE_URL, base + "/generation-evidence"))
        initial_versions: list[dict[str, Any]] = []
        for version in session["versions"]:
            if version["authorType"] not in {"model", "model_repair"}:
                break
            initial_versions.append(version)
        selected = initial_versions[-1] if initial_versions else None
        if selected is not None and (
            selected.get("provenance", {}).get("selected") is False
            or selected.get("provenance", {}).get("selectionDisposition")
            == "unselected_output"
        ):
            selected = None
        status = "completed" if selected is not None else "failed"
        created_at = session["createdAt"]
        provenance = session.get("provenance", {})
        generation_outcome = provenance.get("generationOutcome")
        terminal_at = (
            selected["createdAt"]
            if selected is not None
            else (
                generation_outcome.get("createdAt")
                if isinstance(generation_outcome, dict)
                and isinstance(generation_outcome.get("createdAt"), str)
                else provenance.get("generationRawOutputCreatedAt")
                if isinstance(provenance.get("generationRawOutputCreatedAt"), str)
                else created_at
            )
        )
        profile_source = provenance.get("profileSnapshot")
        profile_source = profile_source if isinstance(profile_source, dict) else {}
        profile_name = profile_source.get("profileName") or profile_source.get(
            "profileLabel"
        )
        profile_name = profile_name if isinstance(profile_name, str) else None
        writer = self._legacy_role_snapshot(profile_source.get("writer"), "writer")
        reviewer = self._legacy_role_snapshot(
            profile_source.get("reviewer"), "reviewer"
        )
        profile_omissions: list[str] = []
        if profile_name is None:
            profile_omissions.append("legacy_profile_name_unavailable")
        if writer is None:
            profile_omissions.append("legacy_writer_snapshot_unavailable")
        if reviewer is None:
            profile_omissions.append("legacy_reviewer_snapshot_unavailable")
        if writer is None or reviewer is None:
            profile_omissions.extend(
                [
                    "legacy_prompt_snapshot_unavailable",
                    "legacy_config_snapshot_unavailable",
                ]
            )
        profile_snapshot = {
            "profileId": str(profile_source.get("profileId") or session["profileId"]),
            "profileName": profile_name,
            "profileDigest": "0" * 64,
            "writer": writer,
            "reviewer": reviewer,
            "omissions": profile_omissions,
        }
        profile_snapshot["profileDigest"] = canonical_sha256(
            {
                key: value
                for key, value in profile_snapshot.items()
                if key != "profileDigest"
            }
        )
        evidence_ref = {
            "contractVersion": "catalyst.workbench.generation-evidence-ref.v1",
            "evidenceId": evidence_id,
            "evidenceDigest": "0" * 64,
            "detailPath": (
                f"/v1/catalyst/workbench/sessions/{session['sessionId']}/turns/"
                f"{turn_id}/generation-evidence"
            ),
        }
        requested = {
            "eventId": str(uuid.uuid5(uuid.NAMESPACE_URL, base + "/events/requested")),
            "status": "requested",
            "generationRunId": generation_run_id,
            "ownerInstanceId": "legacy_synthesis",
            "occurredAt": created_at,
        }
        terminal = {
            "eventId": str(uuid.uuid5(uuid.NAMESPACE_URL, base + f"/events/{status}")),
            "status": status,
            "generationRunId": generation_run_id,
            "occurredAt": terminal_at,
        }
        failure = (
            None
            if status == "completed"
            else {
                "stage": "legacy_generation",
                "code": "legacy_generation_has_no_version",
                "message": "No immutable initial model output was persisted.",
                "evidenceAvailable": isinstance(
                    provenance.get("generationRawOutput"), str
                ),
                "rawEvidenceRef": (
                    f"legacy-session:{session['sessionId']}:raw-output"
                    if isinstance(provenance.get("generationRawOutput"), str)
                    else None
                ),
                "diagnostic": {
                    "contractVersion": (
                        "catalyst.workbench.turn-failure-diagnostic.v1"
                    ),
                    "retryable": False,
                    "details": [],
                },
            }
        )
        validations_by_version = {
            validation["versionId"]: validation["validationId"]
            for validation in session.get("validations", [])
        }
        output_versions = [
            {
                "versionId": version["versionId"],
                "queryDigest": version["queryDigest"],
                "parentVersionId": version["parentVersionId"],
                "role": (
                    "reviewer" if version["authorType"] == "model_repair" else "writer"
                ),
                "authorType": version["authorType"],
                "contractValid": True,
                "validationId": validations_by_version.get(version["versionId"]),
                "selected": selected is not None
                and version["versionId"] == selected["versionId"],
                "generationEvidenceRef": evidence_ref,
            }
            for version in initial_versions
        ]
        turn = {
            "contractVersion": "catalyst.workbench.turn.v1",
            "sessionId": session["sessionId"],
            "turnId": turn_id,
            "ordinal": 1,
            "kind": "initial",
            "origin": "synthesized_legacy",
            "instruction": session["question"],
            "instructionDigest": utf8_sha256(session["question"]),
            "profileSnapshot": profile_snapshot,
            "observedBase": None,
            "editorSnapshot": None,
            "snapshotClassification": "not_applicable",
            "unresolvedPaths": [],
            "effectiveBaseVersion": None,
            "manualVersion": None,
            "revisionContext": None,
            "hubRequestDigest": None,
            "catalystTraceId": None,
            "hubTraceId": provenance.get("hubTraceId"),
            "generationEvidenceRef": evidence_ref,
            "recoveryReferences": {
                "contractVersion": "catalyst.workbench.legacy-recovery-references.v1",
                "sessionId": session["sessionId"],
                "sessionEvidenceRef": f"workbench-session:{session['sessionId']}",
                "orderedVersionIds": [
                    version["versionId"] for version in session["versions"]
                ],
                "persistedCurrentVersion": (
                    self._version_ref(session["currentVersion"])
                    if session["currentVersion"] is not None
                    else None
                ),
                "draftSeedEvidenceRef": (
                    f"legacy-session:{session['sessionId']}:draft-seed"
                    if session.get("draftSeed") is not None
                    else None
                ),
                "rawGenerationEvidenceRef": (
                    f"legacy-session:{session['sessionId']}:raw-output"
                    if isinstance(provenance.get("generationRawOutput"), str)
                    else None
                ),
                "generationOutcomeEvidenceRef": (
                    f"legacy-session:{session['sessionId']}:generation-outcome"
                    if generation_outcome is not None
                    else None
                ),
            },
            "status": status,
            "outputVersions": output_versions,
            "selectedVersionId": selected["versionId"] if selected else None,
            "resultingCurrentVersion": (
                self._version_ref(selected) if selected is not None else None
            ),
            "events": [requested, terminal],
            "failure": failure,
            "createdAt": created_at,
            "updatedAt": terminal_at,
        }
        evidence = self._legacy_generation_evidence(session, turn)
        evidence_ref["evidenceDigest"] = evidence["evidenceDigest"]
        return turn

    @staticmethod
    def _legacy_role_snapshot(value: Any, role: str) -> dict[str, Any] | None:
        if not isinstance(value, dict) or value.get("role") != role:
            return None
        required_strings = ("providerId", "modelClass", "modelId")
        if any(
            not isinstance(value.get(key), str) or not value[key]
            for key in required_strings
        ):
            return None
        config = value.get("config")
        prompt = value.get("systemPrompt")
        if not isinstance(config, dict) or not isinstance(prompt, dict):
            return None
        if any(
            not isinstance(prompt.get(key), str) or not prompt[key]
            for key in ("promptId", "version", "promptRef", "promptDigest")
        ):
            return None
        return {
            "role": role,
            "providerId": value["providerId"],
            "modelClass": value["modelClass"],
            "modelId": value["modelId"],
            "config": config,
            "systemPrompt": {
                key: prompt[key]
                for key in ("promptId", "version", "promptRef", "promptDigest")
            },
        }

    def _legacy_generation_evidence(
        self,
        session: dict[str, Any],
        turn: dict[str, Any],
    ) -> dict[str, Any]:
        evidence_id = turn["generationEvidenceRef"]["evidenceId"]
        raw_output = session.get("provenance", {}).get("generationRawOutput")
        raw_ref = f"legacy-session:{session['sessionId']}:raw-output"
        candidates = [
            {
                "candidateId": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{_LEGACY_TURN_NAMESPACE}/{session['sessionId']}/turns/"
                        f"initial/generation-evidence/candidates/{index + 1}",
                    )
                ),
                "attemptOrdinal": index + 1,
                "role": output["role"],
                "candidateDigest": output["queryDigest"],
                "disposition": (
                    "selected"
                    if output["selected"]
                    else "retained_unselected"
                    if turn["status"] == "failed"
                    else "superseded"
                ),
                "versionRef": {
                    "versionId": output["versionId"],
                    "queryDigest": output["queryDigest"],
                },
                "validationRef": (
                    {
                        "validationId": output["validationId"],
                        "versionId": output["versionId"],
                        "queryDigest": output["queryDigest"],
                    }
                    if output["validationId"] is not None
                    else None
                ),
                "rawEvidence": self._raw_artifact(
                    None,
                    ref=(f"generation-evidence:{evidence_id}:candidate:{index + 1}"),
                    omission_reason=("No separate raw candidate payload was recorded."),
                ),
            }
            for index, output in enumerate(turn["outputVersions"])
        ]
        if isinstance(raw_output, str):
            candidates.append(
                {
                    "candidateId": str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"{_LEGACY_TURN_NAMESPACE}/{session['sessionId']}/turns/"
                            "initial/generation-evidence/raw-output",
                        )
                    ),
                    "attemptOrdinal": len(candidates) + 1,
                    "role": "writer",
                    "candidateDigest": None,
                    "disposition": "diagnostic_only",
                    "versionRef": None,
                    "validationRef": None,
                    "rawEvidence": self._raw_artifact(raw_output, ref=raw_ref),
                }
            )
        profile_snapshot = turn["profileSnapshot"]
        evidence = {
            "contractVersion": "catalyst.workbench.generation-evidence.v1",
            "evidenceId": evidence_id,
            "evidenceDigest": "0" * 64,
            "sessionId": session["sessionId"],
            "turnId": turn["turnId"],
            "turnKind": "initial",
            "origin": "synthesized_legacy",
            "status": turn["status"],
            "instruction": turn["instruction"],
            "instructionDigest": turn["instructionDigest"],
            "editorSnapshot": None,
            "observedBase": None,
            "effectiveBaseVersion": None,
            "manualVersion": None,
            "revisionContext": None,
            "dataset": None,
            "catalog": None,
            "policy": None,
            "outputSchema": None,
            "profile": {
                "profileId": profile_snapshot["profileId"],
                "profileRef": f"legacy-session:{session['sessionId']}:profile",
                "profileDigest": profile_snapshot["profileDigest"],
                "detail": None,
            },
            "correlation": None,
            "selectionPolicy": None,
            "history": {
                "included": [],
                "includedDigest": canonical_sha256([]),
                "omitted": [],
                "omittedDigest": canonical_sha256([]),
            },
            "hubRequest": None,
            "hubResponse": None,
            "invocations": [],
            "totalInvocationDurationMs": 0,
            "candidates": candidates,
            "finalSelection": {
                "status": turn["status"],
                "selectedVersion": turn["resultingCurrentVersion"],
                "failure": (
                    {
                        "stage": turn["failure"]["stage"],
                        "code": turn["failure"]["code"],
                        "evidenceRef": turn["failure"]["rawEvidenceRef"],
                    }
                    if turn["failure"] is not None
                    else None
                ),
            },
            "omissions": [
                "dataset_unavailable",
                "catalog_unavailable",
                "policy_unavailable",
                "output_schema_unavailable",
                "profile_detail_unavailable",
                "correlation_unavailable",
                "selection_policy_unavailable",
                "hub_request_unavailable",
                "hub_response_unavailable",
                "invocation_timing_unavailable",
            ],
            "prohibitedClasses": self._prohibited_evidence_classes(),
            "createdAt": turn["createdAt"],
            "updatedAt": turn["updatedAt"],
        }
        evidence["evidenceDigest"] = self._evidence_digest(evidence)
        return evidence

    @staticmethod
    def _require_session(
        connection: sqlite3.Connection,
        session_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM catalyst_workbench_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise WorkbenchNotFoundError(
                f"Workbench session {session_id} was not found."
            )
        return row

    @staticmethod
    def _require_version(
        connection: sqlite3.Connection,
        version_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM catalyst_workbench_query_versions WHERE version_id = ?
            """,
            (version_id,),
        ).fetchone()
        if row is None:
            raise WorkbenchNotFoundError(
                f"Workbench query version {version_id} was not found."
            )
        return row

    @staticmethod
    def _current_version(
        connection: sqlite3.Connection,
        session: sqlite3.Row,
    ) -> sqlite3.Row | None:
        version_id = session["current_version_id"]
        if version_id is None:
            return None
        return connection.execute(
            """
            SELECT * FROM catalyst_workbench_query_versions WHERE version_id = ?
            """,
            (version_id,),
        ).fetchone()

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "contractVersion": "catalyst.workbench.session.v1",
            "sessionId": row["session_id"],
            "question": row["question"],
            "profileId": row["profile_id"],
            "datasetId": row["dataset_id"],
            "datasetVersion": row["dataset_version"],
            "catalogVersion": row["catalog_version"],
            "currentVersionId": row["current_version_id"],
            "browserState": json.loads(row["browser_state_json"]),
            "provenance": json.loads(row["provenance_json"]),
            "status": row["status"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _version_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "contractVersion": "catalyst.workbench.query-version.v1",
            "versionId": row["version_id"],
            "sessionId": row["session_id"],
            "parentVersionId": row["parent_version_id"],
            "ordinal": row["ordinal"],
            "authorType": row["author_type"],
            "sql": row["sql"],
            "parameters": json.loads(row["parameters_json"]),
            "expectedColumns": json.loads(row["expected_columns_json"]),
            "queryDigest": row["query_digest"],
            "provenance": json.loads(row["provenance_json"]),
            "sourceFindingIds": json.loads(row["source_finding_ids_json"]),
            "repairProposalId": row["repair_proposal_id"],
            "createdAt": row["created_at"],
        }

    class _Transaction:
        def __init__(self, store: WorkbenchStore) -> None:
            self.store = store

        def __enter__(self) -> sqlite3.Connection:
            self.store._lock.acquire()
            self.store._connection.execute("BEGIN IMMEDIATE")
            return self.store._connection

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            try:
                if exc_type is None:
                    self.store._connection.execute("COMMIT")
                else:
                    self.store._connection.execute("ROLLBACK")
            finally:
                self.store._lock.release()

    def _transaction(self) -> _Transaction:
        return self._Transaction(self)


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
