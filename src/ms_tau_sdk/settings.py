"""Validated runtime settings."""

from __future__ import annotations

import os
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigurationError


class TauSDKSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    backend_url: str = Field(
        default="https://api.main-sequence.app",
        validation_alias="MAINSEQUENCE_ENDPOINT",
    )
    auth_mode: Literal["runtime_credential", "jwt"] = Field(
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
    access_token: SecretStr | None = Field(
        default=None,
        validation_alias="MAINSEQUENCE_ACCESS_TOKEN",
    )
    refresh_token: SecretStr | None = Field(
        default=None,
        validation_alias="MAINSEQUENCE_REFRESH_TOKEN",
    )
    local_mode: bool = Field(default=False, validation_alias="TAU_LOCAL_MODE")
    local_provider: str | None = Field(default=None, validation_alias="TAU_LOCAL_PROVIDER")
    local_model: str | None = Field(default=None, validation_alias="TAU_LOCAL_MODEL")
    local_thinking: (
        Literal[
            "off",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ]
        | None
    ) = Field(default=None, validation_alias="TAU_LOCAL_THINKING")
    local_state_root: Path = Field(
        default_factory=lambda: Path.home() / ".tau" / "mainsequence",
        validation_alias="TAU_LOCAL_STATE_ROOT",
    )
    host: str = Field(default="0.0.0.0", validation_alias="MAINSEQUENCE_TAU_HOST")
    port: int = Field(default=8787, validation_alias="MAINSEQUENCE_TAU_PORT")
    trusted_origins: tuple[str, ...] = Field(
        default=(),
        validation_alias="MAINSEQUENCE_TAU_TRUSTED_ORIGINS",
    )
    workspace: Path = Field(
        default_factory=Path.cwd,
        validation_alias="MAINSEQUENCE_TAU_WORKSPACE",
    )
    a2a_asset_root: Path = Field(
        default=Path("/tmp/ms-tau-a2a-assets"),
        validation_alias="MAINSEQUENCE_TAU_A2A_ASSET_ROOT",
    )
    a2a_max_inline_file_bytes: int = Field(
        default=20 * 1024 * 1024,
        validation_alias="MAINSEQUENCE_TAU_A2A_MAX_INLINE_FILE_BYTES",
    )
    a2a_max_aggregate_file_bytes: int = Field(
        default=40 * 1024 * 1024,
        gt=0,
        validation_alias="MAINSEQUENCE_TAU_A2A_MAX_AGGREGATE_FILE_BYTES",
    )
    a2a_max_inline_file_count: int = Field(
        default=8,
        ge=1,
        le=64,
        validation_alias="MAINSEQUENCE_TAU_A2A_MAX_INLINE_FILE_COUNT",
    )
    log_level: str = Field(default="INFO", validation_alias="MAINSEQUENCE_TAU_LOG_LEVEL")
    log_machine_sink: bool = Field(
        default=True,
        validation_alias="MAINSEQUENCE_TAU_LOG_MACHINE_SINK",
    )
    log_human_sink: bool = Field(
        default=False,
        validation_alias="MAINSEQUENCE_TAU_LOG_HUMAN_SINK",
    )
    log_payloads: bool = Field(
        default=False,
        validation_alias="MAINSEQUENCE_TAU_LOG_PAYLOADS",
    )
    backend_connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        validation_alias="MAINSEQUENCE_TAU_BACKEND_CONNECT_TIMEOUT_SECONDS",
    )
    backend_read_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        validation_alias="MAINSEQUENCE_TAU_BACKEND_READ_TIMEOUT_SECONDS",
    )
    backend_write_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        validation_alias="MAINSEQUENCE_TAU_BACKEND_WRITE_TIMEOUT_SECONDS",
    )
    backend_pool_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        validation_alias="MAINSEQUENCE_TAU_BACKEND_POOL_TIMEOUT_SECONDS",
    )
    mcp_read_concurrency: int = Field(
        default=8,
        ge=1,
        validation_alias="MAINSEQUENCE_TAU_MCP_READ_CONCURRENCY",
    )
    backend_max_response_bytes: int = Field(
        default=10 * 1024 * 1024,
        gt=0,
        validation_alias="MAINSEQUENCE_TAU_BACKEND_MAX_RESPONSE_BYTES",
    )
    startup_dependencies_enabled: bool = Field(
        default=True,
        validation_alias="MAINSEQUENCE_TAU_STARTUP_DEPENDENCIES_ENABLED",
    )
    session_entry_batch_max_entries: int = Field(
        default=100,
        ge=1,
        le=100,
        validation_alias="MAINSEQUENCE_TAU_SESSION_ENTRY_BATCH_MAX_ENTRIES",
    )
    session_entry_batch_max_bytes: int = Field(
        default=8 * 1024 * 1024,
        gt=0,
        le=8 * 1024 * 1024,
        validation_alias="MAINSEQUENCE_TAU_SESSION_ENTRY_BATCH_MAX_BYTES",
    )
    max_turn_output_bytes: int = Field(
        default=4 * 1024 * 1024,
        gt=0,
        validation_alias="MAINSEQUENCE_TAU_MAX_TURN_OUTPUT_BYTES",
    )
    a2a_task_output_flush_interval_ms: int = Field(
        default=200,
        ge=25,
        le=5000,
        validation_alias="MAINSEQUENCE_TAU_A2A_TASK_OUTPUT_FLUSH_INTERVAL_MS",
    )
    a2a_task_output_flush_bytes: int = Field(
        default=8 * 1024,
        ge=256,
        le=1024 * 1024,
        validation_alias="MAINSEQUENCE_TAU_A2A_TASK_OUTPUT_FLUSH_BYTES",
    )
    a2a_task_event_poll_interval_seconds: float = Field(
        default=0.5,
        ge=0.05,
        le=10,
        validation_alias="MAINSEQUENCE_TAU_A2A_TASK_EVENT_POLL_SECONDS",
    )
    runtime_lease_ttl_seconds: int = Field(
        default=90,
        ge=10,
        validation_alias="MAINSEQUENCE_TAU_SESSION_LEASE_TTL_SECONDS",
    )
    runtime_lease_renew_interval_seconds: int = Field(
        default=30,
        ge=1,
        validation_alias="MAINSEQUENCE_TAU_SESSION_LEASE_RENEW_SECONDS",
    )
    session_idle_ttl_seconds: int = Field(
        default=900,
        ge=1,
        validation_alias="MAINSEQUENCE_TAU_SESSION_IDLE_TTL_SECONDS",
    )
    session_eviction_interval_seconds: int = Field(
        default=30,
        ge=1,
        validation_alias="MAINSEQUENCE_TAU_SESSION_EVICTION_INTERVAL_SECONDS",
    )
    turn_timeout_seconds: int = Field(
        default=900,
        ge=1,
        validation_alias="MAINSEQUENCE_TAU_TURN_TIMEOUT_SECONDS",
    )
    shutdown_grace_seconds: int = Field(
        default=30,
        ge=1,
        validation_alias="MAINSEQUENCE_TAU_SHUTDOWN_GRACE_SECONDS",
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

    @field_validator("workspace")
    @classmethod
    def validate_workspace(cls, value: Path) -> Path:
        candidate = value.expanduser()
        try:
            workspace = candidate.resolve(strict=True)
        except OSError as error:
            raise ValueError(f"Workspace does not exist: {candidate}") from error
        if not workspace.is_dir():
            raise ValueError(f"Workspace is not a directory: {workspace}")
        if not os.access(workspace, os.R_OK):
            raise ValueError(f"Workspace is not readable: {workspace}")
        return workspace

    @field_validator("local_provider", "local_model")
    @classmethod
    def normalize_local_selection(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @field_validator("local_state_root")
    @classmethod
    def normalize_local_state_root(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> TauSDKSettings:
        if self.runtime_lease_renew_interval_seconds >= self.runtime_lease_ttl_seconds:
            raise ValueError(
                "MAINSEQUENCE_TAU_SESSION_LEASE_RENEW_SECONDS must be less than "
                "MAINSEQUENCE_TAU_SESSION_LEASE_TTL_SECONDS"
            )
        if not self.log_machine_sink and not self.log_human_sink:
            raise ValueError("At least one Main Sequence TAU SDK logging sink must be enabled")
        if self.local_mode:
            if self.auth_mode != "jwt":
                raise ValueError("TAU_LOCAL_MODE requires MAINSEQUENCE_AUTH_MODE=jwt")
            if self.host == "0.0.0.0" and "host" not in self.model_fields_set:
                self.host = "127.0.0.1"
        elif self.auth_mode != "runtime_credential":
            raise ValueError(
                "MAINSEQUENCE_AUTH_MODE=jwt is supported only when TAU_LOCAL_MODE=true"
            )
        return self

    def validate_runtime_auth(self) -> None:
        if self.local_mode:
            missing = []
            if self.access_token is None or not self.access_token.get_secret_value().strip():
                missing.append("MAINSEQUENCE_ACCESS_TOKEN")
            if self.refresh_token is None or not self.refresh_token.get_secret_value().strip():
                missing.append("MAINSEQUENCE_REFRESH_TOKEN")
            if not self.local_provider:
                missing.append("TAU_LOCAL_PROVIDER")
            if not self.local_model:
                missing.append("TAU_LOCAL_MODEL")
            if missing:
                raise ConfigurationError("Missing local mode settings: " + ", ".join(missing))
            return
        missing = []
        if not self.runtime_credential_id:
            missing.append("MAINSEQUENCE_RUNTIME_CREDENTIAL_ID")
        if not self.runtime_credential_secret:
            missing.append("MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET")
        if missing:
            raise ConfigurationError("Missing runtime credential settings: " + ", ".join(missing))

    @property
    def workspace_digest(self) -> str:
        return sha256(str(self.workspace).encode("utf-8")).hexdigest()[:12]

    @property
    def local_state_path(self) -> Path:
        return self.local_state_root / self.workspace_digest / "runtime.sqlite3"

    @property
    def loopback_bind(self) -> bool:
        return self.host.strip().lower() in {"127.0.0.1", "::1", "localhost"}

    def local_session_uid(self, requested: str | None) -> str:
        value = str(requested or "default").strip() or "default"
        prefix = f"local-{self.workspace_digest}-"
        if value.startswith(prefix):
            return value
        suffix = sha256(value.encode("utf-8")).hexdigest()[:16] if value != "default" else value
        return f"{prefix}{suffix}"


@lru_cache
def get_settings() -> TauSDKSettings:
    return TauSDKSettings()
