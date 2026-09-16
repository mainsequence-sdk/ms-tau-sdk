import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
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
    ProviderControl,
    ProviderCredential,
    ProviderExecutionEvidence,
)
from ms_tau_sdk.errors import ConfigurationError
from ms_tau_sdk.providers.definitions import PROVIDER_DEFINITIONS
from ms_tau_sdk.providers.factory import ProviderFactory
from ms_tau_sdk.settings import TauSDKSettings


def _provider_control(provider_name: str, model: str) -> ProviderControl:
    provider = next(
        provider for provider in BUILTIN_PROVIDER_CATALOG if provider.name == provider_name
    )
    metadata = provider.model_metadata.get(model)
    inputs = list(metadata.input if metadata is not None else ()) or ["text"]
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
            "input": inputs,
            "reasoning": bool(ProviderFactory._thinking_levels(provider, model)),
            "thinking_levels": list(ProviderFactory._thinking_levels(provider, model)),
        },
    )


def _custom_provider_control(
    provider_name: str = "acme-gateway",
    model: str = "acme-model",
    *,
    api: str = "openai-completions",
    inputs: list[str] | None = None,
    reasoning: bool = False,
    thinking_levels: list[str] | None = None,
) -> ProviderControl:
    return ProviderControl(
        schema_version=1,
        catalog_digest=f"sha256:{'0' * 64}",
        provider=provider_name,
        model={
            "model": model,
            "api": api,
            "input": inputs or ["text"],
            "reasoning": reasoning,
            "thinking_levels": thinking_levels or [],
        },
    )


@pytest.mark.asyncio
async def test_hydration_uses_django_tau_credential_contract():
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runtime-credentials/token/"):
            return httpx.Response(200, json={"access": "runtime-token"})
        requests.append(json.loads(request.content))
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
                "provider_control": _provider_control("openai", "gpt-5.4").model_dump(mode="json"),
            },
        )

    settings = TauSDKSettings(
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
        evidence = await client.hydrate_provider_credential(
            "openai",
            model="gpt-5.4",
            session_uid="session-1",
            holder_id="test",
        )

    assert evidence.credential.provider == "openai"
    assert evidence.credential.secret() == "provider-secret"
    assert evidence.provider_control.model.model == "gpt-5.4"
    assert requests == [
        {
            "agent_session_uid": "session-1",
            "providers": ["openai"],
            "holder_id": "test",
            "supported_provider_control_schema_versions": [1],
            "execution_selection": {
                "provider": "openai",
                "model": "gpt-5.4",
            },
        }
    ]


@pytest.mark.asyncio
async def test_local_hydration_uses_authenticated_user_without_agent_identity(tmp_path):
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer user-access-token"
        return httpx.Response(
            200,
            json={
                "credentials": {
                    "openai": {
                        "status": "active",
                        "credential_kind": "api_key",
                        "credential": {
                            "type": "api_key",
                            "api_key": "provider-secret",
                        },
                    }
                },
                "provider_control": _provider_control(
                    "openai",
                    "gpt-5.4",
                ).model_dump(mode="json"),
            },
        )

    settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        backend_url="http://backend.test",
        auth_mode="jwt",
        local_mode=True,
        access_token="user-access-token",
        refresh_token="user-refresh-token",
        local_provider="openai",
        local_model="gpt-5.4",
        local_thinking="high",
    )
    async with httpx.AsyncClient(
        base_url=settings.backend_url,
        transport=httpx.MockTransport(handler),
    ) as http:
        client = MainSequenceClient(settings, JWTAuth(settings), client=http)
        evidence = await client.hydrate_local_provider_credential(
            "openai",
            model="gpt-5.4",
            thinking_level="high",
            holder_id="local-process",
        )

    assert evidence.credential.secret() == "provider-secret"
    assert requests == [
        {
            "providers": ["openai"],
            "holder_id": "local-process",
            "supported_provider_control_schema_versions": [1],
            "execution_selection": {
                "provider": "openai",
                "model": "gpt-5.4",
                "thinking_level": "high",
            },
            "execution_context": "local_development",
        }
    ]


