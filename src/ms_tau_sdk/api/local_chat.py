"""Reload-safe local `/api/chat` sessions and the local Agent's identity (ADR 0018)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.models import LocalChatSessionSummary
from ms_tau_sdk.protocols.chat_history import (
    HISTORY_VERSION,
    HistoryMessage,
    current_branch,
    iso_timestamp,
    latest_message_preview,
    project_history,
)
from ms_tau_sdk.runtime.manager import SessionRuntimeManager
from ms_tau_sdk.settings import TauSDKSettings

from .dependencies import backend, runtime_manager, settings

router = APIRouter(prefix="/api/local/v1")
BackendDep = Annotated[MainSequenceClient, Depends(backend)]
RuntimeManagerDep = Annotated[SessionRuntimeManager, Depends(runtime_manager)]
SettingsDep = Annotated[TauSDKSettings, Depends(settings)]
logger = structlog.get_logger(__name__)

AGENT_CARD_PATH = Path(".agents") / "agent_card.json"
LOCAL_AGENT_NAME = "Local Main Sequence TAU Agent"


def _require_local(config: TauSDKSettings) -> None:
    if not config.local_mode:
        raise HTTPException(
            status_code=409,
            detail="Local chat sessions require TAU_LOCAL_MODE=true",
        )


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    name: str | None = None
    description: str | None = None


def _read_agent_identity(workspace: Path) -> AgentIdentity:
    """Read the name and description of the workspace's `.agents/agent_card.json`.

    The platform takes a deployed Agent's name and description from the same file.
    A missing or unreadable card leaves both unset rather than inventing them.
    """

    path = workspace / AGENT_CARD_PATH
    try:
        card = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AgentIdentity()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        logger.warning(
            "local_agent.card_unreadable",
            message="The workspace Agent Card could not be read",
            path=str(AGENT_CARD_PATH),
            error_type=type(error).__name__,
        )
        return AgentIdentity()
    if not isinstance(card, dict):
        return AgentIdentity()

    def text(key: str) -> str | None:
        value = card.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    return AgentIdentity(name=text("name"), description=text("description"))


async def workspace_agent_identity(workspace: Path) -> AgentIdentity:
    return await asyncio.to_thread(_read_agent_identity, workspace)


@dataclass(frozen=True, slots=True)
class _SessionProjection:
    summary: LocalChatSessionSummary
    messages: list[HistoryMessage]
    in_progress: HistoryMessage | None
    status: str
    error: str | None
    updated_at: datetime


async def _project_session(
    client: MainSequenceClient,
    manager: SessionRuntimeManager,
    config: TauSDKSettings,
    session_uid: str,
) -> _SessionProjection:
    """Project a session's durable branch plus the turn this process is running.

    The running turn is read before the durable entries. If that turn committed in
    between, its durable entries already hold everything the live view had.
    """

    live_turn = manager.live_turn(session_uid)
    live_entries = live_turn.entries() if live_turn is not None else None
    transcript = await client.get_local_chat_transcript(
        session_uid,
        turn_uid=live_turn.turn_uid if live_turn is not None else None,
    )
    if transcript.turn_committed:
        live_entries = None
    durable = [
        item.entry
        for item in transcript.entries
        if live_entries is None or live_turn is None or item.turn_uid != live_turn.turn_uid
    ]
    branch = current_branch(durable)
    projected = project_history(
        [*branch, *(live_entries or [])],
        target_agent_uid=config.local_agent_uid,
    )
    messages = projected.messages
    in_progress: HistoryMessage | None = None
    if (
        live_entries is not None
        and messages
        and messages[-1]["role"] == "assistant"
        and projected.source_indexes[-1] >= len(branch)
    ):
        # The running turn's newest assistant message, streaming or awaiting a tool.
        in_progress = {**messages.pop(), "completedAt": None}
    running = (
        live_entries is not None
        or transcript.session.working
        or manager.local_session_running(session_uid)
    )
    status = "running" if running else ("error" if projected.last_turn_error else "completed")
    updated_at = transcript.session.updated_at
    if projected.last_timestamp is not None:
        updated_at = max(updated_at, projected.last_timestamp)
    return _SessionProjection(
        summary=transcript.session,
        messages=messages,
        in_progress=in_progress,
        status=status,
        error=projected.last_turn_error if status == "error" else None,
        updated_at=updated_at,
    )


def _session_summary(
    summary: LocalChatSessionSummary,
    *,
    message_count: int,
    latest_message_preview: str | None,
    updated_at: datetime,
    working: bool,
) -> dict[str, Any]:
    return {
        "sessionUid": summary.session_uid,
        "title": summary.title,
        "messageCount": message_count,
        "latestMessagePreview": latest_message_preview,
        "createdAt": iso_timestamp(summary.created_at),
        "updatedAt": iso_timestamp(updated_at),
        "working": working,
    }


@router.get("/chat-sessions")
async def list_chat_sessions(
    client: BackendDep,
    manager: RuntimeManagerDep,
    config: SettingsDep,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=1024),
) -> dict[str, Any]:
    _require_local(config)
    try:
        page = await client.list_local_chat_sessions(limit=limit, cursor=cursor)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    sessions: list[dict[str, Any]] = []
    for summary in page.sessions:
        if manager.live_turn(summary.session_uid) is None:
            sessions.append(
                _session_summary(
                    summary,
                    message_count=summary.message_count,
                    latest_message_preview=summary.latest_message_preview,
                    updated_at=summary.updated_at,
                    working=(summary.working or manager.local_session_running(summary.session_uid)),
                )
            )
            continue
        projection = await _project_session(client, manager, config, summary.session_uid)
        visible = [
            *projection.messages,
            *([projection.in_progress] if projection.in_progress is not None else []),
        ]
        sessions.append(
            _session_summary(
                summary,
                message_count=len(visible),
                latest_message_preview=latest_message_preview(visible),
                updated_at=max(summary.updated_at, projection.updated_at),
                working=projection.status == "running",
            )
        )
    return {"sessions": sessions, "nextCursor": page.next_cursor}


@router.get("/chat-sessions/{session_uid}/history")
async def chat_session_history(
    session_uid: str,
    client: BackendDep,
    manager: RuntimeManagerDep,
    config: SettingsDep,
) -> dict[str, Any]:
    _require_local(config)
    canonical = config.local_session_uid(session_uid)
    projection = await _project_session(client, manager, config, canonical)
    identity = await workspace_agent_identity(config.workspace)
    return {
        "version": HISTORY_VERSION,
        "session": {
            "sessionId": canonical,
            "threadId": canonical,
            "agentName": identity.name or LOCAL_AGENT_NAME,
            "agentUid": config.local_agent_uid,
            "agentSessionUid": canonical,
            "status": projection.status,
            "startedAt": iso_timestamp(projection.summary.created_at),
            "updatedAt": iso_timestamp(projection.updated_at),
            "error": projection.error,
        },
        "messages": projection.messages,
        "inProgressMessage": projection.in_progress,
    }


@router.get("/agent")
async def local_agent(config: SettingsDep) -> dict[str, str | None]:
    _require_local(config)
    identity = await workspace_agent_identity(config.workspace)
    return {
        "name": identity.name,
        "displayName": identity.name,
        "description": identity.description,
    }
