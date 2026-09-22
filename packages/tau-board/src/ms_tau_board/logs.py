"""Bounded reads of Tau's local JSON Lines operational logs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
    task_uid: str = "",
    event: str = "",
    since: str = "",
    offset: int = 0,
    related_session: bool = False,
    until: str = "",
) -> dict[str, Any]:
    if not 1 <= limit <= MAX_RESULT:
        raise ValueError("Log limit must be between 1 and 200")
    if not 0 <= offset <= 100000:
        raise ValueError("Log offset is outside the supported range")
    filters = {
        "level": level,
        "session": session,
        "task": task,
        "task_uid": task_uid,
        "event": event,
        "since": since,
        "until": until,
    }
    if any(len(value) > 128 for value in filters.values()):
        raise ValueError("Log filter is too long")
    records: list[dict[str, Any]] = []
    matched = 0
    start_time = _time(since)
    end_time = _time(until)
    for suffix in ("", ".1", ".2", ".3", ".4", ".5"):
        path = _safe_file(directory, "tau.jsonl" + suffix)
        if path is None:
            continue
        with path.open("rb") as stream:
            size = path.stat().st_size
            start = 0 if session or task else max(0, size - MAX_FILE_READ)
            stream.seek(start)
            raw = stream.read() if session or task else stream.read(MAX_FILE_READ)
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
            record_session = {
                str(record.get(key) or "")
                for key in ("session_uid", "agent_session_uid", "a2a_context_id")
            }
            record_task = {
                str(record.get(key) or "") for key in ("a2a_task_id", "task_id", "task_uid")
            }
            session_match = bool(session and session in record_session)
            task_match = bool(
                task and (task in record_task or (task_uid and task_uid in record_task))
            )
            timestamp = _time(str(record.get("timestamp") or ""))
            in_window = (
                start_time is None or (timestamp is not None and timestamp >= start_time)
            ) and (end_time is None or (timestamp is not None and timestamp <= end_time))
            if task:
                if not task_match and any(record_task - {""}):
                    continue
                if not task_match and not (related_session and session_match and in_window):
                    continue
            elif session and not session_match:
                continue
            if event and event.lower() not in str(record.get("event") or "").lower():
                continue
            if (since or until) and not in_window:
                continue
            if matched < offset:
                matched += 1
                continue
            records.append(record)
            if len(records) > limit:
                return {
                    "directory": str(directory),
                    "records": records[:limit],
                    "hasMore": True,
                    "offset": offset,
                }
    return {"directory": str(directory), "records": records, "hasMore": False, "offset": offset}


def _time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None
