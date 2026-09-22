"""Read-only inspection of Tau's workspace-scoped SQLite state."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "2"
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
                "status_timestamp, cancellation_requested, created_at, updated_at "
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
            events = connection.execute(
                "SELECT sequence, uid, event_type, status, message_uid, output_uid, "
                "payload_json, created_at FROM a2a_task_events "
                "WHERE task_uid = ? ORDER BY sequence",
                (uid,),
            ).fetchall()
            attempts = connection.execute(
                "SELECT uid, attempt_number, state, created_at, updated_at "
                "FROM a2a_task_attempts WHERE task_uid = ? ORDER BY attempt_number",
                (uid,),
            ).fetchall()
            outputs = connection.execute(
                "SELECT artifact_id, revision, name, finalized, created_at, updated_at "
                "FROM a2a_task_outputs WHERE task_uid = ? ORDER BY created_at",
                (uid,),
            ).fetchall()
    except sqlite3.Error as error:
        raise StateError(f"Cannot read local Task: {type(error).__name__}") from error
    return {
        "task": dict(task),
        "events": [dict(row) for row in events],
        "attempts": [dict(row) for row in attempts],
        "outputs": [dict(row) for row in outputs],
    }
