"""Validated runtime settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from astro.agents import AgentExecutionSnapshot

from .errors import ConfigurationError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    backend_url: str = Field(
        default="https://api.main-sequence.app",
        validation_alias="MAINSEQUENCE_BACKEND",
    )
    auth_mode: Literal["runtime_credential"] = Field(
        default="runtime_credential",
        validation_alias="MAINSEQUENCE_AUTH_MODE",
    )
    runtime_credential_id: str | None = Field(
        default=None,
        validation_alias="MAINSEQUENCE_RUNTIME_CREDENTIAL_ID",
    )
    runtime_credential_secret: str | None = Field(
        default=None,
        validation_alias="MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET",
    )
    host: str = Field(default="0.0.0.0", validation_alias="ASTRO_HOST")
    port: int = Field(default=8787, validation_alias="ASTRO_PORT")
    trusted_origins: tuple[str, ...] = Field(
        default=(),
        validation_alias="ASTRO_TRUSTED_ORIGINS",
    )
    code_repository_root: Path = Field(
        default=Path("/workspace"),
        validation_alias="ASTRO_CODE_REPOSITORY_CWD",
    )
    code_repository_extensions_enabled: bool = Field(
        default=False,
        validation_alias="ASTRO_CODE_REPOSITORY_EXTENSIONS_ENABLED",
    )
    home: Path = Field(
        default_factory=Path.home,
        validation_alias="ASTRO_HOME",
    )
    a2a_asset_root: Path = Field(
        default=Path("/tmp/astro-a2a-assets"),
        validation_alias="ASTRO_A2A_ASSET_ROOT",
    )
    a2a_max_inline_file_bytes: int = Field(
        default=20 * 1024 * 1024,
        validation_alias="ASTRO_A2A_MAX_INLINE_FILE_BYTES",
    )
    a2a_max_aggregate_file_bytes: int = Field(
        default=40 * 1024 * 1024,
        gt=0,
        validation_alias="ASTRO_A2A_MAX_AGGREGATE_FILE_BYTES",
    )
    a2a_max_inline_file_count: int = Field(
        default=8,
        ge=1,
        le=64,
        validation_alias="ASTRO_A2A_MAX_INLINE_FILE_COUNT",
    )
    sessionless_asset_root: Path = Field(
        default=Path("/tmp/astro-sessionless-assets"),
        validation_alias="ASTRO_SESSIONLESS_ASSET_ROOT",
    )
    agent_execution_snapshot: AgentExecutionSnapshot | None = Field(
        default=None,
        validation_alias="ASTRO_AGENT_EXECUTION_SNAPSHOT",
    )
    log_level: str = Field(default="INFO", validation_alias="ASTRO_LOG_LEVEL")
    log_machine_sink: bool = Field(
        default=True,
        validation_alias="ASTRO_LOG_MACHINE_SINK",
    )
    log_human_sink: bool = Field(
        default=False,
        validation_alias="ASTRO_LOG_HUMAN_SINK",
    )
    log_payloads: bool = Field(
        default=False,
        validation_alias="ASTRO_LOG_PAYLOADS",
    )
    backend_connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        validation_alias="ASTRO_BACKEND_CONNECT_TIMEOUT_SECONDS",
    )
    backend_read_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        validation_alias="ASTRO_BACKEND_READ_TIMEOUT_SECONDS",
    )
    backend_write_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        validation_alias="ASTRO_BACKEND_WRITE_TIMEOUT_SECONDS",
    )
    backend_pool_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        validation_alias="ASTRO_BACKEND_POOL_TIMEOUT_SECONDS",
    )
    mcp_read_concurrency: int = Field(
        default=8,
        ge=1,
        validation_alias="ASTRO_MCP_READ_CONCURRENCY",
    )
    backend_max_response_bytes: int = Field(
        default=10 * 1024 * 1024,
        gt=0,
        validation_alias="ASTRO_BACKEND_MAX_RESPONSE_BYTES",
    )
    startup_dependencies_enabled: bool = Field(
        default=True,
        validation_alias="ASTRO_STARTUP_DEPENDENCIES_ENABLED",
    )
    session_entry_batch_max_entries: int = Field(
        default=100,
        ge=1,
        le=100,
        validation_alias="ASTRO_SESSION_ENTRY_BATCH_MAX_ENTRIES",
    )
    session_entry_batch_max_bytes: int = Field(
        default=8 * 1024 * 1024,
        gt=0,
        le=8 * 1024 * 1024,
        validation_alias="ASTRO_SESSION_ENTRY_BATCH_MAX_BYTES",
    )
    max_turn_output_bytes: int = Field(
        default=4 * 1024 * 1024,
        gt=0,
        validation_alias="ASTRO_MAX_TURN_OUTPUT_BYTES",
    )
    runtime_lease_ttl_seconds: int = Field(
        default=90,
        ge=10,
        validation_alias="ASTRO_SESSION_LEASE_TTL_SECONDS",
    )
    runtime_lease_renew_interval_seconds: int = Field(
        default=30,
        ge=1,
        validation_alias="ASTRO_SESSION_LEASE_RENEW_SECONDS",
    )
    session_idle_ttl_seconds: int = Field(
        default=900,
        ge=1,
        validation_alias="ASTRO_SESSION_IDLE_TTL_SECONDS",
    )
    session_eviction_interval_seconds: int = Field(
        default=30,
        ge=1,
        validation_alias="ASTRO_SESSION_EVICTION_INTERVAL_SECONDS",
    )
    turn_timeout_seconds: int = Field(
        default=900,
        ge=1,
        validation_alias="ASTRO_TURN_TIMEOUT_SECONDS",
    )
    shutdown_grace_seconds: int = Field(
        default=30,
        ge=1,
        validation_alias="ASTRO_SHUTDOWN_GRACE_SECONDS",
    )

    @field_validator("backend_url")
    @classmethod
    def normalize_backend_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("trusted_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> Settings:
        if self.runtime_lease_renew_interval_seconds >= self.runtime_lease_ttl_seconds:
            raise ValueError(
                "ASTRO_SESSION_LEASE_RENEW_SECONDS must be less than "
                "ASTRO_SESSION_LEASE_TTL_SECONDS"
            )
        if not self.log_machine_sink and not self.log_human_sink:
            raise ValueError("At least one Astro logging sink must be enabled")
        return self

    def validate_runtime_auth(self) -> None:
        missing = []
        if not self.runtime_credential_id:
            missing.append("MAINSEQUENCE_RUNTIME_CREDENTIAL_ID")
        if not self.runtime_credential_secret:
            missing.append("MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET")
        if missing:
            raise ConfigurationError("Missing runtime credential settings: " + ", ".join(missing))


@lru_cache
def get_settings() -> Settings:
    return Settings()
