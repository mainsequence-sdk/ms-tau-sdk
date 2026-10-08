"""Loaded session model, configuration, and cancellation routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.errors import SessionNotFoundError
from ms_tau_sdk.runtime.manager import SessionRuntimeManager
from ms_tau_sdk.settings import TauSDKSettings

from .dependencies import backend, runtime_manager, settings
from .models import CancelRequest, SessionModelSelection
from .request_identity import require_session_access

router = APIRouter(prefix="/api/chat")
BackendDep = Annotated[MainSequenceClient, Depends(backend)]
RuntimeManagerDep = Annotated[SessionRuntimeManager, Depends(runtime_manager)]
SettingsDep = Annotated[TauSDKSettings, Depends(settings)]


def _session_model(
    session_uid: str,
    *,
    provider: str | None,
    model: str | None,
    thinking_level: str | None,
    custom_id: str | None,
) -> dict[str, object]:
    return {
        "sessionUid": session_uid,
        "model": {
            "provider": provider,
            "model": model,
            "thinkingLevel": thinking_level,
            **({"customId": custom_id} if custom_id is not None else {}),
        },
    }


def _thinking_level(
    manager: SessionRuntimeManager,
    session_uid: str,
    selected: str | None,
) -> str | None:
    # A loaded session runs a selected level its model cannot run at a fallback level instead.
    if selected is None:
        return None
    return manager.running_thinking_level(session_uid) or selected


@router.get("/session-model")
async def session_model(
    client: BackendDep,
    manager: RuntimeManagerDep,
    config: SettingsDep,
    session_uid: str = Query(alias="sessionUid"),
) -> dict[str, object]:
    if config.local_mode:
        session_uid = config.local_session_uid(session_uid)
    try:
        session = await require_session_access(client, session_uid)
        if session is None:
            session = await client.get_session(session_uid)
    except SessionNotFoundError:
        if not config.local_mode:
            raise
        # A local session is created by its first turn or model selection. Until then it reports
        # the configured model it would start on.
        return _session_model(
            session_uid,
            provider=config.local_provider,
            model=config.local_model,
            thinking_level=config.local_thinking,
            custom_id=config.local_custom_id,
        )
    return _session_model(
        session_uid,
        provider=session.active_provider,
        model=session.active_model,
        thinking_level=_thinking_level(manager, session_uid, session.active_thinking),
        custom_id=session.custom_id,
    )


@router.get("/model-providers")
async def model_providers(client: BackendDep, config: SettingsDep) -> dict[str, object]:
    if not config.local_mode:
        raise HTTPException(status_code=409, detail="Provider catalog is local-mode only")
    return await client.list_model_providers(
        **(
            {"organization_environment_uid": config.local_organization_environment_uid}
            if config.local_organization_environment_uid
            else {}
        )
    )


@router.put("/session-model")
async def select_session_model(
    body: SessionModelSelection,
    manager: RuntimeManagerDep,
    config: SettingsDep,
) -> dict[str, object]:
    if not config.local_mode:
        raise HTTPException(status_code=409, detail="Model selection is local-mode only")
    session_uid = config.local_session_uid(body.session_uid)
    await manager.change_session_model(
        session_uid,
        provider=body.provider,
        model=body.model,
        thinking_level=body.thinking_level,
        **({"custom_id": body.custom_id} if body.custom_id is not None else {}),
    )
    session = await manager.backend.get_session(session_uid)
    return _session_model(
        session_uid,
        provider=session.active_provider,
        model=session.active_model,
        thinking_level=_thinking_level(manager, session_uid, session.active_thinking),
        custom_id=session.custom_id,
    )


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
    await require_session_access(client, session_uid)
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
