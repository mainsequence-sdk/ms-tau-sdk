"""Provider-neutral construction from backend-hydrated credentials."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import structlog
from tau_agent.provider import ModelProvider
from tau_ai.anthropic import AnthropicProvider
from tau_ai.env import (
    AnthropicConfig,
    OpenAICompatibleConfig,
    RuntimeProviderAuth,
    RuntimeProviderAuthResolver,
)
from tau_ai.google import GoogleGenerativeAIProvider
from tau_ai.mistral import MistralConversationsProvider
from tau_ai.openai_cache import is_direct_openai_url
from tau_ai.openai_codex import (
    OpenAICodexConfig,
    OpenAICodexCredentials,
    OpenAICodexProvider,
)
from tau_ai.openai_compatible import OpenAICompatibleProvider
from tau_coding.credentials import OAuthCredential
from tau_coding.oauth_registry import get_oauth_provider
from tau_coding.oauth_types import OAuthRuntimeAuth
from tau_coding.provider_catalog import BUILTIN_PROVIDER_CATALOG, ProviderCatalogEntry
from tau_coding.thinking import (
    DEFAULT_THINKING_LEVEL,
    ThinkingLevel,
    anthropic_thinking_budget_for_level,
    normalize_thinking_level,
    reasoning_effort_for_level,
)

from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.models import (
    AgentSession,
    ProviderControl,
    ProviderCredential,
    ProviderExecutionEvidence,
)
from ms_tau_sdk.errors import ConfigurationError

from .definitions import PROVIDER_DEFINITIONS
from .tau_compat import install_openai_compatible_provider_error_patch

CredentialResolver = Callable[[], Awaitable[ProviderCredential]]
CATALOG_BY_NAME = {provider.name: provider for provider in BUILTIN_PROVIDER_CATALOG}
PROVIDER_CREDENTIAL_REFRESH_SKEW = timedelta(seconds=60)
SUPPORTED_API_TRANSPORTS = frozenset(
    {
        "anthropic-messages",
        "google-generative-ai",
        "mistral-conversations",
        "openai-codex-responses",
        "openai-completions",
        "openai-responses",
    }
)
CUSTOM_API_TRANSPORTS = frozenset(
    {
        "openai-completions",
        "openai-responses",
    }
)
CUSTOM_INPUT_KINDS = frozenset({"text", "image"})
BLOCKED_CUSTOM_PROVIDER_HOSTS = frozenset(
    {
        "instance-data",
        "metadata",
        "metadata.google.internal",
    }
)

# The hosts a sign-in's own token may name as its provider's address. A sign-in credential can be
# saved by a person and shared, so a token that names any other host is refused: it would send
# the conversation and the token there.
SIGN_IN_ADDRESS_HOST_SUFFIXES = {"github-copilot": (".githubcopilot.com",)}
# The first system block Tau sends with an Anthropic sign-in, which Anthropic requires of one.
ANTHROPIC_SIGN_IN_SYSTEM_PROMPT = "You are Claude Code, Anthropic's official CLI for Claude."

logger = structlog.get_logger(__name__)


def _fall_back(
    level: ThinkingLevel,
    *,
    selected: ThinkingLevel | None,
    provider_name: str,
    model: str,
) -> ThinkingLevel:
    """Use ``level`` in place of a selected level the model cannot run, and say so."""
    if selected is not None:
        logger.warning(
            "providers.thinking_level_unavailable",
            provider=provider_name,
            model=model,
            selected=selected,
            used=level,
        )
    return level


@dataclass(frozen=True, slots=True)
class ProviderRuntime:
    name: str
    model: str
    thinking_level: ThinkingLevel
    provider: ModelProvider
    credential: ProviderCredential
    provider_control: ProviderControl


class ProviderFactory:
    def __init__(
        self,
        backend: MainSequenceClient,
        *,
        provider_timeout_seconds: float = 60,
    ) -> None:
        self.backend = backend
        # A self-hosted provider can send no bytes until its first token, so the HTTP read
        # timeout bounds time-to-first-token; a cold model load plus a long prompt needs more
        # than a hosted API does, and only the operator knows which one they run.
        self.provider_timeout_seconds = provider_timeout_seconds

    @staticmethod
    def _validate_custom_base_url(
        base_url: str,
        *,
        source: str = "Custom provider credential",
    ) -> None:
        parsed = urlsplit(base_url)
        hostname = str(parsed.hostname or "").casefold()
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ConfigurationError(f"{source} contains an invalid base_url")
        if (
            hostname in BLOCKED_CUSTOM_PROVIDER_HOSTS
            or hostname == "localhost"
            or hostname.endswith(".localhost")
        ):
            raise ConfigurationError(f"{source} base_url targets a prohibited host")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return
        if (
            address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ConfigurationError(f"{source} base_url targets a prohibited address")

    @staticmethod
    def _sign_in_auth(credential: ProviderCredential) -> OAuthRuntimeAuth | None:
        """How Tau sends this credential when it is a sign-in Tau has its own rules for.

        Tau sends an Anthropic or GitHub Copilot sign-in with headers of its own, and takes
        Copilot's address from the token. None for an API key, for a provider Tau has no sign-in
        for, and for ``openai-codex``, whose transport does this itself.

        In a hosted session the credential is a pass (ADR 0030): nothing is read out of it, so the
        answer then carries Tau's headers and no address. The relay owns the address.
        """
        if credential.credential_kind != "oauth" or credential.provider == "openai-codex":
            return None
        oauth_provider = get_oauth_provider(credential.provider)
        if oauth_provider is None:
            return None
        relayed = credential.relay_definition is not None
        auth = oauth_provider.runtime_auth(
            OAuthCredential(
                access="" if relayed else credential.secret(),
                refresh="",
                expires=0,
                account_id=credential.account_id,
            )
        )
        base_url = None if relayed else auth.base_url
        if base_url is not None:
            ProviderFactory._validate_custom_base_url(base_url, source="Sign-in credential")
            hostname = str(urlsplit(base_url).hostname or "").casefold()
            if not hostname.endswith(SIGN_IN_ADDRESS_HOST_SUFFIXES.get(credential.provider, ())):
                raise ConfigurationError(
                    "Sign-in credential names an address that is not its provider's"
                )
        return OAuthRuntimeAuth(
            api_key=credential.secret(),
            base_url=base_url,
            # The transport sends the secret itself, in the place its protocol uses.
            headers={
                name: value
                for name, value in (auth.headers or {}).items()
                if name.casefold() != "authorization"
            },
        )

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

    @staticmethod
    def model_outside_tau_catalog(provider_name: str, model: str) -> bool:
        """Whether this runtime's Tau catalog lists neither the provider nor the model."""
        provider = CATALOG_BY_NAME.get(provider_name)
        return provider is None or model not in provider.models

    def _validate_projected_execution(
        self,
        provider_control: ProviderControl,
        *,
        provider_name: str,
        model: str,
        thinking_level: str | None,
        normalized_thinking: ThinkingLevel,
        media_types: set[str] | None,
        scope: str,
    ) -> ThinkingLevel:
        """Validate a model Tau has no metadata for from its provider control alone.

        A provider this Tau does not know, and a model this Tau does not list of a provider it
        knows (ADR 0029), run from the platform's projection: the inputs it projects, bounded by
        what every Tau transport carries, and the thinking levels it projects. ``scope`` names
        which of the two a refusal is about.
        """
        projected_inputs = set(provider_control.model.input)
        if not projected_inputs or not projected_inputs.issubset(CUSTOM_INPUT_KINDS):
            raise ConfigurationError(
                f"Platform provider-control input capabilities are invalid for {scope}"
            )
        projected_thinking = set(provider_control.model.thinking_levels)
        if not provider_control.model.reasoning and projected_thinking:
            raise ConfigurationError(
                "Platform provider-control thinking levels contradict the reasoning "
                f"capability of {scope}"
            )
        self.validate_input_media(
            provider_control,
            media_types=media_types or set(),
        )
        if thinking_level and normalized_thinking in projected_thinking:
            return normalized_thinking
        if not projected_thinking or not provider_control.model.reasoning:
            default = DEFAULT_THINKING_LEVEL
        elif DEFAULT_THINKING_LEVEL in projected_thinking:
            default = DEFAULT_THINKING_LEVEL
        elif "off" in projected_thinking:
            default = "off"
        else:
            default = provider_control.model.thinking_levels[0]
        return _fall_back(
            default,
            selected=normalized_thinking if thinking_level else None,
            provider_name=provider_name,
            model=model,
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
                "Platform provider-control evidence does not match the selected provider"
            )
        if provider_control.model.model != model:
            raise ConfigurationError(
                "Platform provider-control evidence does not match the selected model"
            )
        try:
            normalized_thinking = normalize_thinking_level(thinking_level)
        except ValueError as error:
            raise ConfigurationError(str(error)) from error
        provider = CATALOG_BY_NAME.get(provider_name)
        if provider is None:
            projected_api = provider_control.model.api
            if projected_api not in CUSTOM_API_TRANSPORTS:
                raise ConfigurationError(
                    f"Provider {provider_name} is not in this runtime's Tau catalog and uses "
                    f"unsupported Tau API transport {projected_api}"
                )
            return self._validate_projected_execution(
                provider_control,
                provider_name=provider_name,
                model=model,
                thinking_level=thinking_level,
                normalized_thinking=normalized_thinking,
                media_types=media_types,
                scope="a provider not in this runtime's Tau catalog",
            )
        metadata = provider.model_metadata.get(model)
        tau_api = (
            "openai-codex-responses"
            if provider.kind == "openai-codex"
            else metadata.api
            if metadata is not None and metadata.api
            else provider.api or provider.kind
        )
        projected_api = provider_control.model.api
        if projected_api not in SUPPORTED_API_TRANSPORTS or projected_api != tau_api:
            raise ConfigurationError(
                f"Provider {provider_name} uses unsupported or contradictory "
                f"Tau API transport {projected_api}"
            )
        if model not in provider.models:
            # A model this Tau does not list, of a provider it knows, runs on the provider's own
            # transport from the provider control (ADR 0029): Tau has no metadata to bound the
            # projection, so the projection is the authority, as for a provider Tau lacks.
            return self._validate_projected_execution(
                provider_control,
                provider_name=provider_name,
                model=model,
                thinking_level=thinking_level,
                normalized_thinking=normalized_thinking,
                media_types=media_types,
                scope="a model not in this runtime's Tau catalog",
            )
        tau_inputs = set(metadata.input if metadata is not None else ()) or {"text"}
        projected_inputs = set(provider_control.model.input)
        if not projected_inputs or not projected_inputs.issubset(tau_inputs):
            raise ConfigurationError(
                "Platform provider-control input capabilities exceed Tau execution support"
            )
        tau_thinking = set(self._thinking_levels(provider, model))
        if provider_control.model.reasoning and not tau_thinking:
            raise ConfigurationError(
                "Platform provider-control reasoning capability exceeds Tau execution support"
            )
        # The platform may offer levels this Tau release cannot run; only shared ones are used.
        available = [
            level for level in provider_control.model.thinking_levels if level in tau_thinking
        ]
        self.validate_input_media(
            provider_control,
            media_types=media_types or set(),
        )
        if thinking_level and normalized_thinking in available:
            return normalized_thinking
        if provider.thinking_default is not None and provider.thinking_default in available:
            default = provider.thinking_default
        elif DEFAULT_THINKING_LEVEL in available or not available:
            default = DEFAULT_THINKING_LEVEL
        else:
            default = available[0]
        return _fall_back(
            default,
            selected=normalized_thinking if thinking_level else None,
            provider_name=provider_name,
            model=model,
        )

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
        if provider is None or model not in provider.models:
            # Tau has no metadata for the model; the projection, checked above, is the authority.
            return
        metadata = provider.model_metadata.get(model)
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
        if (
            session.model_provider_credential_uid is not None
            and credential.model_provider_credential_uid != session.model_provider_credential_uid
        ):
            raise ConfigurationError("Backend credential does not match the session binding")
        thinking_level = self.validate_execution(
            evidence.provider_control,
            provider_name=provider_name,
            model=model,
            thinking_level=session.active_thinking,
        )
        if self.model_outside_tau_catalog(provider_name, model):
            # Once per load, with no credential value: the model runs from the provider control.
            logger.info(
                "providers.model_outside_tau_catalog",
                provider=provider_name,
                model=model,
                session_uid=session.uid,
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
                    if cached_credential.model_provider_credential_uid is not None and (
                        refreshed.credential.model_provider_credential_uid
                        != cached_credential.model_provider_credential_uid
                        or refreshed.credential.organization_environment_uid
                        != cached_credential.organization_environment_uid
                    ):
                        raise ConfigurationError(
                            "Credential refresh changed the configured provider"
                        )
                    # The transport was built on the first answer's address (ADR 0030).
                    if refreshed.credential.relay_definition != cached_credential.relay_definition:
                        raise ConfigurationError("Credential refresh changed the model relay")
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
        timeout_seconds: float | None = None,
    ) -> ModelProvider:
        if timeout_seconds is None:
            timeout_seconds = self.provider_timeout_seconds
        definition = PROVIDER_DEFINITIONS.get(credential.provider)
        catalog_provider = CATALOG_BY_NAME.get(credential.provider)
        # A provider this Tau does not know runs only through Tau's OpenAI-compatible constructor,
        # as an Organization custom provider or as the platform's catalog defines it.
        outside_tau_catalog = catalog_provider is None
        # A model this Tau does not list runs from the provider control alone, as a provider it
        # does not know does (ADR 0029).
        projection_is_authority = catalog_provider is None or (
            model is not None and model not in catalog_provider.models
        )
        is_custom_provider = (
            outside_tau_catalog and credential.credential_kind == "organization_custom"
        )
        # A hosted session holds a pass for the relay, never a secret (ADR 0030). The provider's
        # address and headers stay with whoever serves the relay.
        relay = credential.relay_definition
        catalog_definition = (
            credential.catalog_definition
            if outside_tau_catalog and not is_custom_provider
            else None
        )
        model_metadata = (
            catalog_provider.model_metadata.get(model)
            if catalog_provider is not None and model is not None
            else None
        )
        normalized_thinking = (
            normalize_thinking_level(thinking_level) if thinking_level is not None else None
        )
        if not provider_control.model.reasoning or (
            projection_is_authority
            and normalized_thinking not in provider_control.model.thinking_levels
        ):
            normalized_thinking = None
        mapped_thinking: str | None = None
        if normalized_thinking is not None:
            mapped_thinking = reasoning_effort_for_level(normalized_thinking)
            if (
                model_metadata is not None
                and normalized_thinking in model_metadata.thinking_level_map
            ):
                mapped_thinking = model_metadata.thinking_level_map[normalized_thinking]
        api = provider_control.model.api
        # Tau puts a placeholder in place of every image unless the model reads images.
        supports_images = "image" in provider_control.model.input
        if outside_tau_catalog:
            if api not in CUSTOM_API_TRANSPORTS:
                raise ConfigurationError(
                    f"Provider {credential.provider} is not in this runtime's Tau catalog and "
                    f"uses unsupported Tau API transport {api}"
                )
            if is_custom_provider:
                if credential.api != api:
                    raise ConfigurationError(
                        "Custom provider credential transport does not match "
                        "Platform provider-control evidence"
                    )
                if relay is None:
                    if not credential.base_url:
                        raise ConfigurationError(
                            "Custom provider credential requires an explicit base_url"
                        )
                    self._validate_custom_base_url(credential.base_url)
            elif catalog_definition is not None:
                if relay is None:
                    self._validate_custom_base_url(
                        catalog_definition.base_url,
                        source="Platform catalog definition",
                    )
            else:
                raise ConfigurationError(
                    f"Provider {credential.provider} is not in this runtime's Tau catalog, and "
                    "the platform sent neither an organization_custom credential nor its "
                    "catalog definition"
                )
        # Only an organization_custom credential defines an address and headers (ADR 0027); any
        # other kind's are ignored, not refused, since they may repeat the provider's default.
        credential_connection = credential.credential_kind == "organization_custom"
        if credential_connection and credential.base_url and not outside_tau_catalog:
            self._validate_custom_base_url(credential.base_url)
        if catalog_definition is not None:
            # The address comes from the platform's catalog, never from the saved credential.
            base_url = catalog_definition.base_url
            headers: dict[str, str] = {}
        else:
            base_url = (credential.base_url if credential_connection else None) or (
                model_metadata.base_url
                if model_metadata is not None and model_metadata.base_url
                else definition.base_url
                if definition
                else "https://api.openai.com/v1"
            )
            headers = {
                **(catalog_provider.headers if catalog_provider is not None else {}),
                **(model_metadata.headers if model_metadata is not None else {}),
                **(credential.headers if credential_connection else {}),
            }
        compat = (
            catalog_definition.compat
            if catalog_definition is not None
            else model_metadata.compat
            if model_metadata is not None
            else {}
        )
        if relay is not None:
            self._validate_custom_base_url(relay.base_url, source="Model relay definition")
            # Tau sends prompt-cache keys and, on the Responses API, session-affinity headers by
            # default only to OpenAI's own host. Behind the relay it sees the relay's host, so
            # the provider's own address decides, as it would without the relay.
            if not is_custom_provider and is_direct_openai_url(base_url):
                compat = {
                    "supportsPromptCacheKey": True,
                    **({"sendSessionAffinityHeaders": True} if api == "openai-responses" else {}),
                    **compat,
                }
            base_url = relay.base_url
            headers = {
                **(catalog_provider.headers if catalog_provider is not None else {}),
                **(model_metadata.headers if model_metadata is not None else {}),
            }
        secret = credential.secret()
        if not secret and (relay is not None or not is_custom_provider):
            raise ConfigurationError(
                f"Backend returned no usable credential for {credential.provider}"
            )

        sign_in = self._sign_in_auth(credential)
        sign_in_resolver: RuntimeProviderAuthResolver | None = None
        if sign_in is not None:
            base_url = sign_in.base_url or base_url
            headers = {**headers, **(sign_in.headers or {})}

            async def resolve_sign_in() -> RuntimeProviderAuth:
                # Before each request, as Tau does: a refreshed sign-in may carry a new token and,
                # for GitHub Copilot, a new address.
                current = (
                    await credential_resolver() if credential_resolver is not None else credential
                )
                auth = self._sign_in_auth(current)
                if auth is None or not auth.api_key:
                    raise ConfigurationError(
                        f"Backend returned no usable credential for {credential.provider}"
                    )
                return RuntimeProviderAuth(
                    api_key=auth.api_key, base_url=auth.base_url, headers=auth.headers
                )

            sign_in_resolver = resolve_sign_in

        if api == "openai-codex-responses":
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
                    supports_images=supports_images,
                    provider_name=credential.provider,
                )
            )

        if api == "anthropic-messages":
            # Tau's Anthropic transport calls `<base>/messages` and expects a base that ends in
            # /v1. Tau's own configuration appends it to a catalog address, which has none.
            anthropic_base_url = base_url.rstrip("/")
            if not anthropic_base_url.endswith("/v1"):
                anthropic_base_url = f"{anthropic_base_url}/v1"
            # Tau sends a key as `x-api-key` only to a provider that is Anthropic's own kind. A
            # sign-in, and a Claude model on another kind of provider such as GitHub Copilot,
            # goes as a bearer token.
            anthropic_kind = catalog_provider is not None and catalog_provider.kind == "anthropic"
            return AnthropicProvider(
                AnthropicConfig(
                    api_key=secret,
                    base_url=anthropic_base_url,
                    headers=headers,
                    bearer_auth=sign_in is not None or not anthropic_kind,
                    oauth_system_prompt=(
                        ANTHROPIC_SIGN_IN_SYSTEM_PROMPT
                        if sign_in is not None and anthropic_kind
                        else None
                    ),
                    credential_resolver=sign_in_resolver,
                    provider_name=credential.provider,
                    timeout_seconds=timeout_seconds,
                    max_tokens=max_tokens,
                    supports_images=supports_images,
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
            credential_resolver=sign_in_resolver,
            omit_authorization_header=is_custom_provider and not bool(secret),
            max_tokens=max_tokens,
            supports_images=supports_images,
            timeout_seconds=timeout_seconds,
            reasoning_effort=mapped_thinking,
            reasoning_effort_parameter=(
                catalog_provider.thinking_parameter
                if catalog_provider is not None
                and catalog_provider.thinking_parameter in {"reasoning_effort", "reasoning.effort"}
                else "reasoning_effort"
            ),
            compat=compat,
        )
        if api == "google-generative-ai":
            return GoogleGenerativeAIProvider(config)
        if api == "mistral-conversations":
            return MistralConversationsProvider(config)
        if api in {"openai-completions", "openai-responses"}:
            install_openai_compatible_provider_error_patch()
            return OpenAICompatibleProvider(config)
        raise ConfigurationError(
            f"Provider {credential.provider} uses unsupported Tau API transport {api}"
        )
