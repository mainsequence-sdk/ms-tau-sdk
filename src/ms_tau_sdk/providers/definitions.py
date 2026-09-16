"""Provider defaults used when the backend credential omits transport metadata."""

from __future__ import annotations

from dataclasses import dataclass

from tau_coding.provider_catalog import BUILTIN_PROVIDER_CATALOG


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    name: str
    api: str
    base_url: str


PROVIDER_DEFINITIONS: dict[str, ProviderDefinition] = {
    provider.name: ProviderDefinition(
        name=provider.name,
        api=provider.api or provider.kind,
        base_url=provider.base_url,
    )
    for provider in BUILTIN_PROVIDER_CATALOG
}
