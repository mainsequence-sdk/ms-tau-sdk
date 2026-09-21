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
