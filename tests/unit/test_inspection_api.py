from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from ms_tau_sdk.api import inspection
from ms_tau_sdk.api.models import ToolTestRequest, ToolValidationRequest
from ms_tau_sdk.settings import TauSDKSettings


def _local_settings(tmp_path):
    return TauSDKSettings(
        _env_file=None,
        local_mode=True,
        auth_mode="jwt",
        local_provider="openai",
        local_model="gpt-5.4",
        workspace=tmp_path,
        startup_dependencies_enabled=False,
    )


@pytest.mark.asyncio
async def test_agent_inspection_normalizes_local_session_and_effective_card(tmp_path):
    config = _local_settings(tmp_path)
    manager = SimpleNamespace(
        local_agent_inspection=AsyncMock(
            return_value={
                "available": True,
                "agentCard": {
                    "name": "Fixture Agent",
                    "capabilities": {"pushNotifications": True},
                },
            }
        )
    )

    result = await inspection.agent_inspection("board-session", manager, config)

    canonical = config.local_session_uid("board-session")
    manager.local_agent_inspection.assert_awaited_once_with(canonical)
    assert result["agentCard"]["capabilities"]["pushNotifications"] is False
    assert result["agentCard"]["capabilities"]["extensions"]


@pytest.mark.asyncio
async def test_validation_and_streaming_test_routes_use_workbench_contract(tmp_path):
    config = _local_settings(tmp_path)

    async def events():
        yield {"type": "started", "testUid": "test-1"}
        yield {"type": "completed", "result": {"content": []}}
        yield {"type": "finished", "outcome": "completed"}

    execution = SimpleNamespace(test_uid="test-1", events=events)
    manager = SimpleNamespace(
        validate_project_tool_test=Mock(return_value={"ok": True, "arguments": {"symbol": "MSFT"}}),
        start_project_tool_test=AsyncMock(return_value=execution),
        cancel_project_tool_test=Mock(return_value=True),
    )
    validation = ToolValidationRequest(catalogDigest="sha256:catalog", arguments={"symbol": "MSFT"})
    result = await inspection.validate_tool_test(
        "board-session", "lookup", validation, manager, config
    )
    assert result["ok"] is True

    body = ToolTestRequest(
        catalogDigest="sha256:catalog",
        arguments={"symbol": "MSFT"},
        confirmation="confirmation",
    )
    response = await inspection.test_tool("board-session", "lookup", body, manager, config)
    chunks = [chunk async for chunk in response.body_iterator]
    stream = b"".join(chunks).decode()
    assert '"type":"completed"' in stream
    assert stream.endswith("data: [DONE]\n\n")
    assert response.headers["x-tau-tool-test-uid"] == "test-1"

    cancelled = await inspection.cancel_tool_test("board-session", "test-1", manager, config)
    assert cancelled["ok"] is True
    assert manager.cancel_project_tool_test.call_count == 1


@pytest.mark.asyncio
async def test_inspection_routes_reject_managed_mode(tmp_path):
    config = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        startup_dependencies_enabled=False,
    )
    with pytest.raises(HTTPException) as error:
        await inspection.agent_inspection("session-1", AsyncMock(), config)
    assert error.value.status_code == 409
