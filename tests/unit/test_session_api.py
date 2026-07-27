from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from astro.api.models import CancelRequest, SessionConfigPatch
from astro.api.sessions import cancel_session, patch_session_config, session_model
from astro.backend.models import AgentSession, RuntimeState, RuntimeStatePatch


def test_runtime_state_patch_rejects_worker_state_fields():
    with pytest.raises(ValidationError):
        RuntimeStatePatch.model_validate(
            {
                "active_model": "gpt-5.4",
                "working": True,
            }
        )


@pytest.mark.asyncio
async def test_session_model_reads_provider_selection_from_session():
    client = AsyncMock()
    client.get_session.return_value = AgentSession(
        uid="session-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.3.1",
        llm_provider="openai",
        llm_model="gpt-5.4",
        llm_thinking="high",
    )

    result = await session_model(client, "session-1")

    assert result == {
        "sessionUid": "session-1",
        "model": {
            "provider": "openai",
            "model": "gpt-5.4",
            "thinkingLevel": "high",
        },
    }
    client.get_session.assert_awaited_once_with("session-1")


@pytest.mark.asyncio
async def test_session_config_updates_canonical_session_fields():
    client = AsyncMock()
    client.get_session.return_value = AgentSession(
        uid="session-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.3.1",
        llm_provider="openai",
        llm_model="gpt-5.4",
        llm_thinking="medium",
    )
    manager = SimpleNamespace(
        providers=SimpleNamespace(validate_selection=Mock()),
        evict=AsyncMock(),
    )

    result = await patch_session_config(
        SessionConfigPatch(
            sessionUid="session-1",
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            thinkingLevel="high",
        ),
        client,
        manager,
    )

    manager.providers.validate_selection.assert_called_once_with(
        "anthropic",
        "claude-sonnet-4-20250514",
        "high",
    )
    manager.evict.assert_awaited_once_with("session-1")
    client.patch_runtime_state.assert_awaited_once_with(
        "session-1",
        RuntimeStatePatch(
            active_provider="anthropic",
            active_model="claude-sonnet-4-20250514",
            active_thinking="high",
        ),
    )
    assert result["updatedFields"] == ["provider", "model", "thinkingLevel"]


@pytest.mark.asyncio
async def test_session_cancel_uses_backend_cancel_request():
    client = AsyncMock()
    client.request_runtime_cancel.return_value = RuntimeState(
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.3.1",
        status="working",
        runtime_state="working",
        working=True,
        cancel_state="requested",
        cancel_requested=True,
    )
    manager = SimpleNamespace(
        holder_id="astro-1",
        cancel=AsyncMock(return_value=True),
    )

    result = await cancel_session(
        CancelRequest(sessionUid="session-1", message="stop"),
        manager,
        client,
    )

    client.request_runtime_cancel.assert_awaited_once_with(
        "session-1",
        message="stop",
        requested_by_holder_id="astro-1",
    )
    manager.cancel.assert_awaited_once_with("session-1")
    assert result["state"] == "requested"
    assert result["working"] is True
