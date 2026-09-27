"""SDK-owned discovery and hydration for direct local A2A Message conversations."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.models import (
    LocalConversationMessagePage,
    LocalConversationPage,
    LocalConversationSummary,
)
from ms_tau_sdk.settings import TauSDKSettings

from .dependencies import backend, settings

router = APIRouter(prefix="/api/local/v1/conversations")
BackendDep = Annotated[MainSequenceClient, Depends(backend)]
SettingsDep = Annotated[TauSDKSettings, Depends(settings)]


def _require_local(config: TauSDKSettings) -> None:
    if not config.local_mode:
        raise HTTPException(
            status_code=409,
            detail="Local conversation discovery requires TAU_LOCAL_MODE=true",
        )


def _summary(summary: LocalConversationSummary) -> dict[str, Any]:
    return {
        "contextId": summary.context_id,
        "title": summary.title,
        "messageCount": summary.message_count,
        "latestMessagePreview": summary.latest_message_preview,
        "createdAt": summary.created_at.isoformat().replace("+00:00", "Z"),
        "updatedAt": summary.updated_at.isoformat().replace("+00:00", "Z"),
    }


def _conversation_page(page: LocalConversationPage) -> dict[str, Any]:
    return {
        "conversations": [_summary(item) for item in page.conversations],
        "nextCursor": page.next_cursor,
    }


def _message_page(page: LocalConversationMessagePage) -> dict[str, Any]:
    return {
        "conversation": _summary(page.conversation),
        "messages": [
            {
                "sequence": item.sequence,
                "message": item.message,
                "createdAt": item.created_at.isoformat().replace("+00:00", "Z"),
            }
            for item in page.messages
        ],
        "nextBeforeSequence": page.next_before_sequence,
    }


@router.get("")
async def list_conversations(
    client: BackendDep,
    config: SettingsDep,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=1024),
) -> dict[str, Any]:
    _require_local(config)
    try:
        page = await client.list_local_conversations(limit=limit, cursor=cursor)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return _conversation_page(page)


@router.get("/{context_id}/messages")
async def conversation_messages(
    context_id: str,
    client: BackendDep,
    config: SettingsDep,
    limit: int = Query(default=100, ge=1, le=200),
    before_sequence: int | None = Query(default=None, alias="beforeSequence", ge=1),
) -> dict[str, Any]:
    _require_local(config)
    page = await client.get_local_conversation_messages(
        context_id,
        limit=limit,
        before_sequence=before_sequence,
    )
    return _message_page(page)