@pytest.mark.asyncio
async def test_hydration_derives_openai_codex_account_id_from_access_token():
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "https://api.openai.com/auth": {
                        "chatgpt_account_id": "account-id",
                    }
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
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
                "provider_control": _provider_control("openai-codex", "gpt-5.5").model_dump(
                    mode="json"
                ),
            },
        )

    settings = TauSDKSettings(
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
        evidence = await client.hydrate_provider_credential(
            "openai-codex",
            model="gpt-5.5",
            session_uid="session-1",
            holder_id="test",
        )

    assert evidence.credential.secret() == access_token
    assert evidence.credential.account_id == "account-id"
    assert evidence.credential.expires_at == datetime.fromtimestamp(1_770_000_000, UTC)


def test_hydration_parses_organization_custom_credential_without_user_lifecycle_fields():
    credential = MainSequenceClient.provider_credential_from_hydration(
        "acme-gateway",
        {
            "credentials": {
                "acme-gateway": {
                    "credential_kind": "organization_custom",
                    "credential": {
                        "type": "organization_custom",
                        "base_url": "https://models.example.test/v1",
                        "api": "openai-completions",
                        "headers": {"x-api-key": "header-secret"},
                    },
                }
            }
        },
    )

    assert credential.provider == "acme-gateway"
    assert credential.credential_kind == "organization_custom"
    assert credential.base_url == "https://models.example.test/v1"
    assert credential.api == "openai-completions"
    assert credential.headers == {"x-api-key": "header-secret"}
    assert credential.secret() == ""


def test_provider_defaults_cover_tau_catalog():
    assert len(PROVIDER_DEFINITIONS) >= 28
    assert PROVIDER_DEFINITIONS["deepseek"].base_url == "https://api.deepseek.com"
    assert PROVIDER_DEFINITIONS["github-copilot"].api == "openai-responses"


def test_factory_builds_catalog_openai_compatible_provider():
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]
    provider = factory.build(
        ProviderCredential(provider="deepseek", api_key="secret"),
        provider_control=_provider_control("deepseek", "deepseek-v4-pro"),
        model="deepseek-v4-pro",
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
        ),
        provider_control=_provider_control("openai-codex", "gpt-5.5"),
        model="gpt-5.5",
    )

    assert isinstance(provider, OpenAICodexProvider)


@pytest.mark.asyncio
async def test_factory_reuses_warm_runtime_credential():
    backend = AsyncMock()
    evidence = ProviderExecutionEvidence(
        credential=ProviderCredential(
            provider="openai-codex",
            credential_kind="oauth",
            access_token="access-token",
            account_id="account-id",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        provider_control=_provider_control("openai-codex", "gpt-5.5"),
    )
    factory = ProviderFactory(backend)
    session = AgentSession(
        uid="session-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.3.1",
        active_provider="openai-codex",
        active_model="gpt-5.5",
        created_by_user_uid="user-1",
    )

    runtime = factory.for_session_credential(
        session,
        holder_id="holder-1",
        evidence=evidence,
    )
    resolver = runtime.provider._config.credential_resolver
    first, second = await resolver(), await resolver()

    assert first.access_token == "access-token"
    assert second.access_token == "access-token"
    backend.hydrate_provider_credential.assert_not_awaited()


@pytest.mark.asyncio
async def test_factory_refreshes_expiring_credential_once():
    backend = AsyncMock()
    initial = ProviderExecutionEvidence(
        credential=ProviderCredential(
            provider="openai-codex",
            credential_kind="oauth",
            access_token="expired-token",
            account_id="account-id",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        ),
        provider_control=_provider_control("openai-codex", "gpt-5.5"),
    )
    backend.hydrate_provider_credential.return_value = ProviderExecutionEvidence(
        credential=ProviderCredential(
            provider="openai-codex",
            credential_kind="oauth",
            access_token="fresh-token",
            account_id="account-id",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        provider_control=_provider_control("openai-codex", "gpt-5.5"),
    )
    factory = ProviderFactory(backend)
    session = AgentSession(
        uid="session-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.3.1",
        active_provider="openai-codex",
        active_model="gpt-5.5",
        created_by_user_uid="user-1",
    )

    runtime = factory.for_session_credential(
        session,
        holder_id="holder-1",
        evidence=initial,
    )
    resolver = runtime.provider._config.credential_resolver
    first, second = await asyncio.gather(resolver(), resolver())

    assert first.access_token == "fresh-token"
    assert second.access_token == "fresh-token"
    backend.hydrate_provider_credential.assert_awaited_once_with(
        "openai-codex",
        model="gpt-5.5",
        session_uid="session-1",
        holder_id="holder-1",
    )


def test_factory_validates_model_and_thinking_level():
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]

    assert (
        factory.validate_execution(
            _provider_control("openai", "gpt-5.4"),
            provider_name="openai",
            model="gpt-5.4",
            thinking_level="high",
        )
        == "high"
    )
    with pytest.raises(ConfigurationError, match="does not match the selected model"):
        factory.validate_execution(
            _provider_control("openai", "gpt-5.4"),
            provider_name="openai",
            model="not-a-model",
        )
    with pytest.raises(ConfigurationError, match="not supported"):
        factory.validate_execution(
            _provider_control("openai", "gpt-4o"),
            provider_name="openai",
            model="gpt-4o",
            thinking_level="high",
        )


def test_factory_rejects_all_noncanonical_execution_evidence():
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]

    provider_mismatch = _provider_control("openai", "gpt-5.4")
    with pytest.raises(ConfigurationError, match="selected provider"):
        factory.validate_execution(
            provider_mismatch,
            provider_name="anthropic",
            model="gpt-5.4",
        )

    invalid_thinking = _provider_control("openai", "gpt-5.4")
    with pytest.raises(ConfigurationError, match="Unknown thinking"):
        factory.validate_execution(
            invalid_thinking,
            provider_name="openai",
            model="gpt-5.4",
            thinking_level="impossible",
        )

    unknown_model = _provider_control("openai", "gpt-5.4")
    unknown_model.model.model = "unknown-model"
    with pytest.raises(ConfigurationError, match="not configured"):
        factory.validate_execution(
            unknown_model,
            provider_name="openai",
            model="unknown-model",
        )

    broadened_thinking = _provider_control("openai", "gpt-5.4")
    broadened_thinking.model.thinking_levels.append("minimal")
    with pytest.raises(ConfigurationError, match="thinking levels exceed"):
        factory.validate_execution(
            broadened_thinking,
            provider_name="openai",
            model="gpt-5.4",
        )

    contradictory_reasoning = _provider_control("openai", "gpt-4o")
    contradictory_reasoning.model.reasoning = True
    with pytest.raises(ConfigurationError, match="reasoning capability exceeds"):
        factory.validate_execution(
            contradictory_reasoning,
            provider_name="openai",
            model="gpt-4o",
        )


