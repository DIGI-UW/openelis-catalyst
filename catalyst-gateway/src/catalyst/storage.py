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
