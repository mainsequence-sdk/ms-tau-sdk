"""Local Agent inspection and project-tool workbench routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ms_tau_sdk.protocols.a2a_message import sse
from ms_tau_sdk.runtime.manager import SessionRuntimeManager
from ms_tau_sdk.settings import TauSDKSettings

from .a2a import _effective_agent_card
from .dependencies import runtime_manager, settings
from .models import ToolTestRequest, ToolValidationRequest

router = APIRouter(prefix="/api/local/v1/sessions")
RuntimeManagerDep = Annotated[SessionRuntimeManager, Depends(runtime_manager)]
SettingsDep = Annotated[TauSDKSettings, Depends(settings)]


def _local_session(config: TauSDKSettings, session_uid: str) -> str:
    if not config.local_mode:
        raise HTTPException(status_code=409, detail="Agent inspection is local-mode only")
    return config.local_session_uid(session_uid)


@router.get("/{session_uid}/agent-inspection")
async def agent_inspection(
    session_uid: str,
    manager: RuntimeManagerDep,
    config: SettingsDep,
) -> dict[str, object]:
    resolved = _local_session(config, session_uid)
    result = await manager.local_agent_inspection(resolved)
    if result.get("available"):
        card = result.get("agentCard")
        result["agentCard"] = _effective_agent_card(card if isinstance(card, dict) else None)
    return result


@router.get("/{session_uid}/extension-sources/{source_uid}")
async def extension_source(
    session_uid: str,
    source_uid: str,
    manager: RuntimeManagerDep,
    config: SettingsDep,
) -> dict[str, object]:
    resolved = _local_session(config, session_uid)
    return manager.local_extension_source(resolved, source_uid)


@router.post("/{session_uid}/tools/{tool_name}:validate")
async def validate_tool_test(
    session_uid: str,
    tool_name: str,
    body: ToolValidationRequest,
    manager: RuntimeManagerDep,
    config: SettingsDep,
) -> dict[str, object]:
    resolved = _local_session(config, session_uid)
    return manager.validate_project_tool_test(
        resolved,
        tool_name,
        catalog_digest=body.catalog_digest,
        arguments=body.arguments,
    )


@router.post("/{session_uid}/tools/{tool_name}:test")
async def test_tool(
    session_uid: str,
    tool_name: str,
    body: ToolTestRequest,
    manager: RuntimeManagerDep,
    config: SettingsDep,
) -> StreamingResponse:
    resolved = _local_session(config, session_uid)
    execution = await manager.start_project_tool_test(
        resolved,
        tool_name,
        catalog_digest=body.catalog_digest,
        arguments=body.arguments,
        confirmation=body.confirmation,
    )

    async def stream() -> AsyncIterator[bytes]:
        completed = False
        try:
            async for event in execution.events():
                if event.get("type") == "finished":
                    completed = True
                yield sse(event)
            yield b"data: [DONE]\n\n"
        finally:
            if not completed:
                manager.cancel_project_tool_test(resolved, execution.test_uid)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Tau-Tool-Test-Uid": execution.test_uid,
        },
    )


@router.post("/{session_uid}/tool-tests/{test_uid}:cancel")
async def cancel_tool_test(
    session_uid: str,
    test_uid: str,
    manager: RuntimeManagerDep,
    config: SettingsDep,
) -> dict[str, Any]:
    resolved = _local_session(config, session_uid)
    cancelled = manager.cancel_project_tool_test(resolved, test_uid)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Active tool test not found")
    return {"ok": True, "sessionUid": resolved, "testUid": test_uid}
