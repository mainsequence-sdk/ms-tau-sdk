import base64
import json

import httpx
import pytest
from tau_agent import UserMessage
from tau_ai import (
    AnthropicProvider,
    GoogleGenerativeAIProvider,
    MistralConversationsProvider,
    OpenAICodexProvider,
    OpenAICompatibleProvider,
)
from tau_coding.provider_catalog import BUILTIN_PROVIDER_CATALOG

from astro.backend.auth import RuntimeCredentialAuth
from astro.backend.client import MainSequenceClient
from astro.backend.models import ProviderCredential
from astro.errors import ConfigurationError
from astro.providers.definitions import PROVIDER_DEFINITIONS
from astro.providers.factory import ProviderFactory
from astro.settings import Settings


@pytest.mark.asyncio
async def test_hydration_uses_django_tau_credential_contract():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runtime-credentials/token/"):
            return httpx.Response(200, json={"access": "runtime-token"})
        return httpx.Response(
            200,
            json={
                "credentials": {
                    "openai": {
                        "status": "active",
                        "credential_kind": "api_key",
                        "version": 1,
                        "credential_hash": "sha256:test",
                        "credential": {
                            "type": "api_key",
                            "api_key": "provider-secret",
                        },
                    }
                },
                "missing": [],
                "revoked": [],
            },
        )

    settings = Settings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="id",
        runtime_credential_secret="secret",
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url=settings.backend_url,
        transport=transport,
    ) as http:
        auth = RuntimeCredentialAuth(settings, exchange_client=http)
        client = MainSequenceClient(settings, auth, client=http)
        credential = await client.hydrate_provider_credential(
            "openai",
            created_by_user_uid="user-1",
            session_uid=None,
            holder_id="test",
        )

    assert credential.provider == "openai"
    assert credential.secret() == "provider-secret"


@pytest.mark.asyncio
async def test_hydration_derives_openai_codex_account_id_from_access_token():
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "account-id",
                }
            }
        ).encode()
    ).decode().rstrip("=")
    access_token = f"header.{payload}.signature"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runtime-credentials/token/"):
            return httpx.Response(200, json={"access": "runtime-token"})
        return httpx.Response(
            200,
            json={
                "credentials": {
                    "openai-codex": {
                        "status": "active",
                        "credential_kind": "oauth",
                        "version": 1,
                        "credential_hash": "sha256:test",
                        "credential": {
                            "type": "oauth",
                            "access": access_token,
                            "refresh": "refresh-token",
                            "expires": 1_770_000_000_000,
                        },
                    }
                },
                "missing": [],
                "revoked": [],
            },
        )

    settings = Settings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="id",
        runtime_credential_secret="secret",
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url=settings.backend_url,
        transport=transport,
    ) as http:
        auth = RuntimeCredentialAuth(settings, exchange_client=http)
        client = MainSequenceClient(settings, auth, client=http)
        credential = await client.hydrate_provider_credential(
            "openai-codex",
            created_by_user_uid="user-1",
            session_uid="session-1",
            holder_id="test",
        )

    assert credential.secret() == access_token
    assert credential.account_id == "account-id"


@pytest.mark.asyncio
async def test_flush_uses_django_tau_credential_contract():
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runtime-credentials/token/"):
            return httpx.Response(200, json={"access": "runtime-token"})
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "provider": "openai-codex",
                "version": 2,
                "credential_hash": "sha256:test",
                "status": "active",
                "accepted": True,
            },
        )

    settings = Settings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="id",
        runtime_credential_secret="secret",
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url=settings.backend_url,
        transport=transport,
    ) as http:
        auth = RuntimeCredentialAuth(settings, exchange_client=http)
        client = MainSequenceClient(settings, auth, client=http)
        await client.flush_provider_credential(
            provider="openai-codex",
            created_by_user_uid="user-1",
            session_uid="session-1",
            credential={"type": "oauth", "access": "access-token"},
        )

    assert requests == [
        {
            "created_by_user_uid": "user-1",
            "agent_session_uid": "session-1",
            "provider": "openai-codex",
            "base_version": 0,
            "reason": "signin_completed",
            "credential": {
                "type": "oauth",
                "access": "access-token",
            },
        }
    ]


def test_provider_defaults_cover_tau_catalog():
    assert len(PROVIDER_DEFINITIONS) >= 28
    assert PROVIDER_DEFINITIONS["deepseek"].base_url == "https://api.deepseek.com"
    assert PROVIDER_DEFINITIONS["github-copilot"].api == "openai-responses"


def test_factory_builds_catalog_openai_compatible_provider():
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]
    provider = factory.build(
        ProviderCredential(provider="deepseek", api_key="secret")
    )

    assert isinstance(provider, OpenAICompatibleProvider)


