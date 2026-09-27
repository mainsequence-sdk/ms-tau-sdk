from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from ms_tau_sdk.api.conversations import router
from ms_tau_sdk.api.dependencies import backend, settings
from ms_tau_sdk.backend.models import (
    LocalConversationMessage,
    LocalConversationMessagePage,
    LocalConversationPage,
    LocalConversationSummary,
)
from ms_tau_sdk.settings import TauSDKSettings


def _settings(tmp_path, *, local_mode: bool = True) -> TauSDKSettings:
    if local_mode:
        return TauSDKSettings(
            _env_file=None,
            workspace=tmp_path,
            auth_mode="jwt",
            local_mode=True,
            access_token="access-token",
            refresh_token="refresh-token",
            local_provider="openai",
            local_model="gpt-5.4",
        )
    return TauSDKSettings(_env_file=None, workspace=tmp_path)


@pytest.mark.asyncio
async def test_local_conversation_routes_serialize_discovery_and_history(tmp_path):
    now = datetime(2026, 9, 27, 12, 30, tzinfo=UTC)
    summary = LocalConversationSummary(
        context_id="local-workspace-context",
        title="Review the report",
        message_count=2,
        latest_message_preview="The report is ready.",
        created_at=now,
        updated_at=now,
    )
    client = AsyncMock()
    client.list_local_conversations.return_value = LocalConversationPage(
        conversations=[summary], next_cursor="next-page"
    )
    client.get_local_conversation_messages.return_value = LocalConversationMessagePage(
        conversation=summary,
        messages=[
            LocalConversationMessage(
                sequence=1,
                message={
                    "messageId": "request-1",
                    "contextId": summary.context_id,
                    "role": "ROLE_USER",
                    "parts": [{"text": "Review the report"}],
                },
                created_at=now,
            )
        ],
        next_before_sequence=None,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[settings] = lambda: _settings(tmp_path)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        listed = await http.get("/api/local/v1/conversations", params={"limit": 25})
        history = await http.get(
            f"/api/local/v1/conversations/{summary.context_id}/messages",
            params={"limit": 50, "beforeSequence": 10},
        )

    assert listed.status_code == 200
    assert listed.json()["conversations"][0] == {
        "contextId": summary.context_id,
        "title": "Review the report",
        "messageCount": 2,
        "latestMessagePreview": "The report is ready.",
        "createdAt": "2026-09-27T12:30:00Z",
        "updatedAt": "2026-09-27T12:30:00Z",
    }
    assert listed.json()["nextCursor"] == "next-page"
    assert history.json()["messages"][0]["message"]["role"] == "ROLE_USER"
    client.list_local_conversations.assert_awaited_once_with(limit=25, cursor=None)
    client.get_local_conversation_messages.assert_awaited_once_with(
        summary.context_id, limit=50, before_sequence=10
    )


@pytest.mark.asyncio
async def test_conversation_discovery_is_rejected_outside_local_mode(tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: AsyncMock()
    app.dependency_overrides[settings] = lambda: _settings(tmp_path, local_mode=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.get("/api/local/v1/conversations")

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Local conversation discovery requires TAU_LOCAL_MODE=true"
    )
