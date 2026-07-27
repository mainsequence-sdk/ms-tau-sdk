"""Configuration for the standalone web tools."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebAccessSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        case_sensitive=True,
    )

    exa_api_key: str | None = Field(default=None, validation_alias="EXA_API_KEY")
    perplexity_api_key: str | None = Field(
        default=None,
        validation_alias="PERPLEXITY_API_KEY",
    )
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    github_token: str | None = Field(default=None, validation_alias="GITHUB_TOKEN")
    provider: Literal["auto", "exa", "perplexity", "gemini"] = "auto"
    request_timeout_seconds: float = 60.0
    max_response_bytes: int = 10 * 1024 * 1024
    max_inline_content: int = 30_000
    max_stored_content: int = 2_000_000
    max_redirects: int = 5
    allow_private_networks: bool = False
    allow_local_files: bool = True


__all__ = ["WebAccessSettings"]
