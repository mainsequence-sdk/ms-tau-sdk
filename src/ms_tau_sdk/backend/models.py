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
    "custom_message",
    "model_change",
    "thinking_level_change",
    "compaction",
    "branch_summary",
    "label",
    "leaf",
    "session_info",
    "custom",
]
type TauThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]
type AgentRuntimeActivity = Literal["loading", "idle", "working", "persisting"]
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
type A2ATaskMessage = dict[str, Any]


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
    runtime_capabilities: dict[str, str] = Field(default_factory=dict)

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


class SessionEntryRecord(BackendModel):
    sequence: int
    entry_type: TauEntryType
    entry_json: dict[str, Any]
    idempotency_key: str
    turn_uid: str | None = None


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


class SessionEntryBatchItem(BackendRequestModel):
    idempotency_key: str
    entry: dict[str, Any]


type TauTurnPhase = Literal["started", "progress", "committed"]


class TauTurnLifecycle(BackendRequestModel):
    turn_uid: str
    phase: TauTurnPhase
    activity_sequence: int = Field(ge=1)


class TauTurnCommit(BackendModel):
    turn_uid: str
    next_sequence: int = Field(ge=0)
    committed_at: datetime


class SessionEntryBatchAppendRequest(BackendRequestModel):
    lease_token: str
    expected_sequence: int = Field(ge=0)
    entries: list[SessionEntryBatchItem]
    holder_id: str | None = None
    turn: TauTurnLifecycle | None = None

    @model_validator(mode="after")
    def validate_empty_commit(self) -> SessionEntryBatchAppendRequest:
        if not self.entries and (self.turn is None or self.turn.phase != "committed"):
            raise ValueError("An empty batch is allowed only for a committed turn")
        if self.turn is not None and not self.holder_id:
            raise ValueError("holder_id is required when turn is supplied")
        return self


class SessionEntryBatchAppendResponse(BackendModel):
    entries: list[SessionEntryRecord]
    next_sequence: int = Field(ge=0)
    created_count: int = Field(ge=0)
    replayed: bool
    turn_commit: TauTurnCommit | None = None
    runtime_state: RuntimeState | None = None


type RuntimeLeaseReleaseReason = Literal[
    "runtime_load_failed",
    "runtime_eviction",
]


class RuntimeLease(BackendModel):
    lease_token: str
    holder_id: str
    lease_expires_at: datetime
    checkpoint_version: int = 0
    cancel_requested: bool = False
    cancellation: dict[str, Any] | None = None
    runtime_activity: AgentRuntimeActivity | None = None
    active_turn_uid: str | None = None
    active_task_attempt_uid: str | None = None
    activity_revision: int | None = None
    activity_sequence: int | None = None
    activity_updated_at: datetime | None = None


class RuntimeLeaseRequest(BackendRequestModel):
    holder_id: str
    ttl_seconds: int


class RuntimeLeaseRenewRequest(BackendRequestModel):
    lease_token: str
    holder_id: str
    ttl_seconds: int


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
    runtime_activity: AgentRuntimeActivity | None = None
    active_turn_uid: str | None = None
    active_task_attempt_uid: str | None = None
    activity_revision: int | None = None
    activity_sequence: int | None = None
    activity_updated_at: datetime | None = None
    last_committed_turn_uid: str | None = None
    applied: bool | None = None
    runtime_capabilities: dict[str, str] = Field(default_factory=dict)
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


class RuntimeActivityPatch(BackendRequestModel):
    holder_id: str
    lease_token: str
    expected_activity_revision: int | None = Field(default=None, ge=0)
    activity_sequence: int | None = Field(default=None, ge=1)
    runtime_activity: AgentRuntimeActivity
    active_turn_uid: str | None = None

    @model_validator(mode="after")
    def validate_concurrency_mode(self) -> RuntimeActivityPatch:
        if (self.expected_activity_revision is None) == (self.activity_sequence is None):
            raise ValueError("Send exactly one of expected_activity_revision or activity_sequence")
        return self


