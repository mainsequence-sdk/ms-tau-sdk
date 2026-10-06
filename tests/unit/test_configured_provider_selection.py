import json

import httpx
import pytest

from ms_tau_sdk.backend.auth import JWTAuth
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.errors import BackendError
from ms_tau_sdk.settings import TauSDKSettings


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("selected", [None, "openai-work"])
async def test_optional_subscription_hydration(local, selected):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "credentials": {
                    "openai": {
                        "credential_kind": "api_key",
                        "custom_id": selected,
                        "organization_environment_uid": "22222222-2222-4222-8222-222222222222",
                        "user_uid": "33333333-3333-4333-8333-333333333333",
                        "credential": {"type": "api_key", "key": "test-key"},
                    }
                },
                "provider_control": {
                    "schema_version": 1,
                    "catalog_digest": "sha256:" + "0" * 64,
                    "provider": "openai",
                    "model": {
                        "model": "test-model",
                        "api": "openai-responses",
                        "input": ["text"],
                        "reasoning": False,
                        "thinking_levels": [],
                    },
                },
            },
        )

    settings = TauSDKSettings(
        _env_file=None, backend_url="http://backend.test", access_token="test"
    )
    async with httpx.AsyncClient(
        base_url=settings.backend_url, transport=httpx.MockTransport(handler)
    ) as http:
        client = MainSequenceClient(settings, JWTAuth(settings), client=http)
        if local:
            evidence = await client.hydrate_local_provider_credential(
                "openai",
                model="test-model",
                thinking_level=None,
                holder_id="test",
                custom_id=selected,
            )
        else:
            evidence = await client.hydrate_provider_credential(
                "openai",
                model="test-model",
                session_uid="session",
                holder_id="test",
                custom_id=selected,
            )
    assert evidence.credential.custom_id == selected
    assert evidence.credential.owner_user_uid == "33333333-3333-4333-8333-333333333333"
    if selected is None:
        assert "custom_id" not in bodies[0]
    else:
        assert bodies[0]["custom_id"] == selected


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("resolved", [None, "22222222-2222-4222-8222-222222222222"])
async def test_explicit_custom_id_cannot_be_ignored_or_substituted(local, resolved, monkeypatch):
    settings = TauSDKSettings(
        _env_file=None, backend_url="http://backend.test", access_token="test"
    )
    client = MainSequenceClient(settings, JWTAuth(settings))

    async def request(*args, **kwargs):
        return {
            "credentials": {
                "openai": {
                    "custom_id": resolved,
                    "credential": {"type": "api_key", "key": "test-key"},
                }
            },
            "provider_control": {
                "schema_version": 1,
                "catalog_digest": "sha256:" + "0" * 64,
                "provider": "openai",
                "model": {
                    "model": "test-model",
                    "api": "openai-responses",
                    "input": ["text"],
                    "reasoning": False,
                    "thinking_levels": [],
                },
            },
        }

    monkeypatch.setattr(client, "_request", request)
    try:
        with pytest.raises(BackendError, match="requested configured provider"):
            if local:
                await client.hydrate_local_provider_credential(
                    "openai",
                    model="test-model",
                    thinking_level=None,
                    holder_id="test",
                    custom_id="openai-work",
                )
            else:
                await client.hydrate_provider_credential(
                    "openai",
                    model="test-model",
                    session_uid="session",
                    holder_id="test",
                    custom_id="openai-work",
                )
    finally:
        await client.aclose()
