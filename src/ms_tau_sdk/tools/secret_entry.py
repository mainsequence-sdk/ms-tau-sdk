"""Reference-only Secret entry. Values travel directly from a private browser to Django."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from mcp import types
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from tau_agent.messages import TextContent
from tau_agent.tools import AgentToolResult

from ms_tau_sdk.runtime.task_context import active_task_execution


class SecretEntryStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    uid: UUID
    status: Literal["pending", "submitting", "completed", "cancelled", "expired", "failed"]
    operation: Literal["create", "rotate"]
    name: str
    purpose: str
    organization_environment_uid: UUID
    organization_environment_name: str
    requesting_agent_name: str | None
    consumer_user_uid: UUID | None
    secret_uid: UUID | None
    entry_url: str
    expires_at: datetime
    error_code: str
    requirement_reference: str

    @model_validator(mode="after")
    def trusted_entry_link(self) -> SecretEntryStatus:
        url = urlsplit(self.entry_url)
        local = url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1"}
        if (
            (url.scheme != "https" and not local)
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
            or url.path != "/app/main-sequence-foundry/secrets"
            or parse_qs(url.query)
            != {
                "secretEntryRequest": [str(self.uid)],
                "organization_environment_uid": [str(self.organization_environment_uid)],
            }
            or self.requirement_reference != f"secret-entry:{self.uid}"
            or (self.status != "completed" and self.secret_uid is not None)
        ):
            raise ValueError("Invalid Secret-entry binding")
        return self


def secret_entry_result(canonical_name: str, result: types.CallToolResult) -> AgentToolResult:
    """Ignore free-form MCP content; accept only the complete value-free status contract."""
    try:
        if result.isError or result.structuredContent is None:
            raise ValueError("Secret entry is unavailable")
        # Strict JSON accepts UUID/date strings but rejects coercion and extra fields.
        status = SecretEntryStatus.model_validate_json(json.dumps(result.structuredContent))
    except (ValidationError, ValueError, TypeError):
        return AgentToolResult(
            content=[
                TextContent(
                    text=(
                        "Secret entry could not be verified. Check access or retry; "
                        "never send a value in chat."
                    )
                )
            ],
            details={"mcp_tool": canonical_name, "is_error": True},
        )
    payload = status.model_dump(mode="json")
    if status.status == "pending":
        guidance = (
            f"Enter the value on the authenticated private page: {status.entry_url}\n"
            "Open it yourself outside agent browser control or screen capture. "
            "Do not paste the value here. "
            "Wait for the person, then call secret_entry.status; "
            "opening the page does not complete the request."
        )
    elif status.status == "submitting":
        guidance = (
            "Storage is in progress. Check secret_entry.status; do not repeat the submission."
        )
    elif status.status == "completed":
        guidance = "Django confirmed storage. Saving does not grant access to a consumer."
    elif status.error_code == "storage_outcome_unknown":
        guidance = (
            "The storage outcome is uncertain. Do not retry the value write; "
            "ask an operator to reconcile this request."
        )
    else:
        guidance = f"Secret entry is {status.status}. No completion is confirmed."
    task = active_task_execution()
    if (
        canonical_name == "secret_entry.start"
        and status.status == "pending"
        and task is not None
        and task.interruption_status is None
    ):
        task.request_interruption(
            status="auth_required",
            text=guidance,
            details={"requirementReference": status.requirement_reference},
        )
    return AgentToolResult(
        content=[TextContent(text=guidance), TextContent(text=json.dumps(payload, sort_keys=True))],
        details={"mcp_tool": canonical_name, "is_error": False, "structured_content": payload},
    )
