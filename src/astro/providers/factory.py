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
    anthropic_thinking_budget_for_level,
    normalize_thinking_level,
    reasoning_effort_for_level,
)

from astro.backend.client import MainSequenceClient
from astro.backend.models import (
    AgentSession,
    ProviderControl,
    ProviderCredential,
    ProviderExecutionEvidence,
)
from astro.errors import ConfigurationError

from .definitions import PROVIDER_DEFINITIONS

CredentialResolver = Callable[[], Awaitable[ProviderCredential]]
CATALOG_BY_NAME = {provider.name: provider for provider in BUILTIN_PROVIDER_CATALOG}
PROVIDER_CREDENTIAL_REFRESH_SKEW = timedelta(seconds=60)
SUPPORTED_API_TRANSPORTS = frozenset(
    {
        "anthropic-messages",
        "google-generative-ai",
        "mistral-conversations",
        "openai-codex",
        "openai-completions",
        "openai-responses",
    }
)


@dataclass(frozen=True, slots=True)
class ProviderRuntime:
    name: str
    model: str
    thinking_level: ThinkingLevel
    provider: ModelProvider
    credential: ProviderCredential
    provider_control: ProviderControl


class ProviderFactory:
    def __init__(self, backend: MainSequenceClient) -> None:
        self.backend = backend

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

    def validate_execution(
        self,
        provider_control: ProviderControl,
        *,
        provider_name: str,
        model: str,
        thinking_level: str | None = None,
        media_types: set[str] | None = None,
    ) -> ThinkingLevel:
        if provider_control.provider != provider_name:
            raise ConfigurationError(
                "Django provider-control evidence does not match the selected provider"
            )
        if provider_control.model.model != model:
            raise ConfigurationError(
                "Django provider-control evidence does not match the selected model"
            )
        try:
            normalized_thinking = normalize_thinking_level(thinking_level)
        except ValueError as error:
            raise ConfigurationError(str(error)) from error
        provider = CATALOG_BY_NAME.get(provider_name)
        if provider is None:
            raise ConfigurationError(f"Provider is not supported by Tau: {provider_name}")
        if model not in provider.models:
            raise ConfigurationError(
                f"Model is not configured for provider {provider_name}: {model}"
            )
        metadata = provider.model_metadata.get(model)
        tau_api = (
            metadata.api if metadata is not None and metadata.api else provider.api or provider.kind
        )
        projected_api = provider_control.model.api
        if projected_api not in SUPPORTED_API_TRANSPORTS or projected_api != tau_api:
            raise ConfigurationError(
                f"Provider {provider_name} uses unsupported or contradictory "
                f"Tau API transport {projected_api}"
            )
        tau_inputs = set(metadata.input if metadata is not None else ()) or {"text"}
        projected_inputs = set(provider_control.model.input)
        if not projected_inputs or not projected_inputs.issubset(tau_inputs):
            raise ConfigurationError(
                "Django provider-control input capabilities exceed Tau execution support"
            )
        tau_thinking = set(self._thinking_levels(provider, model))
        projected_thinking = set(provider_control.model.thinking_levels)
        if not projected_thinking.issubset(tau_thinking):
            raise ConfigurationError(
                "Django provider-control thinking levels exceed Tau execution support"
            )
        if provider_control.model.reasoning and not tau_thinking:
            raise ConfigurationError(
                "Django provider-control reasoning capability exceeds Tau execution support"
            )
        available = tau_thinking.intersection(projected_thinking)
        if thinking_level and normalized_thinking not in available:
            supported = ", ".join(sorted(available)) or "none"
            raise ConfigurationError(
                f"Thinking level {normalized_thinking!r} is not supported by "
                f"{provider_name}:{model}; available levels: {supported}"
            )
        self.validate_input_media(
            provider_control,
            media_types=media_types or set(),
        )
        if thinking_level:
            return normalized_thinking
        default = provider.thinking_default
        return default if default in available else DEFAULT_THINKING_LEVEL

    def validate_input_media(
        self,
        provider_control: ProviderControl,
        *,
        media_types: set[str],
    ) -> None:
        if not any(media_type.startswith("image/") for media_type in media_types):
            return
        provider_name = provider_control.provider
        model = provider_control.model.model
        if "image" not in provider_control.model.input:
            raise ConfigurationError(
                f"Model {provider_name}:{model} is not enabled for image input"
            )
        provider = CATALOG_BY_NAME.get(provider_name)
        metadata = provider.model_metadata.get(model) if provider is not None else None
        if metadata is None or "image" not in metadata.input:
            raise ConfigurationError(f"Model {provider_name}:{model} does not support image input")

    def for_session_credential(
        self,
        session: AgentSession,
        *,
        holder_id: str,
        evidence: ProviderExecutionEvidence,
    ) -> ProviderRuntime:
        provider_name = session.active_provider
        model = session.active_model
        if not provider_name or not model:
            raise ConfigurationError(
                f"Session {session.uid} does not have an active provider and model"
            )
        credential = evidence.credential
        if credential.provider != provider_name:
            raise ConfigurationError("Backend credential does not match the selected provider")
        thinking_level = self.validate_execution(
            evidence.provider_control,
            provider_name=provider_name,
            model=model,
            thinking_level=session.active_thinking,
        )
        cached_credential = credential
        cached_provider_control = evidence.provider_control
        credential_refresh_lock = asyncio.Lock()

        async def resolve_credential() -> ProviderCredential:
            nonlocal cached_credential
            if not self._credential_expires_soon(cached_credential):
                return cached_credential
            async with credential_refresh_lock:
                if self._credential_expires_soon(cached_credential):
                    refreshed = await self.backend.hydrate_provider_credential(
                        provider_name,
                        model=model,
                        session_uid=session.uid,
                        holder_id=holder_id,
                    )
                    self.validate_execution(
                        refreshed.provider_control,
                        provider_name=provider_name,
                        model=model,
                        thinking_level=session.active_thinking,
                    )
                    if refreshed.provider_control.model != cached_provider_control.model:
                        raise ConfigurationError(
                            "Provider-control execution capability changed during "
                            "credential refresh"
                        )
                    cached_credential = refreshed.credential
                return cached_credential

        return ProviderRuntime(
            name=provider_name,
            model=model,
            thinking_level=thinking_level,
            provider=self.build(
                credential,
                provider_control=evidence.provider_control,
                model=model,
                thinking_level=thinking_level,
                credential_resolver=resolve_credential,
            ),
            credential=credential,
            provider_control=evidence.provider_control,
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
        provider_control: ProviderControl,
        model: str | None = None,
        credential_resolver: CredentialResolver | None = None,
        thinking_level: str | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float = 60,
    ) -> ModelProvider:
        definition = PROVIDER_DEFINITIONS.get(credential.provider)
        catalog_provider = CATALOG_BY_NAME.get(credential.provider)
        model_metadata = (
            catalog_provider.model_metadata.get(model)
            if catalog_provider is not None and model is not None
            else None
        )
        normalized_thinking = (
            normalize_thinking_level(thinking_level) if thinking_level is not None else None
        )
        mapped_thinking: str | None = None
        if normalized_thinking is not None:
            mapped_thinking = reasoning_effort_for_level(normalized_thinking)
            if (
                model_metadata is not None
                and normalized_thinking in model_metadata.thinking_level_map
            ):
                mapped_thinking = model_metadata.thinking_level_map[normalized_thinking]
        api = provider_control.model.api
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
        if not secret:
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
                    reasoning_effort=mapped_thinking,
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
                    timeout_seconds=timeout_seconds,
                    max_tokens=max_tokens,
                    thinking_budget_tokens=(
                        anthropic_thinking_budget_for_level(normalized_thinking)
                        if normalized_thinking is not None and mapped_thinking is not None
                        else None
                    ),
                )
            )

        config = OpenAICompatibleConfig(
            api_key=secret,
            base_url=base_url,
            headers=headers,
            api=api,
            provider_name=credential.provider,
            omit_authorization_header=False,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            reasoning_effort=mapped_thinking,
            reasoning_effort_parameter=(
                catalog_provider.thinking_parameter
                if catalog_provider is not None
                and catalog_provider.thinking_parameter in {"reasoning_effort", "reasoning.effort"}
                else "reasoning_effort"
            ),
            compat=model_metadata.compat if model_metadata is not None else {},
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
