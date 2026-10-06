"""Refresh persisted, reference-only Secret requests before a resumed conversation turn."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence

from tau_agent.session import CustomEntry, SessionEntry
from tau_agent.types import JSONValue

from ms_tau_sdk.backend.mcp import MainSequenceMCPClient
from ms_tau_sdk.tools.secret_entry import SecretEntryStatus, secret_entry_result


async def refresh_secret_entries(
    *,
    entries: Sequence[SessionEntry],
    client: MainSequenceMCPClient,
    private_proof: dict[str, JSONValue] | None,
    record: Callable[[dict[str, JSONValue]], Awaitable[None]],
) -> str:
    latest: dict[str, SecretEntryStatus] = {}
    for entry in entries:
        if isinstance(entry, CustomEntry) and entry.namespace == "mainsequence.secret_entry":
            # Stored state is not proof of completion. Revalidate pending references below.
            try:
                status = SecretEntryStatus.model_validate_json(json.dumps(entry.data))
            except ValueError:
                raise RuntimeError(
                    "Stored Secret entry status is invalid. Check the request before continuing."
                ) from None
            latest[str(status.uid)] = status
    checks = []
    for status in latest.values():
        if status.status not in {"pending", "submitting"}:
            continue
        try:
            arguments: dict[str, object] = {
                "request_uid": str(status.uid),
                "organization_environment_uid": str(status.organization_environment_uid),
            }
            result = await client.call_tool(
                "secret_entry.status",
                arguments,
                meta={"mainsequence.ai/caller-session-proof/v1": private_proof}
                if private_proof is not None
                else None,
            )
            projected = secret_entry_result("secret_entry.status", result)
            if not isinstance(projected.details, dict):
                raise ValueError("Unverified status")
            payload = projected.details.get("structured_content")
            if projected.details.get("is_error") or not isinstance(payload, dict):
                raise ValueError("Unverified status")
            if payload.get("uid") != str(status.uid) or payload.get(
                "organization_environment_uid"
            ) != str(status.organization_environment_uid):
                raise ValueError("Unbound status")
        except Exception:
            # Do not run the model with an assumed success or copy transport errors to history.
            raise RuntimeError(
                "Secret entry status could not be verified. Retry the conversation after "
                "checking the private page; never send a value in chat."
            ) from None
        await record(payload)
        checks.append(projected.text)
    if not checks:
        return ""
    return (
        "\n\nPlatform Secret-entry status checked by the host before this turn:\n"
        + "\n".join(checks)
        + "\nContinue dependent work only for confirmed completion. Do not repeat an already "
        "completed business operation or request the secret value."
    )
