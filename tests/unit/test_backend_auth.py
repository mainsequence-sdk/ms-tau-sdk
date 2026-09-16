import httpx
import pytest

from ms_tau_sdk.backend.auth import RuntimeCredentialAuth
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.settings import TauSDKSettings


@pytest.mark.asyncio
async def test_runtime_credential_exchange_is_cached():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"access": "access-token", "token_type": "Bearer", "expires_in": 300},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend:8000",
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
    )
    auth = RuntimeCredentialAuth(settings, exchange_client=client)

    first = await auth.headers()
    second = await auth.headers()

    assert first == second == {"Authorization": "Bearer access-token"}
    assert len(requests) == 1
    assert requests[0].url.path == "/api/v1/runtime-credentials/token/"
    await client.aclose()


@pytest.mark.asyncio
async def test_backend_client_binds_auth_to_shared_http_pool():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"access": "access-token", "token_type": "Bearer"},
        )

    http = httpx.AsyncClient(
        base_url="http://backend:8000",
        transport=httpx.MockTransport(handler),
    )
    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend:8000",
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
    )
    auth = RuntimeCredentialAuth(settings)
    MainSequenceClient(settings, auth, client=http)

    assert await auth.headers() == {"Authorization": "Bearer access-token"}
    assert len(requests) == 1
    await http.aclose()


@pytest.mark.asyncio
async def test_non_idempotent_request_retries_only_after_unauthorized():
    backend_attempts = 0
    token_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal backend_attempts, token_attempts
        if request.url.path.endswith("/runtime-credentials/token/"):
            token_attempts += 1
            return httpx.Response(200, json={"access": f"token-{token_attempts}"})
        backend_attempts += 1
        if backend_attempts == 1:
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, json={"ok": True})

    http = httpx.AsyncClient(
        base_url="http://backend:8000",
        transport=httpx.MockTransport(handler),
    )
    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend:8000",
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
    )
    backend = MainSequenceClient(
        settings,
        RuntimeCredentialAuth(settings),
        client=http,
    )

    result = await backend._request("POST", "/resource", json={"value": 1})

    assert result == {"ok": True}
    assert backend_attempts == 2
    assert token_attempts == 2
    await http.aclose()
