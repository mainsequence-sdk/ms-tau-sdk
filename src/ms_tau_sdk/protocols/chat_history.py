"""Platform-compatible chat history projection of Tau session entries.

The Main Sequence platform serves an AgentSession's chat history from
``GET /api/v1/agent-sessions/{uid}/history/``, projected from the session's Tau
entries. Local chat sessions reuse the same projection so one client reader
serves a managed AgentSession and a local chat session alike. The rules follow
the platform's Tau history projection:

- only the active branch, from the newest entry back to the root, is projected;
- every ``user`` or ``assistant`` message entry with visible content becomes one
  message, identified ``u_N`` or ``a_N`` by its position among its role;
- assistant ``thinking`` blocks become ``reasoning`` parts and ``toolCall``
  blocks become ``tool-call`` parts, which a later ``toolResult`` entry
  completes with its ``result`` and ``isError``;
- the provenance stamp recorded before a turn is attached to its user message;
- compaction and branch summaries become summary assistant messages.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ms_tau_sdk.runtime.provenance import PROVENANCE_NAMESPACE

HISTORY_VERSION = 1
HISTORY_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
PREVIEW_MAX_CHARACTERS = 240
TURN_ERROR_FALLBACK = "The last turn ended with an error."

_USER_MARKER = "Latest user message:"
_REUSE_INSTRUCTION = (
    "Use the active session for prior conversation context when the same "
    "backend agent session is reused."
)
_THINK_TAG = re.compile(r"<think>(.*?)</think>", re.IGNORECASE | re.DOTALL)
_TIMESTAMP_KEYS = ("timestamp", "createdAt", "created_at", "time", "message_timestamp")
_MILLISECOND_TIMESTAMP_FLOOR = 10_000_000_000
_TOOL_CALL_BLOCK_TYPES = frozenset({"toolcall", "tool_call", "tool-call"})
_THINKING_BLOCK_TYPES = frozenset({"thinking", "reasoning", "thought"})
_TOOL_RESULT_ROLES = frozenset({"toolresult", "tool_result", "tool-result", "tool"})
_SKIPPED_ASSISTANT_PART_TYPES = frozenset(
    {
        "tool-call",
        "tool_call",
        "toolcall",
        "tool-result",
        "tool_result",
        "toolresult",
        "system",
    }
)
_PROVENANCE_KEYS = (
    "origin",
    "channel",
    "callerAgentName",
    "handleUniqueId",
    "callerAgentSessionUid",
)
_ACTOR_KINDS = frozenset({"user", "agent"})

type HistoryEntry = Mapping[str, Any]
type HistoryMessage = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProjectedHistory:
    """Messages projected from one active branch, oldest first.

    ``source_indexes[i]`` is the branch position of the entry ``messages[i]`` came from.
    """

    messages: list[HistoryMessage]
    source_indexes: list[int]
    last_timestamp: datetime | None
    last_turn_error: str | None


def iso_timestamp(value: datetime) -> str:
    """Render an aware timestamp the way the platform serializes it."""

    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _entry_sources(entry: HistoryEntry) -> list[HistoryEntry]:
    message = entry.get("message")
    return [message, entry] if isinstance(message, Mapping) else [entry]


def _entry_value(entry: HistoryEntry, *keys: str) -> Any:
    for source in _entry_sources(entry):
        for key in keys:
            if key in source:
                return source.get(key)
    return None


def _datetime_value(source: HistoryEntry, *keys: str) -> datetime | None:
    value = _entry_value(source, *keys)
    if value is None or value == "":
        return None
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, int | float):
        timestamp = float(value)
        if timestamp > _MILLISECOND_TIMESTAMP_FLOOR:
            timestamp /= 1000
        try:
            parsed = datetime.fromtimestamp(timestamp, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def entry_timestamp(entry: HistoryEntry) -> datetime | None:
    """Return the message timestamp, else the entry timestamp, of one entry."""

    for source in _entry_sources(entry):
        parsed = _datetime_value(source, *_TIMESTAMP_KEYS)
        if parsed is not None:
            return parsed
    return None


def entry_role(entry: HistoryEntry) -> str:
    role = str(_entry_value(entry, "role") or "").strip().lower()
    if role in {"user", "assistant"}:
        return role
    entry_type = str(_entry_value(entry, "type") or "").strip().lower()
    return entry_type if entry_type in {"user", "assistant"} else ""


def _split_assistant_text(text: str) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    position = 0
    for match in _THINK_TAG.finditer(text):
        prefix = text[position : match.start()]
        if prefix.strip():
            parts.append({"type": "text", "text": prefix.strip()})
        parts.append({"type": "reasoning", "text": str(match.group(1) or "").strip()})
        position = match.end()
    suffix = text[position:]
    if suffix.strip():
        parts.append({"type": "text", "text": suffix.strip()})
    return parts


def _sanitize_user_text(text: str) -> str:
    value = text
    marker_index = value.find(_USER_MARKER)
    if marker_index >= 0:
        value = value[marker_index + len(_USER_MARKER) :]
    trailing_index = value.find(_REUSE_INSTRUCTION)
    if trailing_index >= 0:
        value = value[:trailing_index]
    return value.strip()


def _user_parts(entry: HistoryEntry) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []

    def collect(value: object) -> None:
        if isinstance(value, str):
            text = _sanitize_user_text(value)
            if text:
                parts.append({"type": "text", "text": text})
            return
        if isinstance(value, list):
            for item in value:
                collect(item)
            return
        if not isinstance(value, Mapping):
            return
        item_type = str(value.get("type") or "").strip().lower()
        if item_type and item_type not in {"text", "input_text"}:
            return
        if "parts" in value:
            collect(value.get("parts"))
            return
        content = value.get("content")
        if "content" in value and not isinstance(content, str):
            collect(content)
            return
        text_value = value.get("text")
        if text_value is None and isinstance(content, str):
            text_value = content
        if isinstance(text_value, str):
            collect(text_value)

    collect(_entry_value(entry, "content", "parts", "text"))
    return parts


def _append_assistant_parts(value: object, parts: list[dict[str, Any]]) -> None:
    if isinstance(value, str):
        parts.extend(_split_assistant_text(value))
        return
    if isinstance(value, list):
        for item in value:
            _append_assistant_parts(item, parts)
        return
    if not isinstance(value, Mapping):
        return
    item_type = str(value.get("type") or "").strip().lower()
    if item_type in _SKIPPED_ASSISTANT_PART_TYPES:
        return
    if "parts" in value:
        _append_assistant_parts(value.get("parts"), parts)
        return
    content = value.get("content")
    if "content" in value and not isinstance(content, str):
        _append_assistant_parts(content, parts)
        return
    text_value = value.get("text")
    if text_value is None and isinstance(content, str):
        text_value = content
    if item_type in _THINKING_BLOCK_TYPES:
        parts.append({"type": "reasoning", "text": str(text_value or "")})
    elif isinstance(text_value, str):
        parts.extend(_split_assistant_text(text_value))


def _entry_message(entry: HistoryEntry) -> HistoryEntry:
    message = entry.get("message")
    return message if isinstance(message, Mapping) else entry


def _assistant_parts(entry: HistoryEntry) -> list[dict[str, Any]]:
    content = _entry_message(entry).get("content")
    parts: list[dict[str, Any]] = []
    if not isinstance(content, list):
        _append_assistant_parts(content, parts)
        return parts
    for block in content:
        if not isinstance(block, Mapping):
            _append_assistant_parts(block, parts)
            continue
        block_type = str(block.get("type") or "").strip().lower()
        if block_type in _TOOL_CALL_BLOCK_TYPES:
            tool_call_id = str(block.get("id") or "").strip()
            tool_name = str(block.get("name") or "").strip()
            if not tool_call_id or not tool_name:
                continue
            arguments = block.get("arguments")
            parts.append(
                {
                    "type": "tool-call",
                    "toolCallId": tool_call_id,
                    "toolName": tool_name,
                    "args": dict(arguments) if isinstance(arguments, Mapping) else {},
                    "isError": False,
                }
            )
            continue
        if block_type in _THINKING_BLOCK_TYPES and "thinking" in block:
            if block.get("redacted"):
                continue
            parts.append({"type": "reasoning", "text": str(block.get("thinking") or "")})
            continue
        _append_assistant_parts(block, parts)
    return parts


def _tool_result_message(entry: HistoryEntry) -> HistoryEntry | None:
    message = _entry_message(entry)
    role = str(message.get("role") or "").strip().lower()
    return message if role in _TOOL_RESULT_ROLES else None


def _attach_tool_result(
    tool_parts_by_id: Mapping[str, dict[str, Any]],
    message: HistoryEntry,
) -> None:
    part = tool_parts_by_id.get(str(message.get("toolCallId") or "").strip())
    if part is None:
        return
    result: dict[str, Any] = {}
    if "content" in message:
        result["content"] = message.get("content")
    if "details" in message:
        result["details"] = message.get("details")
    part["result"] = result
    part["isError"] = bool(message.get("isError", False))


def _canonical_uuid(value: object) -> str | None:
    raw_value = str(value or "").strip()
    try:
        canonical_value = str(uuid.UUID(raw_value))
    except (AttributeError, TypeError, ValueError):
        return None
    return canonical_value if raw_value == canonical_value else None


def normalized_provenance(data: object) -> dict[str, str] | None:
    """Keep the provenance fields the platform history publishes."""

    if not isinstance(data, Mapping):
        return None
    normalized: dict[str, str] = {}
    for key in _PROVENANCE_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = value.strip()
    raw_actor_kind = data.get("actorKind")
    actor_kind = raw_actor_kind.strip().lower() if isinstance(raw_actor_kind, str) else ""
    if actor_kind in _ACTOR_KINDS:
        normalized["actorKind"] = actor_kind
        actor_uid = _canonical_uuid(data.get("actorUid"))
        if actor_uid is not None:
            normalized["actorUid"] = actor_uid
            actor_name = data.get("actorName")
            if actor_kind == "user" and isinstance(actor_name, str) and actor_name.strip():
                normalized["actorName"] = actor_name.strip()[:255]
    return normalized or None


def _custom_entry_provenance(entry: HistoryEntry) -> dict[str, str] | None:
    if str(entry.get("type") or "").strip() != "custom":
        return None
    if str(entry.get("namespace") or "").strip() != PROVENANCE_NAMESPACE:
        return None
    return normalized_provenance(entry.get("data"))


def current_branch(entries: Sequence[HistoryEntry]) -> list[HistoryEntry]:
    """Return the active branch in order: the newest entry and its ancestors."""

    by_id = {
        str(entry.get("id")): entry
        for entry in entries
        if str(entry.get("id") or "").strip() and entry.get("type") != "leaf"
    }
    leaf_target: str | None = None
    for entry in entries:
        if entry.get("type") == "leaf" and entry.get("entry_id"):
            leaf_target = str(entry["entry_id"])
    if leaf_target is None:
        for entry in reversed(entries):
            entry_id = str(entry.get("id") or "").strip()
            if entry_id and entry.get("type") != "leaf":
                leaf_target = entry_id
                break

    branch: list[HistoryEntry] = []
    visited: set[str] = set()
    while leaf_target and leaf_target not in visited:
        visited.add(leaf_target)
        found = by_id.get(leaf_target)
        if found is None:
            break
        branch.append(found)
        leaf_target = str(found.get("parent_id") or "").strip() or None
    branch.reverse()
    return branch


def project_history(
    branch: Sequence[HistoryEntry],
    *,
    target_agent_uid: str | None = None,
) -> ProjectedHistory:
    """Project an ordered active branch into platform history messages."""

    messages: list[HistoryMessage] = []
    source_indexes: list[int] = []
    last_timestamp: datetime | None = None
    last_turn_error: str | None = None
    role_counts = {"user": 0, "assistant": 0}
    pending_provenance: dict[str, str] | None = None
    tool_parts_by_id: dict[str, dict[str, Any]] = {}

    for index, entry in enumerate(branch):
        entry_type = str(entry.get("type") or "").strip()
        timestamp = entry_timestamp(entry)
        if timestamp is not None:
            last_timestamp = timestamp
        tool_result = _tool_result_message(entry)
        if tool_result is not None:
            _attach_tool_result(tool_parts_by_id, tool_result)
            continue
        if entry_type == "custom":
            stamped = _custom_entry_provenance(entry)
            if stamped is not None:
                pending_provenance = stamped
            continue

        content: list[dict[str, Any]]
        if entry_type in {"compaction", "branch_summary"}:
            summary = str(entry.get("summary") or "").strip()
            if not summary:
                continue
            role = "assistant"
            label = (
                "Previous conversation was compacted. Summary"
                if entry_type == "compaction"
                else "Branch summary"
            )
            content = [{"type": "text", "text": f"{label}:\n\n{summary}"}]
            message_id = f"{entry_type}_{entry.get('id')}"
        else:
            role = entry_role(entry)
            if role not in role_counts:
                continue
            last_turn_error = _turn_error(entry) if role == "assistant" else None
            content = []
            source_parts = _user_parts(entry) if role == "user" else _assistant_parts(entry)
            for part in source_parts:
                part_type = part.get("type")
                if part_type == "tool-call":
                    content.append(part)
                    tool_parts_by_id[str(part["toolCallId"])] = part
                elif part_type == "reasoning" or (
                    part_type == "text" and str(part.get("text", "")).strip()
                ):
                    content.append({"type": part_type, "text": str(part.get("text", ""))})
            if not content:
                continue
            role_counts[role] += 1
            message_id = f"{'u' if role == 'user' else 'a'}_{role_counts[role]}"

        created_at = iso_timestamp(timestamp or HISTORY_EPOCH)
        provenance = normalized_provenance(_entry_value(entry, "provenance"))
        if role == "user":
            if provenance is None:
                provenance = pending_provenance
            pending_provenance = None
        if provenance is not None:
            provenance = dict(provenance)
            if target_agent_uid:
                provenance["targetAgentUid"] = target_agent_uid
        messages.append(
            {
                "id": message_id,
                "role": role,
                "createdAt": created_at,
                "completedAt": created_at if role == "assistant" else None,
                "content": content,
                "provenance": provenance,
            }
        )
        source_indexes.append(index)

    return ProjectedHistory(
        messages=messages,
        source_indexes=source_indexes,
        last_timestamp=last_timestamp,
        last_turn_error=last_turn_error,
    )


def _turn_error(entry: HistoryEntry) -> str | None:
    message = _entry_message(entry)
    if str(message.get("stopReason") or "") != "error":
        return None
    detail = str(message.get("errorMessage") or "").strip()
    return detail or TURN_ERROR_FALLBACK


def message_text(message: Mapping[str, Any]) -> str:
    """Return a projected message's visible text as one whitespace-normalized line."""

    content = message.get("content")
    if not isinstance(content, list):
        return ""
    texts = [
        str(part.get("text") or "")
        for part in content
        if isinstance(part, Mapping) and part.get("type") == "text"
    ]
    return " ".join(" ".join(texts).split())


def latest_message_preview(messages: Sequence[Mapping[str, Any]]) -> str | None:
    """Return the bounded text of the newest message that has visible text."""

    for message in reversed(messages):
        text = message_text(message)
        if not text:
            continue
        if len(text) <= PREVIEW_MAX_CHARACTERS:
            return text
        return text[: PREVIEW_MAX_CHARACTERS - 1].rstrip() + "…"
    return None
