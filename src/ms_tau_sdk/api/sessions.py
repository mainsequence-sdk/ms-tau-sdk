"""Loaded session model, configuration, and cancellation routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.runtime.manager import SessionRuntimeManager
from ms_tau_sdk.settings import TauSDKSettings

from .dependencies import backend, runtime_manager, settings
from .models import CancelRequest

router = APIRouter(prefix="/api/chat")
BackendDep = Annotated[MainSequenceClient, Depends(backend)]
RuntimeManagerDep = Annotated[SessionRuntimeManager, Depends(runtime_manager)]
SettingsDep = Annotated[TauSDKSettings, Depends(settings)]


@router.get("/session-model")
async def session_model(
    client: BackendDep,
    config: SettingsDep,
    session_uid: str = Query(alias="sessionUid"),
) -> dict[str, object]:
    if config.local_mode:
        session_uid = config.local_session_uid(session_uid)
    session = await client.get_session(session_uid)
    return {
        "sessionUid": session_uid,
        "model": {
            "provider": session.active_provider,
            "model": session.active_model,
            "thinkingLevel": session.active_thinking,
        },
    }


@router.post("/session/cancel")
async def cancel_session(
    body: CancelRequest,
    manager: RuntimeManagerDep,
    client: BackendDep,
    config: SettingsDep,
) -> dict[str, object]:
    session_uid = (
        config.local_session_uid(body.session_uid) if config.local_mode else body.session_uid
    )
    state = await client.request_runtime_cancel(
        session_uid,
        message=body.message or "",
        requested_by_holder_id=manager.holder_id,
    )
    await manager.cancel(session_uid)
    return {
        "ok": True,
        "sessionUid": session_uid,
        "agentSessionUid": session_uid,
        "state": state.cancel_state or "not_running",
        "working": state.working,
    }
