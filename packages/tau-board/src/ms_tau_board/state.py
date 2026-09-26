"""Read-only inspection of Tau's workspace-scoped SQLite state."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "4"
TABLES = (
    "sessions",
    "entries",
    "a2a_tasks",
    "a2a_task_messages",
    "a2a_task_outputs",
    "a2a_task_attempts",
    "a2a_task_events",
    "snapshots",
)
PRIVATE_FIELDS = {
    "entry_json",
    "message_json",
    "parts_json",
    "payload_json",
    "snapshot_json",
    "status_message_json",
    "metadata_json",
}
EXECUTION_VALUE_MAX_BYTES = 64 * 1024
_SENSITIVE_ENTRY_KEYS = {
    "api_key",
    "authorization",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "prompt",
    "secret",
    "system_prompt",
    "token",
}
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


class StateError(ValueError):
    """The selected local store cannot be inspected safely."""


def _database_path(directory: Path) -> Path | None:
    root = directory.resolve()
    path = root / "runtime.sqlite3"
    if not path.exists():
        return None
    if not path.is_file() or path.resolve().parent != root:
        raise StateError("runtime.sqlite3 must be a regular file in the selected directory")
    return path


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 2000")
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()
        if version is None or str(version["value"]) != SCHEMA_VERSION:
            raise StateError("Unknown Tau local SQLite schema version")
    except BaseException:
        connection.close()
        raise
    return connection


def summary(directory: Path) -> dict[str, Any]:
    path = _database_path(directory)
    if path is None:
        return {"available": False, "directory": str(directory), "tables": {}}
    try:
        with closing(_connect(path)) as connection:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in TABLES
            }
    except sqlite3.Error as error:
        raise StateError(f"Cannot read local SQLite state: {type(error).__name__}") from error
    return {
        "available": True,
        "directory": str(directory),
        "schemaVersion": SCHEMA_VERSION,
        "tables": counts,
    }


def list_rows(directory: Path, table: str, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    if table not in TABLES:
        raise StateError("Unknown local-state table")
    if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
        raise StateError("Pagination is outside the supported range")
    path = _database_path(directory)
    if path is None:
        return {"available": False, "table": table, "rows": [], "total": 0}
    try:
        with closing(_connect(path)) as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            rows = connection.execute(
                f"SELECT rowid AS board_rowid, * FROM {table} ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            values = []
            for row in rows:
                item = dict(row)
                for key, value in list(item.items()):
                    if key in PRIVATE_FIELDS:
                        item[key] = "[open row to view]" if value else None
                    elif isinstance(value, str) and len(value) > 160:
                        item[key] = value[:160] + "…"
                values.append(item)
    except sqlite3.Error as error:
        raise StateError(f"Cannot read local SQLite state: {type(error).__name__}") from error
    return {
        "available": True,
        "table": table,
        "rows": values,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def row_detail(directory: Path, table: str, rowid: int) -> dict[str, Any]:
    if table not in TABLES:
        raise StateError("Unknown local-state table")
    if rowid < 1:
        raise StateError("Row ID must be positive")
    path = _database_path(directory)
    if path is None:
        raise StateError("Local SQLite state does not exist yet")
    try:
        with closing(_connect(path)) as connection:
            row = connection.execute(
                f"SELECT rowid AS board_rowid, * FROM {table} WHERE rowid = ?", (rowid,)
            ).fetchone()
    except sqlite3.Error as error:
        raise StateError(f"Cannot read local SQLite state: {type(error).__name__}") from error
    if row is None:
        raise StateError("Row not found")
    return {"table": table, "row": dict(row)}


def sessions(directory: Path, *, limit: int = 100) -> dict[str, Any]:
    """Return recent local sessions for the board's session picker."""
    if not 1 <= limit <= 200:
        raise StateError("Session limit must be between 1 and 200")
    path = _database_path(directory)
    if path is None:
        return {"sessions": []}
    try:
        with closing(_connect(path)) as connection:
            rows = connection.execute(
                "SELECT uid, provider, model, thinking, runtime_activity, created_at, "
                "updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except sqlite3.Error as error:
        raise StateError(f"Cannot read local sessions: {type(error).__name__}") from error
    return {"sessions": [dict(row) for row in rows]}


def tasks(directory: Path, *, session_uid: str = "", limit: int = 100) -> dict[str, Any]:
    if not 1 <= limit <= 200 or len(session_uid) > 128:
        raise StateError("Invalid Task query")
    path = _database_path(directory)
    if path is None:
        return {"tasks": []}
    try:
        with closing(_connect(path)) as connection:
            where = "WHERE context_id = ?" if session_uid else ""
            params: tuple[object, ...] = (session_uid, limit) if session_uid else (limit,)
            rows = connection.execute(
                "SELECT uid, task_id, context_id, agent_session_uid, status, "
                "status_timestamp, cancellation_requested, failure_code, "
                "failure_category, failure_retryable, correlation_id, recovery_owner, "
                "recovery_count, terminal_at, created_at, updated_at "
                f"FROM a2a_tasks {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
    except sqlite3.Error as error:
        raise StateError(f"Cannot read local Tasks: {type(error).__name__}") from error
    return {"tasks": [dict(row) for row in rows]}


def task_detail(directory: Path, task_id: str) -> dict[str, Any]:
    if not task_id or len(task_id) > 128:
        raise StateError("Invalid Task ID")
    path = _database_path(directory)
    if path is None:
        raise StateError("Local SQLite state does not exist yet")
    try:
        with closing(_connect(path)) as connection:
            task = connection.execute(
                "SELECT * FROM a2a_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise StateError("Task not found")
            uid = task["uid"]
            runtime_selection = connection.execute(
                "SELECT provider, model, thinking FROM sessions WHERE uid = ?",
                (task["context_id"],),
            ).fetchone()
            messages = connection.execute(
                "SELECT sequence, message_id, role, message_json, created_at "
                "FROM a2a_task_messages WHERE task_uid = ? ORDER BY sequence",
                (uid,),
            ).fetchall()
            events = connection.execute(
                "SELECT sequence, uid, event_type, status, message_uid, output_uid, "
                "payload_json, created_at FROM a2a_task_events "
                "WHERE task_uid = ? ORDER BY sequence",
                (uid,),
            ).fetchall()
            attempts = connection.execute(
                "SELECT uid, attempt_number, state, outcome_category, failure_code, "
                "failure_detail, retryable, correlation_id, turn_uid, "
                "entry_start_sequence, entry_end_sequence, turn_resolution, "
                "created_at, updated_at "
                "FROM a2a_task_attempts WHERE task_uid = ? ORDER BY attempt_number",
                (uid,),
            ).fetchall()
            outputs = connection.execute(
                "SELECT artifact_id, revision, name, parts_json, metadata_json, "
                "finalized, created_at, updated_at "
                "FROM a2a_task_outputs WHERE task_uid = ? ORDER BY created_at",
                (uid,),
            ).fetchall()
            execution_entries = connection.execute(
                """
                SELECT DISTINCT entries.sequence, entries.entry_type,
                    entries.entry_json, entries.idempotency_key, entries.turn_uid
                FROM entries
                JOIN a2a_task_attempts AS attempts
                    ON attempts.turn_uid = entries.turn_uid
                    AND attempts.task_uid = ?
                WHERE entries.session_uid = ?
                    AND attempts.turn_uid IS NOT NULL
                    AND (
                        attempts.entry_start_sequence IS NULL
                        OR entries.sequence >= attempts.entry_start_sequence
                    )
                    AND (
                        attempts.entry_end_sequence IS NULL
                        OR entries.sequence < attempts.entry_end_sequence
                    )
                ORDER BY entries.sequence
                """,
                (uid, task["context_id"]),
            ).fetchall()
    except sqlite3.Error as error:
        raise StateError(f"Cannot read local Task: {type(error).__name__}") from error
    task_value = dict(task)
    raw_status_message = task_value.pop("status_message_json", None)
    try:
        status_message = json.loads(raw_status_message) if raw_status_message else None
    except (TypeError, json.JSONDecodeError):
        status_message = None
    task_value["status_message"] = status_message
    task_value["runtime"] = dict(runtime_selection) if runtime_selection is not None else None
    status_text = ""
    if isinstance(status_message, dict):
        parts = status_message.get("parts")
        if isinstance(parts, list):
            status_text = "\n".join(
                str(part.get("text") or "")
                for part in parts
                if isinstance(part, dict) and part.get("text")
            )
    task_value["failure"] = {
        "code": str(task_value.get("failure_code") or ""),
        "category": str(task_value.get("failure_category") or ""),
        "retryable": (
            bool(task_value["failure_retryable"])
            if task_value.get("failure_retryable") is not None
            else None
        ),
        "message": status_text,
        "correlationId": str(task_value.get("correlation_id") or ""),
    }
    message_values = []
    for row in messages:
        value = dict(row)
        value["message"] = _decode_json(value.pop("message_json"), {})
        message_values.append(value)
    output_values = []
    for row in outputs:
        value = dict(row)
        parts = _decode_json(value.pop("parts_json"), [])
        metadata = _decode_json(value.pop("metadata_json"), {})
        text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
        byte_size = 0
        for part in parts:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                byte_size += len(part["text"].encode("utf-8"))
            elif "data" in part:
                byte_size += len(
                    json.dumps(
                        part["data"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                )
        value.update(
            {
                "parts": parts,
                "metadata": metadata,
                "text": text,
                "byte_size": byte_size,
            }
        )
        output_values.append(value)
    event_values = []
    for row in events:
        value = dict(row)
        value["payload"] = _decode_json(value.pop("payload_json"), {})
        event_values.append(value)
    entry_values = []
    for row in execution_entries:
        value = dict(row)
        value["entry"] = _safe_execution_value(_decode_json(value.pop("entry_json"), {}))
        entry_values.append(value)
    return {
        "task": task_value,
        "messages": message_values,
        "events": event_values,
        "attempts": [dict(row) for row in attempts],
        "outputs": output_values,
        "executionEntries": entry_values,
    }


def _decode_json(value: object, default: Any) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _safe_execution_value(value: Any, *, key: str = "") -> Any:
    """Bound and redact operational entry data before it reaches the browser."""

    normalized = key.lower().replace("-", "_")
    if normalized in _SENSITIVE_ENTRY_KEYS or normalized.endswith(
        ("_credential", "_password", "_secret", "_token")
    ):
        return "[REDACTED]"
    safe: Any
    if isinstance(value, dict):
        safe = {
            str(child_key): _safe_execution_value(child, key=str(child_key))
            for child_key, child in value.items()
        }
    elif isinstance(value, list):
        safe = [_safe_execution_value(child) for child in value]
    elif isinstance(value, str):
        safe = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
    else:
        safe = value
    encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= EXECUTION_VALUE_MAX_BYTES:
        return safe
    return {
        "truncated": True,
        "originalBytes": len(encoded),
        "message": "Execution entry exceeds the Tau Board inspection limit.",
    }
