"""Custom provider credential hydrate endpoints (ADR 0027, amended 2026-11-10 and by ADR 0030)."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from opentelemetry import baggage
from opentelemetry import context as otel_context
from pydantic import SecretStr, ValidationError
from structlog.testing import capture_logs
from tau_agent import UserMessage
from tau_ai import OpenAICodexProvider, OpenAICompatibleProvider
from tau_coding.provider_catalog import BUILTIN_PROVIDER_CATALOG

from ms_tau_sdk.backend.assertions import ASSERTION_HEADER
from ms_tau_sdk.backend.auth import JWTAuth, RuntimeCredentialAuth
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.hydrate_endpoint import ReleaseRelayAuth
from ms_tau_sdk.backend.local import LocalDevelopmentBackend
from ms_tau_sdk.backend.models import (
    AgentSession,
    ProviderControl,
    TauRuntimeBootstrapRequest,
)
from ms_tau_sdk.backend.release_readiness import release_warmth
from ms_tau_sdk.errors import (
    ApplicationReadinessTimeoutError,
    BackendConflictError,
    BackendError,
    BackendTimeoutError,
    ConfigurationError,
    SessionNotFoundError,
)
from ms_tau_sdk.model_relay import RelayRefused, RelayUpstream, create_model_relay_router
from ms_tau_sdk.providers.factory import ProviderFactory
from ms_tau_sdk.settings import (
    LEGACY_PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID_ENV,
    PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID_ENV,
    TauSDKSettings,
)

RELEASE_UID = "7d1c2f4e-0b6a-4f0e-9a51-3c2d8e9b1a77"
RPC_URL = "https://hydrate.example.test/releases/hunch"
RELEASE_HOST = "hydrate.example.test"
LOCAL_ORIGIN = "http://127.0.0.1:8611"
BACKEND_URL = "http://backend.test"
HYDRATE_PATH = "/api/v1/model-provider-credentials/hydrate/"
ACCESS_PATH = f"/api/v1/resource-releases/{RELEASE_UID}/resolve-runtime-access/"
PROVIDER_SECRET = "sk-provider-secret-never-logged"
BOOTSTRAP_SECRET = "sk-bootstrap-secret-never-used"
SESSION_PASS = "session-pass-never-logged"
RELAY_BASE_URL = f"{RPC_URL}/model-relay"


def _provider_control(provider_name: str = "openai", model: str = "gpt-5.4") -> ProviderControl:
    provider = next(
        provider for provider in BUILTIN_PROVIDER_CATALOG if provider.name == provider_name
    )
    metadata = provider.model_metadata.get(model)
    levels = list(ProviderFactory._thinking_levels(provider, model))
    return ProviderControl(
        schema_version=1,
        catalog_digest=f"sha256:{'0' * 64}",
        provider=provider_name,
        model={
            "model": model,
            "api": (
                "openai-codex-responses"
                if provider.kind == "openai-codex"
                else metadata.api
                if metadata is not None and metadata.api
                else provider.api or provider.kind
            ),
            "input": list(metadata.input if metadata is not None else ()) or ["text"],
            "reasoning": bool(levels),
            "thinking_levels": levels,
        },
    )


def _custom_control() -> ProviderControl:
    return ProviderControl(
        schema_version=1,
        catalog_digest=f"sha256:{'0' * 64}",
        provider="acme-gateway",
        model={
            "model": "acme-model",
            "api": "openai-completions",
            "input": ["text"],
            "reasoning": False,
            "thinking_levels": [],
        },
    )


def _answer(
    provider: str = "openai",
    model: str = "gpt-5.4",
    *,
    uid: str = "release-credential-1",
    kind: str = "api_key",
    credential: dict[str, Any] | None = None,
    environment: str | None = None,
    control: ProviderControl | None = None,
    custom_id: str | None = None,
) -> dict[str, Any]:
    """tdag-django's documented hydrate answer, as a custom endpoint serves it."""

    entry: dict[str, Any] = {
        "uid": uid,
        "status": "active",
        "credential_kind": kind,
        "version": 7,
        "credential_hash": "sha256:not-a-secret",
        "credential": credential or {"type": "api_key", "key": PROVIDER_SECRET},
    }
    if environment is not None:
        entry["organization_environment_uid"] = environment
    if custom_id is not None:
        entry["custom_id"] = custom_id
    return {
        "credentials": {provider: entry},
        "missing": [],
        "revoked": [],
        "provider_control": (control or _provider_control(provider, model)).model_dump(mode="json"),
    }


def _relayed(answer: dict[str, Any]) -> dict[str, Any]:
    """The same answer as an endpoint gives a runtime that announces the relay (ADR 0030).

    The pass takes the secret's place and nothing else is corrected, so a malformed answer stays
    malformed.
    """

    relayed = json.loads(json.dumps(answer))
    for entry in relayed.get("credentials", {}).values():
        credential = entry.get("credential")
        if isinstance(credential, dict):
            for field in ("key", "api_key", "access", "access_token"):
                if field in credential:
                    credential[field] = SESSION_PASS
            if entry.get("credential_kind") == "organization_custom":
                credential.pop("base_url", None)
                credential.pop("headers", None)
        entry["relay_definition"] = {"base_url": RELAY_BASE_URL}
    return relayed


def _access(token: str = "release-token-1", *, expires_in: timedelta | None = None) -> dict:
    access: dict[str, Any] = {"mode": "token", "token": token, "rpc_url": RPC_URL}
    if expires_in is not None:
        access["expires_at"] = (datetime.now(UTC) + expires_in).isoformat()
    return {
        "resource_release_uid": RELEASE_UID,
        "runtime_access": {"state": "ready", "can_request": True},
        "access": access,
    }


def _waking(retry_after_ms: int = 1) -> dict:
    return {
        "resource_release_uid": RELEASE_UID,
        "runtime_access": {
            "state": "waking",
            "can_request": False,
            "retry_after_ms": retry_after_ms,
        },
        "access": None,
    }