def test_factory_builds_openai_codex_provider():
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]
    provider = factory.build(
        ProviderCredential(
            provider="openai-codex",
            credential_kind="oauth",
            access_token="access-token",
            account_id="account-id",
        )
    )

    assert isinstance(provider, OpenAICodexProvider)


def test_factory_validates_model_and_thinking_level():
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]

    assert factory.validate_selection("openai", "gpt-5.4", "high") == "high"
    with pytest.raises(ConfigurationError, match="Model is not configured"):
        factory.validate_selection("openai", "not-a-model")
    with pytest.raises(ConfigurationError, match="not supported"):
        factory.validate_selection("openai", "gpt-4o", "high")


def test_factory_applies_model_specific_transport_override():
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]

    provider = factory.build(
        ProviderCredential(provider="github-copilot", access_token="secret"),
        model="claude-sonnet-4.6",
    )

    assert isinstance(provider, AnthropicProvider)


def test_factory_accepts_custom_tau_model_provider():
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]
    custom_provider = object()
    factory.register("bedrock", lambda _credential: custom_provider)  # type: ignore[arg-type]

    selected = factory.build(
        ProviderCredential(provider="bedrock", api_key="secret"),
    )

    assert selected is custom_provider
    assert factory.validate_selection("bedrock", "claude-custom", "high") == "high"


@pytest.mark.parametrize(
    ("provider_name", "model"),
    [
        (provider.name, provider.default_model)
        for provider in BUILTIN_PROVIDER_CATALOG
        if provider.default_model
    ],
)
def test_factory_constructs_every_tau_catalog_provider(provider_name, model):
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]
    credential = (
        ProviderCredential(
            provider=provider_name,
            credential_kind="oauth",
            access_token="access-token",
            account_id="account-id",
        )
        if provider_name == "openai-codex"
        else ProviderCredential(provider=provider_name, api_key="api-key")
    )

    factory.validate_selection(provider_name, model)
    provider = factory.build(credential, model=model)

    assert callable(provider.stream_response)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("credential", "model", "expected_type"),
    [
        (
            ProviderCredential(
                provider="openai",
                api="openai-responses",
                api_key="api-key",
            ),
            "gpt-5.4",
            OpenAICompatibleProvider,
        ),
        (
            ProviderCredential(
                provider="anthropic",
                api="anthropic-messages",
                api_key="api-key",
            ),
            "claude-sonnet-4-6",
            AnthropicProvider,
        ),
        (
            ProviderCredential(
                provider="google",
                api="google-generative-ai",
                api_key="api-key",
            ),
            "gemini-flash-latest",
            GoogleGenerativeAIProvider,
        ),
        (
            ProviderCredential(
                provider="mistral",
                api="mistral-conversations",
                api_key="api-key",
            ),
            "devstral-medium-latest",
            MistralConversationsProvider,
        ),
        (
            ProviderCredential(
                provider="openai-codex",
                credential_kind="oauth",
                access_token="access-token",
                account_id="account-id",
            ),
            "gpt-5.5",
            OpenAICodexProvider,
        ),
    ],
)
async def test_every_tau_transport_class_completes_fake_stream(
    credential,
    model,
    expected_type,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            body = "\n\n".join(
                [
                    'data: {"type":"message_start","message":{"usage":{"input_tokens":1}}}',
                    (
                        'data: {"type":"content_block_delta","index":0,'
                        '"delta":{"type":"text_delta","text":"ok"}}'
                    ),
                    (
                        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
                        '"usage":{"output_tokens":1}}'
                    ),
                ]
            )
        elif ":streamGenerateContent" in request.url.path:
            body = (
                'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]},'
                '"finishReason":"STOP"}]}'
            )
        elif request.url.path.endswith("/chat/completions"):
            body = "\n\n".join(
                [
                    (
                        'data: {"choices":[{"delta":{"content":"ok"},'
                        '"finish_reason":"stop"}]}'
                    ),
                    "data: [DONE]",
                ]
            )
        else:
            body = "\n\n".join(
                [
                    'data: {"type":"response.output_text.delta","delta":"ok"}',
                    (
                        'data: {"type":"response.completed",'
                        '"response":{"status":"completed","usage":'
                        '{"input_tokens":1,"output_tokens":1}}}'
                    ),
                ]
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"{body}\n\n",
        )

    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]
    provider = factory.build(credential, model=model)
    assert isinstance(provider, expected_type)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider._client = client
        events = [
            event
            async for event in provider.stream_response(
                model=model,
                system="system",
                messages=[UserMessage(content="hello")],
                tools=[],
            )
        ]

    assert [event.delta for event in events if event.type == "text_delta"] == ["ok"]
    assert events[-1].type == "done"
    assert events[-1].message.text == "ok"
