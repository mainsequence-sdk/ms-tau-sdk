"""Provider-neutral construction from backend-hydrated credentials."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tau_agent.provider import ModelProvider
from tau_ai.anthropic import AnthropicProvider
from tau_ai.env import AnthropicConfig, OpenAICompatibleConfig
from tau_ai.google import GoogleGenerativeAIProvider
from tau_ai.mistral import MistralConversationsProvider
from tau_ai.openai_codex import (
    OpenAICodexConfig,
    OpenAICodexCredentials,
    OpenAICodexProvider,
)
from tau_ai.openai_compatible import OpenAICompatibleProvider
from tau_coding.provider_catalog import BUILTIN_PROVIDER_CATALOG, ProviderCatalogEntry
from tau_coding.thinking import (
    DEFAULT_THINKING_LEVEL,
    ThinkingLevel,
    normalize_thinking_level,
)

from astro.backend.client import MainSequenceClient
from astro.backend.models import AgentSession, ProviderCredential
from astro.errors import ConfigurationError

from .definitions import PROVIDER_DEFINITIONS

CustomProviderBuilder = Callable[[ProviderCredential], ModelProvider]
CredentialResolver = Callable[[], Awaitable[ProviderCredential]]
CATALOG_BY_NAME = {provider.name: provider for provider in BUILTIN_PROVIDER_CATALOG}
PROVIDER_CREDENTIAL_REFRESH_SKEW = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class ProviderRuntime:
    name: str
    model: str
    thinking_level: ThinkingLevel
    provider: ModelProvider
    credential: ProviderCredential


class ProviderFactory:
    def __init__(self, backend: MainSequenceClient) -> None:
        self.backend = backend
        self._custom: dict[str, CustomProviderBuilder] = {}

    def register(self, provider_name: str, builder: CustomProviderBuilder) -> None:
        """Register an Astro-specific provider using Tau's public protocol."""
        self._custom[provider_name] = builder

    @staticmethod
    def _thinking_levels(
        provider: ProviderCatalogEntry,
        model: str,
    ) -> tuple[ThinkingLevel, ...]:
        metadata = provider.model_metadata.get(model)
        if metadata is not None and metadata.reasoning is False:
            return ()
        if provider.thinking_models and model not in provider.thinking_models:
            return ()
        return tuple(
            level
            for level in provider.thinking_levels or ()
            if metadata is None
            or (
                metadata.thinking_level_map.get(level, level if level != "xhigh" else None)
                is not None
            )
        )

    def validate_selection(
        self,
        provider_name: str,
        model: str,
        thinking_level: str | None = None,
    ) -> ThinkingLevel:
        try:
            normalized_thinking = normalize_thinking_level(thinking_level)
        except ValueError as error:
            raise ConfigurationError(str(error)) from error
        if provider_name in self._custom or provider_name == "ollama":
            return normalized_thinking
        provider = CATALOG_BY_NAME.get(provider_name)
        if provider is None:
            raise ConfigurationError(f"Provider is not supported by Tau: {provider_name}")
        if model not in provider.models:
            raise ConfigurationError(
                f"Model is not configured for provider {provider_name}: {model}"
            )
        available = self._thinking_levels(provider, model)
        if thinking_level and normalized_thinking not in available:
            supported = ", ".join(available) or "none"
            raise ConfigurationError(
                f"Thinking level {normalized_thinking!r} is not supported by "
                f"{provider_name}:{model}; available levels: {supported}"
            )
        if thinking_level:
            return normalized_thinking
        default = provider.thinking_default
        return default if default in available else DEFAULT_THINKING_LEVEL

    async def for_session(
        self,
        session: AgentSession,
        *,
        holder_id: str,
    ) -> ProviderRuntime:
        provider_name = session.active_provider
        model = session.active_model
        if not provider_name or not model:
            raise ConfigurationError(
                f"Session {session.uid} does not have an active provider and model"
            )
        thinking_level = self.validate_selection(
            provider_name,
            model,
            session.active_thinking,
        )
        credential = await self.backend.hydrate_provider_credential(
            provider_name,
            session_uid=session.uid,
            holder_id=holder_id,
        )
        cached_credential = credential
        credential_refresh_lock = asyncio.Lock()

        async def resolve_credential() -> ProviderCredential:
            nonlocal cached_credential
            if not self._credential_expires_soon(cached_credential):
                return cached_credential
            async with credential_refresh_lock:
                if self._credential_expires_soon(cached_credential):
                    cached_credential = await self.backend.hydrate_provider_credential(
                        provider_name,
                        session_uid=session.uid,
                        holder_id=holder_id,
                    )
                return cached_credential

        return ProviderRuntime(
            name=provider_name,
            model=model,
            thinking_level=thinking_level,
            provider=self.build(
                credential,
                model=model,
                credential_resolver=resolve_credential,
            ),
            credential=credential,
        )

    @staticmethod
    def _credential_expires_soon(credential: ProviderCredential) -> bool:
        expires_at = credential.expires_at
        if expires_at is None:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= datetime.now(UTC) + PROVIDER_CREDENTIAL_REFRESH_SKEW

    def build(
        self,
        credential: ProviderCredential,
        *,
        model: str | None = None,
        credential_resolver: CredentialResolver | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float = 60,
    ) -> ModelProvider:
        custom = self._custom.get(credential.provider)
        if custom is not None:
            return custom(credential)

        definition = PROVIDER_DEFINITIONS.get(credential.provider)
        catalog_provider = CATALOG_BY_NAME.get(credential.provider)
        model_metadata = (
            catalog_provider.model_metadata.get(model)
            if catalog_provider is not None and model is not None
            else None
        )
        api = credential.api or (
            model_metadata.api
            if model_metadata is not None and model_metadata.api
            else definition.api
            if definition
            else "openai-completions"
        )
        base_url = credential.base_url or (
            model_metadata.base_url
            if model_metadata is not None and model_metadata.base_url
            else definition.base_url
            if definition
            else "https://api.openai.com/v1"
        )
        headers = {
            **(catalog_provider.headers if catalog_provider is not None else {}),
            **(model_metadata.headers if model_metadata is not None else {}),
            **credential.headers,
        }
        secret = credential.secret()
        if not secret and credential.provider != "ollama":
            raise ConfigurationError(
                f"Backend returned no usable credential for {credential.provider}"
            )

        if api == "openai-codex":
            if not credential.access_token or not credential.account_id:
                raise ConfigurationError(
                    "OpenAI Codex requires backend-managed access_token and account_id"
                )

            async def resolve_codex_credentials() -> OpenAICodexCredentials:
                current = (
                    await credential_resolver() if credential_resolver is not None else credential
                )
                if not current.access_token or not current.account_id:
                    raise ConfigurationError(
                        "OpenAI Codex requires backend-managed access_token and account_id"
                    )
                return OpenAICodexCredentials(
                    access_token=current.access_token.get_secret_value(),
                    account_id=current.account_id,
                )

            return OpenAICodexProvider(
                OpenAICodexConfig(
                    credential_resolver=resolve_codex_credentials,
                    base_url=base_url,
                    headers=headers,
                    timeout_seconds=timeout_seconds,
                    provider_name=credential.provider,
                )
            )

        if api == "anthropic-messages":
            return AnthropicProvider(
                AnthropicConfig(
                    api_key=secret,
                    base_url=base_url,
                    headers=headers,
                    provider_name=credential.provider,
                )
            )

        config = OpenAICompatibleConfig(
            api_key=secret or "ollama",
            base_url=base_url,
            headers=headers,
            api=api,
            provider_name=credential.provider,
            omit_authorization_header=credential.provider == "ollama" and not secret,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        if api == "google-generative-ai":
            return GoogleGenerativeAIProvider(config)
        if api == "mistral-conversations":
            return MistralConversationsProvider(config)
        if api in {"openai-completions", "openai-responses"}:
            return OpenAICompatibleProvider(config)
        raise ConfigurationError(
            f"Provider {credential.provider} uses unsupported Tau API transport {api}"
        )
