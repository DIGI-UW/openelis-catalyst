from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .digest import query_digest
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
    ) -> None:
        self.path = str(path)
        self._now = now or _utc_now
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

                CREATE INDEX IF NOT EXISTS catalyst_workbench_versions_session_idx
                    ON catalyst_workbench_query_versions(session_id, ordinal);
                CREATE INDEX IF NOT EXISTS catalyst_workbench_validations_version_idx
                    ON catalyst_workbench_validations(version_id, ordinal);
                CREATE INDEX IF NOT EXISTS catalyst_workbench_executions_version_idx
                    ON catalyst_workbench_executions(version_id, ordinal);
                CREATE INDEX IF NOT EXISTS catalyst_workbench_events_session_idx
                    ON catalyst_workbench_events(session_id, sequence);

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

    def append_validation(
        self,
        version_id: str,
        validation: dict[str, Any],
    ) -> dict[str, Any]:
        validation_id = str(uuid.uuid4())
        timestamp = _timestamp(self._now())
        with self._transaction() as connection:
            version = self._require_version(connection, version_id)
            if validation.get("queryDigest") != version["query_digest"]:
                raise ValueError("Validation digest does not match the query version.")
            ordinal = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal
                    FROM catalyst_workbench_validations
                    WHERE version_id = ?
                    """,
                    (version_id,),
                ).fetchone()["next_ordinal"]
            )
            stored = {
                **validation,
                "validationId": validation_id,
                "sessionId": version["session_id"],
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
                (
                    validation_id,
                    version["session_id"],
                    version_id,
                    ordinal,
                    _json(stored),
                    timestamp,
                ),
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
                """
                UPDATE catalyst_workbench_sessions SET updated_at = ?
                WHERE session_id = ?
                """,
                (timestamp, version["session_id"]),
            )
            self._append_event(
                connection,
                session_id=version["session_id"],
                event_type="validation.completed",
                actor="validator",
                entity_refs={
                    "sessionId": version["session_id"],
                    "versionId": version_id,
                    "validationId": validation_id,
                },
                payload={"validation": stored},
                timestamp=timestamp,
            )
        return stored

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