class TauRuntimeBootstrapRequest(BackendRequestModel):
    holder_id: str
    ttl_seconds: int = Field(ge=1)
    bootstrap_request_uid: str
    history_after_sequence: int | None = Field(default=None, ge=0)
    supported_snapshot_schema_versions: list[int] = Field(default_factory=lambda: [2])
    supported_provider_control_schema_versions: list[int] = Field(default_factory=lambda: [1])
    tau_runtime_version: str


class ProviderControlModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    api: str
    input: list[str]
    reasoning: bool
    thinking_levels: list[TauThinkingLevel]


class ProviderControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    catalog_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider: str
    model: ProviderControlModel


class TauResumeSnapshot(BackendModel):
    base_sequence: int = Field(ge=0)
    last_committed_turn_uid: str
    snapshot_schema_version: int = Field(ge=1)
    tau_runtime_version: str
    runtime_config_sha256: str
    payload_sha256: str
    canonical_size: int = Field(ge=0)
    snapshot: dict[str, Any]


class TauRuntimeBootstrap(BackendModel):
    session: AgentSession
    lease: RuntimeLease
    runtime_state: RuntimeState
    history: SessionEntryList
    resume_snapshot: TauResumeSnapshot | None = None
    provider_credentials: dict[str, Any]
    provider_control: ProviderControl
    runtime_capabilities: dict[str, str]
    bootstrap_replayed: bool = False


class TauResumeSnapshotUploadRequest(BackendRequestModel):
    holder_id: str
    lease_token: str
    base_sequence: int = Field(ge=0)
    last_committed_turn_uid: str
    snapshot_schema_version: int = Field(ge=1)
    tau_runtime_version: str
    runtime_config_sha256: str
    payload_sha256: str
    snapshot: dict[str, Any]


class TauResumeSnapshotUploadResponse(BackendModel):
    applied: bool
    replayed: bool
    base_sequence: int = Field(ge=0)
    last_committed_turn_uid: str
    snapshot_schema_version: int = Field(ge=1)
    tau_runtime_version: str
    runtime_config_sha256: str
    payload_sha256: str
    canonical_size: int = Field(ge=0)


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


class ProviderExecutionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential: ProviderCredential
    provider_control: ProviderControl


class AgentTask(BackendModel):
    uid: str
    task_id: str
    context_id: str
    agent_uid: str
    agent_session_uid: str | None = None
    status: AgentTaskStatus
    status_message: A2ATaskMessage | None = None
    status_timestamp: datetime | None = None
    cancellation_requested: bool = False
    latest_message: A2ATaskMessage | None = None
    history: list[A2ATaskMessage] | None = None
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    last_event_sequence: int = 0
    failure_code: str = ""
    failure_category: str = ""
    failure_retryable: bool | None = None
    correlation_id: str = ""
    recovery_owner: str = ""
    recovery_count: int = 0


class AgentTaskCreateResult(BackendModel):
    task: AgentTask
    created: bool


class AgentTaskDispatch(BackendModel):
    uid: str
    state: Literal["pending", "signaled", "claimed", "settled", "canceled"]
    current_attempt_uid: str | None = None


class AgentTaskExecutionAttempt(BackendModel):
    uid: str
    dispatch_uid: str
    attempt_number: int = Field(ge=1)
    state: Literal[
        "claimed",
        "running",
        "interrupted",
        "completed",
        "failed",
        "canceled",
        "expired",
        "ambiguous",
    ]
    turn_uid: str | None = None
    entry_start_sequence: int | None = Field(default=None, ge=0)
    entry_end_sequence: int | None = Field(default=None, ge=0)
    turn_resolution: Literal["pending", "committed", "abandoned"] | None = None


class AgentTaskSnapshot(BackendModel):
    task: AgentTask
    event_cursor: int = Field(ge=0)


class AgentTaskEvent(BackendModel):
    uid: str | None = None
    sequence: int = Field(ge=1)
    event_type: str
    status: str = ""
    message_uid: str | None = None
    output_uid: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentTaskEventPage(BackendModel):
    events: list[AgentTaskEvent] = Field(default_factory=list)
    next_cursor: int = Field(ge=0)
    has_more: bool = False
