"""Loaded session model, configuration, and cancellation routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from astro.backend.client import MainSequenceClient
from astro.runtime.manager import SessionRuntimeManager

from .dependencies import backend, runtime_manager
from .models import CancelRequest

router = APIRouter(prefix="/api/chat")
BackendDep = Annotated[MainSequenceClient, Depends(backend)]
RuntimeManagerDep = Annotated[SessionRuntimeManager, Depends(runtime_manager)]


@router.get("/session-model")
async def session_model(
    client: BackendDep,
    session_uid: str = Query(alias="sessionUid"),
) -> dict[str, object]:
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
) -> dict[str, object]:
    state = await client.request_runtime_cancel(
        body.session_uid,
        message=body.message or "",
        requested_by_holder_id=manager.holder_id,
    )
    await manager.cancel(body.session_uid)
    return {
        "ok": True,
        "sessionUid": body.session_uid,
        "agentSessionUid": body.session_uid,
        "state": state.cancel_state or "not_running",
        "working": state.working,
    }