def test_factory_rejects_image_projection_and_credential_mismatches():
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]
    control = _provider_control("deepseek", "deepseek-v4-pro")

    with pytest.raises(ConfigurationError, match="not enabled for image input"):
        factory.validate_input_media(control, media_types={"image/png"})

    control.model.input.append("image")
    with pytest.raises(ConfigurationError, match="does not support image input"):
        factory.validate_input_media(control, media_types={"image/png"})

    session = AgentSession(
        uid="session-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.3.1",
        active_provider="openai",
        active_model="gpt-5.4",
        created_by_user_uid="user-1",
    )
    evidence = ProviderExecutionEvidence(
        credential=ProviderCredential(provider="anthropic", api_key="secret"),
        provider_control=_provider_control("openai", "gpt-5.4"),
    )
    with pytest.raises(ConfigurationError, match="credential does not match"):
        factory.for_session_credential(
            session,
            holder_id="holder-1",
            evidence=evidence,
        )

    session.active_model = None
    with pytest.raises(ConfigurationError, match="does not have an active provider and model"):
        factory.for_session_credential(
            session,
            holder_id="holder-1",
            evidence=evidence,
        )


def test_factory_rejects_contradictory_transport_and_media_projection():
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]
    transport = _provider_control("openai", "gpt-5.4")
    transport.model.api = "anthropic-messages"
    with pytest.raises(ConfigurationError, match="transport"):
        factory.validate_execution(
            transport,
            provider_name="openai",
            model="gpt-5.4",
        )

    media = _provider_control("deepseek", "deepseek-v4-pro")
    media.model.input.append("image")
    with pytest.raises(ConfigurationError, match="input capabilities"):
        factory.validate_execution(
            media,
            provider_name="deepseek",
            model="deepseek-v4-pro",
        )


def test_provider_control_rejects_malformed_digest_and_custom_registry_is_absent():
    with pytest.raises(ValidationError):
        ProviderControl(
            schema_version=1,
            catalog_digest="not-a-digest",
            provider="openai",
            model={
                "model": "gpt-5.4",
                "api": "openai-responses",
                "input": ["text"],
                "reasoning": True,
                "thinking_levels": ["high"],
            },
        )
    assert not hasattr(ProviderFactory, "register")


