"""Bounded reads of Tau's local JSON Lines operational logs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_FILE_READ = 1024 * 1024
MAX_RESULT = 200


def _safe_file(directory: Path, name: str) -> Path | None:
    root = directory.resolve()
    log_dir = root / "logs"
    if log_dir.is_symlink():
        return None
    path = root / "logs" / name
    if not path.exists():
        return None
    if not path.is_file() or path.resolve().parent != log_dir:
        return None
    return path


def read_logs(
    directory: Path,
    *,
    limit: int = 100,
    level: str = "",
    session: str = "",
    task: str = "",
    event: str = "",
    since: str = "",
) -> dict[str, Any]:
    if not 1 <= limit <= MAX_RESULT:
        raise ValueError("Log limit must be between 1 and 200")
    filters = {"level": level, "session": session, "task": task, "event": event, "since": since}
    if any(len(value) > 128 for value in filters.values()):
        raise ValueError("Log filter is too long")
    records: list[dict[str, Any]] = []
    for suffix in ("", ".1", ".2", ".3", ".4", ".5"):
        path = _safe_file(directory, "tau.jsonl" + suffix)
        if path is None:
            continue
        with path.open("rb") as stream:
            size = path.stat().st_size
            start = max(0, size - MAX_FILE_READ)
            stream.seek(start)
            raw = stream.read(MAX_FILE_READ)
        lines = raw.splitlines()
        if start and lines:
            lines = lines[1:]
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(record, dict):
                continue
            if level and str(record.get("level", "")).lower() != level.lower():
                continue
            if session and session not in str(
                record.get("session_uid") or record.get("agent_session_uid") or ""
            ):
                continue
            if task and task not in str(record.get("a2a_task_id") or record.get("task_id") or ""):
                continue
            if event and event.lower() not in str(record.get("event") or "").lower():
                continue
            if since and str(record.get("timestamp") or "") < since:
                continue
            records.append(record)
            if len(records) >= limit:
                return {"directory": str(directory), "records": records}
    return {"directory": str(directory), "records": records}
