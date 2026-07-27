"""Model catalog and credential-status routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from astro.backend.client import MainSequenceClient
from astro.providers.catalog import collect_model_catalog

from .dependencies import backend

router = APIRouter(prefix="/api")
BackendDep = Annotated[MainSequenceClient, Depends(backend)]


class ProviderSignInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_by_user_uid: str | None = None
    agent_session_uid: str | None = None
    credential: dict[str, object]
    base_version: int = Field(default=0, ge=0)


class ProviderSignOffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_by_user_uid: str | None = None


@router.get("/chat/get_available_models")
@router.get("/models/catalog")
async def models(
    client: BackendDep,
    created_by_user_uid: str | None = Query(default=None),
    x_mainsequence_user_uid: str | None = Header(default=None),
) -> dict[str, object]:
    return await collect_model_catalog(
        client,
        created_by_user_uid=created_by_user_uid or x_mainsequence_user_uid,
    )


@router.get("/model-providers")
async def provider_statuses(
    client: BackendDep,
    created_by_user_uid: str | None = Query(default=None),
    x_mainsequence_user_uid: str | None = Header(default=None),
) -> dict[str, object]:
    user_uid = created_by_user_uid or x_mainsequence_user_uid
    statuses = await client.list_provider_statuses(created_by_user_uid=user_uid)
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
    x_mainsequence_user_uid: str | None = Header(default=None),
) -> dict[str, object]:
    user_uid = body.created_by_user_uid or x_mainsequence_user_uid
    if not user_uid:
        raise HTTPException(status_code=400, detail="created_by_user_uid is required")
    result = await client.flush_provider_credential(
        provider=provider,
        created_by_user_uid=user_uid,
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
    x_mainsequence_user_uid: str | None = Header(default=None),
) -> dict[str, object]:
    user_uid = body.created_by_user_uid or x_mainsequence_user_uid
    if not user_uid:
        raise HTTPException(status_code=400, detail="created_by_user_uid is required")
    result = await client.revoke_provider_credential(
        provider=provider,
        created_by_user_uid=user_uid,
    )
    return {
        "ok": True,
        "provider": provider,
        "authenticated": False,
        **result,
    }