class _Network:
    """The platform, the hydrate release and the developer's loopback service, on one transport.

    Each queue answers its requests in order and repeats its last answer.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.access: list[dict | httpx.Response] = [_access()]
        self.hydrate: list[dict | httpx.Response] = [_answer()]
        self.platform_hydrate: list[dict | httpx.Response] = [_answer()]

    @staticmethod
    def _next(
        queue: list[dict | httpx.Response], request: httpx.Request | None = None
    ) -> httpx.Response:
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, httpx.Response):
            return answer
        # A hydrate source relays exactly the runtimes that announce the relay.
        if request is not None and 1 in json.loads(request.content).get(
            "supported_model_relay_versions", []
        ):
            answer = _relayed(answer)
        return httpx.Response(200, json=answer)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "backend.test":
            if request.url.path == "/api/v1/runtime-credentials/token/":
                return httpx.Response(200, json={"access": "runtime-token"})
            if request.url.path == ACCESS_PATH:
                return self._next(self.access)
            if request.url.path == HYDRATE_PATH:
                return self._next(self.platform_hydrate, request)
            return httpx.Response(404, json={"detail": "not found"})
        return self._next(self.hydrate, request)

    def sent(self, *, host: str, path: str | None = None) -> list[httpx.Request]:
        return [
            request
            for request in self.requests
            if request.url.host == host and (path is None or request.url.path == path)
        ]

    def hydrations(self) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.path.endswith(HYDRATE_PATH)]


def _hosted_settings(token_file: Path, **overrides: Any) -> TauSDKSettings:
    return TauSDKSettings(
        _env_file=None,
        backend_url=BACKEND_URL,
        runtime_credential_id="runtime-credential-id",
        runtime_identity_token_file=token_file,
        **overrides,
    )


def _local_settings(tmp_path: Path, **overrides: Any) -> TauSDKSettings:
    return TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        local_state_root=tmp_path / "state",
        backend_url=BACKEND_URL,
        auth_mode="jwt",
        local_mode=True,
        access_token="user-access-token",
        refresh_token="user-refresh-token",
        local_provider="openai",
        local_model="gpt-5.4",
        local_thinking="high",
        **overrides,
    )


@asynccontextmanager
async def _client(
    network: _Network, settings: TauSDKSettings, *, auth: Any = None
) -> AsyncIterator[MainSequenceClient]:
    transport = httpx.MockTransport(network.handle)
    async with (
        httpx.AsyncClient(base_url=settings.backend_url, transport=transport) as http,
        httpx.AsyncClient(transport=transport) as hydrate_http,
    ):
        if auth is None:
            auth = (
                JWTAuth(settings)
                if settings.local_mode
                else RuntimeCredentialAuth(settings, exchange_client=http)
            )
        yield MainSequenceClient(settings, auth, client=http, hydrate_client=hydrate_http)


async def _hosted_hydrate(client: MainSequenceClient, model: str = "gpt-5.4", provider="openai"):
    return await client.hydrate_provider_credential(
        provider, model=model, session_uid="session-1", holder_id="holder-1"
    )


def _no_secret(*values: object) -> None:
    for value in values:
        assert PROVIDER_SECRET not in str(value)
        assert SESSION_PASS not in str(value)
        assert "release-token" not in str(value)


# Settings


def test_release_setting_takes_a_uid_and_empty_means_tdag_django(tmp_path):
    assert (
        TauSDKSettings(
            _env_file=None, provider_credential_hydrate_release_uid=RELEASE_UID.upper()
        ).provider_credential_hydrate_release_uid
        == RELEASE_UID
    )
    unset = TauSDKSettings(_env_file=None, provider_credential_hydrate_release_uid="  ")
    assert unset.provider_credential_hydrate_release_uid is None
    assert unset.provider_credential_source == "mainsequence"
    local = _local_settings(tmp_path, provider_credential_hydrate_release_uid=RELEASE_UID)
    assert local.provider_credential_source == "release"


@pytest.mark.parametrize("value", ["hunch-hydrate", "7d1c2f4e0b6a4f0e9a513c2d8e9b1a77", "1234"])
def test_a_release_setting_that_is_not_a_uid_fails_startup_naming_it(value):
    with pytest.raises(ValidationError) as raised:
        TauSDKSettings(_env_file=None, provider_credential_hydrate_release_uid=value)

    assert "TAU_PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID" in str(raised.value)


def test_the_release_setting_has_a_name_a_workflow_file_may_declare(monkeypatch):
    # The platform reserves MAINSEQUENCE_ and MAIN_SEQUENCE_ for what it issues itself (#144).
    assert (
        PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID_ENV == "TAU_PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID"
    )
    assert not PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID_ENV.startswith(
        ("MAINSEQUENCE_", "MAIN_SEQUENCE_")
    )
    monkeypatch.setenv(PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID_ENV, RELEASE_UID.upper())

    settings = TauSDKSettings(_env_file=None)

    assert settings.provider_credential_hydrate_release_uid == RELEASE_UID
    assert settings.provider_credential_source == "release"


@pytest.mark.asyncio
async def test_a_hosted_runtime_hydrates_from_the_release_its_workflow_declares(
    monkeypatch, runtime_identity_token_file
):
    # A harness_agent's spec.env_vars reaches the runtime as its environment.
    monkeypatch.setenv(PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID_ENV, RELEASE_UID)
    network = _Network()
    async with _client(network, _hosted_settings(runtime_identity_token_file)) as client:
        evidence = await _hosted_hydrate(client)

    assert evidence.credential.secret() == SESSION_PASS
    assert network.sent(host="backend.test", path=HYDRATE_PATH) == []
    [hydrate] = network.hydrations()
    assert hydrate.url.host == RELEASE_HOST


def test_the_earlier_release_setting_name_is_still_read(monkeypatch, tmp_path):
    monkeypatch.setenv(LEGACY_PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID_ENV, RELEASE_UID)
    assert TauSDKSettings(_env_file=None).provider_credential_hydrate_release_uid == RELEASE_UID
    monkeypatch.delenv(LEGACY_PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID_ENV)
    env_file = tmp_path / ".env"
    env_file.write_text(f"{LEGACY_PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID_ENV}={RELEASE_UID}\n")
    assert TauSDKSettings(_env_file=env_file).provider_credential_hydrate_release_uid == RELEASE_UID


def test_the_tau_release_setting_wins_over_the_earlier_name(monkeypatch):
    monkeypatch.setenv(
        LEGACY_PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID_ENV, "11111111-2222-4333-8444-555555555555"
    )
    monkeypatch.setenv(PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID_ENV, RELEASE_UID)

    assert TauSDKSettings(_env_file=None).provider_credential_hydrate_release_uid == RELEASE_UID


@pytest.mark.parametrize(
    "name",
    [
        "TAU_PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID",
        "MAINSEQUENCE_TAU_PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID",
    ],
)
def test_the_release_setting_is_read_from_its_environment_variable(monkeypatch, name):
    monkeypatch.setenv(name, "not-a-uid")
    with pytest.raises(ValidationError) as raised:
        TauSDKSettings(_env_file=None)

    assert PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID_ENV in str(raised.value)
    assert LEGACY_PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID_ENV in str(raised.value)


@pytest.mark.parametrize(
    ("value", "origin"),
    [
        ("http://127.0.0.1:8611", "http://127.0.0.1:8611"),
        ("http://localhost:8611/", "http://localhost:8611"),
        ("HTTPS://LOCALHOST", "https://localhost"),
        ("http://[::1]:9000", "http://[::1]:9000"),
        ("  ", None),
    ],
)
def test_a_local_hydrate_address_is_a_loopback_origin(tmp_path, value, origin):
    settings = _local_settings(tmp_path, local_provider_credential_hydrate_url=value)

    assert settings.local_provider_credential_hydrate_url == origin
    assert settings.provider_credential_source == ("local_address" if origin else "mainsequence")


@pytest.mark.parametrize(
    "value",
    [
        "http://example.test:8611",
        "http://127.0.0.2:8611",
        "http://10.0.0.1:8611",
        "http://0.0.0.0:8611",
        "http://localhost.:8611",
        "ftp://localhost:8611",
        "localhost:8611",
        "http://localhost:8611/api",
        "http://localhost:8611/api/v1/model-provider-credentials/hydrate/",
        "http://localhost:8611/?q=1",
        "http://localhost:8611/?",
        "http://localhost:8611/#fragment",
        "http://developer:hunter2-password@localhost:8611",
        "http://localhost:0",
        "http://localhost:99999",
        "http://localhost:port",
    ],
)
def test_a_local_hydrate_address_that_is_not_a_loopback_origin_fails_naming_it(tmp_path, value):
    with pytest.raises(ValidationError) as raised:
        _local_settings(tmp_path, local_provider_credential_hydrate_url=value)

    assert "TAU_LOCAL_PROVIDER_CREDENTIAL_HYDRATE_URL" in str(raised.value)
    assert "hunter2-password" not in str(raised.value)


def test_a_local_hydrate_address_needs_local_mode():
    with pytest.raises(ValidationError) as raised:
        TauSDKSettings(_env_file=None, local_provider_credential_hydrate_url=LOCAL_ORIGIN)

    assert "TAU_LOCAL_PROVIDER_CREDENTIAL_HYDRATE_URL is valid only with TAU_LOCAL_MODE" in str(
        raised.value
    )


def test_a_local_hydrate_address_and_a_hydrate_release_are_exclusive(tmp_path):
    with pytest.raises(ValidationError) as raised:
        _local_settings(
            tmp_path,
            local_provider_credential_hydrate_url=LOCAL_ORIGIN,
            provider_credential_hydrate_release_uid=RELEASE_UID,
        )

    assert "TAU_LOCAL_PROVIDER_CREDENTIAL_HYDRATE_URL" in str(raised.value)
    assert "TAU_PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID" in str(raised.value)


# Hosted mode, with and without the release


@pytest.mark.asyncio
async def test_unset_hydrates_from_tdag_django_and_reaches_no_other_host(
    runtime_identity_token_file,
):
    network = _Network()
    async with _client(network, _hosted_settings(runtime_identity_token_file)) as client:
        evidence = await _hosted_hydrate(client)

    assert evidence.credential.secret() == SESSION_PASS
    assert evidence.credential.relay_definition.base_url == RELAY_BASE_URL
    assert {request.url.host for request in network.requests} == {"backend.test"}
    assert [request.url.path for request in network.hydrations()] == [HYDRATE_PATH]
    assert network.sent(host="backend.test", path=ACCESS_PATH) == []


@pytest.mark.asyncio
async def test_a_hosted_release_receives_tdag_djangos_body_and_only_its_token(
    runtime_identity_token_file,
):
    unset = _Network()
    async with _client(unset, _hosted_settings(runtime_identity_token_file)) as client:
        await _hosted_hydrate(client)
    network = _Network()
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    # Caller context of the work never travels to the release.
    attached = otel_context.attach(baggage.set_baggage("mainsequence.portal.user", "person-1"))
    try:
        async with _client(network, settings) as client:
            evidence = await _hosted_hydrate(client)
    finally:
        otel_context.detach(attached)

    assert evidence.credential.model_provider_credential_uid == "release-credential-1"
    assert evidence.credential.secret() == SESSION_PASS
    # The token comes from the release's runtime access, with the runtime's own credential and
    # without a delegation.
    [access] = network.sent(host="backend.test", path=ACCESS_PATH)
    assert access.method == "POST"
    assert access.headers["authorization"] == "Bearer runtime-token"
    assert not [name for name in access.headers if name.lower().startswith("x-mainsequence-")]
    # The hydrate goes to the release's RPC URL, never to tdag-django.
    assert network.sent(host="backend.test", path=HYDRATE_PATH) == []
    [hydrate] = network.hydrations()
    assert str(hydrate.url) == f"{RPC_URL}{HYDRATE_PATH}"
    assert hydrate.method == "POST"
    assert hydrate.content == unset.hydrations()[0].content
    assert json.loads(hydrate.content) == {
        "agent_session_uid": "session-1",
        "providers": ["openai"],
        "holder_id": "holder-1",
        "supported_provider_control_schema_versions": [1],
        "execution_selection": {"provider": "openai", "model": "gpt-5.4"},
        "supported_model_relay_versions": [1],
    }
    assert hydrate.headers["authorization"] == "Bearer release-token-1"
    assert hydrate.headers["content-type"] == "application/json"
    names = {name.lower() for name in hydrate.headers}
    assert not [name for name in names if name.startswith("x-mainsequence-")]
    assert ASSERTION_HEADER.lower() not in names
    assert {"baggage", "traceparent", "tracestate", "cookie"}.isdisjoint(names)


@pytest.mark.asyncio
async def test_the_release_token_is_reused_until_it_nears_expiry(runtime_identity_token_file):
    network = _Network()
    network.access = [
        _access("release-token-1", expires_in=timedelta(hours=1)),
        _access("release-token-2", expires_in=timedelta(hours=1)),
    ]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        await _hosted_hydrate(client)
        await _hosted_hydrate(client)

    assert len(network.sent(host="backend.test", path=ACCESS_PATH)) == 1
    assert [request.headers["authorization"] for request in network.hydrations()] == [
        "Bearer release-token-1",
        "Bearer release-token-1",
    ]


@pytest.mark.asyncio
async def test_an_expiring_release_token_is_renewed(runtime_identity_token_file):
    network = _Network()
    network.access = [
        _access("release-token-1", expires_in=timedelta(seconds=5)),
        _access("release-token-2", expires_in=timedelta(hours=1)),
    ]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        await _hosted_hydrate(client)
        await _hosted_hydrate(client)

    assert [request.headers["authorization"] for request in network.hydrations()] == [
        "Bearer release-token-1",
        "Bearer release-token-2",
    ]


@pytest.mark.asyncio
async def test_a_401_gets_a_new_token_once(runtime_identity_token_file):
    network = _Network()
    network.access = [_access("release-token-1"), _access("release-token-2")]
    network.hydrate = [httpx.Response(401), _answer()]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        evidence = await _hosted_hydrate(client)

    assert evidence.credential.secret() == SESSION_PASS
    assert len(network.sent(host="backend.test", path=ACCESS_PATH)) == 2
    assert [request.headers["authorization"] for request in network.hydrations()] == [
        "Bearer release-token-1",
        "Bearer release-token-2",
    ]


@pytest.mark.asyncio
async def test_a_second_401_fails_without_a_value(runtime_identity_token_file):
    network = _Network()
    network.access = [_access("release-token-1"), _access("release-token-2")]
    network.hydrate = [httpx.Response(401)]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        with pytest.raises(BackendError) as raised:
            await _hosted_hydrate(client)

    assert raised.value.backend_status == 401
    assert len(network.hydrations()) == 2
    assert len(network.sent(host="backend.test", path=ACCESS_PATH)) == 2
    _no_secret(raised.value, raised.value.detail)


@pytest.mark.asyncio
async def test_a_403_from_the_release_fails_without_a_value(runtime_identity_token_file):
    network = _Network()
    network.hydrate = [
        httpx.Response(403, json={"error_code": "access_denied", "error_detail": "Not allowed"})
    ]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        with pytest.raises(BackendError) as raised:
            await _hosted_hydrate(client)

    assert raised.value.backend_status == 403
    assert raised.value.detail == {"error_code": "access_denied", "error_detail": "Not allowed"}
    assert len(network.hydrations()) == 1


@pytest.mark.asyncio
async def test_a_403_from_runtime_access_fails_before_any_hydrate(runtime_identity_token_file):
    network = _Network()
    network.access = [httpx.Response(403, json={"code": "access_denied"})]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        with pytest.raises(BackendError) as raised:
            await _hosted_hydrate(client)

    assert raised.value.backend_status == 403
    assert network.hydrations() == []


@pytest.mark.asyncio
async def test_a_409_surfaces_its_code_and_truncated_detail(runtime_identity_token_file):
    network = _Network()
    network.hydrate = [
        httpx.Response(
            409,
            json={
                "error_code": "model_provider_credential_unavailable",
                "error_detail": "No active credential. " + "x" * 900,
                "field_errors": {"providers": ["bad"]},
                "credential": {"key": PROVIDER_SECRET},
            },
        )
    ]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    with capture_logs() as logs:
        async with _client(network, settings) as client:
            with pytest.raises(BackendConflictError) as raised:
                await _hosted_hydrate(client)

    detail = raised.value.detail
    assert detail["error_code"] == "model_provider_credential_unavailable"
    assert detail["error_detail"].startswith("No active credential.")
    assert len(detail["error_detail"]) == 500
    assert set(detail) == {"error_code", "error_detail"}
    _no_secret(raised.value, detail, logs)
    [rejected] = [line for line in logs if line.get("status_code") == 409]
    assert rejected["backend_error_code"] == "model_provider_credential_unavailable"
    assert rejected["target_system"] == "provider_credential_hydrate_release"


def _bad_credential_answer() -> dict[str, Any]:
    answer = _answer()
    answer["credentials"]["openai"]["credential"]["headers"] = {"x-api-key": [PROVIDER_SECRET]}
    return answer


def _two_credentials() -> dict[str, Any]:
    answer = _answer()
    answer["credentials"]["anthropic"] = answer["credentials"]["openai"]
    return answer


def _without(field: str) -> dict[str, Any]:
    answer = _answer()
    answer.pop(field)
    return answer


def _without_uid() -> dict[str, Any]:
    answer = _answer()
    answer["credentials"]["openai"].pop("uid")
    return answer


def _missing() -> dict[str, Any]:
    answer = _answer()
    answer["missing"] = ["openai"]
    return answer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (httpx.Response(200, text="<html>not json</html>"), BackendError),
        (httpx.Response(200, json=[_answer()]), BackendError),
        (httpx.Response(404, json={"detail": "Not found"}), BackendError),
        (httpx.Response(500, json={"detail": "boom"}), BackendError),
        (httpx.Response(302, headers={"location": "https://elsewhere.test/"}), BackendError),
        (_without("provider_control"), BackendError),
        (_answer(model="gpt-5.5"), BackendConflictError),
        (_answer(control=_provider_control("openai", "gpt-5.5")), BackendConflictError),
        (_answer("anthropic"), BackendError),
        (_two_credentials(), BackendError),
        (_without_uid(), BackendError),
        (_missing(), BackendError),
        (_answer(kind="oauth"), BackendError),
        # A relayed credential carries no headers at all (ADR 0030).
        (_bad_credential_answer(), ConfigurationError),
        (httpx.Response(200, json=_answer()), ConfigurationError),
    ],
)
async def test_a_malformed_answer_is_a_hydration_failure_without_a_value(
    runtime_identity_token_file, monkeypatch, answer, error
):
    monkeypatch.setattr("ms_tau_sdk.backend.hydrate_endpoint.asyncio.sleep", AsyncMock())
    network = _Network()
    network.hydrate = [answer]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    with capture_logs() as logs:
        async with _client(network, settings) as client:
            with pytest.raises(error) as raised:
                await _hosted_hydrate(client)

    assert not isinstance(raised.value, SessionNotFoundError)
    _no_secret(raised.value, raised.value.detail, raised.value.__context__, logs)
    assert network.sent(host="backend.test", path=HYDRATE_PATH) == []


@pytest.mark.asyncio
async def test_a_server_error_is_retried_as_tdag_djangos_is(
    runtime_identity_token_file, monkeypatch
):
    monkeypatch.setattr("ms_tau_sdk.backend.hydrate_endpoint.asyncio.sleep", AsyncMock())
    network = _Network()
    network.hydrate = [httpx.Response(503), _answer()]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        evidence = await _hosted_hydrate(client)

    assert evidence.credential.secret() == SESSION_PASS
    assert len(network.hydrations()) == 2


@pytest.mark.asyncio
async def test_a_waking_release_is_waited_for(runtime_identity_token_file):
    network = _Network()
    network.access = [_waking(1), _waking(1), _access()]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        evidence = await _hosted_hydrate(client)

    assert evidence.credential.secret() == SESSION_PASS
    assert len(network.sent(host="backend.test", path=ACCESS_PATH)) == 3


@pytest.mark.asyncio
async def test_a_release_that_stays_waking_fails_after_the_ready_timeout(
    runtime_identity_token_file,
):
    network = _Network()
    network.access = [_waking(20)]
    settings = _hosted_settings(
        runtime_identity_token_file,
        provider_credential_hydrate_release_uid=RELEASE_UID,
        mcp_application_ready_timeout_seconds=0.3,
    )
    async with _client(network, settings) as client:
        with pytest.raises(ApplicationReadinessTimeoutError):
            await _hosted_hydrate(client)

    assert network.hydrations() == []
    assert len(network.sent(host="backend.test", path=ACCESS_PATH)) > 1


def _codex_token() -> str:
    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": "account-id"}}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def _session(provider: str, model: str) -> AgentSession:
    return AgentSession(
        uid="session-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.4.2",
        active_provider=provider,
        active_model=model,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "model", "answer", "built"),
    [
        (
            "openai-codex",
            "gpt-5.5",
            _answer(
                "openai-codex",
                "gpt-5.5",
                kind="oauth",
                credential={
                    "type": "oauth",
                    "access": _codex_token(),
                    "account_id": "account-id",
                },
            ),
            OpenAICodexProvider,
        ),
        ("openai", "gpt-5.4", _answer(), OpenAICompatibleProvider),
        (
            "acme-gateway",
            "acme-model",
            _answer(
                "acme-gateway",
                "acme-model",
                kind="organization_custom",
                control=_custom_control(),
                credential={
                    "type": "organization_custom",
                    "base_url": "https://models.example.test/v1",
                    "api": "openai-completions",
                    "headers": {},
                    "api_key": PROVIDER_SECRET,
                },
            ),
            OpenAICompatibleProvider,
        ),
    ],
)
async def test_documented_answers_build_providers_with_the_unchanged_factory(
    runtime_identity_token_file, provider, model, answer, built
):
    network = _Network()
    network.hydrate = [answer]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        evidence = await _hosted_hydrate(client, model=model, provider=provider)
        runtime = ProviderFactory(client).for_session_credential(
            _session(provider, model), holder_id="holder-1", evidence=evidence
        )

    assert isinstance(runtime.provider, built)


def _expiring_codex(*, uid: str = "release-credential-1", environment: str | None = None) -> dict:
    return _answer(
        "openai-codex",
        "gpt-5.5",
        uid=uid,
        kind="oauth",
        environment=environment,
        credential={
            "type": "oauth",
            "access": _codex_token(),
            "account_id": "account-id",
            "expires": int((datetime.now(UTC) - timedelta(seconds=1)).timestamp() * 1000),
        },
    )


@pytest.mark.asyncio
async def test_near_expiry_the_runtime_hydrates_again_from_the_release(
    runtime_identity_token_file,
):
    network = _Network()
    network.hydrate = [_expiring_codex(), _expiring_codex()]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        evidence = await _hosted_hydrate(client, model="gpt-5.5", provider="openai-codex")
        runtime = ProviderFactory(client).for_session_credential(
            _session("openai-codex", "gpt-5.5"), holder_id="holder-1", evidence=evidence
        )
        await runtime.provider._config.credential_resolver()

    assert len(network.hydrations()) == 2
    assert network.sent(host="backend.test", path=HYDRATE_PATH) == []
    assert {request.url.host for request in network.hydrations()} == {RELEASE_HOST}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refreshed",
    [_expiring_codex(uid="release-credential-2"), _expiring_codex(environment="environment-2")],
)
async def test_a_refresh_that_changes_the_uid_or_environment_is_refused(
    runtime_identity_token_file, refreshed
):
    network = _Network()
    network.hydrate = [_expiring_codex(), refreshed]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        evidence = await _hosted_hydrate(client, model="gpt-5.5", provider="openai-codex")
        runtime = ProviderFactory(client).for_session_credential(
            _session("openai-codex", "gpt-5.5"), holder_id="holder-1", evidence=evidence
        )
        with pytest.raises(ConfigurationError, match="changed the configured provider"):
            await runtime.provider._config.credential_resolver()


@pytest.mark.asyncio
async def test_no_credential_or_token_reaches_the_logs(runtime_identity_token_file):
    network = _Network()
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    with capture_logs() as logs:
        async with _client(network, settings) as client:
            evidence = await _hosted_hydrate(client)

    _no_secret(logs, repr(evidence.credential), repr(client._hydrate_endpoint))
    assert {line["target_system"] for line in logs if "target_system" in line} >= {
        "provider_credential_hydrate_release"
    }


# Local mode


@pytest.mark.asyncio
async def test_local_mode_without_an_endpoint_sends_todays_body(tmp_path):
    network = _Network()
    async with _client(network, _local_settings(tmp_path)) as client:
        await client.hydrate_local_provider_credential(
            "openai",
            model="gpt-5.4",
            thinking_level="high",
            holder_id="local-process",
            agent_session_uid="ignored-without-an-endpoint",
        )

    [hydrate] = network.hydrations()
    assert hydrate.url.host == "backend.test"
    assert hydrate.headers["authorization"] == "Bearer user-access-token"
    assert json.loads(hydrate.content) == {
        "providers": ["openai"],
        "holder_id": "local-process",
        "supported_provider_control_schema_versions": [1],
        "execution_selection": {"provider": "openai", "model": "gpt-5.4", "thinking_level": "high"},
    }


@pytest.mark.asyncio
async def test_a_local_address_receives_the_local_body_with_the_session_and_no_credentials(
    tmp_path,
):
    network = _Network()
    settings = _local_settings(
        tmp_path,
        local_provider_credential_hydrate_url=LOCAL_ORIGIN + "/",
        local_organization_environment_uid="environment-configured",
    )
    network.hydrate = [_answer(custom_id="work")]
    auth = Mock()
    auth.headers = AsyncMock(side_effect=AssertionError("no Main Sequence credential is used"))
    attached = otel_context.attach(baggage.set_baggage("mainsequence.portal.user", "person-1"))
    try:
        async with _client(network, settings, auth=auth) as client:
            evidence = await client.hydrate_local_provider_credential(
                "openai",
                model="gpt-5.4",
                thinking_level="high",
                holder_id="local-process",
                custom_id="work",
                organization_environment_uid="environment-configured",
                agent_session_uid="local-abc-default",
            )
    finally:
        otel_context.detach(attached)

    assert evidence.credential.secret() == PROVIDER_SECRET
    auth.headers.assert_not_awaited()
    [hydrate] = network.requests
    assert str(hydrate.url) == f"{LOCAL_ORIGIN}{HYDRATE_PATH}"
    assert json.loads(hydrate.content) == {
        "agent_session_uid": "local-abc-default",
        "providers": ["openai"],
        "holder_id": "local-process",
        "supported_provider_control_schema_versions": [1],
        "execution_selection": {"provider": "openai", "model": "gpt-5.4", "thinking_level": "high"},
        "organization_environment_uid": "environment-configured",
        "custom_id": "work",
    }
    names = {name.lower() for name in hydrate.headers}
    assert "authorization" not in names
    assert not [name for name in names if name.startswith("x-mainsequence-")]
    assert {"baggage", "traceparent", "cookie"}.isdisjoint(names)


@pytest.mark.asyncio
async def test_a_local_endpoint_that_answers_a_relayed_entry_is_refused(tmp_path):
    # Local mode announces no relay, so a relayed answer is refused, not run (ADR 0030).
    network = _Network()
    settings = _local_settings(tmp_path, local_provider_credential_hydrate_url=LOCAL_ORIGIN)
    network.hydrate = [httpx.Response(200, json=_relayed(_answer()))]
    async with _client(network, settings) as client:
        with pytest.raises(ConfigurationError, match="relay_definition") as raised:
            await client.hydrate_local_provider_credential(
                "openai",
                model="gpt-5.4",
                thinking_level="high",
                holder_id="local-process",
                agent_session_uid="local-abc-default",
            )

    _no_secret(raised.value)
    assert "supported_model_relay_versions" not in json.loads(network.requests[0].content)


@pytest.mark.asyncio
async def test_a_custom_endpoint_hydrates_only_for_a_session(tmp_path):
    network = _Network()
    settings = _local_settings(tmp_path, local_provider_credential_hydrate_url=LOCAL_ORIGIN)
    async with _client(network, settings) as client:
        with pytest.raises(ConfigurationError, match="only for a session"):
            await client.hydrate_local_provider_credential(
                "openai", model="gpt-5.4", thinking_level=None, holder_id="local-process"
            )

    assert network.requests == []


@pytest.mark.asyncio
async def test_a_local_address_refusal_is_reported_like_tdag_djangos(tmp_path):
    network = _Network()
    network.hydrate = [
        httpx.Response(
            409,
            json={
                "error_code": "model_provider_credential_unavailable",
                "error_detail": "Connect your ChatGPT subscription first.",
            },
        )
    ]
    settings = _local_settings(tmp_path, local_provider_credential_hydrate_url=LOCAL_ORIGIN)
    async with _client(network, settings) as client:
        with pytest.raises(BackendConflictError) as raised:
            await client.hydrate_local_provider_credential(
                "openai",
                model="gpt-5.4",
                thinking_level=None,
                holder_id="local-process",
                agent_session_uid="local-abc-default",
            )

    assert raised.value.detail == {
        "error_code": "model_provider_credential_unavailable",
        "error_detail": "Connect your ChatGPT subscription first.",
    }


@pytest.mark.asyncio
async def test_local_mode_with_a_release_uses_the_developers_credential_and_names_the_session(
    tmp_path,
):
    network = _Network()
    settings = _local_settings(tmp_path, provider_credential_hydrate_release_uid=RELEASE_UID)
    async with _client(network, settings) as client:
        await client.hydrate_local_provider_credential(
            "openai",
            model="gpt-5.4",
            thinking_level="high",
            holder_id="local-process",
            agent_session_uid="local-abc-default",
        )

    [access] = network.sent(host="backend.test", path=ACCESS_PATH)
    assert access.headers["authorization"] == "Bearer user-access-token"
    assert not [name for name in access.headers if name.lower().startswith("x-mainsequence-")]
    [hydrate] = network.hydrations()
    assert str(hydrate.url) == f"{RPC_URL}{HYDRATE_PATH}"
    assert hydrate.headers["authorization"] == "Bearer release-token-1"
    assert json.loads(hydrate.content) == {
        "agent_session_uid": "local-abc-default",
        "providers": ["openai"],
        "holder_id": "local-process",
        "supported_provider_control_schema_versions": [1],
        "execution_selection": {"provider": "openai", "model": "gpt-5.4", "thinking_level": "high"},
    }


def _bootstrap_request(holder_id: str = "holder-1") -> TauRuntimeBootstrapRequest:
    return TauRuntimeBootstrapRequest(
        holder_id=holder_id,
        ttl_seconds=60,
        bootstrap_request_uid=f"bootstrap-{holder_id}",
        tau_runtime_version="test",
    )


@asynccontextmanager
async def _local_backend(
    network: _Network, settings: TauSDKSettings
) -> AsyncIterator[LocalDevelopmentBackend]:
    async with _client(network, settings) as services:
        backend = LocalDevelopmentBackend(settings, services)
        try:
            yield backend
        finally:
            await backend.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        {"local_provider_credential_hydrate_url": LOCAL_ORIGIN},
        {"provider_credential_hydrate_release_uid": RELEASE_UID},
    ],
)
async def test_local_readiness_hydrates_nothing_with_a_custom_endpoint(tmp_path, endpoint):
    network = _Network()
    async with _local_backend(network, _local_settings(tmp_path, **endpoint)) as backend:
        await backend.prefetch_provider()

    assert network.requests == []


@pytest.mark.asyncio
async def test_local_readiness_without_an_endpoint_is_unchanged(tmp_path):
    network = _Network()
    async with _local_backend(network, _local_settings(tmp_path)) as backend:
        await backend.prefetch_provider()

    [hydrate] = network.hydrations()
    assert hydrate.url.host == "backend.test"
    assert "agent_session_uid" not in json.loads(hydrate.content)


@pytest.mark.asyncio
async def test_a_local_session_names_itself_at_load_and_refresh(tmp_path):
    network = _Network()
    settings = _local_settings(
        tmp_path,
        local_provider_credential_hydrate_url=LOCAL_ORIGIN,
        local_organization_environment_uid="environment-configured",
    )
    session_uid = settings.local_session_uid(None)
    async with _local_backend(network, settings) as backend:
        # The endpoint omits the Environment, which the configured one does not require.
        bootstrap = await backend.bootstrap_tau_runtime(session_uid, _bootstrap_request())
        await backend.hydrate_provider_credential(
            "openai", model="gpt-5.4", session_uid=session_uid, holder_id="holder-1"
        )

    assert bootstrap.session.model_provider_credential_uid == "release-credential-1"
    assert bootstrap.session.organization_environment_uid is None
    bodies = [json.loads(request.content) for request in network.requests]
    assert [body["agent_session_uid"] for body in bodies] == [session_uid, session_uid]
    assert {body["organization_environment_uid"] for body in bodies} == {"environment-configured"}
    assert {request.url.host for request in network.requests} == {"127.0.0.1"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("refreshed", "message"),
    [
        (_answer(uid="release-credential-2"), "changed the local session credential"),
        (_answer(environment="environment-2"), "changed the local session Environment"),
    ],
)
async def test_a_local_refresh_keeps_the_endpoints_uid_and_environment(
    tmp_path, refreshed, message
):
    network = _Network()
    network.hydrate = [_answer(), refreshed]
    settings = _local_settings(tmp_path, local_provider_credential_hydrate_url=LOCAL_ORIGIN)
    session_uid = settings.local_session_uid(None)
    async with _local_backend(network, settings) as backend:
        await backend.bootstrap_tau_runtime(session_uid, _bootstrap_request())
        with pytest.raises(BackendConflictError, match=message):
            await backend.hydrate_provider_credential(
                "openai", model="gpt-5.4", session_uid=session_uid, holder_id="holder-1"
            )


@pytest.mark.asyncio
async def test_a_local_model_selection_hydrates_from_the_endpoint_for_its_session(tmp_path):
    network = _Network()
    network.hydrate = [_answer(), _answer(model="gpt-5.5")]
    settings = _local_settings(tmp_path, local_provider_credential_hydrate_url=LOCAL_ORIGIN)
    session_uid = settings.local_session_uid("board-chat")
    async with _local_backend(network, settings) as backend:
        await backend.bootstrap_tau_runtime(session_uid, _bootstrap_request())
        changed = await backend.update_session_config(
            session_uid, provider="openai", model="gpt-5.5", thinking_level=None
        )

    assert changed.active_model == "gpt-5.5"
    selection = json.loads(network.requests[-1].content)
    assert selection["agent_session_uid"] == session_uid
    assert selection["execution_selection"] == {"provider": "openai", "model": "gpt-5.5"}


@pytest.mark.asyncio
async def test_a_local_model_selection_is_validated_against_the_endpoints_provider_control(
    tmp_path,
):
    network = _Network()
    network.hydrate = [_answer()]
    settings = _local_settings(tmp_path, local_provider_credential_hydrate_url=LOCAL_ORIGIN)
    session_uid = settings.local_session_uid("board-chat")
    async with _local_backend(network, settings) as backend:
        await backend.bootstrap_tau_runtime(session_uid, _bootstrap_request())
        with pytest.raises(BackendConflictError, match="provider control does not match"):
            await backend.update_session_config(
                session_uid, provider="openai", model="gpt-5.5", thinking_level=None
            )
        session = await backend.get_session(session_uid)

    assert session.active_model == "gpt-5.4"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (httpx.ConnectError("connection refused"), BackendError),
        (httpx.ReadTimeout("timed out"), BackendTimeoutError),
    ],
)
async def test_an_unreachable_endpoint_is_retried_then_fails_without_its_token(
    runtime_identity_token_file, monkeypatch, failure, error
):
    monkeypatch.setattr("ms_tau_sdk.backend.hydrate_endpoint.asyncio.sleep", AsyncMock())
    network = _Network()
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    attempts: list[httpx.Request] = []

    def unreachable(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise failure

    async with (
        httpx.AsyncClient(
            base_url=settings.backend_url, transport=httpx.MockTransport(network.handle)
        ) as http,
        httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as hydrate_http,
    ):
        client = MainSequenceClient(
            settings,
            RuntimeCredentialAuth(settings, exchange_client=http),
            client=http,
            hydrate_client=hydrate_http,
        )
        with pytest.raises(error) as raised:
            await _hosted_hydrate(client)

    assert len(attempts) == 3
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    _no_secret(raised.value, raised.value.detail)


@pytest.mark.asyncio
async def test_a_kept_release_token_is_reused_only_while_the_release_is_known_warm(
    runtime_identity_token_file,
):
    from ms_tau_sdk.backend.release_readiness import release_warmth

    network = _Network()
    network.access = [_access(), _access()]
    network.hydrate = [_answer(), _answer(), _answer()]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        await _hosted_hydrate(client)
        await _hosted_hydrate(client)
        asked_while_warm = len(network.sent(host="backend.test", path=ACCESS_PATH))
        # The release may have scaled to zero: the platform is asked again, which wakes it.
        release_warmth.forget(RELEASE_UID)
        await _hosted_hydrate(client)

    assert asked_while_warm == 1
    assert len(network.sent(host="backend.test", path=ACCESS_PATH)) == 2
    assert release_warmth.is_warm(RELEASE_UID) is True


# The release's model relay, behind request identity (ADR 0030, section 7)

_CALLER: ContextVar[str | None] = ContextVar("relay_test_caller", default=None)


class _RequestIdentity:
    """Request identity as mainsequence-sdk applies it to the release.

    A call without a token the platform minted for this release has no Main Sequence caller and is
    refused before any route runs; an admitted call reaches the route without ``Authorization``.
    """

    def __init__(self, app: Any, *, tokens: set[str]) -> None:
        self.app = app
        self.tokens = tokens
        self.seen: list[str] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        authorization = dict(scope["headers"]).get(b"authorization", b"").decode()
        self.seen.append(authorization)
        if authorization.removeprefix("Bearer ") not in self.tokens:
            refusal = JSONResponse({"detail": "No Main Sequence caller."}, status_code=401)
            await refusal(scope, receive, send)
            return
        admitted = [(name, value) for name, value in scope["headers"] if name != b"authorization"]
        caller = _CALLER.set("agent-runtime")
        try:
            await self.app({**scope, "headers": admitted}, receive, send)
        finally:
            _CALLER.reset(caller)


def _provider_sse(request: httpx.Request) -> httpx.Response:
    """A minimal completed stream on any Tau transport."""

    path = request.url.path
    if path.endswith("/messages"):
        events = [
            '{"type":"message_start","message":{"usage":{"input_tokens":1}}}',
            '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}',
            '{"type":"message_delta","delta":{"stop_reason":"end_turn"},'
            '"usage":{"output_tokens":1}}',
        ]
    elif ":streamGenerateContent" in path:
        events = ['{"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}']
    else:
        events = [
            '{"type":"response.output_text.delta","delta":"ok"}',
            '{"type":"response.completed","response":{"status":"completed","usage":'
            '{"input_tokens":1,"output_tokens":1}}}',
        ]
    body = "".join(f"data: {event}\n\n" for event in events)
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=httpx.ByteStream(body.encode())
    )


@dataclass
class _ReleaseRelay:
    identity: _RequestIdentity
    passes: list[tuple[str, str | None]]
    provider_calls: list[httpx.Request]


@asynccontextmanager
async def _release_relay(
    api: str, provider_base_url: str, *, tokens: set[str]
) -> AsyncIterator[_ReleaseRelay]:
    """The hydrate release's relay, served with the SDK's component under its RPC URL."""

    passes: list[tuple[str, str | None]] = []
    provider_calls: list[httpx.Request] = []

    async def resolve_pass(value: str) -> str:
        passes.append((value, _CALLER.get()))
        if value != SESSION_PASS:
            raise RelayRefused("The session pass is not valid for a running session.")
        return "session-1"

    async def resolve_upstream(session: str) -> RelayUpstream:
        return RelayUpstream(
            api=api,
            base_url=provider_base_url,
            secret=SecretStr(PROVIDER_SECRET),
            account_id="account-id" if api == "openai-codex-responses" else None,
        )

    def provider(request: httpx.Request) -> httpx.Response:
        provider_calls.append(request)
        return _provider_sse(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as provider_http:
        app = FastAPI()
        app.include_router(
            create_model_relay_router(
                resolve_pass=resolve_pass, resolve_upstream=resolve_upstream, http=provider_http
            ),
            prefix=httpx.URL(RELAY_BASE_URL).path,
        )
        yield _ReleaseRelay(_RequestIdentity(app, tokens=tokens), passes, provider_calls)


async def _turn(provider: Any, model: str, app: Any, *, auth: httpx.Auth | None) -> list[Any]:
    """One model call of the provider, sent to the relay app with the given client auth."""

    if provider._client is not None:
        await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), auth=auth)
    try:
        return [
            event
            async for event in provider.stream_response(
                model=model, system="system", messages=[UserMessage(content="hello")], tools=[]
            )
        ]
    finally:
        await provider._client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_name", "model", "credential", "provider_base_url", "place"),
    [
        pytest.param(
            "openai", "gpt-5.4", None, "https://api.openai.com/v1", "bearer", id="responses"
        ),
        pytest.param(
            "anthropic",
            "claude-sonnet-4-6",
            None,
            "https://api.anthropic.com",
            "x-api-key",
            id="anthropic",
        ),
        pytest.param(
            "google",
            "gemini-flash-latest",
            None,
            "https://generativelanguage.googleapis.com/v1beta",
            "query",
            id="google",
        ),
        pytest.param(
            "openai-codex",
            "gpt-5.5",
            {"type": "oauth", "access": _codex_token(), "account_id": "account-id"},
            "https://chatgpt.com/backend-api",
            "bearer",
            id="codex",
        ),
    ],
)
async def test_a_model_call_reaches_the_release_relay_as_the_hydrate_call_does(
    runtime_identity_token_file, provider_name, model, credential, provider_base_url, place
):
    network = _Network()
    network.hydrate = [
        _answer(
            provider_name, model, kind="oauth" if credential else "api_key", credential=credential
        )
    ]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        evidence = await _hosted_hydrate(client, model=model, provider=provider_name)
        runtime = ProviderFactory(client).for_session_credential(
            _session(provider_name, model), holder_id="holder-1", evidence=evidence
        )
        auth = runtime.provider._client.auth
        assert isinstance(auth, ReleaseRelayAuth)
        api = evidence.provider_control.model.api
        async with _release_relay(api, provider_base_url, tokens={"release-token-1"}) as relay:
            events = await _turn(runtime.provider, model, relay.identity, auth=auth)

    assert events[-1].type == "done"
    # Request identity admitted the call by the release token, as it admits the hydrate call.
    [hydrate] = network.hydrations()
    assert hydrate.headers["authorization"] == "Bearer release-token-1"
    assert relay.identity.seen == ["Bearer release-token-1"]
    # The relay read the pass from its own header and saw the verified caller.
    assert relay.passes == [(SESSION_PASS, "agent-runtime")]
    [call] = relay.provider_calls
    assert str(call.url).startswith(provider_base_url)
    if place == "bearer":
        assert call.headers["authorization"] == f"Bearer {PROVIDER_SECRET}"
    elif place == "x-api-key":
        assert call.headers["x-api-key"] == PROVIDER_SECRET
    else:
        assert call.url.params["key"] == PROVIDER_SECRET
    sent = (str(call.headers) + str(call.url)).lower()
    assert SESSION_PASS not in sent
    assert "release-token" not in sent
    assert "x-model-relay" not in sent


@pytest.mark.asyncio
async def test_without_the_release_token_request_identity_refuses_the_model_call(
    runtime_identity_token_file,
):
    """3.0.0 sent only the pass, which gives request identity no Main Sequence caller (#142)."""

    network = _Network()
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        evidence = await _hosted_hydrate(client)
        provider = ProviderFactory(backend=None).build(  # type: ignore[arg-type]
            evidence.credential, provider_control=evidence.provider_control, model="gpt-5.4"
        )
        async with _release_relay(
            "openai-responses", "https://api.openai.com/v1", tokens={"release-token-1"}
        ) as relay:
            events = await _turn(provider, "gpt-5.4", relay.identity, auth=None)

    assert events[-1].type == "error"
    assert relay.identity.seen == [f"Bearer {SESSION_PASS}"]
    assert relay.passes == []
    assert relay.provider_calls == []


@pytest.mark.asyncio
async def test_a_refused_release_token_is_renewed_once_for_a_model_call(
    runtime_identity_token_file,
):
    network = _Network()
    network.access = [_access("release-token-1"), _access("release-token-2")]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        evidence = await _hosted_hydrate(client)
        runtime = ProviderFactory(client).for_session_credential(
            _session("openai", "gpt-5.4"), holder_id="holder-1", evidence=evidence
        )
        auth = runtime.provider._client.auth
        async with _release_relay(
            "openai-responses", "https://api.openai.com/v1", tokens={"release-token-2"}
        ) as relay:
            events = await _turn(runtime.provider, "gpt-5.4", relay.identity, auth=auth)

    assert events[-1].type == "done"
    assert relay.identity.seen == ["Bearer release-token-1", "Bearer release-token-2"]
    assert relay.passes == [(SESSION_PASS, "agent-runtime")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relay_base_url",
    [
        "https://relay.example.test/model-relay",
        "https://hydrate.example.test/releases/other/model-relay",
        "https://hydrate.example.test/releases/hunch-other/model-relay",
        "https://hydrate.example.test/releases/hunch/%2e%2e/other/model-relay",
        "http://hydrate.example.test/releases/hunch/model-relay",
        "https://hydrate.example.test:8443/releases/hunch/model-relay",
    ],
)
async def test_a_release_that_answers_a_relay_outside_its_address_is_refused(
    runtime_identity_token_file, relay_base_url
):
    answer = _relayed(_answer())
    answer["credentials"]["openai"]["relay_definition"] = {"base_url": relay_base_url}
    network = _Network()
    network.hydrate = [httpx.Response(200, json=answer)]
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    async with _client(network, settings) as client:
        with pytest.raises(ConfigurationError, match="not under its own address") as raised:
            await _hosted_hydrate(client)

    _no_secret(raised.value)


@pytest.mark.asyncio
async def test_the_release_token_never_leaves_for_another_address(runtime_identity_token_file):
    network = _Network()
    settings = _hosted_settings(
        runtime_identity_token_file, provider_credential_hydrate_release_uid=RELEASE_UID
    )
    sent: list[httpx.Request] = []

    def anywhere(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return _provider_sse(request)

    async with _client(network, settings) as client:
        evidence = await _hosted_hydrate(client)
        runtime = ProviderFactory(client).for_session_credential(
            _session("openai", "gpt-5.4"), holder_id="holder-1", evidence=evidence
        )
        auth = runtime.provider._client.auth
        # The platform moved the release after the hydrate: the relay is no longer under it.
        moved = _access("release-token-2")
        moved["access"]["rpc_url"] = "https://moved.example.test/releases/hunch"
        network.access = [moved]
        release_warmth.forget(RELEASE_UID)
        await runtime.provider._client.aclose()
        runtime.provider._client = httpx.AsyncClient(
            transport=httpx.MockTransport(anywhere), auth=auth
        )
        with pytest.raises(ConfigurationError, match="not under") as raised:
            async for _ in runtime.provider.stream_response(
                model="gpt-5.4", system="system", messages=[UserMessage(content="hi")], tools=[]
            ):
                pass
        await runtime.provider._client.aclose()

    assert sent == []
    _no_secret(raised.value)
