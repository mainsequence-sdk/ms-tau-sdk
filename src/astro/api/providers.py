"""Model catalog and credential-status routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from astro.backend.client import MainSequenceClient
from astro.providers.catalog import collect_model_catalog

from .dependencies import backend

router = APIRouter(prefix="/api")
BackendDep = Annotated[MainSequenceClient, Depends(backend)]


class ProviderSignInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_session_uid: str
    credential: dict[str, object]
    base_version: int = Field(default=0, ge=0)


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
) -> dict[str, object]:
    result = await client.flush_provider_credential(
        provider=provider,
        session_uid=body.agent_session_uid,
        credential=body.credential,
        base_version=body.base_version,
    )
    return {
        "ok": True,
        "provider": provider,
        "authenticated": True,
        **result,
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
