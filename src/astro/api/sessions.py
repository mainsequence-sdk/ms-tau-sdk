"""Loaded session model, configuration, and cancellation routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from astro.backend.client import MainSequenceClient
from astro.backend.models import RuntimeStatePatch
from astro.errors import ConfigurationError
from astro.runtime.manager import SessionRuntimeManager

from .dependencies import backend, runtime_manager
from .models import CancelRequest, SessionConfigPatch

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


@router.patch("/session-config")
async def patch_session_config(
    body: SessionConfigPatch,
    client: BackendDep,
    manager: RuntimeManagerDep,
) -> dict[str, object]:
    current = await client.get_session(body.session_uid)
    provider = body.provider or current.active_provider
    model = body.model or current.active_model
    thinking_level = body.thinking_level or current.active_thinking
    if not provider or not model:
        raise HTTPException(
            status_code=409,
            detail="Session requires an active provider and model",
        )
    try:
        manager.providers.validate_selection(provider, model, thinking_level)
    except ConfigurationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    await manager.evict(body.session_uid)
    await client.patch_runtime_state(
        body.session_uid,
        RuntimeStatePatch(
            active_provider=provider,
            active_model=model,
            active_thinking=thinking_level,
        ),
    )
    fields = [
        name
        for name, value in (
            ("provider", body.provider),
            ("model", body.model),
            ("thinkingLevel", body.thinking_level),
        )
        if value is not None
    ]
    return {
        "ok": True,
        "sessionUid": body.session_uid,
        "updatedFields": fields,
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
