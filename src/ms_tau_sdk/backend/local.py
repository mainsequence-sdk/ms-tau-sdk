"""SQLite-backed local runtime state with authenticated Main Sequence services."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Never, TypeVar, cast

from ms_tau_sdk.errors import (
    BackendConflictError,
    LocalModeUnsupportedError,
    SessionNotFoundError,
)
from ms_tau_sdk.protocols.a2a_failure import (
    failure_details,
    failure_status_message,
    validate_status_message,
)
from ms_tau_sdk.protocols.a2a_roles import A2AMessageDirection, message_to_protocol
from ms_tau_sdk.settings import TauSDKSettings

from .client import MainSequenceClient
from .models import (
    AgentCardEnvelope,
    AgentRuntimeActivity,
    AgentSession,
    AgentTask,
    AgentTaskCreateResult,
    AgentTaskDispatch,
    AgentTaskEvent,
    AgentTaskEventPage,
    AgentTaskExecutionAttempt,
    AgentTaskSnapshot,
    AgentTaskStatus,
    ProviderCredential,
    ProviderExecutionEvidence,
    RuntimeActivityPatch,
    RuntimeLease,
    RuntimeLeaseReleaseRequest,
    RuntimeLeaseRenewRequest,
    RuntimeLeaseRequest,
    RuntimeState,
    RuntimeStatePatch,
    SessionEntryAppendRequest,
    SessionEntryBatchAppendRequest,
    SessionEntryBatchAppendResponse,
    SessionEntryList,
    SessionEntryRecord,
    TauEntryType,
    TauResumeSnapshot,
    TauResumeSnapshotUploadRequest,
    TauResumeSnapshotUploadResponse,
    TauRuntimeBootstrap,
    TauRuntimeBootstrapRequest,
    TauTurnCommit,
)

LOCAL_RUNTIME_CAPABILITIES = {
    "tau_runtime_bootstrap": "v3",
    "tau_resume_snapshot": "v2",
    "tau_activity_sequence": "v1",
    "tau_turn_commit": "v1",
}
_SCHEMA_VERSION = 4
_T = TypeVar("_T")

_LOCAL_A2A_RESPONSE_KIND_EXTENSION_URI = "https://mainsequence.ai/a2a/extensions/response-kind/v1"
_LOCAL_A2A_TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _from_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class LocalDevelopmentBackend(MainSequenceClient):
    """Local persistence adapter that delegates only provider hydration remotely."""

    def __init__(
        self,
        settings: TauSDKSettings,
        services: MainSequenceClient,
    ) -> None:
        self.settings = settings
        self.auth = services.auth
        self._services = services
        self._path = settings.local_state_path
        self._database_lock = asyncio.Lock()
        self._initialized = False
        self._recovery_metrics: dict[str, int | float] = {
            "submitted": 0,
            "working": 0,
            "stale_attempts": 0,
            "recovery_failures": 0,
            "ambiguous_outcomes": 0,
            "oldest_pending_seconds": 0.0,
            "oldest_working_seconds": 0.0,
        }

    @property
    def state_path(self) -> Path:
        return self._path

    async def _request(self, *_args: object, **_kwargs: object) -> Never:
        raise LocalModeUnsupportedError(
            "This platform orchestration operation requires a registered Agent or AgentSession"
        )

    async def aclose(self) -> None:
        await self._services.aclose()

    async def list_model_providers(self) -> dict[str, Any]:
        return await self._services.list_model_providers()

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._database_lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    uid TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    thinking TEXT,
                    runtime_config_sha256 TEXT NOT NULL,
                    next_sequence INTEGER NOT NULL DEFAULT 0,
                    runtime_activity TEXT NOT NULL DEFAULT 'idle',
                    active_turn_uid TEXT,
                    active_task_attempt_uid TEXT,
                    activity_revision INTEGER NOT NULL DEFAULT 0,
                    activity_sequence INTEGER NOT NULL DEFAULT 0,
                    last_committed_turn_uid TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    cancellation_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entries (
                    session_uid TEXT NOT NULL REFERENCES sessions(uid) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    entry_type TEXT NOT NULL,
                    entry_json TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    turn_uid TEXT,
                    PRIMARY KEY (session_uid, sequence),
                    UNIQUE (session_uid, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS leases (
                    session_uid TEXT PRIMARY KEY REFERENCES sessions(uid) ON DELETE CASCADE,
                    lease_token TEXT NOT NULL,
                    holder_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    checkpoint_version INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS turn_commits (
                    session_uid TEXT NOT NULL REFERENCES sessions(uid) ON DELETE CASCADE,
                    turn_uid TEXT NOT NULL,
                    next_sequence INTEGER NOT NULL,
                    committed_at TEXT NOT NULL,
                    PRIMARY KEY (session_uid, turn_uid)
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    session_uid TEXT PRIMARY KEY REFERENCES sessions(uid) ON DELETE CASCADE,
                    base_sequence INTEGER NOT NULL,
                    last_committed_turn_uid TEXT NOT NULL,
                    snapshot_schema_version INTEGER NOT NULL,
                    tau_runtime_version TEXT NOT NULL,
                    runtime_config_sha256 TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    canonical_size INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS a2a_tasks (
                    uid TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    context_id TEXT NOT NULL,
                    agent_uid TEXT NOT NULL,
                    agent_session_uid TEXT,
                    status TEXT NOT NULL,
                    status_message_json TEXT,
                    status_timestamp TEXT NOT NULL,
                    cancellation_requested INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL,
                    last_event_sequence INTEGER NOT NULL DEFAULT 0,
                    dispatch_uid TEXT,
                    current_attempt_uid TEXT,
                    failure_code TEXT NOT NULL DEFAULT '',
                    failure_category TEXT NOT NULL DEFAULT '',
                    failure_retryable INTEGER,
                    correlation_id TEXT NOT NULL DEFAULT '',
                    recovery_owner TEXT NOT NULL DEFAULT '',
                    recovery_count INTEGER NOT NULL DEFAULT 0,
                    terminal_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS a2a_tasks_context_idx
                    ON a2a_tasks(context_id, created_at);
                CREATE TABLE IF NOT EXISTS a2a_task_messages (
                    task_uid TEXT NOT NULL REFERENCES a2a_tasks(uid) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    message_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    message_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (task_uid, sequence),
                    UNIQUE (task_uid, message_id)
                );
                CREATE TABLE IF NOT EXISTS a2a_task_outputs (
                    task_uid TEXT NOT NULL REFERENCES a2a_tasks(uid) ON DELETE CASCADE,
                    artifact_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    parts_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    finalized INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (task_uid, artifact_id)
                );
                CREATE TABLE IF NOT EXISTS a2a_task_attempts (
                    uid TEXT PRIMARY KEY,
                    task_uid TEXT NOT NULL REFERENCES a2a_tasks(uid) ON DELETE CASCADE,
                    dispatch_uid TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    holder_id TEXT NOT NULL,
                    outcome_category TEXT NOT NULL DEFAULT '',
                    failure_code TEXT NOT NULL DEFAULT '',
                    failure_detail TEXT NOT NULL DEFAULT '',
                    retryable INTEGER,
                    correlation_id TEXT NOT NULL DEFAULT '',
                    turn_uid TEXT,
                    entry_start_sequence INTEGER,
                    entry_end_sequence INTEGER,
                    turn_resolution TEXT,
                    settlement_fingerprint TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (task_uid, attempt_number)
                );
                CREATE TABLE IF NOT EXISTS a2a_task_events (
                    task_uid TEXT NOT NULL REFERENCES a2a_tasks(uid) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    uid TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT '',
                    message_uid TEXT,
                    output_uid TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (task_uid, sequence)
                );
                """
            )
            self._ensure_schema_column(
                connection, "a2a_tasks", "failure_code", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_schema_column(
                connection, "a2a_tasks", "failure_category", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_schema_column(connection, "a2a_tasks", "failure_retryable", "INTEGER")
            self._ensure_schema_column(
                connection, "a2a_tasks", "correlation_id", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_schema_column(
                connection, "a2a_tasks", "recovery_owner", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_schema_column(
                connection, "a2a_tasks", "recovery_count", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_schema_column(connection, "a2a_tasks", "terminal_at", "TEXT")
            self._ensure_schema_column(
                connection,
                "a2a_task_attempts",
                "outcome_category",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_schema_column(
                connection,
                "a2a_task_attempts",
                "failure_code",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_schema_column(
                connection,
                "a2a_task_attempts",
                "failure_detail",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_schema_column(connection, "a2a_task_attempts", "retryable", "INTEGER")
            self._ensure_schema_column(
                connection,
                "a2a_task_attempts",
                "correlation_id",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_schema_column(connection, "sessions", "active_task_attempt_uid", "TEXT")
            self._ensure_schema_column(connection, "entries", "turn_uid", "TEXT")
            self._ensure_schema_column(connection, "a2a_task_attempts", "turn_uid", "TEXT")
            self._ensure_schema_column(
                connection, "a2a_task_attempts", "entry_start_sequence", "INTEGER"
            )
            self._ensure_schema_column(
                connection, "a2a_task_attempts", "entry_end_sequence", "INTEGER"
            )
            self._ensure_schema_column(connection, "a2a_task_attempts", "turn_resolution", "TEXT")
            self._ensure_schema_column(
                connection, "a2a_task_attempts", "settlement_fingerprint", "TEXT"
            )
            connection.execute(
                "INSERT OR REPLACE INTO schema_metadata(key, value) VALUES('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )

    @staticmethod
    def _ensure_schema_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        columns = {
            str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    async def _run(self, operation: Callable[[], _T]) -> _T:
        await self._ensure_initialized()
        async with self._database_lock:
            return await asyncio.to_thread(operation)

    def _selection(self) -> tuple[str, str, str | None]:
        provider = self.settings.local_provider
        model = self.settings.local_model
        if not provider or not model:
            raise BackendConflictError("Local provider/model selection is not configured")
        return provider, model, self.settings.local_thinking

    def _runtime_config_sha256(self, selection: tuple[str, str, str | None] | None = None) -> str:
        provider, model, thinking = selection or self._selection()
        payload = json.dumps(
            {"provider": provider, "model": model, "thinking": thinking},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    async def _hydrate_evidence(
        self, *, holder_id: str, selection: tuple[str, str, str | None] | None = None
    ) -> ProviderExecutionEvidence:
        provider, model, thinking = selection or self._selection()
        evidence = await self._services.hydrate_local_provider_credential(
            provider,
            model=model,
            thinking_level=thinking,
            holder_id=holder_id,
        )
        if (
            evidence.provider_control.provider != provider
            or evidence.provider_control.model.model != model
        ):
            raise BackendConflictError(
                "Main Sequence provider evidence does not match the local selection"
            )
        return evidence

    async def prefetch_provider(self) -> None:
        """Prove local provider authorization during readiness without creating a session."""

        await self._hydrate_evidence(holder_id=f"local-startup-{self.settings.workspace_digest}")

    @staticmethod
    def _credential_envelope(evidence: ProviderExecutionEvidence) -> dict[str, Any]:
        credential = evidence.credential
        raw: dict[str, Any] = {
            "type": credential.credential_kind,
            "api": credential.api,
            "api_key": (
                credential.api_key.get_secret_value() if credential.api_key is not None else None
            ),
            "access_token": (
                credential.access_token.get_secret_value()
                if credential.access_token is not None
                else None
            ),
            "expires_at": (
                credential.expires_at.isoformat() if credential.expires_at is not None else None
            ),
            "account_id": credential.account_id,
            "base_url": credential.base_url,
            "headers": credential.headers,
        }
        return {
            "credentials": {
                credential.provider: {
                    "credential_kind": credential.credential_kind,
                    "credential": {key: value for key, value in raw.items() if value is not None},
                    "version": credential.metadata.get("version"),
                    "credential_hash": credential.metadata.get("credential_hash"),
                }
            }
        }

    async def hydrate_provider_credential(
        self,
        provider: str,
        *,
        model: str,
        session_uid: str | None = None,
        agent_uid: str | None = None,
        holder_id: str,
    ) -> ProviderExecutionEvidence:
        del agent_uid
        selected_provider, selected_model, selected_thinking = self._selection()
        if session_uid is not None:
            session = await self.get_session(session_uid)
            selected_provider = session.active_provider or ""
            selected_model = session.active_model or ""
            selected_thinking = session.active_thinking
        if provider != selected_provider or model != selected_model:
            raise BackendConflictError(
                "Local provider refresh does not match the session selection"
            )
        if session_uid is not None:
            canonical = self.settings.local_session_uid(session_uid)
            if canonical != session_uid:
                raise SessionNotFoundError("Local session identifier is invalid")
        return await self._hydrate_evidence(
            holder_id=holder_id,
            selection=(selected_provider, selected_model, selected_thinking),
        )

    async def get_session(self, session_uid: str) -> AgentSession:
        def operation() -> AgentSession:
            with closing(self._connect()) as connection, connection:
                row = connection.execute(
                    "SELECT * FROM sessions WHERE uid = ?",
                    (session_uid,),
                ).fetchone()
                if row is None:
                    raise SessionNotFoundError(f"Local session not found: {session_uid}")
                return self._session_from_row(row)

        return await self._run(operation)

    async def update_session_config(
        self,
        session_uid: str,
        *,
        provider: str,
        model: str,
        thinking_level: str | None,
    ) -> AgentSession:
        if not provider or not model:
            raise BackendConflictError("A provider and model are required")
        selection = (provider, model, thinking_level)
        await self._hydrate_evidence(
            holder_id=f"local-selection-{self.settings.workspace_digest}",
            selection=selection,
        )

        def operation() -> None:
            with closing(self._connect()) as connection, connection:
                row = connection.execute(
                    "SELECT runtime_activity FROM sessions WHERE uid = ?", (session_uid,)
                ).fetchone()
                if row is None:
                    raise SessionNotFoundError(f"Local session not found: {session_uid}")
                if row["runtime_activity"] == "working":
                    raise BackendConflictError("Cannot change a model while the session is working")
                connection.execute(
                    "UPDATE sessions SET provider = ?, model = ?, thinking = ?, "
                    "runtime_config_sha256 = ?, updated_at = ? WHERE uid = ?",
                    (
                        *selection,
                        self._runtime_config_sha256(selection),
                        _iso(_utcnow()),
                        session_uid,
                    ),
                )

        await self._run(operation)
        return await self.get_session(session_uid)

    async def get_agent_card(self, session_uid: str) -> AgentCardEnvelope:
        context_id = self.settings.local_session_uid(session_uid)
        agent_uid = f"local-agent-{self.settings.workspace_digest}"
        return AgentCardEnvelope(
            agent_session_uid=context_id,
            agent_uid=agent_uid,
            agent_card={
                "name": "Local Main Sequence TAU Agent",
                "description": "Workspace-local TAU runtime for A2A development.",
                "version": "local",
                "capabilities": {
                    "streaming": True,
                    "pushNotifications": False,
                    "extensions": [
                        {
                            "uri": _LOCAL_A2A_RESPONSE_KIND_EXTENSION_URI,
                            "description": (
                                "Select whether message:send returns a completed message "
                                "or an asynchronous task."
                            ),
                            "required": False,
                            "params": {
                                "supportedResponseKinds": ["message", "task"],
                                "defaultResponseKind": "message",
                            },
                        }
                    ],
                },
            },
        )

    @staticmethod
    def _json_dump(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _json_load(value: object, default: _T) -> _T:
        if value is None or value == "":
            return default
        return cast(_T, json.loads(str(value)))

    @staticmethod
    def _task_row(connection: sqlite3.Connection, task_uid: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM a2a_tasks WHERE uid = ?",
            (task_uid,),
        ).fetchone()
        if row is None:
            raise SessionNotFoundError(f"A2A task not found: {task_uid}")
        return cast(sqlite3.Row, row)

    def _task_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        history_length: int | None = None,
    ) -> AgentTask:
        message = connection.execute(
            """
            SELECT message_json FROM a2a_task_messages
            WHERE task_uid = ? ORDER BY sequence DESC LIMIT 1
            """,
            (str(row["uid"]),),
        ).fetchone()
        output_rows = connection.execute(
            """
            SELECT * FROM a2a_task_outputs
            WHERE task_uid = ? ORDER BY created_at, artifact_id
            """,
            (str(row["uid"]),),
        ).fetchall()
        outputs = [
            {
                "uid": str(output["artifact_id"]),
                "artifact_id": str(output["artifact_id"]),
                "name": str(output["name"]),
                "parts": self._json_load(output["parts_json"], []),
                "metadata": self._json_load(output["metadata_json"], {}),
                "revision": int(output["revision"]),
                "finalized": bool(output["finalized"]),
            }
            for output in output_rows
        ]
        history = (
            self._task_messages(connection, str(row["uid"]), limit=history_length)
            if history_length is not None and history_length > 0
            else ([] if history_length is not None else None)
        )
        return AgentTask(
            uid=str(row["uid"]),
            task_id=str(row["task_id"]),
            context_id=str(row["context_id"]),
            agent_uid=str(row["agent_uid"]),
            agent_session_uid=(
                str(row["agent_session_uid"]) if row["agent_session_uid"] is not None else None
            ),
            status=cast(AgentTaskStatus, str(row["status"])),
            status_message=self._json_load(row["status_message_json"], None),
            status_timestamp=_from_iso(str(row["status_timestamp"])),
            cancellation_requested=bool(row["cancellation_requested"]),
            latest_message=(
                self._json_load(message["message_json"], {}) if message is not None else None
            ),
            history=history,
            outputs=outputs,
            metadata=self._json_load(row["metadata_json"], {}),
            last_event_sequence=int(row["last_event_sequence"]),
            failure_code=str(row["failure_code"]),
            failure_category=str(row["failure_category"]),
            failure_retryable=(
                bool(row["failure_retryable"]) if row["failure_retryable"] is not None else None
            ),
            correlation_id=str(row["correlation_id"]),
            recovery_owner=str(row["recovery_owner"]),
            recovery_count=int(row["recovery_count"]),
        )

    def _task_messages(
        self,
        connection: sqlite3.Connection,
        task_uid: str,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        bounded = max(0, min(int(limit), 100))
        if bounded == 0:
            return []
        rows = connection.execute(
            """
            SELECT message_json FROM (
                SELECT sequence, message_json FROM a2a_task_messages
                WHERE task_uid = ? ORDER BY sequence DESC LIMIT ? OFFSET ?
            ) ORDER BY sequence
            """,
            (task_uid, bounded, max(0, int(offset))),
        ).fetchall()
        return [self._json_load(item["message_json"], {}) for item in rows]

    def _task_message_tails(
        self,
        connection: sqlite3.Connection,
        task_uids: list[str],
        *,
        limit: int,
    ) -> dict[str, list[dict[str, Any]]]:
        """Load one bounded Message tail for every Task in a single storage query."""

        bounded = max(0, min(int(limit), 100))
        if bounded == 0 or not task_uids:
            return {task_uid: [] for task_uid in task_uids}
        placeholders = ",".join("?" for _ in task_uids)
        rows = connection.execute(
            f"""
            SELECT task_uid, sequence, message_json FROM (
                SELECT task_uid, sequence, message_json,
                    ROW_NUMBER() OVER (
                        PARTITION BY task_uid ORDER BY sequence DESC
                    ) AS tail_position
                FROM a2a_task_messages
                WHERE task_uid IN ({placeholders})
            )
            WHERE tail_position <= ?
            ORDER BY task_uid, sequence
            """,
            (*task_uids, bounded),
        ).fetchall()
        histories: dict[str, list[dict[str, Any]]] = {task_uid: [] for task_uid in task_uids}
        for row in rows:
            histories[str(row["task_uid"])].append(self._json_load(row["message_json"], {}))
        return histories

    def _append_task_event(
        self,
        connection: sqlite3.Connection,
        task_uid: str,
        *,
        event_type: str,
        status: str = "",
        message_uid: str | None = None,
        output_uid: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> int:
        row = self._task_row(connection, task_uid)
        sequence = int(row["last_event_sequence"]) + 1
        now = _utcnow()
        connection.execute(
            """
            INSERT INTO a2a_task_events(
                task_uid, sequence, uid, event_type, status,
                message_uid, output_uid, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_uid,
                sequence,
                str(uuid.uuid4()),
                event_type,
                status,
                message_uid,
                output_uid,
                self._json_dump(dict(payload or {})),
                _iso(now),
            ),
        )
        connection.execute(
            "UPDATE a2a_tasks SET last_event_sequence = ?, updated_at = ? WHERE uid = ?",
            (sequence, _iso(now), task_uid),
        )
        return sequence

    def _append_task_message(
        self,
        connection: sqlite3.Connection,
        task_uid: str,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(message)
        message_id = str(normalized.get("messageId") or "").strip()
        if not message_id:
            raise BackendConflictError("Local A2A Message.messageId is required")
        if normalized.get("role") not in {
            A2AMessageDirection.REQUESTER.value,
            A2AMessageDirection.RESPONDER.value,
        }:
            raise BackendConflictError("Local A2A Message has an invalid binding role")
        try:
            message_to_protocol(normalized)
        except ValueError as error:
            raise BackendConflictError(str(error)) from error
        existing = connection.execute(
            """
            SELECT message_json FROM a2a_task_messages
            WHERE task_uid = ? AND message_id = ?
            """,
            (task_uid, message_id),
        ).fetchone()
        if existing is not None:
            persisted: dict[str, Any] = self._json_load(existing["message_json"], {})
            if persisted != normalized:
                raise BackendConflictError("Local A2A message idempotency conflict")
            return persisted
        sequence_row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
            FROM a2a_task_messages WHERE task_uid = ?
            """,
            (task_uid,),
        ).fetchone()
        assert sequence_row is not None
        connection.execute(
            """
            INSERT INTO a2a_task_messages(
                task_uid, sequence, message_id, role, message_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                task_uid,
                int(sequence_row["next_sequence"]),
                message_id,
                str(normalized.get("role") or ""),
                self._json_dump(normalized),
                _iso(_utcnow()),
            ),
        )
        self._append_task_event(
            connection,
            task_uid,
            event_type="message_added",
            message_uid=message_id,
            payload={"messageId": message_id},
        )
        return normalized

    def _session_from_row(self, row: sqlite3.Row) -> AgentSession:
        return AgentSession.model_validate(
            {
                "uid": str(row["uid"]),
                "agent_uid": f"local-agent-{self.settings.workspace_digest}",
                "harness": "tau",
                "harness_protocol": "tau-session-v1",
                "harness_version": "local",
                "active_provider": str(row["provider"]),
                "active_model": str(row["model"]),
                "active_thinking": row["thinking"],
                "status": "local",
                "runtime_capabilities": LOCAL_RUNTIME_CAPABILITIES,
                "runtime_config_sha256": str(row["runtime_config_sha256"]),
            }
        )

    def _runtime_state_from_row(
        self,
        row: sqlite3.Row,
        *,
        applied: bool | None = None,
    ) -> RuntimeState:
        activity = cast(AgentRuntimeActivity, str(row["runtime_activity"]))
        cancel_requested = bool(row["cancel_requested"])
        return RuntimeState(
            harness="tau",
            harness_protocol="tau-session-v1",
            harness_version="local",
            active_provider=str(row["provider"]),
            active_model=str(row["model"]),
            active_thinking=row["thinking"],
            status="local",
            working=activity in {"working", "persisting"},
            runtime_activity=activity,
            active_turn_uid=row["active_turn_uid"],
            active_task_attempt_uid=row["active_task_attempt_uid"],
            activity_revision=int(row["activity_revision"]),
            activity_sequence=int(row["activity_sequence"]),
            activity_updated_at=_from_iso(str(row["updated_at"])),
            last_committed_turn_uid=row["last_committed_turn_uid"],
            applied=applied,
            runtime_capabilities=LOCAL_RUNTIME_CAPABILITIES,
            cancel_state="requested" if cancel_requested else "not_running",
            cancel_requested=cancel_requested,
            cancellation_id=row["cancellation_id"],
        )

    @staticmethod
    def _lease_from_rows(session: sqlite3.Row, lease: sqlite3.Row) -> RuntimeLease:
        return RuntimeLease(
            lease_token=str(lease["lease_token"]),
            holder_id=str(lease["holder_id"]),
            lease_expires_at=_from_iso(str(lease["expires_at"])),
            checkpoint_version=int(lease["checkpoint_version"]),
            cancel_requested=bool(session["cancel_requested"]),
            runtime_activity=cast(
                AgentRuntimeActivity,
                str(session["runtime_activity"]),
            ),
            active_turn_uid=session["active_turn_uid"],
            active_task_attempt_uid=session["active_task_attempt_uid"],
            activity_revision=int(session["activity_revision"]),
            activity_sequence=int(session["activity_sequence"]),
            activity_updated_at=_from_iso(str(session["updated_at"])),
        )

    def _require_lease(
        self,
        connection: sqlite3.Connection,
        session_uid: str,
        *,
        lease_token: str,
        holder_id: str | None = None,
    ) -> sqlite3.Row:
        lease = connection.execute(
            "SELECT * FROM leases WHERE session_uid = ?",
            (session_uid,),
        ).fetchone()
        if (
            lease is None
            or str(lease["lease_token"]) != lease_token
            or (holder_id is not None and str(lease["holder_id"]) != holder_id)
            or _from_iso(str(lease["expires_at"])) <= _utcnow()
        ):
            raise BackendConflictError("Local runtime lease is missing, expired, or invalid")
        return cast(sqlite3.Row, lease)

    async def bootstrap_tau_runtime(
        self,
        session_uid: str,
        request: TauRuntimeBootstrapRequest,
    ) -> TauRuntimeBootstrap:
        def stored_selection() -> tuple[str, str, str | None]:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT provider, model, thinking FROM sessions WHERE uid = ?", (session_uid,)
                ).fetchone()
                if row is None:
                    return self._selection()
                return str(row["provider"]), str(row["model"]), row["thinking"]

        selection = await self._run(stored_selection)
        provider, model, thinking = selection
        evidence = await self._hydrate_evidence(holder_id=request.holder_id, selection=selection)

        def operation() -> TauRuntimeBootstrap:
            now = _utcnow()
            expires = now + timedelta(seconds=request.ttl_seconds)
            runtime_config_sha256 = self._runtime_config_sha256(selection)
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM sessions WHERE uid = ?",
                    (session_uid,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO sessions(
                            uid, provider, model, thinking, runtime_config_sha256,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_uid,
                            provider,
                            model,
                            thinking,
                            runtime_config_sha256,
                            _iso(now),
                            _iso(now),
                        ),
                    )
                    row = connection.execute(
                        "SELECT * FROM sessions WHERE uid = ?",
                        (session_uid,),
                    ).fetchone()
                elif (
                    str(row["provider"]) != provider
                    or str(row["model"]) != model
                    or row["thinking"] != thinking
                    or str(row["runtime_config_sha256"]) != runtime_config_sha256
                ):
                    raise BackendConflictError(
                        "Existing local session uses a different provider/model selection"
                    )
                assert row is not None
                current_lease = connection.execute(
                    "SELECT * FROM leases WHERE session_uid = ?",
                    (session_uid,),
                ).fetchone()
                active_task_attempt_uid = str(row["active_task_attempt_uid"] or "")
                if active_task_attempt_uid and (
                    current_lease is None
                    or _from_iso(str(current_lease["expires_at"])) <= now
                    or str(current_lease["holder_id"]) != request.holder_id
                ):
                    raise BackendConflictError(
                        "Local Task turn recovery is pending before lease replacement"
                    )
                if (
                    current_lease is not None
                    and _from_iso(str(current_lease["expires_at"])) > now
                    and str(current_lease["holder_id"]) != request.holder_id
                ):
                    raise BackendConflictError("Local session is owned by another running process")
                checkpoint_version = (
                    int(current_lease["checkpoint_version"]) + 1 if current_lease is not None else 1
                )
                lease_token = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO leases(
                        session_uid, lease_token, holder_id, expires_at, checkpoint_version
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(session_uid) DO UPDATE SET
                        lease_token=excluded.lease_token,
                        holder_id=excluded.holder_id,
                        expires_at=excluded.expires_at,
                        checkpoint_version=excluded.checkpoint_version
                    """,
                    (
                        session_uid,
                        lease_token,
                        request.holder_id,
                        _iso(expires),
                        checkpoint_version,
                    ),
                )
                snapshot_row = connection.execute(
                    "SELECT * FROM snapshots WHERE session_uid = ?",
                    (session_uid,),
                ).fetchone()
                resume_snapshot = self._snapshot_from_row(snapshot_row) if snapshot_row else None
                after_sequence = resume_snapshot.base_sequence if resume_snapshot else 0
                history = self._entries_from_connection(
                    connection,
                    session_uid,
                    after_sequence=after_sequence,
                )
                refreshed = connection.execute(
                    "SELECT * FROM sessions WHERE uid = ?",
                    (session_uid,),
                ).fetchone()
                lease_row = connection.execute(
                    "SELECT * FROM leases WHERE session_uid = ?",
                    (session_uid,),
                ).fetchone()
                assert refreshed is not None and lease_row is not None
                return TauRuntimeBootstrap(
                    session=self._session_from_row(refreshed),
                    lease=self._lease_from_rows(refreshed, lease_row),
                    runtime_state=self._runtime_state_from_row(refreshed),
                    history=history,
                    resume_snapshot=resume_snapshot,
                    provider_credentials=self._credential_envelope(evidence),
                    provider_control=evidence.provider_control,
                    runtime_capabilities=LOCAL_RUNTIME_CAPABILITIES,
                )

        return await self._run(operation)

    def _entries_from_connection(
        self,
        connection: sqlite3.Connection,
        session_uid: str,
        *,
        after_sequence: int = 0,
    ) -> SessionEntryList:
        session = connection.execute(
            "SELECT next_sequence FROM sessions WHERE uid = ?",
            (session_uid,),
        ).fetchone()
        if session is None:
            raise SessionNotFoundError(f"Local session not found: {session_uid}")
        rows = connection.execute(
            """
            SELECT sequence, entry_type, entry_json, idempotency_key, turn_uid
            FROM entries
            WHERE session_uid = ? AND sequence >= ?
            ORDER BY sequence
            """,
            (session_uid, after_sequence),
        ).fetchall()
        return SessionEntryList(
            entries=[
                SessionEntryRecord(
                    sequence=int(row["sequence"]),
                    entry_type=cast(TauEntryType, str(row["entry_type"])),
                    entry_json=json.loads(str(row["entry_json"])),
                    idempotency_key=str(row["idempotency_key"]),
                    turn_uid=(str(row["turn_uid"]) if row["turn_uid"] else None),
                )
                for row in rows
            ],
            next_sequence=int(session["next_sequence"]),
        )

    async def get_entries(self, session_uid: str) -> SessionEntryList:
        def operation() -> SessionEntryList:
            with closing(self._connect()) as connection, connection:
                return self._entries_from_connection(connection, session_uid)

        return await self._run(operation)

    async def append_entry(
        self,
        session_uid: str,
        request: SessionEntryAppendRequest,
    ) -> SessionEntryRecord:
        from .models import SessionEntryBatchItem

        result = await self.append_entries(
            session_uid,
            SessionEntryBatchAppendRequest(
                lease_token=request.lease_token,
                expected_sequence=request.expected_sequence,
                entries=[
                    SessionEntryBatchItem(
                        idempotency_key=request.idempotency_key,
                        entry=request.entry,
                    )
                ],
            ),
        )
        return result.entries[0]

    async def append_entries(
        self,
        session_uid: str,
        request: SessionEntryBatchAppendRequest,
    ) -> SessionEntryBatchAppendResponse:
        def operation() -> SessionEntryBatchAppendResponse:
            now = _utcnow()
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                self._require_lease(
                    connection,
                    session_uid,
                    lease_token=request.lease_token,
                    holder_id=request.holder_id if request.turn is not None else None,
                )
                session = connection.execute(
                    "SELECT * FROM sessions WHERE uid = ?",
                    (session_uid,),
                ).fetchone()
                if session is None:
                    raise SessionNotFoundError(f"Local session not found: {session_uid}")
                active_task_attempt_uid = str(session["active_task_attempt_uid"] or "")
                active_turn_uid = str(session["active_turn_uid"] or "")
                if active_task_attempt_uid:
                    attempt = connection.execute(
                        "SELECT * FROM a2a_task_attempts WHERE uid = ?",
                        (active_task_attempt_uid,),
                    ).fetchone()
                    if attempt is None or str(attempt["turn_resolution"] or "") != "pending":
                        raise BackendConflictError("Local Task turn reservation is inconsistent")
                    if request.turn is None or request.turn.turn_uid != str(attempt["turn_uid"]):
                        raise BackendConflictError(
                            "Tau entries must use the active Task attempt turn"
                        )
                elif active_turn_uid and (
                    request.turn is None or request.turn.turn_uid != active_turn_uid
                ):
                    raise BackendConflictError("Tau entries must use the active turn")
                if request.turn is not None:
                    resolved_attempt = connection.execute(
                        "SELECT turn_resolution FROM a2a_task_attempts WHERE turn_uid = ?",
                        (request.turn.turn_uid,),
                    ).fetchone()
                    if resolved_attempt is not None and str(
                        resolved_attempt["turn_resolution"] or ""
                    ) in {"committed", "abandoned"}:
                        exact_commit_replay = (
                            str(resolved_attempt["turn_resolution"]) == "committed"
                            and request.turn.phase == "committed"
                            and not request.entries
                        )
                        if not exact_commit_replay:
                            raise BackendConflictError("Resolved Task turn cannot accept writes")
                current_sequence = int(session["next_sequence"])
                records: list[SessionEntryRecord] = []
                replayed = False
                if request.expected_sequence != current_sequence:
                    replayed = True
                    for offset, item in enumerate(request.entries):
                        expected_sequence = request.expected_sequence + offset
                        row = connection.execute(
                            """
                            SELECT sequence, entry_type, entry_json, idempotency_key, turn_uid
                            FROM entries WHERE session_uid = ? AND idempotency_key = ?
                            """,
                            (session_uid, item.idempotency_key),
                        ).fetchone()
                        if row is None:
                            raise BackendConflictError("Local Tau entry sequence conflict")
                        entry_json = json.loads(str(row["entry_json"]))
                        if int(row["sequence"]) != expected_sequence or entry_json != item.entry:
                            raise BackendConflictError("Local Tau idempotency replay diverged")
                        records.append(
                            SessionEntryRecord(
                                sequence=int(row["sequence"]),
                                entry_type=cast(
                                    TauEntryType,
                                    str(row["entry_type"]),
                                ),
                                entry_json=entry_json,
                                idempotency_key=str(row["idempotency_key"]),
                                turn_uid=(str(row["turn_uid"]) if row["turn_uid"] else None),
                            )
                        )
                else:
                    for offset, item in enumerate(request.entries):
                        sequence = request.expected_sequence + offset
                        entry_type = cast(
                            TauEntryType,
                            str(item.entry.get("type") or "custom"),
                        )
                        canonical = json.dumps(
                            item.entry,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        try:
                            connection.execute(
                                """
                                INSERT INTO entries(
                                    session_uid, sequence, entry_type, entry_json,
                                    idempotency_key, turn_uid
                                ) VALUES (?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    session_uid,
                                    sequence,
                                    entry_type,
                                    canonical,
                                    item.idempotency_key,
                                    request.turn.turn_uid if request.turn is not None else None,
                                ),
                            )
                        except sqlite3.IntegrityError as error:
                            raise BackendConflictError(
                                "Local Tau entry idempotency conflict"
                            ) from error
                        records.append(
                            SessionEntryRecord(
                                sequence=sequence,
                                entry_type=entry_type,
                                entry_json=item.entry,
                                idempotency_key=item.idempotency_key,
                                turn_uid=(
                                    request.turn.turn_uid if request.turn is not None else None
                                ),
                            )
                        )
                    current_sequence += len(request.entries)
                    connection.execute(
                        "UPDATE sessions SET next_sequence = ?, updated_at = ? WHERE uid = ?",
                        (current_sequence, _iso(now), session_uid),
                    )

                turn_commit: TauTurnCommit | None = None
                if request.turn is not None:
                    if request.turn.phase == "committed":
                        existing = connection.execute(
                            """
                            SELECT * FROM turn_commits
                            WHERE session_uid = ? AND turn_uid = ?
                            """,
                            (session_uid, request.turn.turn_uid),
                        ).fetchone()
                        if (
                            existing is not None
                            and int(existing["next_sequence"]) != current_sequence
                        ):
                            raise BackendConflictError("Local Tau turn commit diverged")
                        committed_at = _from_iso(str(existing["committed_at"])) if existing else now
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO turn_commits(
                                session_uid, turn_uid, next_sequence, committed_at
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (
                                session_uid,
                                request.turn.turn_uid,
                                current_sequence,
                                _iso(committed_at),
                            ),
                        )
                        connection.execute(
                            """
                            UPDATE sessions SET
                                runtime_activity='idle', active_turn_uid=NULL,
                                active_task_attempt_uid=NULL,
                                activity_sequence=?, last_committed_turn_uid=?,
                                cancel_requested=0, cancellation_id=NULL, updated_at=?
                            WHERE uid=?
                            """,
                            (
                                request.turn.activity_sequence,
                                request.turn.turn_uid,
                                _iso(now),
                                session_uid,
                            ),
                        )
                        turn_commit = TauTurnCommit(
                            turn_uid=request.turn.turn_uid,
                            next_sequence=current_sequence,
                            committed_at=committed_at,
                        )
                        if active_task_attempt_uid:
                            connection.execute(
                                """
                                UPDATE a2a_task_attempts SET entry_end_sequence=?,
                                    turn_resolution='committed', updated_at=?
                                WHERE uid=? AND turn_uid=? AND turn_resolution='pending'
                                """,
                                (
                                    current_sequence,
                                    _iso(now),
                                    active_task_attempt_uid,
                                    request.turn.turn_uid,
                                ),
                            )
                    else:
                        connection.execute(
                            """
                            UPDATE sessions SET
                                runtime_activity='working', active_turn_uid=?,
                                activity_sequence=?, updated_at=?
                            WHERE uid=?
                            """,
                            (
                                request.turn.turn_uid,
                                request.turn.activity_sequence,
                                _iso(now),
                                session_uid,
                            ),
                        )
                refreshed = connection.execute(
                    "SELECT * FROM sessions WHERE uid = ?",
                    (session_uid,),
                ).fetchone()
                assert refreshed is not None
                return SessionEntryBatchAppendResponse(
                    entries=records,
                    next_sequence=current_sequence,
                    created_count=0 if replayed else len(records),
                    replayed=replayed,
                    turn_commit=turn_commit,
                    runtime_state=self._runtime_state_from_row(refreshed),
                )

        return await self._run(operation)

    async def acquire_runtime_lease(
        self,
        session_uid: str,
        request: RuntimeLeaseRequest,
    ) -> RuntimeLease:
        bootstrap = await self.bootstrap_tau_runtime(
            session_uid,
            TauRuntimeBootstrapRequest(
                holder_id=request.holder_id,
                ttl_seconds=request.ttl_seconds,
                bootstrap_request_uid=uuid.uuid4().hex,
                tau_runtime_version="local",
            ),
        )
        return bootstrap.lease

    async def renew_runtime_lease(
        self,
        session_uid: str,
        request: RuntimeLeaseRenewRequest,
    ) -> RuntimeLease:
        def operation() -> RuntimeLease:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                lease = self._require_lease(
                    connection,
                    session_uid,
                    lease_token=request.lease_token,
                    holder_id=request.holder_id,
                )
                expires = _utcnow() + timedelta(seconds=request.ttl_seconds)
                checkpoint = int(lease["checkpoint_version"]) + 1
                connection.execute(
                    """
                    UPDATE leases SET expires_at=?, checkpoint_version=?
                    WHERE session_uid=?
                    """,
                    (_iso(expires), checkpoint, session_uid),
                )
                session = connection.execute(
                    "SELECT * FROM sessions WHERE uid=?",
                    (session_uid,),
                ).fetchone()
                refreshed = connection.execute(
                    "SELECT * FROM leases WHERE session_uid=?",
                    (session_uid,),
                ).fetchone()
                assert session is not None and refreshed is not None
                return self._lease_from_rows(session, refreshed)

        return await self._run(operation)

    async def release_runtime_lease(
        self,
        session_uid: str,
        request: RuntimeLeaseReleaseRequest,
    ) -> None:
        def operation() -> None:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                self._require_lease(
                    connection,
                    session_uid,
                    lease_token=request.lease_token,
                    holder_id=request.holder_id,
                )
                session = connection.execute(
                    "SELECT active_task_attempt_uid FROM sessions WHERE uid=?",
                    (session_uid,),
                ).fetchone()
                if session is not None and session["active_task_attempt_uid"]:
                    raise BackendConflictError(
                        "Local Task turn must be resolved before releasing its lease"
                    )
                connection.execute("DELETE FROM leases WHERE session_uid=?", (session_uid,))

        await self._run(operation)

    async def get_runtime_state(self, session_uid: str) -> RuntimeState:
        def operation() -> RuntimeState:
            with closing(self._connect()) as connection, connection:
                row = connection.execute(
                    "SELECT * FROM sessions WHERE uid=?",
                    (session_uid,),
                ).fetchone()
                if row is None:
                    raise SessionNotFoundError(f"Local session not found: {session_uid}")
                return self._runtime_state_from_row(row)

        return await self._run(operation)

    async def patch_runtime_state(
        self,
        session_uid: str,
        request: RuntimeStatePatch,
    ) -> RuntimeState:
        selected = self._selection()
        requested = (
            request.active_provider or selected[0],
            request.active_model or selected[1],
            request.active_thinking if request.active_thinking is not None else selected[2],
        )
        if requested != selected:
            raise BackendConflictError(
                "Local runtime selection cannot be changed through this route"
            )
        return await self.get_runtime_state(session_uid)

    async def patch_runtime_activity(
        self,
        session_uid: str,
        request: RuntimeActivityPatch,
    ) -> RuntimeState:
        def operation() -> RuntimeState:
            now = _utcnow()
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                self._require_lease(
                    connection,
                    session_uid,
                    lease_token=request.lease_token,
                    holder_id=request.holder_id,
                )
                row = connection.execute(
                    "SELECT * FROM sessions WHERE uid=?",
                    (session_uid,),
                ).fetchone()
                if row is None:
                    raise SessionNotFoundError(f"Local session not found: {session_uid}")
                revision = int(row["activity_revision"])
                sequence = int(row["activity_sequence"])
                applied = True
                if request.expected_activity_revision is not None:
                    if request.expected_activity_revision != revision:
                        raise BackendConflictError("Local runtime activity revision conflict")
                    revision += 1
                else:
                    assert request.activity_sequence is not None
                    if request.activity_sequence <= sequence:
                        applied = False
                    else:
                        sequence = request.activity_sequence
                        revision += 1
                if applied:
                    active_task_attempt_uid = str(row["active_task_attempt_uid"] or "")
                    if active_task_attempt_uid and request.active_turn_uid != str(
                        row["active_turn_uid"] or ""
                    ):
                        raise BackendConflictError(
                            "Generic runtime activity cannot replace or clear a Task turn"
                        )
                    connection.execute(
                        """
                        UPDATE sessions SET runtime_activity=?, active_turn_uid=?,
                            activity_revision=?, activity_sequence=?, updated_at=?
                        WHERE uid=?
                        """,
                        (
                            request.runtime_activity,
                            request.active_turn_uid,
                            revision,
                            sequence,
                            _iso(now),
                            session_uid,
                        ),
                    )
                refreshed = connection.execute(
                    "SELECT * FROM sessions WHERE uid=?",
                    (session_uid,),
                ).fetchone()
                assert refreshed is not None
                return self._runtime_state_from_row(refreshed, applied=applied)

        return await self._run(operation)

    async def request_runtime_cancel(
        self,
        session_uid: str,
        *,
        reason: str = "user_requested",
        message: str = "",
        requested_by_holder_id: str = "",
    ) -> RuntimeState:
        del reason, message, requested_by_holder_id

        def operation() -> RuntimeState:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                cancellation_id = uuid.uuid4().hex
                cursor = connection.execute(
                    """
                    UPDATE sessions SET cancel_requested=1, cancellation_id=?, updated_at=?
                    WHERE uid=?
                    """,
                    (cancellation_id, _iso(_utcnow()), session_uid),
                )
                if cursor.rowcount != 1:
                    raise SessionNotFoundError(f"Local session not found: {session_uid}")
                row = connection.execute(
                    "SELECT * FROM sessions WHERE uid=?",
                    (session_uid,),
                ).fetchone()
                assert row is not None
                return self._runtime_state_from_row(row)

        return await self._run(operation)

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> TauResumeSnapshot:
        return TauResumeSnapshot(
            base_sequence=int(row["base_sequence"]),
            last_committed_turn_uid=str(row["last_committed_turn_uid"]),
            snapshot_schema_version=int(row["snapshot_schema_version"]),
            tau_runtime_version=str(row["tau_runtime_version"]),
            runtime_config_sha256=str(row["runtime_config_sha256"]),
            payload_sha256=str(row["payload_sha256"]),
            canonical_size=int(row["canonical_size"]),
            snapshot=json.loads(str(row["snapshot_json"])),
        )

    async def upload_tau_resume_snapshot(
        self,
        session_uid: str,
        request: TauResumeSnapshotUploadRequest,
    ) -> TauResumeSnapshotUploadResponse:
        def operation() -> TauResumeSnapshotUploadResponse:
            canonical = json.dumps(
                request.snapshot,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                self._require_lease(
                    connection,
                    session_uid,
                    lease_token=request.lease_token,
                    holder_id=request.holder_id,
                )
                session = connection.execute(
                    "SELECT * FROM sessions WHERE uid=?",
                    (session_uid,),
                ).fetchone()
                if session is None:
                    raise SessionNotFoundError(f"Local session not found: {session_uid}")
                if int(session["next_sequence"]) != request.base_sequence:
                    raise BackendConflictError("Local snapshot sequence is not current")
                existing = connection.execute(
                    "SELECT * FROM snapshots WHERE session_uid=?",
                    (session_uid,),
                ).fetchone()
                replayed = bool(
                    existing is not None
                    and int(existing["base_sequence"]) == request.base_sequence
                    and str(existing["payload_sha256"]) == request.payload_sha256
                )
                connection.execute(
                    """
                    INSERT INTO snapshots(
                        session_uid, base_sequence, last_committed_turn_uid,
                        snapshot_schema_version, tau_runtime_version,
                        runtime_config_sha256, payload_sha256, canonical_size, snapshot_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_uid) DO UPDATE SET
                        base_sequence=excluded.base_sequence,
                        last_committed_turn_uid=excluded.last_committed_turn_uid,
                        snapshot_schema_version=excluded.snapshot_schema_version,
                        tau_runtime_version=excluded.tau_runtime_version,
                        runtime_config_sha256=excluded.runtime_config_sha256,
                        payload_sha256=excluded.payload_sha256,
                        canonical_size=excluded.canonical_size,
                        snapshot_json=excluded.snapshot_json
                    """,
                    (
                        session_uid,
                        request.base_sequence,
                        request.last_committed_turn_uid,
                        request.snapshot_schema_version,
                        request.tau_runtime_version,
                        request.runtime_config_sha256,
                        request.payload_sha256,
                        len(canonical.encode("utf-8")),
                        canonical,
                    ),
                )
                return TauResumeSnapshotUploadResponse(
                    applied=not replayed,
                    replayed=replayed,
                    base_sequence=request.base_sequence,
                    last_committed_turn_uid=request.last_committed_turn_uid,
                    snapshot_schema_version=request.snapshot_schema_version,
                    tau_runtime_version=request.tau_runtime_version,
                    runtime_config_sha256=request.runtime_config_sha256,
                    payload_sha256=request.payload_sha256,
                    canonical_size=len(canonical.encode("utf-8")),
                )

        return await self._run(operation)

    async def create_task(self, payload: Mapping[str, Any]) -> AgentTaskCreateResult:
        task_id = str(payload.get("task_id") or "").strip()
        requested_context = str(payload.get("context_id") or "").strip()
        if not task_id or not requested_context:
            raise BackendConflictError("Local A2A task_id and context_id are required")
        context_id = self.settings.local_session_uid(requested_context)
        agent_uid = f"local-agent-{self.settings.workspace_digest}"

        def operation() -> AgentTaskCreateResult:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT * FROM a2a_tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                if existing is not None:
                    if str(existing["context_id"]) != context_id:
                        raise BackendConflictError(
                            "Local A2A task id already belongs to another context"
                        )
                    return AgentTaskCreateResult(
                        task=self._task_from_row(connection, existing),
                        created=False,
                    )
                now = _utcnow()
                task_uid = str(uuid.uuid4())
                dispatch_uid = str(uuid.uuid4())
                metadata = payload.get("metadata")
                connection.execute(
                    """
                    INSERT INTO a2a_tasks(
                        uid, task_id, context_id, agent_uid, agent_session_uid,
                        status, status_timestamp, metadata_json, dispatch_uid,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'submitted', ?, ?, ?, ?, ?)
                    """,
                    (
                        task_uid,
                        task_id,
                        context_id,
                        agent_uid,
                        context_id,
                        _iso(now),
                        self._json_dump(metadata if isinstance(metadata, Mapping) else {}),
                        dispatch_uid,
                        _iso(now),
                        _iso(now),
                    ),
                )
                initial_message = payload.get("initial_message")
                if isinstance(initial_message, Mapping):
                    self._append_task_message(connection, task_uid, initial_message)
                self._append_task_event(
                    connection,
                    task_uid,
                    event_type="status_changed",
                    status="submitted",
                )
                row = self._task_row(connection, task_uid)
                return AgentTaskCreateResult(
                    task=self._task_from_row(connection, row),
                    created=True,
                )

        return await self._run(operation)

    async def list_tasks(self, **filters: Any) -> list[AgentTask]:
        def operation() -> list[AgentTask]:
            clauses: list[str] = []
            values: list[str] = []
            context_id = str(filters.get("context_id") or "").strip()
            task_id = str(filters.get("task_id") or "").strip()
            history_value = filters.get("history_length")
            history_length = int(history_value) if history_value is not None else None
            if context_id:
                clauses.append("context_id = ?")
                values.append(self.settings.local_session_uid(context_id))
            if task_id:
                clauses.append("task_id = ?")
                values.append(task_id)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            with closing(self._connect()) as connection, connection:
                rows = connection.execute(
                    "SELECT * FROM a2a_tasks" + where + " ORDER BY created_at, uid",
                    values,
                ).fetchall()
                tasks = [self._task_from_row(connection, row) for row in rows]
                if history_length is None:
                    return tasks
                if history_length <= 0:
                    return [task.model_copy(update={"history": []}) for task in tasks]
                histories = self._task_message_tails(
                    connection,
                    [task.uid for task in tasks],
                    limit=history_length,
                )
                return [task.model_copy(update={"history": histories[task.uid]}) for task in tasks]

        return await self._run(operation)

    async def get_task(
        self,
        task_uid: str,
        *,
        history_length: int | None = None,
    ) -> AgentTask:
        def operation() -> AgentTask:
            with closing(self._connect()) as connection, connection:
                return self._task_from_row(
                    connection,
                    self._task_row(connection, task_uid),
                    history_length=history_length,
                )

        return await self._run(operation)

    async def list_task_messages(
        self,
        task_uid: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            with closing(self._connect()) as connection:
                self._task_row(connection, task_uid)
                return self._task_messages(
                    connection,
                    task_uid,
                    limit=limit,
                    offset=offset,
                )

        return await self._run(operation)

    async def list_task_dispatches(self, task_uid: str) -> list[AgentTaskDispatch]:
        def operation() -> list[AgentTaskDispatch]:
            with closing(self._connect()) as connection:
                task = self._task_row(connection, task_uid)
                dispatch_uid = str(task["dispatch_uid"] or "")
                if not dispatch_uid:
                    return []
                status = str(task["status"])
                state = (
                    "pending"
                    if status == "submitted"
                    else "claimed"
                    if status == "working"
                    else "settled"
                )
                return [
                    AgentTaskDispatch(
                        uid=dispatch_uid,
                        state=cast(Any, state),
                        current_attempt_uid=(
                            str(task["current_attempt_uid"])
                            if task["current_attempt_uid"]
                            else None
                        ),
                    )
                ]

        return await self._run(operation)

    async def get_task_snapshot(self, task_uid: str) -> AgentTaskSnapshot:
        task = await self.get_task(task_uid)
        return AgentTaskSnapshot(task=task, event_cursor=task.last_event_sequence)

    async def list_task_events(
        self,
        task_uid: str,
        *,
        after_sequence: int,
        limit: int = 100,
    ) -> AgentTaskEventPage:
        bounded_limit = max(1, min(int(limit), 500))

        def operation() -> AgentTaskEventPage:
            with closing(self._connect()) as connection, connection:
                self._task_row(connection, task_uid)
                rows = connection.execute(
                    """
                    SELECT * FROM a2a_task_events
                    WHERE task_uid = ? AND sequence > ?
                    ORDER BY sequence LIMIT ?
                    """,
                    (task_uid, max(0, int(after_sequence)), bounded_limit + 1),
                ).fetchall()
                page_rows = rows[:bounded_limit]
                events = [
                    AgentTaskEvent(
                        uid=str(row["uid"]),
                        sequence=int(row["sequence"]),
                        event_type=str(row["event_type"]),
                        status=str(row["status"]),
                        message_uid=(str(row["message_uid"]) if row["message_uid"] else None),
                        output_uid=(str(row["output_uid"]) if row["output_uid"] else None),
                        payload=self._json_load(row["payload_json"], {}),
                    )
                    for row in page_rows
                ]
                next_cursor = events[-1].sequence if events else max(0, int(after_sequence))
                return AgentTaskEventPage(
                    events=events,
                    next_cursor=next_cursor,
                    has_more=len(rows) > bounded_limit,
                )

        return await self._run(operation)

    def _validate_task_attempt(
        self,
        connection: sqlite3.Connection,
        task: sqlite3.Row,
        *,
        attempt_uid: str,
        holder_id: str,
        lease_token: str,
    ) -> sqlite3.Row:
        if bool(task["cancellation_requested"]) or str(task["status"]) == "canceled":
            raise BackendConflictError("Local A2A task cancellation was requested")
        if str(task["current_attempt_uid"] or "") != attempt_uid:
            raise BackendConflictError("Local A2A task attempt is not current")
        attempt = connection.execute(
            "SELECT * FROM a2a_task_attempts WHERE uid = ? AND task_uid = ?",
            (attempt_uid, str(task["uid"])),
        ).fetchone()
        if attempt is None or str(attempt["holder_id"]) != holder_id:
            raise BackendConflictError("Local A2A task attempt ownership is invalid")
        self._require_lease(
            connection,
            str(task["context_id"]),
            lease_token=lease_token,
            holder_id=holder_id,
        )
        return cast(sqlite3.Row, attempt)

    @staticmethod
    def _attempt_from_row(row: sqlite3.Row) -> AgentTaskExecutionAttempt:
        return AgentTaskExecutionAttempt(
            uid=str(row["uid"]),
            dispatch_uid=str(row["dispatch_uid"]),
            attempt_number=int(row["attempt_number"]),
            state=cast(Any, str(row["state"])),
            turn_uid=(str(row["turn_uid"]) if row["turn_uid"] else None),
            entry_start_sequence=(
                int(row["entry_start_sequence"])
                if row["entry_start_sequence"] is not None
                else None
            ),
            entry_end_sequence=(
                int(row["entry_end_sequence"]) if row["entry_end_sequence"] is not None else None
            ),
            turn_resolution=(
                cast(Any, str(row["turn_resolution"])) if row["turn_resolution"] else None
            ),
        )

    async def claim_task_dispatch(
        self,
        task_uid: str,
        *,
        holder_id: str,
        lease_token: str,
        dispatch_uid: str,
        executor_runtime_id: str = "",
        executor_instance_id: str = "",
    ) -> AgentTaskExecutionAttempt:
        del executor_runtime_id, executor_instance_id

        def operation() -> AgentTaskExecutionAttempt:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                task = self._task_row(connection, task_uid)
                self._require_lease(
                    connection,
                    str(task["context_id"]),
                    lease_token=lease_token,
                    holder_id=holder_id,
                )
                if str(task["status"]) != "submitted":
                    raise BackendConflictError("Local A2A task is not available for dispatch")
                count = connection.execute(
                    "SELECT COUNT(*) AS value FROM a2a_task_attempts WHERE task_uid = ?",
                    (task_uid,),
                ).fetchone()
                assert count is not None
                attempt_number = int(count["value"]) + 1
                attempt_uid = str(uuid.uuid4())
                resolved_dispatch_uid = str(dispatch_uid)
                now = _utcnow()
                connection.execute(
                    """
                    INSERT INTO a2a_task_attempts(
                        uid, task_uid, dispatch_uid, attempt_number, state,
                        holder_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'claimed', ?, ?, ?)
                    """,
                    (
                        attempt_uid,
                        task_uid,
                        resolved_dispatch_uid,
                        attempt_number,
                        holder_id,
                        _iso(now),
                        _iso(now),
                    ),
                )
                connection.execute(
                    """
                    UPDATE a2a_tasks SET status='working', status_timestamp=?,
                        dispatch_uid=?, current_attempt_uid=?, recovery_owner='', updated_at=?
                    WHERE uid=?
                    """,
                    (
                        _iso(now),
                        resolved_dispatch_uid,
                        attempt_uid,
                        _iso(now),
                        task_uid,
                    ),
                )
                self._append_task_event(
                    connection,
                    task_uid,
                    event_type="status_changed",
                    status="working",
                )
                attempt = connection.execute(
                    "SELECT * FROM a2a_task_attempts WHERE uid=?", (attempt_uid,)
                ).fetchone()
                assert attempt is not None
                return self._attempt_from_row(attempt)

        return await self._run(operation)

    def _terminalize_recovery_failure(
        self,
        connection: sqlite3.Connection,
        task: sqlite3.Row,
        *,
        recovery_owner: str,
        failure_code: str,
        category: str,
        text: str,
        ambiguous: bool,
    ) -> None:
        task_uid = str(task["uid"])
        attempt_uid = str(task["current_attempt_uid"] or "")
        attempt = (
            connection.execute(
                "SELECT * FROM a2a_task_attempts WHERE uid = ? AND task_uid = ?",
                (attempt_uid, task_uid),
            ).fetchone()
            if attempt_uid
            else None
        )
        if attempt is None:
            attempt_number = int(
                connection.execute(
                    "SELECT COUNT(*) FROM a2a_task_attempts WHERE task_uid = ?",
                    (task_uid,),
                ).fetchone()[0]
            )
        else:
            attempt_number = int(attempt["attempt_number"])
        correlation_id = str(task["correlation_id"] or task["task_id"])
        status_message = failure_status_message(
            context_id=str(task["context_id"]),
            task_id=str(task["task_id"]),
            failure_code=failure_code,
            category=category,
            retryable=False,
            attempt_number=attempt_number,
            correlation_id=correlation_id,
            text=text,
        )
        now = _utcnow()
        persisted_message = self._append_task_message(connection, task_uid, status_message)
        connection.execute(
            """
            UPDATE a2a_tasks SET status='failed', status_message_json=?,
                status_timestamp=?, failure_code=?, failure_category=?,
                failure_retryable=0, correlation_id=?, recovery_owner=?,
                terminal_at=?, updated_at=? WHERE uid=?
            """,
            (
                self._json_dump(status_message),
                _iso(now),
                failure_code,
                category,
                correlation_id,
                recovery_owner,
                _iso(now),
                _iso(now),
                task_uid,
            ),
        )
        if attempt is not None:
            session = connection.execute(
                "SELECT next_sequence FROM sessions WHERE uid=?",
                (str(task["context_id"]),),
            ).fetchone()
            entry_end_sequence = int(session["next_sequence"]) if session is not None else None
            connection.execute(
                """
                UPDATE a2a_task_attempts SET state=?, outcome_category=?,
                    failure_code=?, failure_detail=?, retryable=0,
                    correlation_id=?, entry_end_sequence=?,
                    turn_resolution=CASE
                        WHEN turn_resolution='pending' THEN 'abandoned'
                        ELSE turn_resolution
                    END, updated_at=? WHERE uid=?
                """,
                (
                    "ambiguous" if ambiguous else "failed",
                    category,
                    failure_code,
                    failure_code,
                    correlation_id,
                    entry_end_sequence,
                    _iso(now),
                    attempt_uid,
                ),
            )
            connection.execute(
                """
                UPDATE sessions SET runtime_activity='idle', active_turn_uid=NULL,
                    active_task_attempt_uid=NULL, updated_at=?
                WHERE uid=? AND active_task_attempt_uid=?
                """,
                (_iso(now), str(task["context_id"]), attempt_uid),
            )
        self._append_task_event(
            connection,
            task_uid,
            event_type="status_changed",
            status="failed",
            message_uid=str(persisted_message["messageId"]),
            payload={"messageId": persisted_message["messageId"], "recovery": True},
        )

    async def reconcile_a2a_tasks(
        self,
        *,
        recovery_owner: str,
        stale_after_seconds: float,
        pending_timeout_seconds: float,
        max_recovery_attempts: int,
    ) -> list[AgentTask]:
        """Resolve abandoned work and return safe submitted Tasks for scheduling."""

        def operation() -> list[AgentTask]:
            now = _utcnow()
            stale_before = now - timedelta(seconds=stale_after_seconds)
            pending_before = now - timedelta(seconds=pending_timeout_seconds)
            submitted: list[AgentTask] = []
            stale_attempts = 0
            ambiguous_outcomes = 0
            recovery_failures = 0
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT * FROM a2a_tasks
                    WHERE status IN ('submitted', 'working')
                    ORDER BY created_at, uid
                    """
                ).fetchall()
                for row in rows:
                    status = str(row["status"])
                    if status == "submitted":
                        lease = connection.execute(
                            "SELECT holder_id, expires_at FROM leases WHERE session_uid = ?",
                            (str(row["context_id"]),),
                        ).fetchone()
                        owned_elsewhere = bool(
                            lease is not None
                            and str(lease["holder_id"]) != recovery_owner
                            and _from_iso(str(lease["expires_at"])) > now
                        )
                        if owned_elsewhere:
                            continue
                        exhausted = int(row["recovery_count"]) >= max_recovery_attempts
                        timed_out = _from_iso(str(row["created_at"])) <= pending_before
                        if exhausted or timed_out:
                            self._terminalize_recovery_failure(
                                connection,
                                row,
                                recovery_owner=recovery_owner,
                                failure_code="recovery_exhausted",
                                category="recovery",
                                text="Task could not be started within its recovery policy.",
                                ambiguous=False,
                            )
                            recovery_failures += 1
                            continue
                        submitted.append(self._task_from_row(connection, row))
                        continue

                    attempt_uid = str(row["current_attempt_uid"] or "")
                    attempt = (
                        connection.execute(
                            "SELECT * FROM a2a_task_attempts WHERE uid = ?",
                            (attempt_uid,),
                        ).fetchone()
                        if attempt_uid
                        else None
                    )
                    lease = connection.execute(
                        "SELECT holder_id, expires_at FROM leases WHERE session_uid = ?",
                        (str(row["context_id"]),),
                    ).fetchone()
                    lease_active = bool(
                        attempt is not None
                        and lease is not None
                        and str(lease["holder_id"]) == str(attempt["holder_id"])
                        and _from_iso(str(lease["expires_at"])) > now
                    )
                    last_activity_value = (
                        attempt["updated_at"] if attempt is not None else row["status_timestamp"]
                    )
                    last_activity = _from_iso(str(last_activity_value))
                    if lease_active or last_activity > stale_before:
                        continue
                    stale_attempts += 1
                    self._terminalize_recovery_failure(
                        connection,
                        row,
                        recovery_owner=recovery_owner,
                        failure_code="ambiguous_execution_outcome",
                        category="ambiguous_outcome",
                        text=(
                            "Task execution ownership was lost and its external effects "
                            "could not be proven safe to replay."
                        ),
                        ambiguous=True,
                    )
                    ambiguous_outcomes += 1

                current = connection.execute(
                    "SELECT status, created_at, status_timestamp FROM a2a_tasks "
                    "WHERE status IN ('submitted', 'working')"
                ).fetchall()
                pending_ages = [
                    (now - _from_iso(str(item["created_at"]))).total_seconds()
                    for item in current
                    if item["status"] == "submitted"
                ]
                working_ages = [
                    (now - _from_iso(str(item["status_timestamp"]))).total_seconds()
                    for item in current
                    if item["status"] == "working"
                ]
                self._recovery_metrics = {
                    "submitted": len(pending_ages),
                    "working": len(working_ages),
                    "stale_attempts": stale_attempts,
                    "recovery_failures": recovery_failures,
                    "ambiguous_outcomes": ambiguous_outcomes,
                    "oldest_pending_seconds": max(pending_ages, default=0.0),
                    "oldest_working_seconds": max(working_ages, default=0.0),
                }
            return submitted

        return await self._run(operation)

    async def record_a2a_recovery_failure(
        self,
        task_uid: str,
        *,
        recovery_owner: str,
        error_type: str,
    ) -> AgentTask:
        """Persist one safe deferred-recovery attempt for a submitted Task."""

        def operation() -> AgentTask:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                task = self._task_row(connection, task_uid)
                if str(task["status"]) != "submitted":
                    return self._task_from_row(connection, task)
                count = int(task["recovery_count"]) + 1
                now = _utcnow()
                connection.execute(
                    """
                    UPDATE a2a_tasks SET recovery_owner=?, recovery_count=?, updated_at=?
                    WHERE uid=?
                    """,
                    (recovery_owner, count, _iso(now), task_uid),
                )
                self._append_task_event(
                    connection,
                    task_uid,
                    event_type="recovery_deferred",
                    payload={"attempt": count, "errorType": error_type},
                )
                return self._task_from_row(connection, self._task_row(connection, task_uid))

        return await self._run(operation)

    def a2a_recovery_snapshot(self) -> dict[str, int | float]:
        return dict(self._recovery_metrics)

    async def start_task_attempt(
        self,
        task_uid: str,
        *,
        attempt_uid: str,
        holder_id: str,
        lease_token: str,
        turn_uid: str,
    ) -> AgentTaskExecutionAttempt:
        def operation() -> AgentTaskExecutionAttempt:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                task = self._task_row(connection, task_uid)
                attempt = self._validate_task_attempt(
                    connection,
                    task,
                    attempt_uid=attempt_uid,
                    holder_id=holder_id,
                    lease_token=lease_token,
                )
                if str(attempt["state"]) not in {"claimed", "running"}:
                    raise BackendConflictError("Local A2A task attempt cannot be started")
                existing_turn_uid = str(attempt["turn_uid"] or "")
                if existing_turn_uid and existing_turn_uid != turn_uid:
                    raise BackendConflictError("Local A2A task attempt turn is immutable")
                session = connection.execute(
                    "SELECT * FROM sessions WHERE uid=?",
                    (str(task["context_id"]),),
                ).fetchone()
                if session is None:
                    raise SessionNotFoundError(f"Local session not found: {task['context_id']!s}")
                owner = str(session["active_task_attempt_uid"] or "")
                active_turn = str(session["active_turn_uid"] or "")
                if owner and owner != attempt_uid:
                    raise BackendConflictError("Another Task owns the local Tau turn")
                if active_turn and active_turn != turn_uid:
                    raise BackendConflictError("Another Tau turn is already active")
                now = _utcnow()
                connection.execute(
                    """
                    UPDATE a2a_task_attempts SET state='running', turn_uid=?,
                        entry_start_sequence=?, turn_resolution='pending', updated_at=?
                    WHERE uid=?
                    """,
                    (turn_uid, int(session["next_sequence"]), _iso(now), attempt_uid),
                )
                connection.execute(
                    """
                    UPDATE sessions SET runtime_activity='working', active_turn_uid=?,
                        active_task_attempt_uid=?, updated_at=? WHERE uid=?
                    """,
                    (turn_uid, attempt_uid, _iso(now), str(task["context_id"])),
                )
                refreshed = connection.execute(
                    "SELECT * FROM a2a_task_attempts WHERE uid=?", (attempt_uid,)
                ).fetchone()
                assert refreshed is not None
                return self._attempt_from_row(refreshed)

        return await self._run(operation)

    async def _mutate_task_output(
        self,
        task_uid: str,
        *,
        attempt_uid: str,
        holder_id: str,
        lease_token: str,
        operation: str,
        artifact_id: str,
        parts: list[dict[str, Any]],
        expected_revision: int | None = None,
        name: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if operation not in {"create", "append", "finalize"}:
            raise BackendConflictError("Unknown local A2A output mutation")

        def mutate() -> dict[str, Any]:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                task = self._task_row(connection, task_uid)
                self._validate_task_attempt(
                    connection,
                    task,
                    attempt_uid=attempt_uid,
                    holder_id=holder_id,
                    lease_token=lease_token,
                )
                current = connection.execute(
                    """
                    SELECT * FROM a2a_task_outputs
                    WHERE task_uid = ? AND artifact_id = ?
                    """,
                    (task_uid, artifact_id),
                ).fetchone()
                if operation == "create" and current is not None:
                    raise BackendConflictError("Local A2A output already exists")
                if operation != "create" and current is None:
                    raise BackendConflictError("Local A2A output does not exist")
                current_revision = int(current["revision"]) if current is not None else 0
                if expected_revision is not None and expected_revision != current_revision:
                    raise BackendConflictError("Local A2A output revision conflict")
                revision = current_revision + 1
                existing_parts: list[dict[str, Any]] = (
                    self._json_load(current["parts_json"], []) if current is not None else []
                )
                resolved_parts = [*existing_parts, *parts]
                existing_metadata: dict[str, Any] = (
                    self._json_load(current["metadata_json"], {}) if current is not None else {}
                )
                resolved_metadata = {**existing_metadata, **dict(metadata or {})}
                resolved_name = name or (str(current["name"]) if current is not None else "")
                now = _utcnow()
                if current is None:
                    connection.execute(
                        """
                        INSERT INTO a2a_task_outputs(
                            task_uid, artifact_id, revision, name, parts_json,
                            metadata_json, finalized, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_uid,
                            artifact_id,
                            revision,
                            resolved_name,
                            self._json_dump(resolved_parts),
                            self._json_dump(resolved_metadata),
                            int(operation == "finalize"),
                            _iso(now),
                            _iso(now),
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE a2a_task_outputs SET revision=?, name=?, parts_json=?,
                            metadata_json=?, finalized=?, updated_at=?
                        WHERE task_uid=? AND artifact_id=?
                        """,
                        (
                            revision,
                            resolved_name,
                            self._json_dump(resolved_parts),
                            self._json_dump(resolved_metadata),
                            int(bool(current["finalized"]) or operation == "finalize"),
                            _iso(now),
                            task_uid,
                            artifact_id,
                        ),
                    )
                payload = {
                    "uid": artifact_id,
                    "artifact_id": artifact_id,
                    "name": resolved_name,
                    "parts": parts,
                    "metadata": resolved_metadata,
                    "append": operation == "append",
                    "lastChunk": operation == "finalize",
                    "revision": revision,
                }
                self._append_task_event(
                    connection,
                    task_uid,
                    event_type="output_added" if current is None else "output_updated",
                    output_uid=artifact_id,
                    payload=payload,
                )
                return payload

        return await self._run(mutate)

    async def create_task_output(
        self,
        task_uid: str,
        *,
        attempt_uid: str,
        holder_id: str,
        lease_token: str,
        artifact_id: str,
        parts: list[dict[str, Any]],
        name: str = "",
        description: str = "",
        metadata: Mapping[str, Any] | None = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        del description, extensions
        return await self._mutate_task_output(
            task_uid,
            attempt_uid=attempt_uid,
            holder_id=holder_id,
            lease_token=lease_token,
            operation="create",
            artifact_id=artifact_id,
            parts=parts,
            name=name,
            metadata=metadata,
        )

    async def append_task_output(
        self,
        task_uid: str,
        *,
        attempt_uid: str,
        holder_id: str,
        lease_token: str,
        output_uid: str,
        expected_revision: int,
        parts: list[dict[str, Any]],
        metadata: Mapping[str, Any] | None = None,
        extensions: Mapping[str, Any] | None = None,
        last_chunk: bool = False,
    ) -> dict[str, Any]:
        del extensions
        return await self._mutate_task_output(
            task_uid,
            attempt_uid=attempt_uid,
            holder_id=holder_id,
            lease_token=lease_token,
            operation="finalize" if last_chunk else "append",
            artifact_id=output_uid,
            parts=parts,
            expected_revision=expected_revision,
            metadata=metadata,
        )

    async def finalize_task_output(
        self,
        task_uid: str,
        *,
        attempt_uid: str,
        holder_id: str,
        lease_token: str,
        output_uid: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        return await self._mutate_task_output(
            task_uid,
            attempt_uid=attempt_uid,
            holder_id=holder_id,
            lease_token=lease_token,
            operation="finalize",
            artifact_id=output_uid,
            parts=[],
            expected_revision=expected_revision,
        )

    async def settle_task_attempt(
        self,
        task_uid: str,
        *,
        attempt_uid: str,
        holder_id: str,
        lease_token: str,
        status: str,
        status_message: Mapping[str, Any] | None = None,
        outcome_category: str = "",
        failure_detail: str = "",
    ) -> AgentTaskExecutionAttempt:
        allowed = {
            "input_required",
            "auth_required",
            "completed",
            "failed",
            "canceled",
            "rejected",
        }
        if status not in allowed:
            raise BackendConflictError("Invalid local A2A terminal or interruption status")

        def operation() -> AgentTaskExecutionAttempt:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                task = self._task_row(connection, task_uid)
                message_payload = validate_status_message(
                    status_message,
                    context_id=str(task["context_id"]),
                    task_id=str(task["task_id"]),
                )
                if status in {"input_required", "auth_required", "failed", "rejected"} and (
                    message_payload is None
                ):
                    raise BackendConflictError(
                        f"Local A2A {status} settlement requires a complete status Message"
                    )
                normalized_settlement = {
                    "task_uid": task_uid,
                    "attempt_uid": attempt_uid,
                    "status": status,
                    "status_message": message_payload,
                    "outcome_category": str(outcome_category),
                    "failure_detail": str(failure_detail)[:512],
                }
                fingerprint = hashlib.sha256(
                    self._json_dump(normalized_settlement).encode("utf-8")
                ).hexdigest()
                stored_attempt = connection.execute(
                    "SELECT * FROM a2a_task_attempts WHERE uid=? AND task_uid=?",
                    (attempt_uid, task_uid),
                ).fetchone()
                if stored_attempt is None or str(stored_attempt["holder_id"]) != holder_id:
                    raise BackendConflictError("Local A2A task attempt ownership is invalid")
                existing_fingerprint = str(stored_attempt["settlement_fingerprint"] or "")
                if existing_fingerprint:
                    if existing_fingerprint != fingerprint:
                        raise BackendConflictError("Local A2A settlement replay diverged")
                    return self._attempt_from_row(stored_attempt)
                if str(stored_attempt["state"]) not in {"claimed", "running"}:
                    raise BackendConflictError("Pre-cutover local A2A settlement is not replayable")
                attempt = self._validate_task_attempt(
                    connection,
                    task,
                    attempt_uid=attempt_uid,
                    holder_id=holder_id,
                    lease_token=lease_token,
                )
                turn_resolution = str(attempt["turn_resolution"] or "")
                if status in {"completed", "input_required", "auth_required"} and (
                    turn_resolution != "committed"
                ):
                    raise BackendConflictError(
                        f"Local A2A {status} settlement requires the matching Tau turn commit"
                    )
                now = _utcnow()
                public_failure = failure_details(message_payload)
                failure_code = str(public_failure.get("code") or "")
                failure_category = str(public_failure.get("category") or outcome_category or "")
                failure_retryable = public_failure.get("retryable")
                correlation_id = str(public_failure.get("correlationId") or "")
                is_terminal = status in _LOCAL_A2A_TERMINAL_STATES
                if turn_resolution == "pending":
                    session = connection.execute(
                        "SELECT next_sequence FROM sessions WHERE uid=?",
                        (str(task["context_id"]),),
                    ).fetchone()
                    if session is None:
                        raise SessionNotFoundError(
                            f"Local session not found: {task['context_id']!s}"
                        )
                    turn_resolution = "abandoned"
                    connection.execute(
                        """
                        UPDATE sessions SET runtime_activity='idle', active_turn_uid=NULL,
                            active_task_attempt_uid=NULL, updated_at=? WHERE uid=?
                            AND active_task_attempt_uid=?
                        """,
                        (_iso(now), str(task["context_id"]), attempt_uid),
                    )
                    entry_end_sequence = int(session["next_sequence"])
                else:
                    entry_end_sequence = attempt["entry_end_sequence"]
                persisted_message = (
                    self._append_task_message(connection, task_uid, message_payload)
                    if message_payload is not None
                    else None
                )
                message_uid = (
                    str(persisted_message["messageId"]) if persisted_message is not None else None
                )
                connection.execute(
                    """
                    UPDATE a2a_tasks SET status=?, status_message_json=?,
                        status_timestamp=?, cancellation_requested=?,
                        failure_code=?, failure_category=?, failure_retryable=?,
                        correlation_id=?, terminal_at=?, recovery_owner='', updated_at=?
                    WHERE uid=?
                    """,
                    (
                        status,
                        self._json_dump(message_payload) if message_payload is not None else None,
                        _iso(now),
                        int(status == "canceled"),
                        failure_code,
                        failure_category,
                        (int(bool(failure_retryable)) if failure_retryable is not None else None),
                        correlation_id,
                        _iso(now) if is_terminal else None,
                        _iso(now),
                        task_uid,
                    ),
                )
                connection.execute(
                    """
                    UPDATE a2a_task_attempts SET state=?, outcome_category=?,
                        failure_code=?, failure_detail=?, retryable=?, correlation_id=?,
                        entry_end_sequence=?, turn_resolution=?, settlement_fingerprint=?,
                        updated_at=? WHERE uid=?
                    """,
                    (
                        "interrupted" if status in {"input_required", "auth_required"} else status,
                        outcome_category,
                        failure_code,
                        failure_detail,
                        (int(bool(failure_retryable)) if failure_retryable is not None else None),
                        correlation_id,
                        entry_end_sequence,
                        turn_resolution or None,
                        fingerprint,
                        _iso(now),
                        attempt_uid,
                    ),
                )
                connection.execute(
                    "UPDATE a2a_tasks SET current_attempt_uid=NULL WHERE uid=?",
                    (task_uid,),
                )
                self._append_task_event(
                    connection,
                    task_uid,
                    event_type="status_changed",
                    status=status,
                    message_uid=message_uid,
                    payload=({"messageId": message_uid} if message_uid else {}),
                )
                refreshed = connection.execute(
                    "SELECT * FROM a2a_task_attempts WHERE uid=?", (attempt_uid,)
                ).fetchone()
                assert refreshed is not None
                return self._attempt_from_row(refreshed)

        return await self._run(operation)

    async def continue_task(
        self,
        task_uid: str,
        message: Mapping[str, Any],
    ) -> AgentTask:
        def operation() -> AgentTask:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                task = self._task_row(connection, task_uid)
                if str(task["status"]) not in {"input_required", "auth_required"}:
                    raise BackendConflictError("Local A2A task is not awaiting continuation")
                self._append_task_message(connection, task_uid, message)
                now = _utcnow()
                connection.execute(
                    """
                    UPDATE a2a_tasks SET status='submitted', status_message_json=NULL,
                        status_timestamp=?, cancellation_requested=0, dispatch_uid=?,
                        current_attempt_uid=NULL, failure_code='', failure_category='',
                        failure_retryable=NULL, correlation_id='', recovery_owner='',
                        recovery_count=0, terminal_at=NULL, updated_at=? WHERE uid=?
                    """,
                    (_iso(now), str(uuid.uuid4()), _iso(now), task_uid),
                )
                self._append_task_event(
                    connection,
                    task_uid,
                    event_type="status_changed",
                    status="submitted",
                )
                return self._task_from_row(
                    connection,
                    self._task_row(connection, task_uid),
                )

        return await self._run(operation)

    async def update_task_recovery_metadata(
        self,
        task_uid: str,
        *,
        output_contract: Mapping[str, Any],
        provenance: Mapping[str, Any],
    ) -> AgentTask:
        """Persist the exact continuation execution contract used after a safe restart."""

        def operation() -> AgentTask:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                task = self._task_row(connection, task_uid)
                if str(task["status"]) != "submitted":
                    raise BackendConflictError(
                        "Local A2A recovery metadata requires a submitted Task"
                    )
                metadata = self._json_load(task["metadata_json"], {})
                metadata["execution"] = {
                    "output_contract": dict(output_contract),
                    "provenance": dict(provenance),
                }
                connection.execute(
                    "UPDATE a2a_tasks SET metadata_json=?, updated_at=? WHERE uid=?",
                    (self._json_dump(metadata), _iso(_utcnow()), task_uid),
                )
                return self._task_from_row(connection, self._task_row(connection, task_uid))

        return await self._run(operation)

    async def cancel_task(self, task_uid: str) -> AgentTask:
        def operation() -> AgentTask:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                task = self._task_row(connection, task_uid)
                if str(task["status"]) in _LOCAL_A2A_TERMINAL_STATES:
                    return self._task_from_row(connection, task)
                now = _utcnow()
                connection.execute(
                    """
                    UPDATE a2a_tasks SET status='canceled', cancellation_requested=1,
                        status_timestamp=?, terminal_at=?, recovery_owner='',
                        current_attempt_uid=NULL, updated_at=?
                    WHERE uid=?
                    """,
                    (_iso(now), _iso(now), _iso(now), task_uid),
                )
                if task["current_attempt_uid"]:
                    attempt_uid = str(task["current_attempt_uid"])
                    attempt = connection.execute(
                        "SELECT * FROM a2a_task_attempts WHERE uid=? AND task_uid=?",
                        (attempt_uid, task_uid),
                    ).fetchone()
                    session = connection.execute(
                        "SELECT next_sequence FROM sessions WHERE uid=?",
                        (str(task["context_id"]),),
                    ).fetchone()
                    pending_turn = bool(
                        attempt is not None and str(attempt["turn_resolution"] or "") == "pending"
                    )
                    connection.execute(
                        """
                        UPDATE a2a_task_attempts SET state='canceled',
                            entry_end_sequence=CASE
                                WHEN turn_resolution='pending' THEN ?
                                ELSE entry_end_sequence
                            END,
                            turn_resolution=CASE
                                WHEN turn_resolution='pending' THEN 'abandoned'
                                ELSE turn_resolution
                            END,
                            updated_at=?
                        WHERE uid=?
                        """,
                        (
                            int(session["next_sequence"])
                            if pending_turn and session is not None
                            else None,
                            _iso(now),
                            attempt_uid,
                        ),
                    )
                    if pending_turn:
                        connection.execute(
                            """
                            UPDATE sessions SET runtime_activity='idle',
                                active_turn_uid=NULL, active_task_attempt_uid=NULL,
                                updated_at=?
                            WHERE uid=? AND active_task_attempt_uid=?
                            """,
                            (_iso(now), str(task["context_id"]), attempt_uid),
                        )
                self._append_task_event(
                    connection,
                    task_uid,
                    event_type="status_changed",
                    status="canceled",
                )
                return self._task_from_row(
                    connection,
                    self._task_row(connection, task_uid),
                )

        return await self._run(operation)

    async def state_ready(self) -> bool:
        await self._ensure_initialized()
        return self._path.is_file()

    @staticmethod
    def provider_credential_from_hydration(
        provider: str,
        data: object,
    ) -> ProviderCredential:
        return MainSequenceClient.provider_credential_from_hydration(provider, data)
