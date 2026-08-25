"""Public model catalog built from Tau and backend credential status."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from tau_coding.oauth_registry import get_oauth_provider
from tau_coding.provider_catalog import BUILTIN_PROVIDER_CATALOG

from astro.backend.client import MainSequenceClient


async def collect_model_catalog(
    backend: MainSequenceClient,
    *,
    session_uid: str | None,
) -> dict[str, Any]:
    statuses = (
        await backend.list_provider_statuses(
            session_uid=session_uid,
        )
        if session_uid
        else []
    )
    status_by_provider = {status.provider: status for status in statuses}
    providers = []
    for provider in BUILTIN_PROVIDER_CATALOG:
        status = status_by_provider.get(provider.name)
        available = status is not None and status.status in {"active", "configured"}
        providers.append(
            {
                "provider": provider.name,
                "display_name": provider.display_name,
                "api": provider.api,
                "auth_methods": list(provider.auth_methods),
                "sign_in_available": get_oauth_provider(provider.name) is not None,
                "available": available,
                "credential_status": status.status if status else "missing",
                "default_model": provider.default_model,
                "models": [
                    {
                        "id": model,
                        **(
                            asdict(provider.model_metadata[model])
                            if model in provider.model_metadata
                            else {}
                        ),
                    }
                    for model in provider.models
                ],
            }
        )
    return {
        "version": 2,
        "runtime": "tau",
        "providers": providers,
    }