@pytest.mark.parametrize(
    "credential",
    [
        ProviderCredential(
            provider="acme-gateway",
            credential_kind="organization_custom",
            api="openai-completions",
            api_key="api-secret",
            base_url="https://models.example.test/v1",
        ),
        ProviderCredential(
            provider="acme-gateway",
            credential_kind="organization_custom",
            api="openai-completions",
            headers={"Authorization": "Bearer header-secret"},
            base_url="https://models.example.test/v1",
        ),
        ProviderCredential(
            provider="acme-gateway",
            credential_kind="organization_custom",
            api="openai-completions",
            api_key="api-secret",
            headers={"Authorization": "Bearer header-secret"},
            base_url="https://models.example.test/v1",
        ),
        ProviderCredential(
            provider="acme-gateway",
            credential_kind="organization_custom",
            api="openai-completions",
            base_url="https://models.example.test/v1",
        ),
    ],
)
def test_factory_reuses_openai_compatible_constructor_for_custom_auth_modes(credential):
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]
    control = _custom_provider_control()

    assert (
        factory.validate_execution(
            control,
            provider_name="acme-gateway",
            model="acme-model",
        )
        == "medium"
    )
    provider = factory.build(
        credential,
        provider_control=control,
        model="acme-model",
    )

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._config.base_url == "https://models.example.test/v1"
    assert provider._config.headers == credential.headers
    assert provider._config.omit_authorization_header is (not bool(credential.secret()))


def test_factory_rejects_invalid_custom_provider_evidence_before_construction():
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]
    unsupported = _custom_provider_control(api="anthropic-messages")
    with pytest.raises(ConfigurationError, match="unsupported Tau API transport"):
        factory.validate_execution(
            unsupported,
            provider_name="acme-gateway",
            model="acme-model",
        )

    control = _custom_provider_control(
        reasoning=True,
        thinking_levels=["low", "high"],
        inputs=["text", "image"],
    )
    assert (
        factory.validate_execution(
            control,
            provider_name="acme-gateway",
            model="acme-model",
        )
        == "low"
    )
    assert (
        factory.validate_execution(
            control,
            provider_name="acme-gateway",
            model="acme-model",
            thinking_level="high",
            media_types={"image/png"},
        )
        == "high"
    )

    with pytest.raises(ConfigurationError, match="organization_custom credential"):
        factory.build(
            ProviderCredential(
                provider="acme-gateway",
                credential_kind="api_key",
                api="openai-completions",
                api_key="api-secret",
                base_url="https://models.example.test/v1",
            ),
            provider_control=_custom_provider_control(),
            model="acme-model",
        )
    with pytest.raises(ConfigurationError, match="explicit base_url"):
        factory.build(
            ProviderCredential(
                provider="acme-gateway",
                credential_kind="organization_custom",
                api="openai-completions",
            ),
            provider_control=_custom_provider_control(),
            model="acme-model",
        )
    with pytest.raises(ConfigurationError, match="transport does not match"):
        factory.build(
            ProviderCredential(
                provider="acme-gateway",
                credential_kind="organization_custom",
                api="openai-responses",
                base_url="https://models.example.test/v1",
            ),
            provider_control=_custom_provider_control(),
            model="acme-model",
        )
    with pytest.raises(ConfigurationError, match="prohibited"):
        factory.build(
            ProviderCredential(
                provider="acme-gateway",
                credential_kind="organization_custom",
                api="openai-completions",
                base_url="http://169.254.169.254/latest",
            ),
            provider_control=_custom_provider_control(),
            model="acme-model",
        )
    with pytest.raises(ConfigurationError, match="invalid base_url"):
        factory.build(
            ProviderCredential(
                provider="acme-gateway",
                credential_kind="organization_custom",
                api="openai-completions",
                base_url="https://models.example.test/v1?api_key=secret",
            ),
            provider_control=_custom_provider_control(),
            model="acme-model",
        )


def test_factory_applies_model_specific_transport_override():
    factory = ProviderFactory(backend=None)  # type: ignore[arg-type]

    provider = factory.build(
        ProviderCredential(provider="github-copilot", access_token="secret"),
        provider_control=_provider_control("github-copilot", "claude-sonnet-4.6"),
        model="claude-sonnet-4.6",
    )

    assert isinstance(provider, AnthropicProvider)


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

    control = _provider_control(provider_name, model)
    factory.validate_execution(
        control,
        provider_name=provider_name,
        model=model,
    )
    provider = factory.build(credential, provider_control=control, model=model)

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
                'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}'
            )
        elif request.url.path.endswith("/chat/completions"):
            body = "\n\n".join(
                [
                    ('data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}'),
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
    provider = factory.build(
        credential,
        provider_control=_provider_control(credential.provider, model),
        model=model,
    )
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
