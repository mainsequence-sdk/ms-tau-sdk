"""Typed backend request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    model_validator,
)

type HarnessKind = Literal["pi", "tau"]
type HarnessProtocol = Literal["pi-checkpoint-v1", "tau-session-v1"]
type TauEntryType = Literal[
    "message",
    "model_change",
    "thinking_level_change",
    "compaction",
    "branch_summary",
    "label",
    "leaf",
    "session_info",
    "custom",
]
type TauThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]
type AgentCapabilityKind = Literal["skill", "prompt", "extension"]
type AgentCapabilitySourceType = Literal[
    "inline",
    "registry",
    "repository",
    "api",
    "external",
]
type AgentTaskStatus = Literal[
    "submitted",
    "working",
    "input_required",
    "auth_required",
    "completed",
    "failed",
    "canceled",
    "rejected",
]


class BackendModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class BackendRequestModel(BackendModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AgentSession(BackendModel):
    uid: str
    agent_uid: str | None = None
    harness: HarnessKind
    harness_protocol: HarnessProtocol
    harness_version: str
    active_provider: str | None = Field(
        default=None,
        validation_alias=AliasChoices("active_provider", "llm_provider"),
    )
    active_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("active_model", "llm_model"),
    )
    active_thinking: TauThinkingLevel | None = Field(
        default=None,
        validation_alias=AliasChoices("active_thinking", "llm_thinking"),
    )
    status: str | None = None
    created_by_user_uid: str | None = None

    @model_validator(mode="after")
    def validate_harness_protocol(self) -> AgentSession:
        expected = {
            "pi": "pi-checkpoint-v1",
            "tau": "tau-session-v1",
        }[self.harness]
        if self.harness_protocol != expected:
            raise ValueError(f"Harness {self.harness!r} requires protocol {expected!r}")
        return self


class AgentCardEnvelope(BackendModel):
    agent_session_uid: str
    agent_uid: str
    agent_card: dict[str, Any] | None = None


class AgentCapability(BackendModel):
    uid: str
    kind: AgentCapabilityKind
    source_type: AgentCapabilitySourceType
    capability_path: str = ""
    has_content: bool = False
    content_sha256: str = ""


class SessionCapabilityBinding(BackendModel):
    uid: str
    capability_uid: str
    capability: AgentCapability
    is_enabled: bool = True
    source_type: AgentCapabilitySourceType


class CapabilityContent(BackendModel):
    content: str
    content_sha256: str = ""
    content_mime_type: str = ""
    content_size: int = 0


class SessionEntryRecord(BackendModel):
    sequence: int
    entry_type: TauEntryType
    entry_json: dict[str, Any]
    idempotency_key: str


class SessionEntryList(BackendModel):
    entries: list[SessionEntryRecord] = Field(default_factory=list)
    next_sequence: int = 0
    has_more: bool = False
    next_after_sequence: int | None = None


class SessionEntryAppendRequest(BackendRequestModel):
    lease_token: str
    expected_sequence: int
    idempotency_key: str
    entry: dict[str, Any]


type RuntimeLeasePurpose = Literal["runtime_run"]
type RuntimeLeaseReleaseReason = Literal[
    "runtime_load_failed",
    "runtime_eviction",
]


class RuntimeLease(BackendModel):
    lease_token: str
    holder_id: str
    lease_expires_at: datetime
    checkpoint_version: int = 0
    lease_purpose: RuntimeLeasePurpose = "runtime_run"
    cancel_requested: bool = False
    cancellation: dict[str, Any] | None = None


class RuntimeLeaseRequest(BackendRequestModel):
    holder_id: str
    ttl_seconds: int
    lease_purpose: Literal["runtime_run"] = "runtime_run"


class RuntimeLeaseRenewRequest(BackendRequestModel):
    lease_token: str
    holder_id: str
    ttl_seconds: int
    lease_purpose: Literal["runtime_run"] = "runtime_run"


class RuntimeLeaseReleaseRequest(BackendRequestModel):
    lease_token: str
    holder_id: str
    reason: RuntimeLeaseReleaseReason


class RuntimeState(BackendModel):
    harness: HarnessKind
    harness_protocol: HarnessProtocol
    harness_version: str
    runtime_state: str | None = None
    active_provider: str | None = None
    active_model: str | None = None
    active_thinking: TauThinkingLevel | None = None
    status: str | None = None
    working: bool = False
    cancel_state: Literal["not_running", "requested"] | None = None
    cancel_requested: bool = False
    cancellation_id: str | None = None

    @model_validator(mode="after")
    def validate_harness_protocol(self) -> RuntimeState:
        expected = {
            "pi": "pi-checkpoint-v1",
            "tau": "tau-session-v1",
        }[self.harness]
        if self.harness_protocol != expected:
            raise ValueError(f"Harness {self.harness!r} requires protocol {expected!r}")
        return self


class RuntimeStatePatch(BackendRequestModel):
    active_provider: str | None = None
    active_model: str | None = None
    active_thinking: TauThinkingLevel | None = None


class ProviderCredential(BackendModel):
    provider: str
    credential_kind: str = "api_key"
    api: str | None = None
    api_key: SecretStr | None = None
    access_token: SecretStr | None = None
    expires_at: datetime | None = None
    account_id: str | None = None
    base_url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def secret(self) -> str:
        if self.api_key is not None:
            return self.api_key.get_secret_value()
        if self.access_token is not None:
            return self.access_token.get_secret_value()
        return ""


class ProviderStatus(BackendModel):
    provider: str
    status: Literal[
        "active",
        "configured",
        "missing",
        "expired",
        "revoked",
        "error",
    ]
    credential_kind: str | None = None
    message: str | None = None


class AgentTask(BackendModel):
    uid: str
    task_id: str
    context_id: str
    agent_uid: str
    agent_session_uid: str | None = None
    status: AgentTaskStatus
    status_message: Any = None
    status_timestamp: datetime | None = None
    cancellation_requested: bool = False
    latest_message: dict[str, Any] | None = None
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentTaskCreateResult(BackendModel):
    task: AgentTask
    created: bool
