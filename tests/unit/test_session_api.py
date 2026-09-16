from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from ms_tau_sdk.api.models import CancelRequest
from ms_tau_sdk.api.sessions import cancel_session, session_model
from ms_tau_sdk.backend.models import AgentSession, RuntimeState, RuntimeStatePatch


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
        holder_id="ms-tau-1",
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
        requested_by_holder_id="ms-tau-1",
    )
    manager.cancel.assert_awaited_once_with("session-1")
    assert result["state"] == "requested"
    assert result["working"] is True
