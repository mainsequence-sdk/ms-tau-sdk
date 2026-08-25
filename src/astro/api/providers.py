"""Model catalog and credential-status routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from astro.backend.client import MainSequenceClient
from astro.providers.catalog import collect_model_catalog
from astro.providers.signin import ProviderSignInManager

from .dependencies import backend, provider_signin_manager

router = APIRouter(prefix="/api")
BackendDep = Annotated[MainSequenceClient, Depends(backend)]
ProviderSignInDep = Annotated[
    ProviderSignInManager,
    Depends(provider_signin_manager),
]


class ProviderSignInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_session_uid: str
    credential: dict[str, object] | None = None
    base_version: int = Field(default=0, ge=0)


class ProviderSignInManualRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str


class ProviderSignOffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_session_uid: str


@router.get("/chat/get_available_models")
@router.get("/models/catalog")
async def models(
    client: BackendDep,
    agent_session_uid: str | None = Query(default=None),
) -> dict[str, object]:
    return await collect_model_catalog(
        client,
        session_uid=agent_session_uid,
    )


@router.get("/model-providers")
async def provider_statuses(
    client: BackendDep,
    agent_session_uid: str = Query(),
) -> dict[str, object]:
    statuses = await client.list_provider_statuses(
        session_uid=agent_session_uid,
    )
    return {
        "version": 2,
        "providers": [status.model_dump(mode="json") for status in statuses],
        "backendCredentialStatus": {"ok": True},
    }


@router.post("/model-providers/{provider}/signin")
async def provider_signin(
    provider: str,
    body: ProviderSignInRequest,
    client: BackendDep,
    signin: ProviderSignInDep,
) -> JSONResponse:
    if body.credential is None:
        attempt = await signin.start(
            provider=provider,
            agent_session_uid=body.agent_session_uid,
        )
        return JSONResponse(
            status_code=202,
            content={
                "ok": True,
                "provider": provider,
                "attempt": attempt,
            },
        )
    result = await client.flush_provider_credential(
        provider=provider,
        session_uid=body.agent_session_uid,
        credential=body.credential,
        base_version=body.base_version,
    )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "provider": provider,
            "authenticated": True,
            **result,
        },
    )


@router.get("/model-providers/{provider}/signin/{attempt_id}")
async def provider_signin_attempt(
    provider: str,
    attempt_id: str,
    signin: ProviderSignInDep,
    agent_session_uid: str = Query(),
) -> dict[str, object]:
    return {
        "ok": True,
        "attempt": signin.get(
            provider=provider,
            agent_session_uid=agent_session_uid,
            attempt_id=attempt_id,
        ),
    }


@router.post("/model-providers/{provider}/signin/{attempt_id}/manual")
async def provider_signin_manual(
    provider: str,
    attempt_id: str,
    body: ProviderSignInManualRequest,
    signin: ProviderSignInDep,
    agent_session_uid: str = Query(),
) -> JSONResponse:
    attempt = await signin.continue_attempt(
        provider=provider,
        agent_session_uid=agent_session_uid,
        attempt_id=attempt_id,
        value=body.input,
    )
    return JSONResponse(
        status_code=202,
        content={"ok": True, "attempt": attempt},
    )


@router.post("/model-providers/{provider}/signin/{attempt_id}/cancel")
async def provider_signin_cancel(
    provider: str,
    attempt_id: str,
    signin: ProviderSignInDep,
    agent_session_uid: str = Query(),
) -> dict[str, object]:
    return {
        "ok": True,
        "attempt": await signin.cancel(
            provider=provider,
            agent_session_uid=agent_session_uid,
            attempt_id=attempt_id,
        ),
    }


@router.post("/model-providers/{provider}/signoff")
async def provider_signoff(
    provider: str,
    body: ProviderSignOffRequest,
    client: BackendDep,
) -> dict[str, object]:
    result = await client.revoke_provider_credential(
        provider=provider,
        session_uid=body.agent_session_uid,
    )
    return {
        "ok": True,
        "provider": provider,
        "authenticated": False,
        **result,
    }
