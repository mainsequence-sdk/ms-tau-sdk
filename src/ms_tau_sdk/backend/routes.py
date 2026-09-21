"""Canonical backend API routes used by Main Sequence TAU SDK's runtime client."""

from __future__ import annotations

from typing import Literal

API_V1_PREFIX = "/api/v1"

RUNTIME_CREDENTIAL_TOKEN = f"{API_V1_PREFIX}/runtime-credentials/token/"
AGENT_SESSIONS = f"{API_V1_PREFIX}/agent-sessions/"
MODEL_PROVIDER_CREDENTIALS = f"{API_V1_PREFIX}/model-provider-credentials/"
AGENT_TASKS = f"{API_V1_PREFIX}/agent-tasks/"

type RuntimeLeaseOperation = Literal["acquire", "renew", "release"]
type ModelProviderCredentialOperation = Literal["hydrate", "status", "flush", "revoke"]
type AgentTaskOperation = Literal[
    "cancel",
    "continue",
    "dispatches",
    "dispatches/claim",
    "attempts/start",
    "attempts/settle",
    "outputs/create",
    "outputs/append",
    "outputs/finalize",
    "snapshot",
    "events",
]


def agent_session(session_uid: str) -> str:
    return f"{AGENT_SESSIONS}{session_uid}/"


def agent_session_agent_card(session_uid: str) -> str:
    return f"{agent_session(session_uid)}agent-card/"


def agent_session_entries(session_uid: str) -> str:
    return f"{agent_session(session_uid)}entries/"


def agent_session_entries_append(session_uid: str) -> str:
    return f"{agent_session_entries(session_uid)}append/"


def agent_session_entries_append_batch(session_uid: str) -> str:
    return f"{agent_session_entries(session_uid)}append-batch/"


def agent_session_runtime_lease(
    session_uid: str,
    operation: RuntimeLeaseOperation,
) -> str:
    return f"{agent_session(session_uid)}runtime-lease/{operation}/"


def agent_session_runtime_state(session_uid: str) -> str:
    return f"{agent_session(session_uid)}runtime-state/"


def agent_session_tau_runtime_activity(session_uid: str) -> str:
    return f"{agent_session(session_uid)}tau-runtime-activity/"


def agent_session_tau_runtime_bootstrap(session_uid: str) -> str:
    return f"{agent_session(session_uid)}tau-runtime/bootstrap/"


def agent_session_tau_resume_snapshot(session_uid: str) -> str:
    return f"{agent_session(session_uid)}tau-runtime/resume-snapshot/"


def agent_session_runtime_cancel_request(session_uid: str) -> str:
    return f"{agent_session(session_uid)}runtime-cancel-request/"


def model_provider_credentials(operation: ModelProviderCredentialOperation) -> str:
    return f"{MODEL_PROVIDER_CREDENTIALS}{operation}/"


def agent_task(task_uid: str) -> str:
    return f"{AGENT_TASKS}{task_uid}/"


def agent_task_operation(task_uid: str, operation: AgentTaskOperation) -> str:
    return f"{agent_task(task_uid)}{operation}/"
