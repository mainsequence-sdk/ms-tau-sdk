"""A hosted session calls models only through a relay (ADR 0030, sections 1 to 6)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from structlog.testing import capture_logs
from tau_agent import UserMessage
from tau_ai import (
    AnthropicProvider,
    GoogleGenerativeAIProvider,
    MistralConversationsProvider,
    OpenAICodexProvider,
    OpenAICompatibleProvider,
)
from tau_coding.provider_catalog import BUILTIN_PROVIDER_CATALOG

from ms_tau_sdk.backend.auth import JWTAuth, RuntimeCredentialAuth
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.models import (
    AgentSession,
    ModelRelayDefinition,
    ProviderControl,
    ProviderCredential,
    ProviderExecutionEvidence,
)
from ms_tau_sdk.errors import ConfigurationError
from ms_tau_sdk.model_relay import RelayRefused, RelayUpstream, create_model_relay_router
from ms_tau_sdk.providers.factory import ProviderFactory
from ms_tau_sdk.settings import TauSDKSettings

SESSION_PASS = "session-pass-never-logged"
PROVIDER_SECRET = "sk-provider-secret-never-logged"
RELAY_BASE_URL = "https://relay.example.test/model-relay"
BACKEND_URL = "http://backend.test"
HYDRATE_PATH = "/api/v1/model-provider-credentials/hydrate/"

# One provider of Tau's catalog per wire protocol, the place its transport puts a secret, and the
# path it calls below the relay's base.
TRANSPORTS = [
    pytest.param(
        "openai", "gpt-5.4", OpenAICompatibleProvider, "bearer", "/responses", id="responses"
    ),
    pytest.param(
        "deepseek",
        "deepseek-v4-pro",
        OpenAICompatibleProvider,
        "bearer",
        "/chat/completions",
        id="completions",
    ),
    pytest.param(
        "anthropic",
        "claude-sonnet-4-6",
        AnthropicProvider,
        "x-api-key",
        "/v1/messages",
        id="anthropic",
    ),
    pytest.param(
        "openai-codex", "gpt-5.5", OpenAICodexProvider, "bearer", "/codex/responses", id="codex"
    ),
    pytest.param(
        "google",
        "gemini-2.5-pro",
        GoogleGenerativeAIProvider,
        "query",
        "/models/gemini-2.5-pro:streamGenerateContent",
        id="google",
    ),
    pytest.param(
        "mistral",
        "mistral-large-latest",
        MistralConversationsProvider,
        "bearer",
        "/v1/chat/completions",
        id="mistral",
    ),
]


def _provider_control(provider_name: str, model: str) -> ProviderControl:
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


def _relayed(provider: str, **fields: Any) -> ProviderCredential:
    return ProviderCredential(
        provider=provider,
        credential_kind="oauth" if provider == "openai-codex" else "api_key",
        api_key=None if provider == "openai-codex" else SESSION_PASS,
        access_token=SESSION_PASS if provider == "openai-codex" else None,
        account_id="account-id" if provider == "openai-codex" else None,
        relay_definition=ModelRelayDefinition(base_url=RELAY_BASE_URL),
        **fields,
    )


def _sse(request: httpx.Request) -> httpx.Response:
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
    elif path.endswith("/chat/completions"):
        events = ['{"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}', "[DONE]"]
    else:
        events = [
            '{"type":"response.output_text.delta","delta":"ok"}',
            '{"type":"response.completed","response":{"status":"completed","usage":'
            '{"input_tokens":1,"output_tokens":1}}}',
        ]
    body = "".join(f"data: {event}\n\n" for event in events)
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=httpx.ByteStream(body.encode()),
    )


async def _call(provider: Any, model: str, client: httpx.AsyncClient) -> list[Any]:
    provider._client = client
    return [
        event
        async for event in provider.stream_response(
            model=model,
            system="system",
            messages=[UserMessage(content="hello")],
            tools=[],
        )
    ]


def _pass_from(request: httpx.Request, place: str) -> str | None:
    if place == "x-api-key":
        return request.headers.get("x-api-key")
    if place == "query":
        return request.url.params.get("key")
    authorization = request.headers.get("authorization", "")
    return authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else None


# Section 4: every transport is built on the relay's address and the pass


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider_name", "model", "built", "place", "path"), TRANSPORTS)
async def test_every_transport_calls_the_relay_with_the_pass(
    provider_name, model, built, place, path
):
    requests: list[httpx.Request] = []

    def relay(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _sse(request)

    provider = ProviderFactory(backend=None).build(  # type: ignore[arg-type]
        _relayed(provider_name),
        provider_control=_provider_control(provider_name, model),
        model=model,
    )
    assert isinstance(provider, built)
    async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as client:
        events = await _call(provider, model, client)

    assert events[-1].type == "done"
    [request] = requests
    assert str(request.url).startswith(f"{RELAY_BASE_URL}{path}")
    assert _pass_from(request, place) == SESSION_PASS
    if provider_name == "openai-codex":
        assert request.headers["chatgpt-account-id"] == "account-id"


@pytest.mark.parametrize(("provider_name", "model", "built", "place", "path"), TRANSPORTS)
def test_a_relayed_entry_keeps_no_credential_address_or_header(
    provider_name, model, built, place, path
):
    # A relayed entry carrying an address or headers is refused while it is parsed; a credential
    # built by hand with them still gets only the relay's address and Tau's catalog headers.
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]
    carrying = _relayed(
        provider_name,
        base_url="https://collector.example.test/v1",
        headers={"X-Extra": "1"},
        catalog_definition={"base_url": "https://collector.example.test/v1"},
    )

    provider = factory.build(
        carrying, provider_control=_provider_control(provider_name, model), model=model
    )

    # Only the Anthropic transport is given a base that ends in /v1.
    assert provider._config.base_url.removesuffix("/v1") == RELAY_BASE_URL
    assert "X-Extra" not in provider._config.headers


def test_the_provider_keeps_its_openai_cache_defaults_behind_the_relay():
    # Tau decides prompt-cache keys and session affinity from OpenAI's own host; behind the relay
    # the provider's own address still decides.
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]
    control = _provider_control("openai", "gpt-5.4")
    direct = factory.build(
        ProviderCredential(provider="openai", api_key=PROVIDER_SECRET),
        provider_control=control,
        model="gpt-5.4",
    )
    relayed = factory.build(_relayed("openai"), provider_control=control, model="gpt-5.4")

    assert direct._prompt_cache_key("affinity") == "affinity"
    assert relayed._prompt_cache_key("affinity") == "affinity"
    assert relayed._session_affinity_format(responses=True) == direct._session_affinity_format(
        responses=True
    )

    deepseek = factory.build(
        _relayed("deepseek"),
        provider_control=_provider_control("deepseek", "deepseek-v4-pro"),
        model="deepseek-v4-pro",
    )
    assert deepseek._config.compat.get("supportsPromptCacheKey") is not True


@pytest.mark.parametrize(
    "relay",
    [
        "http://localhost:8000/model-relay",
        "http://127.0.0.1/model-relay",
        "http://169.254.169.254/model-relay",
        "http://metadata.google.internal/model-relay",
        "https://user:pw@relay.example.test/model-relay",
        "ftp://relay.example.test/model-relay",
        "https://relay.example.test/model-relay?next=1",
    ],
)
def test_a_relay_address_the_prohibited_host_checks_refuse_is_refused(relay):
    with pytest.raises(ConfigurationError, match="Model relay definition") as raised:
        ProviderFactory(backend=None).build(  # type: ignore[arg-type]
            _relayed("openai").model_copy(
                update={"relay_definition": ModelRelayDefinition(base_url=relay)}
            ),
            provider_control=_provider_control("openai", "gpt-5.4"),
            model="gpt-5.4",
        )
    assert SESSION_PASS not in str(raised.value)


def _custom_control(model: str = "acme-model") -> ProviderControl:
    return ProviderControl(
        schema_version=1,
        catalog_digest=f"sha256:{'0' * 64}",
        provider="acme-gateway",
        model={
            "model": model,
            "api": "openai-completions",
            "input": ["text"],
            "reasoning": False,
            "thinking_levels": [],
        },
    )


@pytest.mark.asyncio
async def test_an_organization_custom_provider_runs_through_the_relay_without_its_address():
    requests: list[httpx.Request] = []

    def relay(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _sse(request)

    credential = MainSequenceClient.provider_credential_from_hydration(
        "acme-gateway",
        {
            "credentials": {
                "acme-gateway": {
                    "uid": "custom-1",
                    "credential_kind": "organization_custom",
                    "credential": {
                        "type": "organization_custom",
                        "api": "openai-completions",
                        "api_key": SESSION_PASS,
                    },
                    "relay_definition": {"base_url": RELAY_BASE_URL},
                }
            }
        },
        relayed=True,
    )
    provider = ProviderFactory(backend=None).build(  # type: ignore[arg-type]
        credential, provider_control=_custom_control(), model="acme-model"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as client:
        events = await _call(provider, "acme-model", client)

    assert events[-1].type == "done"
    [request] = requests
    assert str(request.url) == f"{RELAY_BASE_URL}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {SESSION_PASS}"


@pytest.mark.parametrize(
    "credential",
    [
        {
            "type": "organization_custom",
            "api": "openai-completions",
            "api_key": SESSION_PASS,
            "base_url": "https://models.example.test/v1",
        },
        {
            "type": "organization_custom",
            "api": "openai-completions",
            "api_key": SESSION_PASS,
            "headers": {"X-Org": "1"},
        },
    ],
)
def test_a_relayed_entry_with_an_address_or_headers_is_refused(credential):
    with pytest.raises(ConfigurationError, match="base_url or headers") as raised:
        MainSequenceClient.provider_credential_from_hydration(
            "acme-gateway",
            {
                "credentials": {
                    "acme-gateway": {
                        "credential_kind": "organization_custom",
                        "credential": credential,
                        "relay_definition": {"base_url": RELAY_BASE_URL},
                    }
                }
            },
            relayed=True,
        )
    assert SESSION_PASS not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_name", "model", "endpoint"),
    [
        ("anthropic", "claude-sonnet-4-6", "https://api.anthropic.com/v1/messages"),
        ("minimax", "MiniMax-M2.7", "https://api.minimax.io/anthropic/v1/messages"),
        (
            "fireworks",
            "accounts/fireworks/models/deepseek-v4-flash",
            "https://api.fireworks.ai/inference/v1/messages",
        ),
    ],
)
async def test_an_anthropic_protocol_provider_is_called_under_v1(provider_name, model, endpoint):
    # Tau's catalog addresses for the Anthropic protocol have no /v1, and its transport adds
    # none: without it the call goes to `<base>/messages`, which no provider serves.
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _sse(request)

    control = _provider_control(provider_name, model)
    for credential, expected in (
        (ProviderCredential(provider=provider_name, api_key=PROVIDER_SECRET), endpoint),
        (_relayed(provider_name), f"{RELAY_BASE_URL}/v1/messages"),
    ):
        provider = ProviderFactory(backend=None).build(  # type: ignore[arg-type]
            credential, provider_control=control, model=model
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await _call(provider, model, client)
        assert str(requests[-1].url) == expected


def test_an_anthropic_address_that_ends_in_v1_is_kept():
    control = _provider_control("anthropic", "claude-sonnet-4-6")
    provider = ProviderFactory(backend=None).build(  # type: ignore[arg-type]
        _relayed("anthropic").model_copy(
            update={"relay_definition": ModelRelayDefinition(base_url=f"{RELAY_BASE_URL}/v1/")}
        ),
        provider_control=control,
        model="claude-sonnet-4-6",
    )

    assert provider._config.base_url == f"{RELAY_BASE_URL}/v1"


# Section 5: a refusal by the relay reaches the runtime once, as an authorization failure


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider_name", "model", "built", "place", "path"), TRANSPORTS)
async def test_a_relay_refusal_is_not_retried(provider_name, model, built, place, path):
    requests: list[httpx.Request] = []

    def relay(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            403, json={"detail": "The session pass is not valid for a running session."}
        )

    provider = ProviderFactory(backend=None).build(  # type: ignore[arg-type]
        _relayed(provider_name),
        provider_control=_provider_control(provider_name, model),
        model=model,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as client:
        events = await _call(provider, model, client)

    assert len(requests) == 1
    assert events[-1].type == "error"
    assert SESSION_PASS not in json.dumps([str(event) for event in events])


# The runtime, the SDK's relay and a provider, end to end


@asynccontextmanager
async def _through_the_relay(
    provider_requests: list[httpx.Request], *, api: str, base_url: str, account_id: str | None
) -> AsyncIterator[httpx.AsyncClient]:
    def provider(request: httpx.Request) -> httpx.Response:
        provider_requests.append(request)
        return _sse(request)

    async def resolve_pass(value: str) -> str:
        if value != SESSION_PASS:
            raise RelayRefused("The session pass is not valid for a running session.")
        return "session-1"

    async def resolve_upstream(session: str) -> RelayUpstream:
        return RelayUpstream(
            api=api,
            base_url=base_url,
            secret=SecretStr(PROVIDER_SECRET),
            account_id=account_id,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), follow_redirects=False
    ) as provider_http:
        app = FastAPI()
        app.include_router(
            create_model_relay_router(
                resolve_pass=resolve_pass, resolve_upstream=resolve_upstream, http=provider_http
            ),
            prefix="/model-relay",
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as runtime_http:
            yield runtime_http


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider_name", "model", "built", "place", "path"), TRANSPORTS)
async def test_a_turn_runs_through_the_sdk_relay_to_the_provider(
    provider_name, model, built, place, path
):
    control = _provider_control(provider_name, model)
    direct = ProviderFactory(backend=None).build(  # type: ignore[arg-type]
        ProviderCredential(
            provider=provider_name,
            credential_kind="oauth" if provider_name == "openai-codex" else "api_key",
            api_key=PROVIDER_SECRET,
            access_token=PROVIDER_SECRET,
            account_id="account-id",
        ),
        provider_control=control,
        model=model,
    )
    direct_requests: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        direct_requests.append(request)
        return _sse(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        await _call(direct, model, client)
    [expected] = direct_requests
    # The relay serves the provider at the address the runtime would have called directly.
    provider_base = str(expected.url).split(path.removeprefix("/v1"))[0]

    relayed_requests: list[httpx.Request] = []
    relayed = ProviderFactory(backend=None).build(  # type: ignore[arg-type]
        _relayed(provider_name), provider_control=control, model=model
    )
    async with _through_the_relay(
        relayed_requests,
        api=control.model.api,
        base_url=provider_base,
        account_id="account-id" if provider_name == "openai-codex" else None,
    ) as client:
        events = await _call(relayed, model, client)

    assert events[-1].type == "done"
    [received] = relayed_requests
    # The provider receives what it would have received without the relay.
    assert received.method == expected.method
    assert str(received.url) == str(expected.url)
    assert _pass_from(received, place) == PROVIDER_SECRET
    assert json.loads(received.read()) == json.loads(expected.read())
    assert SESSION_PASS not in str(received.headers)


# Section 3: the requests announce the relay, and a hosted answer without one is refused


def _hosted_settings(token_file: Path) -> TauSDKSettings:
    return TauSDKSettings(
        _env_file=None,
        backend_url=BACKEND_URL,
        runtime_credential_id="runtime-credential-id",
        runtime_identity_token_file=token_file,
    )


def _answer(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "credentials": {"openai": {"uid": "credential-1", "credential_kind": "api_key", **entry}},
        "missing": [],
        "revoked": [],
        "provider_control": _provider_control("openai", "gpt-5.4").model_dump(mode="json"),
    }


@asynccontextmanager
async def _client(
    settings: TauSDKSettings, answer: dict[str, Any], bodies: list[dict[str, Any]]
) -> AsyncIterator[MainSequenceClient]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/runtime-credentials/token/":
            return httpx.Response(200, json={"access": "runtime-token"})
        assert request.url.path == HYDRATE_PATH
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=answer)

    async with httpx.AsyncClient(
        base_url=BACKEND_URL, transport=httpx.MockTransport(handler)
    ) as http:
        auth = (
            JWTAuth(settings)
            if settings.local_mode
            else RuntimeCredentialAuth(settings, exchange_client=http)
        )
        yield MainSequenceClient(settings, auth, client=http)


@pytest.mark.asyncio
async def test_a_hosted_answer_without_a_relay_is_refused_naming_the_field(
    runtime_identity_token_file,
):
    bodies: list[dict[str, Any]] = []
    answer = _answer({"credential": {"type": "api_key", "key": PROVIDER_SECRET}})
    with capture_logs() as logs:
        async with _client(_hosted_settings(runtime_identity_token_file), answer, bodies) as client:
            with pytest.raises(ConfigurationError, match="relay_definition") as raised:
                await client.hydrate_provider_credential(
                    "openai", model="gpt-5.4", session_uid="session-1", holder_id="holder-1"
                )

    assert bodies[0]["supported_model_relay_versions"] == [1]
    assert PROVIDER_SECRET not in str(raised.value)
    assert PROVIDER_SECRET not in str(logs)


def _local_settings(tmp_path: Path) -> TauSDKSettings:
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
    )


@pytest.mark.asyncio
async def test_local_mode_announces_nothing_and_runs_on_the_real_credential(tmp_path):
    bodies: list[dict[str, Any]] = []
    answer = _answer({"credential": {"type": "api_key", "key": PROVIDER_SECRET}})
    async with _client(_local_settings(tmp_path), answer, bodies) as client:
        assert client.relays_model_calls is False
        evidence = await client.hydrate_local_provider_credential(
            "openai", model="gpt-5.4", thinking_level=None, holder_id="local"
        )

    assert "supported_model_relay_versions" not in bodies[0]
    assert evidence.credential.relay_definition is None
    provider = ProviderFactory(backend=None).build(  # type: ignore[arg-type]
        evidence.credential, provider_control=evidence.provider_control, model="gpt-5.4"
    )
    assert provider._config.base_url == "https://api.openai.com/v1"
    assert provider._config.api_key == PROVIDER_SECRET


@pytest.mark.asyncio
async def test_local_mode_refuses_a_relayed_answer(tmp_path):
    bodies: list[dict[str, Any]] = []
    answer = _answer(
        {
            "credential": {"type": "api_key", "key": SESSION_PASS},
            "relay_definition": {"base_url": RELAY_BASE_URL},
        }
    )
    async with _client(_local_settings(tmp_path), answer, bodies) as client:
        with pytest.raises(ConfigurationError, match="does not call models through the model"):
            await client.hydrate_local_provider_credential(
                "openai", model="gpt-5.4", thinking_level=None, holder_id="local"
            )


# Codex: the pass stays valid for the run, and a refresh may not move the relay


def _session() -> AgentSession:
    return AgentSession(
        uid="session-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.4.2",
        active_provider="openai-codex",
        active_model="gpt-5.5",
    )


@pytest.mark.asyncio
async def test_a_relayed_codex_session_resolves_the_pass_and_its_account_id():
    control = _provider_control("openai-codex", "gpt-5.5")
    runtime = ProviderFactory(backend=None).for_session_credential(  # type: ignore[arg-type]
        _session(),
        holder_id="holder-1",
        evidence=ProviderExecutionEvidence(
            credential=_relayed("openai-codex"), provider_control=control
        ),
    )

    resolved = await runtime.provider._config.credential_resolver()

    assert resolved.access_token == SESSION_PASS
    assert resolved.account_id == "account-id"
    assert runtime.provider._config.base_url == RELAY_BASE_URL


@pytest.mark.asyncio
async def test_a_refresh_that_moves_the_relay_is_refused():
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock

    control = _provider_control("openai-codex", "gpt-5.5")
    expiring = _relayed("openai-codex", expires_at=datetime.now(UTC) - timedelta(seconds=1))
    moved = expiring.model_copy(
        update={"relay_definition": ModelRelayDefinition(base_url="https://other.example.test/r")}
    )
    backend = AsyncMock()
    backend.hydrate_provider_credential.return_value = ProviderExecutionEvidence(
        credential=moved, provider_control=control
    )
    runtime = ProviderFactory(backend).for_session_credential(
        _session(),
        holder_id="holder-1",
        evidence=ProviderExecutionEvidence(credential=expiring, provider_control=control),
    )

    with pytest.raises(ConfigurationError, match="changed the model relay"):
        await runtime.provider._config.credential_resolver()


def test_no_repr_holds_the_pass():
    credential = _relayed("openai")
    assert SESSION_PASS not in repr(credential)
    assert SESSION_PASS not in str(credential)


# Sign-ins other than openai-codex are sent as Tau sends them, direct and relayed

COPILOT_TOKEN = "tid=1;exp=2;proxy-ep=proxy.business.githubcopilot.com;sku=x"
ANTHROPIC_TOKEN = "sk-ant-oat-sign-in-token-never-logged"


def _sign_in(provider: str, token: str | None = None, **fields: Any) -> ProviderCredential:
    """A sign-in credential: the real token in local mode, the pass in a hosted session."""
    if token is None:
        fields["relay_definition"] = ModelRelayDefinition(base_url=RELAY_BASE_URL)
    return ProviderCredential(
        provider=provider,
        credential_kind="oauth",
        access_token=token or SESSION_PASS,
        **fields,
    )


async def _request_of(credential: ProviderCredential, model: str) -> httpx.Request:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _sse(request)

    provider = ProviderFactory(backend=None).build(  # type: ignore[arg-type]
        credential,
        provider_control=_provider_control(credential.provider, model),
        model=model,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = await _call(provider, model, client)
    assert events[-1].type == "done"
    [request] = requests
    return request


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token", "address", "secret"),
    [
        (ANTHROPIC_TOKEN, "https://api.anthropic.com/v1/messages", ANTHROPIC_TOKEN),
        (None, f"{RELAY_BASE_URL}/v1/messages", SESSION_PASS),
    ],
    ids=["local", "relayed"],
)
async def test_an_anthropic_sign_in_is_sent_as_tau_sends_it(token, address, secret):
    request = await _request_of(_sign_in("anthropic", token), "claude-sonnet-4-6")

    assert str(request.url) == address
    # A sign-in is a bearer token, never an API key, so a relay puts the token back as one.
    assert request.headers["authorization"] == f"Bearer {secret}"
    assert "x-api-key" not in request.headers
    assert request.headers["anthropic-beta"] == "claude-code-20250219,oauth-2025-04-20"
    assert request.headers["x-app"] == "cli"
    assert request.headers["user-agent"] == "claude-cli/tau"
    system = json.loads(request.content)["system"]
    assert system[0]["text"] == "You are Claude Code, Anthropic's official CLI for Claude."


@pytest.mark.asyncio
async def test_an_anthropic_api_key_is_still_sent_as_one():
    request = await _request_of(
        ProviderCredential(provider="anthropic", api_key=PROVIDER_SECRET), "claude-sonnet-4-6"
    )

    assert request.headers["x-api-key"] == PROVIDER_SECRET
    assert "authorization" not in request.headers
    assert "anthropic-beta" not in request.headers
    assert "Claude Code" not in request.content.decode()


COPILOT_MODELS = [
    pytest.param("gpt-5-mini", "/responses", id="responses"),
    pytest.param("claude-fable-5", "/chat/completions", id="completions"),
    pytest.param("claude-haiku-4.5", "/v1/messages", id="claude"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("model", "path"), COPILOT_MODELS)
async def test_a_local_copilot_sign_in_calls_the_address_its_token_names(model, path):
    request = await _request_of(_sign_in("github-copilot", COPILOT_TOKEN), model)

    assert str(request.url) == f"https://api.business.githubcopilot.com{path}"
    assert request.headers["authorization"] == f"Bearer {COPILOT_TOKEN}"
    assert "x-api-key" not in request.headers
    assert request.headers["copilot-integration-id"] == "vscode-chat"
    assert request.headers["editor-version"].startswith("vscode/")


@pytest.mark.asyncio
@pytest.mark.parametrize(("model", "path"), COPILOT_MODELS)
async def test_a_relayed_copilot_sign_in_calls_the_relay_and_reads_nothing_from_the_pass(
    model, path
):
    # A pass that looks like a Copilot token names no address: the relay owns it.
    credential = _sign_in("github-copilot").model_copy(
        update={"access_token": SecretStr("tid=1;proxy-ep=proxy.elsewhere.test")}
    )
    request = await _request_of(credential, model)

    assert str(request.url) == f"{RELAY_BASE_URL}{path}"
    assert request.headers["authorization"] == "Bearer tid=1;proxy-ep=proxy.elsewhere.test"
    assert "x-api-key" not in request.headers
    assert request.headers["copilot-integration-id"] == "vscode-chat"


@pytest.mark.parametrize(
    "host",
    ["proxy.localhost", "proxy.attacker.example", "proxy.githubcopilot.com.attacker.example"],
)
def test_a_copilot_token_that_names_a_host_that_is_not_githubs_is_refused(host):
    # A saved sign-in credential can be shared: its token must not choose where a conversation goes.
    with pytest.raises(ConfigurationError, match="Sign-in credential") as raised:
        ProviderFactory(backend=None).build(  # type: ignore[arg-type]
            _sign_in("github-copilot", f"tid=1;proxy-ep={host}"),
            provider_control=_provider_control("github-copilot", "gpt-5-mini"),
            model="gpt-5-mini",
        )
    assert "tid=1" not in str(raised.value)


@pytest.mark.asyncio
async def test_a_refreshed_local_copilot_sign_in_moves_to_its_new_token_and_address():
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock

    control = _provider_control("github-copilot", "gpt-5-mini")
    expired = datetime.now(UTC) - timedelta(seconds=1)
    refreshed_token = "tid=2;proxy-ep=proxy.enterprise.githubcopilot.com"
    backend = AsyncMock()
    backend.hydrate_provider_credential.return_value = ProviderExecutionEvidence(
        credential=_sign_in("github-copilot", refreshed_token), provider_control=control
    )
    session = _session().model_copy(
        update={"active_provider": "github-copilot", "active_model": "gpt-5-mini"}
    )
    runtime = ProviderFactory(backend).for_session_credential(
        session,
        holder_id="holder-1",
        evidence=ProviderExecutionEvidence(
            credential=_sign_in("github-copilot", COPILOT_TOKEN, expires_at=expired),
            provider_control=control,
        ),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _sse(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await _call(runtime.provider, "gpt-5-mini", client)

    [request] = requests
    assert str(request.url) == "https://api.enterprise.githubcopilot.com/responses"
    assert request.headers["authorization"] == f"Bearer {refreshed_token}"


@pytest.mark.asyncio
async def test_an_anthropic_sign_in_reaches_the_provider_through_the_sdk_relay():
    received: list[httpx.Request] = []
    relayed = ProviderFactory(backend=None).build(  # type: ignore[arg-type]
        _sign_in("anthropic"),
        provider_control=_provider_control("anthropic", "claude-sonnet-4-6"),
        model="claude-sonnet-4-6",
    )
    async with _through_the_relay(
        received, api="anthropic-messages", base_url="https://api.anthropic.com", account_id=None
    ) as client:
        events = await _call(relayed, "claude-sonnet-4-6", client)

    assert events[-1].type == "done"
    [request] = received
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["authorization"] == f"Bearer {PROVIDER_SECRET}"
    assert "x-api-key" not in request.headers
    assert request.headers["anthropic-beta"] == "claude-code-20250219,oauth-2025-04-20"
    assert SESSION_PASS not in str(request.headers)
