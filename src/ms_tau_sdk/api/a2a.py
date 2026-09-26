"""Standard A2A REST, JSON-RPC, and SSE transport over Tau sessions."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, cast

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from structlog.contextvars import bind_contextvars
from tau_agent.types import JSONValue

from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.models import (
    AgentTask,
    AgentTaskCreateResult,
    AgentTaskSnapshot,
)
from ms_tau_sdk.errors import BackendError, LeaseLostError, TaskTerminalizationUnknownError
from ms_tau_sdk.logging import bind_request_log_fields
from ms_tau_sdk.protocols.a2a_failure import (
    failure_status_message,
    normalize_status_message,
)
from ms_tau_sdk.protocols.a2a_message import (
    agent_message as serialize_agent_message,
)
from ms_tau_sdk.protocols.a2a_message import (
    output_contract as parse_output_contract,
)
from ms_tau_sdk.protocols.a2a_message import (
    prepare_a2a_input,
)
from ms_tau_sdk.protocols.a2a_message import (
    sse as encode_sse,
)
from ms_tau_sdk.protocols.a2a_roles import (
    A2AMessageDirection,
    A2AProtocolRole,
    message_to_binding,
    message_to_protocol,
)
from ms_tau_sdk.protocols.strict_json import (
    StrictJsonContract,
    StrictJsonError,
    build_repair_prompt,
    validate_strict_json,
)
from ms_tau_sdk.runtime.manager import RuntimeExecutionFence, SessionRuntimeManager
from ms_tau_sdk.runtime.provenance import (
    CallerIdentityError,
    TurnProvenance,
    turn_provenance_from_request,
)
from ms_tau_sdk.runtime.task_context import (
    TaskExecutionContext,
    active_task_execution,
    task_execution_scope,
)
from ms_tau_sdk.settings import TauSDKSettings

from .dependencies import backend, runtime_manager, settings

router = APIRouter()
BackendDep = Annotated[MainSequenceClient, Depends(backend)]
RuntimeManagerDep = Annotated[SessionRuntimeManager, Depends(runtime_manager)]
SettingsDep = Annotated[TauSDKSettings, Depends(settings)]
logger = structlog.get_logger(__name__)

REST_BASE = "/api/a2a/v1"
TERMINAL = {"completed", "failed", "canceled", "rejected"}
INTERRUPTED = {"input_required", "auth_required"}
TASK_RETURN_STATES = TERMINAL | INTERRUPTED
STATE_MAP = {
    "submitted": "TASK_STATE_SUBMITTED",
    "working": "TASK_STATE_WORKING",
    "input_required": "TASK_STATE_INPUT_REQUIRED",
    "auth_required": "TASK_STATE_AUTH_REQUIRED",
    "completed": "TASK_STATE_COMPLETED",
    "failed": "TASK_STATE_FAILED",
    "canceled": "TASK_STATE_CANCELED",
    "rejected": "TASK_STATE_REJECTED",
}
PUSH_NOTIFICATION_NOT_SUPPORTED_MESSAGE = "Push notifications are not supported"
RESPONSE_KIND_EXTENSION_URI = "https://mainsequence.ai/a2a/extensions/response-kind/v1"
PUSH_NOTIFICATION_RPC_METHODS = frozenset(
    {
        "CreateTaskPushNotificationConfig",
        "GetTaskPushNotificationConfig",
        "ListTaskPushNotificationConfigs",
        "DeleteTaskPushNotificationConfig",
        "tasks/pushNotificationConfig/set",
        "tasks/pushNotificationConfig/get",
        "tasks/pushNotificationConfig/list",
        "tasks/pushNotificationConfig/delete",
    }
)
SAFE_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
DEFAULT_TASK_HISTORY_LENGTH = 100


def _history_length(value: object, *, default: int = DEFAULT_TASK_HISTORY_LENGTH) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HTTPException(status_code=400, detail="historyLength must be a non-negative integer")
    return min(value, DEFAULT_TASK_HISTORY_LENGTH)


def _body_history_length(body: dict[str, Any]) -> int:
    configuration = body.get("configuration", {})
    if not isinstance(configuration, dict):
        raise HTTPException(status_code=400, detail="configuration must be an object")
    return _history_length(configuration.get("historyLength"))


def _safe_correlation(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized if SAFE_CORRELATION_PATTERN.fullmatch(normalized) else None


def _bind_a2a_context(
    request: Request,
    *,
    method: str,
    request_id: object = None,
    message: dict[str, Any] | None = None,
    task_id: object = None,
    streaming: bool = False,
) -> None:
    message = message or {}
    fields: dict[str, object] = {
        "a2a_method": method[:128],
        "a2a_streaming": streaming,
    }
    candidates = {
        "a2a_request_id": request_id,
        "a2a_context_id": message.get("contextId"),
        "a2a_message_id": message.get("messageId"),
        "a2a_task_id": task_id,
    }
    for correlation_field, value in candidates.items():
        normalized = _safe_correlation(value)
        if normalized is not None:
            fields[correlation_field] = normalized
    if "a2a_context_id" in fields:
        fields["agent_session_uid"] = fields["a2a_context_id"]
    bind_request_log_fields(request.scope, **fields)


class ResponseKind(StrEnum):
    MESSAGE = "message"
    TASK = "task"


def _activated_extensions(value: str | None) -> set[str]:
    return {extension.strip() for extension in str(value or "").split(",") if extension.strip()}


def _response_kind(
    body: dict[str, Any],
    *,
    a2a_extensions: str | None,
    streaming: bool = False,
) -> tuple[ResponseKind, bool, bool]:
    configuration = body.get("configuration", {})
    if not isinstance(configuration, dict):
        raise HTTPException(status_code=400, detail="configuration must be an object")
    return_immediately = configuration.get("returnImmediately", False)
    if not isinstance(return_immediately, bool):
        raise HTTPException(
            status_code=400,
            detail="configuration.returnImmediately must be a boolean",
        )
    if streaming and "returnImmediately" in configuration:
        raise HTTPException(
            status_code=400,
            detail="configuration.returnImmediately is not valid for message:stream",
        )
    if "responseKind" not in configuration:
        return ResponseKind.MESSAGE, False, return_immediately
    if streaming:
        raise HTTPException(
            status_code=400,
            detail="configuration.responseKind is not valid for message:stream",
        )
    try:
        response_kind = ResponseKind(configuration["responseKind"])
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=400,
            detail="configuration.responseKind must be 'message' or 'task'",
        ) from error
    if RESPONSE_KIND_EXTENSION_URI not in _activated_extensions(a2a_extensions):
        raise HTTPException(
            status_code=400,
            detail=(
                f"configuration.responseKind requires A2A-Extensions: {RESPONSE_KIND_EXTENSION_URI}"
            ),
        )
    return response_kind, True, return_immediately


def _response_kind_extension(response_kinds: list[str]) -> dict[str, Any]:
    return {
        "uri": RESPONSE_KIND_EXTENSION_URI,
        "description": (
            "Select whether message:send returns a completed message or an asynchronous task."
        ),
        "required": False,
        "params": {
            "supportedResponseKinds": response_kinds,
            "defaultResponseKind": ResponseKind.MESSAGE.value,
        },
    }


def _supported_response_kinds(agent_card: dict[str, Any] | None) -> list[str]:
    if not isinstance(agent_card, dict):
        return [ResponseKind.MESSAGE.value]
    capabilities = agent_card.get("capabilities")
    if not isinstance(capabilities, dict):
        return [ResponseKind.MESSAGE.value]
    extensions = capabilities.get("extensions")
    if not isinstance(extensions, list):
        return [ResponseKind.MESSAGE.value]
    for extension in extensions:
        if not isinstance(extension, dict) or extension.get("uri") != RESPONSE_KIND_EXTENSION_URI:
            continue
        params = extension.get("params")
        values = params.get("supportedResponseKinds") if isinstance(params, dict) else None
        if not isinstance(values, list):
            return [ResponseKind.MESSAGE.value]
        supported = [ResponseKind.MESSAGE.value]
        if ResponseKind.TASK.value in values:
            supported.append(ResponseKind.TASK.value)
        return supported
    return [ResponseKind.MESSAGE.value]


def _with_effective_response_kind_capability(
    agent_card: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if agent_card is None:
        return None
    normalized = dict(agent_card)
    advertised = normalized.get("capabilities")
    capabilities = dict(advertised) if isinstance(advertised, dict) else {}
    extensions = capabilities.get("extensions")
    preserved = (
        [
            dict(extension)
            for extension in extensions
            if isinstance(extension, dict) and extension.get("uri") != RESPONSE_KIND_EXTENSION_URI
        ]
        if isinstance(extensions, list)
        else []
    )
    preserved.append(_response_kind_extension(_supported_response_kinds(agent_card)))
    capabilities["extensions"] = preserved
    normalized["capabilities"] = capabilities
    return normalized


PROTECTED_MESSAGE_RPC_METHODS = frozenset(
    {"SendMessage", "message/send", "SendStreamingMessage", "message/stream"}
)


def _log_caller_identity_rejection(request: Request, error: CallerIdentityError) -> None:
    logger.warning(
        "turn.caller_identity_rejected",
        message="Protected message route rejected a request without a valid caller identity",
        route=request.url.path,
        failing_headers=list(error.failing_headers),
    )


def _caller_identity_rejection(request: Request, error: CallerIdentityError) -> JSONResponse:
    _log_caller_identity_rejection(request, error)
    return JSONResponse(status_code=error.status_code, content=error.body())


def _caller_identity_json_rpc_error(
    request: Request, request_id: object, error: CallerIdentityError
) -> dict[str, Any]:
    _log_caller_identity_rejection(request, error)
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32000,
            "message": error.detail,
            "data": {"code": error.code},
        },
    }


def _push_notification_error_info() -> dict[str, str]:
    return {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "PUSH_NOTIFICATION_NOT_SUPPORTED",
        "domain": "a2a-protocol.org",
    }


def _push_notification_rest_error() -> JSONResponse:
    return JSONResponse(
        status_code=400,
        media_type="application/a2a+json",
        content={
            "error": {
                "code": 400,
                "status": "FAILED_PRECONDITION",
                "message": PUSH_NOTIFICATION_NOT_SUPPORTED_MESSAGE,
                "details": [_push_notification_error_info()],
            }
        },
    )


def _push_notification_json_rpc_error(request_id: object) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32003,
            "message": PUSH_NOTIFICATION_NOT_SUPPORTED_MESSAGE,
            "data": [_push_notification_error_info()],
        },
    }


def _unsupported_operation_rest_error(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        media_type="application/a2a+json",
        content={
            "error": {
                "code": 400,
                "status": "FAILED_PRECONDITION",
                "message": message,
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "UNSUPPORTED_OPERATION",
                        "domain": "a2a-protocol.org",
                    }
                ],
            }
        },
    )


def _unsupported_operation_json_rpc_error(
    request_id: object,
    message: str,
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32004, "message": message},
    }


def _without_push_notification_capability(
    agent_card: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if agent_card is None:
        return None
    normalized = dict(agent_card)
    advertised = normalized.get("capabilities")
    capabilities = dict(advertised) if isinstance(advertised, dict) else {}
    capabilities.pop("push_notifications", None)
    capabilities["pushNotifications"] = False
    normalized["capabilities"] = capabilities
    return normalized


def _effective_agent_card(agent_card: dict[str, Any] | None) -> dict[str, Any] | None:
    return _with_effective_response_kind_capability(
        _without_push_notification_capability(agent_card)
    )


async def _require_advertised_response_kind(
    client: MainSequenceClient,
    *,
    context_id: str,
    response_kind: ResponseKind,
) -> None:
    envelope = await client.get_agent_card(context_id)
    supported = _supported_response_kinds(_effective_agent_card(envelope.agent_card))
    if response_kind.value not in supported:
        raise HTTPException(
            status_code=400,
            detail=(
                f"responseKind '{response_kind.value}' is not advertised by the receiving agent"
            ),
        )


def _sse(payload: dict[str, Any]) -> bytes:
    return encode_sse(payload)


def _materialize_pdfs(
    *,
    parts: object,
    context_id: str,
    message_id: str,
    config: TauSDKSettings,
) -> list[Any]:
    prepared = prepare_a2a_input(
        {
            "message": {
                "messageId": message_id,
                "contextId": context_id,
                "role": A2AMessageDirection.REQUESTER.value,
                "parts": parts,
            }
        },
        config,
        allowed_media_types={"application/pdf"},
    )
    return [item.path for item in prepared.files]


def _request_parts(body: dict[str, Any], config: TauSDKSettings) -> tuple[dict[str, Any], str]:
    normalized_body = dict(body)
    if isinstance(body.get("message"), dict):
        public_message = dict(body["message"])
        public_message.setdefault("role", A2AProtocolRole.USER.value)
        try:
            normalized_body["message"] = message_to_binding(public_message)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    if config.local_mode and isinstance(normalized_body.get("message"), dict):
        normalized_body = dict(normalized_body)
        local_message = dict(normalized_body["message"])
        requested_context = str(local_message.get("contextId") or "").strip()
        if requested_context:
            local_message["contextId"] = config.local_session_uid(requested_context)
        normalized_body["message"] = local_message
    prepared = prepare_a2a_input(
        normalized_body,
        config,
        allowed_media_types={"application/pdf"},
    )
    return prepared.message, prepared.prompt()


def _a2a_turn_provenance(
    config: TauSDKSettings,
    request: Request,
) -> TurnProvenance:
    if config.local_mode:
        return {
            "channel": "a2a",
            "origin": "agent",
            "actorKind": "agent",
            "actorUid": f"local-a2a-client-{config.workspace_digest}",
        }
    return turn_provenance_from_request("a2a", request.headers)


def _output_contract(body: dict[str, Any]) -> StrictJsonContract:
    return parse_output_contract(body)


def _agent_message(
    *,
    context_id: str,
    text: str,
    strict_json: bool,
) -> dict[str, Any]:
    binding_message = serialize_agent_message(
        context_id=context_id,
        text=text,
        strict_json=strict_json,
    )
    return cast(dict[str, Any], message_to_protocol(binding_message))


def _task_payload(task: AgentTask, *, history_length: int | None = None) -> dict[str, Any]:
    artifacts = [
        {
            "artifactId": str(output.get("artifact_id") or output.get("uid")),
            "name": output.get("name", ""),
            "parts": output.get("parts", []),
        }
        for output in task.outputs
    ]
    timestamp = task.status_timestamp or datetime.now(UTC)
    status: dict[str, Any] = {
        "state": STATE_MAP.get(task.status, "TASK_STATE_UNKNOWN"),
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
    }
    status_message = normalize_status_message(
        task.status_message,
        context_id=task.context_id,
        task_id=task.task_id,
    )
    if status_message is not None:
        status["message"] = message_to_protocol(status_message)
    payload: dict[str, Any] = {
        "id": task.task_id,
        "contextId": task.context_id,
        "status": status,
        "artifacts": artifacts,
    }
    if history_length != 0 and task.history is not None:
        payload["history"] = [message_to_protocol(message) for message in task.history]
    return payload


async def _task_with_history(
    client: MainSequenceClient,
    task: AgentTask,
    *,
    history_length: int,
) -> AgentTask:
    if history_length == 0:
        return task.model_copy(update={"history": None})
    history = await client.list_task_messages(task.uid, limit=history_length, offset=0)
    return task.model_copy(update={"history": history})


def _append_bounded(
    chunks: list[str],
    value: str,
    *,
    current_bytes: int,
    max_output_bytes: int,
) -> int:
    next_bytes = current_bytes + len(value.encode())
    if next_bytes > max_output_bytes:
        raise HTTPException(
            status_code=502,
            detail="Agent output exceeded MAINSEQUENCE_TAU_MAX_TURN_OUTPUT_BYTES",
        )
    chunks.append(value)
    return next_bytes


def _event_text(event_type: str, data: dict[str, Any], *, has_chunks: bool) -> str:
    if event_type in {"text_delta", "text-delta"}:
        return str(data.get("delta", data.get("textDelta", "")))
    if event_type == "message_end" and not has_chunks:
        message = data.get("message")
        if isinstance(message, dict):
            content = message.get("content", [])
            return "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
    return ""


def _assistant_message_text(message: dict[str, Any]) -> str:
    content = message.get("content", [])
    if not isinstance(content, list):
        return ""
    return "".join(
        part["text"]
        for part in content
        if isinstance(part, dict)
        and part.get("type") == "text"
        and isinstance(part.get("text"), str)
    )


def _assistant_message(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict) and value.get("role") == "assistant":
        return value
    return None


def _last_assistant_message(value: object) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    for message in reversed(value):
        assistant = _assistant_message(message)
        if assistant is not None:
            return assistant
    return None


def _turn_failure_detail(message: dict[str, Any], stop_reason: str) -> str:
    value = message.get("errorMessage", message.get("error_message"))
    if value:
        normalized = " ".join(str(value).split())
        if len(normalized) > 500:
            normalized = normalized[:497].rstrip() + "..."
        return f"Agent turn failed: {normalized}"
    if stop_reason == "aborted":
        return "Agent turn was aborted"
    return "Agent turn failed without a provider diagnostic"


@dataclass(slots=True)
class _TurnAccumulator:
    max_output_bytes: int
    delta_chunks: list[str] = field(default_factory=list)
    delta_bytes: int = 0
    latest_message_end: dict[str, Any] | None = None
    latest_agent_end: dict[str, Any] | None = None
    event_types: set[str] = field(default_factory=set)
    assistant_content_types: set[str] = field(default_factory=set)

    def consume(self, event: object) -> None:
        event_type = str(getattr(event, "type", ""))
        data = getattr(event, "data", {})
        if not isinstance(data, dict):
            return
        self.event_types.add(event_type)
        if event_type in {"text_delta", "text-delta"}:
            value = str(data.get("delta", data.get("textDelta", "")))
            if value:
                self.delta_bytes = _append_bounded(
                    self.delta_chunks,
                    value,
                    current_bytes=self.delta_bytes,
                    max_output_bytes=self.max_output_bytes,
                )
            return
        if event_type == "message_end":
            assistant = _assistant_message(data.get("message"))
            if assistant is not None:
                self.latest_message_end = assistant
                self._record_content_types(assistant)
            return
        if event_type == "agent_end":
            assistant = _last_assistant_message(data.get("messages"))
            if assistant is not None:
                self.latest_agent_end = assistant
                self._record_content_types(assistant)

    def result(self) -> str:
        final_message = self.latest_agent_end or self.latest_message_end
        if final_message is not None:
            stop_reason = str(
                final_message.get(
                    "stopReason",
                    final_message.get("stop_reason", ""),
                )
            )
            if stop_reason in {"error", "aborted"}:
                raise HTTPException(
                    status_code=502,
                    detail=_turn_failure_detail(final_message, stop_reason),
                )
            final_text = _assistant_message_text(final_message).strip()
            if final_text:
                self._check_final_size(final_text)
                return final_text

        delta_text = "".join(self.delta_chunks).strip()
        if delta_text:
            return delta_text
        raise HTTPException(
            status_code=502,
            detail="Agent turn produced no textual answer",
        )

    def diagnostics(self) -> dict[str, object]:
        final_message = self.latest_agent_end or self.latest_message_end or {}
        return {
            "event_types": sorted(self.event_types),
            "assistant_content_types": sorted(self.assistant_content_types),
            "terminal_reason": final_message.get(
                "stopReason",
                final_message.get("stop_reason"),
            ),
            "delta_bytes": self.delta_bytes,
        }

    def _record_content_types(self, message: dict[str, Any]) -> None:
        content = message.get("content", [])
        if not isinstance(content, list):
            return
        self.assistant_content_types.update(
            str(part.get("type")) for part in content if isinstance(part, dict) and part.get("type")
        )

    def _check_final_size(self, value: str) -> None:
        if len(value.encode()) > self.max_output_bytes:
            raise HTTPException(
                status_code=502,
                detail="Agent output exceeded MAINSEQUENCE_TAU_MAX_TURN_OUTPUT_BYTES",
            )


async def _collect_turn(
    manager: SessionRuntimeManager,
    context_id: str,
    prompt: str,
    *,
    max_output_bytes: int,
    provenance: TurnProvenance,
    turn_uid: str | None = None,
) -> str:
    accumulator = _TurnAccumulator(max_output_bytes=max_output_bytes)
    prompt_options: dict[str, Any] = {"provenance": provenance}
    if turn_uid is not None:
        prompt_options["turn_uid"] = turn_uid
    async for event in manager.prompt(context_id, prompt, **prompt_options):
        accumulator.consume(event)
    context = active_task_execution()
    if context is not None and context.interruption_status is not None:
        return ""
    try:
        return accumulator.result()
    except HTTPException as error:
        logger.warning(
            "a2a.turn.invalid_output",
            message="Tau turn did not produce a valid textual A2A answer",
            context_id=context_id,
            status_code=error.status_code,
            **accumulator.diagnostics(),
        )
        raise


async def _collect_validated_turn(
    manager: SessionRuntimeManager,
    context_id: str,
    prompt: str,
    contract: StrictJsonContract,
    *,
    max_output_bytes: int,
    provenance: TurnProvenance,
    turn_uid: str | None = None,
) -> str:
    text = await _collect_turn(
        manager,
        context_id,
        prompt,
        max_output_bytes=max_output_bytes,
        provenance=provenance,
        turn_uid=turn_uid,
    )
    if not contract.enabled:
        return text
    original_text = text
    repair_attempts = 0 if turn_uid is not None else contract.repair_attempts
    for attempt in range(repair_attempts + 1):
        try:
            canonical, _value = validate_strict_json(text, contract)
            return canonical
        except StrictJsonError as error:
            if attempt >= repair_attempts:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Agent did not satisfy the strict JSON output contract after "
                        f"{repair_attempts} repair attempts: {error}"
                    ),
                ) from error
            text = await _collect_turn(
                manager,
                context_id,
                build_repair_prompt(
                    invalid_text=original_text,
                    validation_error=str(error),
                    contract=contract,
                    attempt=attempt + 1,
                ),
                max_output_bytes=max_output_bytes,
                provenance=provenance,
            )
    raise AssertionError("strict JSON repair loop did not terminate")


def _task_message_for_backend(
    message: dict[str, Any],
    *,
    context_id: str,
    task_id: str,
    local_mode: bool,
) -> dict[str, Any]:
    extensions = message.get("extensions", [])
    if local_mode:
        return {
            "messageId": message["messageId"],
            "contextId": context_id,
            "taskId": task_id,
            "role": A2AMessageDirection.REQUESTER.value,
            "parts": message["parts"],
            "metadata": message.get("metadata", {}),
            "extensions": extensions,
            "referenceTaskIds": message.get("referenceTaskIds", []),
        }
    if extensions:
        raise HTTPException(
            status_code=400,
            detail="message.extensions is not supported for managed Tasks",
        )
    # Django's AgentTaskInitialMessageSerializer requires message_id and defaults
    # optional extensions. Omission works with both its dictionary and URI-list forms.
    return {
        "message_id": message["messageId"],
        "parts": message["parts"],
        "metadata": message.get("metadata", {}),
        "reference_task_ids": message.get("referenceTaskIds", []),
    }


async def _create_backend_task(
    client: MainSequenceClient,
    *,
    message: dict[str, Any],
    task_id: str,
    local_mode: bool,
    output_contract: StrictJsonContract | None = None,
    provenance: TurnProvenance | None = None,
) -> AgentTaskCreateResult:
    context_id = str(message["contextId"])
    initial_message = _task_message_for_backend(
        message, context_id=context_id, task_id=task_id, local_mode=local_mode
    )
    session = await client.get_session(context_id)
    if not session.agent_uid:
        raise HTTPException(status_code=409, detail="Session has no bound agent")
    return await client.create_task(
        {
            "agent_uid": session.agent_uid,
            "agent_session_uid": context_id,
            "context_id": context_id,
            "task_id": task_id,
            "initial_message": initial_message,
            "metadata": {
                "transport": "a2a",
                "execution": {
                    "output_contract": {
                        "mode": (output_contract or StrictJsonContract()).mode,
                        "schema": (output_contract or StrictJsonContract()).schema,
                        "repair_attempts": (
                            output_contract or StrictJsonContract()
                        ).repair_attempts,
                    },
                    "provenance": dict(provenance or {}),
                },
            },
        }
    )


async def _wait_for_task_return_state(
    client: MainSequenceClient,
    task: AgentTask,
    *,
    poll_interval_seconds: float,
    wait_timeout_seconds: float,
) -> AgentTask:
    current = task
    deadline = time.monotonic() + wait_timeout_seconds
    while current.status not in TASK_RETURN_STATES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(poll_interval_seconds, remaining))
        current = (await client.get_task_snapshot(current.uid)).task
    return current


@dataclass(frozen=True, slots=True)
class _ClaimedTask:
    attempt_uid: str
    attempt_number: int
    turn_uid: str
    holder_id: str
    lease_token: str


class _TaskCancellationRequested(Exception):
    """The durable Task aggregate contains an explicit cancellation request."""

    def __init__(self, task: AgentTask) -> None:
        self.task = task
        super().__init__(f"Cancellation requested for Task {task.task_id}")


async def _claim_backend_task(
    client: MainSequenceClient,
    manager: SessionRuntimeManager,
    task: AgentTask,
    *,
    dispatch_uid: str | None = None,
) -> _ClaimedTask:
    resolved_dispatch_uid = dispatch_uid
    if not resolved_dispatch_uid:
        dispatches = await client.list_task_dispatches(task.uid)
        open_dispatch = next(
            (
                candidate
                for candidate in reversed(dispatches)
                if candidate.state in {"pending", "signaled"}
            ),
            None,
        )
        if open_dispatch is None:
            raise BackendError(f"Backend Task {task.task_id} has no claimable dispatch")
        resolved_dispatch_uid = open_dispatch.uid
    fence = await manager.task_execution_fence(task.agent_session_uid or task.context_id)
    attempt = await client.claim_task_dispatch(
        task.uid,
        holder_id=fence.holder_id,
        lease_token=fence.lease_token,
        dispatch_uid=resolved_dispatch_uid,
        executor_instance_id=fence.holder_id,
    )
    fence = await manager.task_execution_fence(task.agent_session_uid or task.context_id)
    turn_uid = str(uuid.uuid4())
    attempt = await client.start_task_attempt(
        task.uid,
        attempt_uid=attempt.uid,
        holder_id=fence.holder_id,
        lease_token=fence.lease_token,
        turn_uid=turn_uid,
    )
    return _ClaimedTask(
        attempt_uid=attempt.uid,
        attempt_number=attempt.attempt_number,
        turn_uid=turn_uid,
        holder_id=fence.holder_id,
        lease_token=fence.lease_token,
    )


async def _current_task_fence(
    manager: SessionRuntimeManager,
    task: AgentTask,
    claim: _ClaimedTask,
) -> RuntimeExecutionFence:
    """Read the current rotating session lease token for an attempt-fenced write."""

    fence = await manager.task_execution_fence(task.agent_session_uid or task.context_id)
    if fence.holder_id != claim.holder_id:
        raise LeaseLostError(f"Task execution lease holder changed for Task {task.task_id}")
    return fence


async def _settle_claimed_task(
    client: MainSequenceClient,
    manager: SessionRuntimeManager,
    task: AgentTask,
    claim: _ClaimedTask,
    *,
    status: str,
    status_message: dict[str, Any] | None = None,
    outcome_category: str = "",
    failure_detail: str = "",
) -> AgentTask:
    fence = await _current_task_fence(manager, task, claim)
    optional: dict[str, Any] = {}
    if status_message is not None:
        optional["status_message"] = status_message
    if outcome_category:
        optional["outcome_category"] = outcome_category
    if failure_detail:
        optional["failure_detail"] = failure_detail
    await client.settle_task_attempt(
        task.uid,
        attempt_uid=claim.attempt_uid,
        holder_id=fence.holder_id,
        lease_token=fence.lease_token,
        status=status,
        **optional,
    )
    return await client.get_task(task.uid)


async def _settle_execution_failure(
    client: MainSequenceClient,
    manager: SessionRuntimeManager,
    task: AgentTask,
    claim: _ClaimedTask,
    error: Exception,
) -> AgentTask:
    status_message = failure_status_message(
        context_id=task.context_id,
        task_id=task.task_id,
        failure_code="execution_failed",
        category="execution",
        retryable=False,
        attempt_number=claim.attempt_number,
        correlation_id=task.task_id,
    )
    try:
        failed = await _settle_claimed_task(
            client,
            manager,
            task,
            claim,
            status="failed",
            status_message=status_message,
            outcome_category="execution",
            failure_detail=type(error).__name__,
        )
    except Exception as settlement_error:
        logger.error(
            "a2a.task.terminalization_unknown",
            message="Task execution failed before its terminal state could be persisted",
            task_uid=task.uid,
            task_id=task.task_id,
            attempt_uid=claim.attempt_uid,
            attempt_number=claim.attempt_number,
            execution_error_type=type(error).__name__,
            settlement_error_type=type(settlement_error).__name__,
            outcome="terminalization_unknown",
        )
        raise TaskTerminalizationUnknownError(
            "Task execution stopped before its terminal state could be persisted",
            detail={
                "taskId": task.task_id,
                "correlationId": task.task_id,
                "retryable": True,
            },
        ) from settlement_error
    logger.info(
        "a2a.task.failed",
        message="Task execution failure was persisted",
        task_uid=task.uid,
        task_id=task.task_id,
        attempt_uid=claim.attempt_uid,
        attempt_number=claim.attempt_number,
        error_type=type(error).__name__,
        outcome="execution_failed",
    )
    return failed


@dataclass(slots=True)
class _DurableArtifactWriter:
    client: MainSequenceClient
    manager: SessionRuntimeManager
    task: AgentTask
    claim: _ClaimedTask
    flush_interval_ms: int
    flush_bytes: int
    cancellation_poll_interval_seconds: float
    artifact_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    output_uid: str | None = None
    revision: int = 0
    pending: list[str] = field(default_factory=list)
    pending_bytes: int = 0
    last_flush_at: float = field(default_factory=time.monotonic)
    last_cancellation_check_at: float = 0.0

    async def ensure_not_canceled(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if (
            not force
            and now - self.last_cancellation_check_at < self.cancellation_poll_interval_seconds
        ):
            return
        current = await self.client.get_task(self.task.uid)
        self.last_cancellation_check_at = now
        if current.cancellation_requested or current.status == "canceled":
            raise _TaskCancellationRequested(current)

    async def append_text(self, value: str) -> None:
        if not value:
            return
        self.pending.append(value)
        self.pending_bytes += len(value.encode("utf-8"))
        elapsed_ms = (time.monotonic() - self.last_flush_at) * 1000
        if self.pending_bytes >= self.flush_bytes or elapsed_ms >= self.flush_interval_ms:
            await self.flush()

    async def flush(self, *, final: bool = False) -> None:
        text = "".join(self.pending)
        if not text and not final:
            return
        await self.ensure_not_canceled(force=True)
        fence = await _current_task_fence(self.manager, self.task, self.claim)
        created = self.revision == 0
        if created:
            result = await self.client.create_task_output(
                self.task.uid,
                attempt_uid=self.claim.attempt_uid,
                holder_id=fence.holder_id,
                lease_token=fence.lease_token,
                artifact_id=self.artifact_id,
                parts=[{"text": text}] if text else [],
                name="Agent response",
            )
            self.output_uid = str(result["uid"])
        elif final:
            if self.output_uid is None:
                raise RuntimeError("Backend Task output UID is unavailable")
            result = await self.client.finalize_task_output(
                self.task.uid,
                attempt_uid=self.claim.attempt_uid,
                holder_id=fence.holder_id,
                lease_token=fence.lease_token,
                output_uid=self.output_uid,
                expected_revision=self.revision,
            )
        else:
            if self.output_uid is None:
                raise RuntimeError("Backend Task output UID is unavailable")
            result = await self.client.append_task_output(
                self.task.uid,
                attempt_uid=self.claim.attempt_uid,
                holder_id=fence.holder_id,
                lease_token=fence.lease_token,
                output_uid=self.output_uid,
                expected_revision=self.revision,
                parts=[{"text": text}] if text else [],
            )
        self.revision = int(result.get("revision") or self.revision + 1)
        self.pending.clear()
        self.pending_bytes = 0
        self.last_flush_at = time.monotonic()
        if final and created:
            await self.flush(final=True)

    async def write_strict_json(self, text: str) -> None:
        await self.ensure_not_canceled(force=True)
        fence = await _current_task_fence(self.manager, self.task, self.claim)
        result = await self.client.create_task_output(
            self.task.uid,
            attempt_uid=self.claim.attempt_uid,
            holder_id=fence.holder_id,
            lease_token=fence.lease_token,
            artifact_id=self.artifact_id,
            parts=[{"data": json.loads(text), "mediaType": "application/json"}],
            name="Agent response",
        )
        self.output_uid = str(result["uid"])
        self.revision = int(result.get("revision") or 1)
        await self.flush(final=True)


async def _collect_task_turn(
    manager: SessionRuntimeManager,
    task: AgentTask,
    prompt: str,
    contract: StrictJsonContract,
    *,
    max_output_bytes: int,
    provenance: TurnProvenance,
    writer: _DurableArtifactWriter,
) -> str:
    if contract.enabled:
        await writer.ensure_not_canceled(force=True)
        text = await _collect_validated_turn(
            manager,
            task.context_id,
            prompt,
            contract,
            max_output_bytes=max_output_bytes,
            provenance=provenance,
            turn_uid=writer.claim.turn_uid,
        )
        context = active_task_execution()
        if context is not None and context.interruption_status is not None:
            return ""
        await writer.ensure_not_canceled(force=True)
        await writer.write_strict_json(text)
        return text

    accumulator = _TurnAccumulator(max_output_bytes=max_output_bytes)
    async for event in manager.prompt(
        task.context_id,
        prompt,
        provenance=provenance,
        turn_uid=writer.claim.turn_uid,
    ):
        await writer.ensure_not_canceled()
        accumulator.consume(event)
        value = _event_text(event.type, event.data, has_chunks=bool(accumulator.delta_chunks[:-1]))
        if event.type in {"text_delta", "text-delta"} and value:
            await writer.append_text(value)
    context = active_task_execution()
    if context is not None and context.interruption_status is not None:
        await writer.flush()
        return ""
    text = accumulator.result()
    if not accumulator.delta_chunks:
        await writer.append_text(text)
    await writer.flush(final=True)
    return text


async def _execute_task(
    client: MainSequenceClient,
    manager: SessionRuntimeManager,
    task: AgentTask,
    *,
    prompt: str,
    output_contract: StrictJsonContract,
    max_output_bytes: int,
    provenance: TurnProvenance,
    config: TauSDKSettings | None = None,
    dispatch_uid: str | None = None,
    claim: _ClaimedTask | None = None,
) -> dict[str, Any]:
    bind_contextvars(
        a2a_task_id=task.task_id,
        task_uid=task.uid,
        a2a_context_id=task.context_id,
        session_uid=task.context_id,
        agent_session_uid=task.context_id,
    )
    if claim is None:
        claim = await _claim_backend_task(
            client,
            manager,
            task,
            dispatch_uid=dispatch_uid,
        )
    resolved = config or manager.settings
    writer = _DurableArtifactWriter(
        client=client,
        manager=manager,
        task=task,
        claim=claim,
        flush_interval_ms=resolved.a2a_task_output_flush_interval_ms,
        flush_bytes=resolved.a2a_task_output_flush_bytes,
        cancellation_poll_interval_seconds=resolved.a2a_task_event_poll_interval_seconds,
    )
    task_context = TaskExecutionContext(
        task_uid=task.uid,
        task_id=task.task_id,
        context_id=task.context_id,
        attempt_uid=claim.attempt_uid,
        holder_id=claim.holder_id,
        lease_token=claim.lease_token,
    )
    try:
        await writer.ensure_not_canceled(force=True)
        with task_execution_scope(task_context):
            text = await _collect_task_turn(
                manager,
                task,
                prompt,
                output_contract,
                max_output_bytes=max_output_bytes,
                provenance=provenance,
                writer=writer,
            )
        if task_context.interruption_status is not None:
            await writer.ensure_not_canceled(force=True)
            await _settle_claimed_task(
                client,
                manager,
                task,
                claim,
                status=task_context.interruption_status,
                status_message=task_context.interruption_message,
            )
            manager.mark_response_delivered(task.context_id)
            return {
                "taskId": task.task_id,
                "state": task_context.interruption_status,
            }
        message = _agent_message(
            context_id=task.context_id,
            text=text,
            strict_json=output_contract.enabled,
        )
        await writer.ensure_not_canceled(force=True)
        await _settle_claimed_task(
            client,
            manager,
            task,
            claim,
            status="completed",
        )
        manager.mark_response_delivered(task.context_id)
        return message
    except _TaskCancellationRequested as cancellation:
        canceled = cancellation.task
        if canceled.status != "canceled":
            canceled = await _settle_claimed_task(
                client,
                manager,
                task,
                claim,
                status="canceled",
            )
        manager.mark_response_delivered(task.context_id)
        return {"taskId": canceled.task_id, "state": canceled.status}
    except asyncio.CancelledError:
        if not manager.draining:
            with contextlib.suppress(Exception):
                current = await client.get_task(task.uid)
                if current.cancellation_requested and current.status not in TERMINAL:
                    await _settle_claimed_task(
                        client,
                        manager,
                        task,
                        claim,
                        status="canceled",
                    )
        raise
    except (BackendError, LeaseLostError):
        raise
    except Exception as error:
        await _settle_execution_failure(client, manager, task, claim, error)
        raise


async def _schedule_task_accelerator(
    client: MainSequenceClient,
    manager: SessionRuntimeManager,
    task: AgentTask,
    *,
    prompt: str,
    output_contract: StrictJsonContract,
    max_output_bytes: int,
    provenance: TurnProvenance,
    config: TauSDKSettings,
) -> bool:
    """Best-effort local start after durable creation; dispatch remains recovery owner."""

    try:
        claim = await _claim_backend_task(client, manager, task)
    except (BackendError, LeaseLostError, RuntimeError) as error:
        record_failure = getattr(client, "record_a2a_recovery_failure", None)
        if record_failure is not None:
            with contextlib.suppress(Exception):
                await record_failure(
                    task.uid,
                    recovery_owner=str(getattr(manager, "holder_id", "unknown")),
                    error_type=type(error).__name__,
                )
        logger.info(
            "a2a.task.accelerator.deferred",
            task_uid=task.uid,
            task_id=task.task_id,
            error_type=type(error).__name__,
        )
        return False
    execution = _execute_task(
        client,
        manager,
        task,
        prompt=prompt,
        output_contract=output_contract,
        max_output_bytes=max_output_bytes,
        provenance=provenance,
        config=config,
        claim=claim,
    )
    try:
        _background, scheduled = manager.create_a2a_task_execution(
            task.uid,
            execution,
            name=f"a2a-task-{task.task_id}",
        )
    except RuntimeError as error:
        execution.close()
        logger.info(
            "a2a.task.accelerator.deferred",
            task_uid=task.uid,
            task_id=task.task_id,
            error_type=type(error).__name__,
        )
        return False
    return scheduled


async def _persist_local_recovery_contract(
    client: MainSequenceClient,
    task: AgentTask,
    *,
    output_contract: StrictJsonContract,
    provenance: TurnProvenance,
) -> AgentTask:
    update = getattr(client, "update_task_recovery_metadata", None)
    if update is None:
        return task
    return cast(
        AgentTask,
        await update(
            task.uid,
            output_contract={
                "mode": output_contract.mode,
                "schema": output_contract.schema,
                "repair_attempts": output_contract.repair_attempts,
            },
            provenance=dict(provenance),
        ),
    )


def _recovery_execution_contract(
    task: AgentTask,
    config: TauSDKSettings,
) -> tuple[str, StrictJsonContract, TurnProvenance]:
    latest = task.latest_message
    if not isinstance(latest, dict):
        raise ValueError("Persisted Task has no requester Message")
    message = {
        "messageId": str(latest.get("messageId") or ""),
        "contextId": task.context_id,
        "role": A2AProtocolRole.USER.value,
        "parts": latest.get("parts"),
        "metadata": latest.get("metadata") or {},
        "extensions": latest.get("extensions") or [],
        "referenceTaskIds": (latest.get("referenceTaskIds") or []),
    }
    _normalized, prompt = _request_parts({"message": message}, config)
    execution = task.metadata.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    raw_contract = execution.get("output_contract")
    raw_contract = raw_contract if isinstance(raw_contract, dict) else {}
    mode = str(raw_contract.get("mode") or "none")
    if mode not in {"none", "json", "json_object", "json_schema"}:
        raise ValueError("Persisted Task output contract is invalid")
    raw_schema = raw_contract.get("schema")
    output_contract = StrictJsonContract(
        mode=mode,  # type: ignore[arg-type]
        schema=raw_schema if isinstance(raw_schema, dict) else None,
        repair_attempts=int(raw_contract.get("repair_attempts", 3)),
    )
    raw_provenance = execution.get("provenance")
    provenance: TurnProvenance = (
        dict(raw_provenance)
        if isinstance(raw_provenance, dict)
        else {
            "channel": "a2a",
            "origin": "agent",
            "actorKind": "agent",
            "actorUid": f"local-a2a-client-{config.workspace_digest}",
        }
    )
    return prompt, output_contract, provenance


async def _reconcile_local_tasks_once(
    client: MainSequenceClient,
    manager: SessionRuntimeManager,
    config: TauSDKSettings,
) -> None:
    reconcile = getattr(client, "reconcile_a2a_tasks", None)
    if reconcile is None:
        return
    tasks = await reconcile(
        recovery_owner=manager.holder_id,
        stale_after_seconds=config.local_a2a_task_stale_after_seconds,
        pending_timeout_seconds=config.local_a2a_task_pending_timeout_seconds,
        max_recovery_attempts=config.local_a2a_task_max_recovery_attempts,
    )
    for task in tasks:
        try:
            prompt, output_contract, provenance = _recovery_execution_contract(task, config)
            await manager.get(task.context_id)
            scheduled = await _schedule_task_accelerator(
                client,
                manager,
                task,
                prompt=prompt,
                output_contract=output_contract,
                max_output_bytes=config.max_turn_output_bytes,
                provenance=provenance,
                config=config,
            )
            logger.info(
                "a2a.task.recovery.scheduled" if scheduled else "a2a.task.recovery.deferred",
                message=(
                    "Scheduled a durable local Task for recovery"
                    if scheduled
                    else "Deferred local Task recovery"
                ),
                task_uid=task.uid,
                task_id=task.task_id,
                recovery_owner=manager.holder_id,
                outcome="scheduled" if scheduled else "deferred",
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            record_failure = getattr(client, "record_a2a_recovery_failure", None)
            if record_failure is not None:
                with contextlib.suppress(Exception):
                    await record_failure(
                        task.uid,
                        recovery_owner=manager.holder_id,
                        error_type=type(error).__name__,
                    )
            logger.warning(
                "a2a.task.recovery.deferred",
                message="Deferred local Task recovery after a safe startup failure",
                task_uid=task.uid,
                task_id=task.task_id,
                recovery_owner=manager.holder_id,
                error_type=type(error).__name__,
                outcome="deferred",
            )


async def start_local_task_reconciler(
    client: MainSequenceClient,
    manager: SessionRuntimeManager,
    config: TauSDKSettings,
) -> asyncio.Task[None] | None:
    """Run the startup pass and return the local periodic recovery owner."""

    if not config.local_mode or getattr(client, "reconcile_a2a_tasks", None) is None:
        return None
    await _reconcile_local_tasks_once(client, manager, config)

    async def loop() -> None:
        while True:
            await asyncio.sleep(config.local_a2a_task_reconcile_interval_seconds)
            try:
                await _reconcile_local_tasks_once(client, manager, config)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error(
                    "a2a.task.recovery.scan_failed",
                    message="Local Task recovery scan failed and will be retried",
                    recovery_owner=manager.holder_id,
                    error_type=type(error).__name__,
                    outcome="retry_scheduled",
                )

    return asyncio.create_task(loop(), name="ms-tau-local-a2a-reconciler")


async def _execute_message(
    manager: SessionRuntimeManager,
    *,
    context_id: str,
    prompt: str,
    output_contract: StrictJsonContract,
    max_output_bytes: int,
    provenance: TurnProvenance,
) -> dict[str, Any]:
    text = await _collect_validated_turn(
        manager,
        context_id,
        prompt,
        output_contract,
        max_output_bytes=max_output_bytes,
        provenance=provenance,
    )
    message = _agent_message(
        context_id=context_id,
        text=text,
        strict_json=output_contract.enabled,
    )
    manager.mark_response_delivered(context_id)
    return message


def _artifact_update(
    task: AgentTask,
    *,
    artifact_id: str,
    part: dict[str, Any],
    append: bool,
    last_chunk: bool,
) -> dict[str, Any]:
    return {
        "artifactUpdate": {
            "taskId": task.task_id,
            "contextId": task.context_id,
            "artifact": {
                "artifactId": artifact_id,
                "parts": [part],
            },
            "append": append,
            "lastChunk": last_chunk,
        }
    }


async def _stream_task_events(
    client: MainSequenceClient,
    manager: SessionRuntimeManager,
    task: AgentTask,
    *,
    prompt: str,
    output_contract: StrictJsonContract,
    max_output_bytes: int,
    provenance: TurnProvenance,
    config: TauSDKSettings | None = None,
    history_length: int = DEFAULT_TASK_HISTORY_LENGTH,
) -> AsyncIterator[dict[str, Any]]:
    claim = await _claim_backend_task(client, manager, task)
    resolved = config or manager.settings
    writer = _DurableArtifactWriter(
        client=client,
        manager=manager,
        task=task,
        claim=claim,
        flush_interval_ms=resolved.a2a_task_output_flush_interval_ms,
        flush_bytes=resolved.a2a_task_output_flush_bytes,
        cancellation_poll_interval_seconds=resolved.a2a_task_event_poll_interval_seconds,
    )
    task_context = TaskExecutionContext(
        task_uid=task.uid,
        task_id=task.task_id,
        context_id=task.context_id,
        attempt_uid=claim.attempt_uid,
        holder_id=claim.holder_id,
        lease_token=claim.lease_token,
    )
    working = await client.get_task(task.uid)
    working = await _task_with_history(client, working, history_length=history_length)
    yield {"task": _task_payload(working, history_length=history_length)}
    artifact_id = writer.artifact_id
    text = ""
    emitted = False
    try:
        await writer.ensure_not_canceled(force=True)
        if output_contract.enabled:
            with task_execution_scope(task_context):
                text = await _collect_validated_turn(
                    manager,
                    task.context_id,
                    prompt,
                    output_contract,
                    max_output_bytes=max_output_bytes,
                    provenance=provenance,
                    turn_uid=claim.turn_uid,
                )
            if text and task_context.interruption_status is None:
                await writer.write_strict_json(text)
                yield _artifact_update(
                    task,
                    artifact_id=artifact_id,
                    part={
                        "data": json.loads(text),
                        "mediaType": "application/json",
                    },
                    append=False,
                    last_chunk=True,
                )
                emitted = True
        else:
            accumulator = _TurnAccumulator(max_output_bytes=max_output_bytes)
            with task_execution_scope(task_context):
                async for event in manager.prompt(
                    task.context_id,
                    prompt,
                    provenance=provenance,
                    turn_uid=claim.turn_uid,
                ):
                    await writer.ensure_not_canceled()
                    accumulator.consume(event)
                    if event.type not in {"text_delta", "text-delta"}:
                        continue
                    value = _event_text(
                        event.type,
                        event.data,
                        has_chunks=bool(accumulator.delta_chunks[:-1]),
                    )
                    if not value:
                        continue
                    await writer.append_text(value)
                    yield _artifact_update(
                        task,
                        artifact_id=artifact_id,
                        part={"text": value},
                        append=emitted,
                        last_chunk=False,
                    )
                    emitted = True
            if task_context.interruption_status is None:
                text = accumulator.result()
                if not accumulator.delta_chunks:
                    await writer.append_text(text)
                    yield _artifact_update(
                        task,
                        artifact_id=artifact_id,
                        part={"text": text},
                        append=False,
                        last_chunk=False,
                    )
                    emitted = True
        if task_context.interruption_status is not None:
            await writer.flush()
            await writer.ensure_not_canceled(force=True)
            interrupted = await _settle_claimed_task(
                client,
                manager,
                task,
                claim,
                status=task_context.interruption_status,
                status_message=task_context.interruption_message,
            )
            interrupted = await _task_with_history(
                client, interrupted, history_length=history_length
            )
            yield {"task": _task_payload(interrupted, history_length=history_length)}
            return
        if not output_contract.enabled:
            await writer.flush(final=True)
        await writer.ensure_not_canceled(force=True)
        completed = await _settle_claimed_task(
            client,
            manager,
            task,
            claim,
            status="completed",
        )
        if emitted and not output_contract.enabled:
            yield _artifact_update(
                task,
                artifact_id=artifact_id,
                part={"text": ""},
                append=True,
                last_chunk=True,
            )
        completed = await _task_with_history(client, completed, history_length=history_length)
        yield {
            "task": _task_payload(completed, history_length=history_length),
            "final": True,
        }
    except _TaskCancellationRequested as cancellation:
        canceled = cancellation.task
        if canceled.status != "canceled":
            canceled = await _settle_claimed_task(
                client,
                manager,
                task,
                claim,
                status="canceled",
            )
        canceled = await _task_with_history(client, canceled, history_length=history_length)
        yield {
            "task": _task_payload(canceled, history_length=history_length),
            "final": True,
        }
    except asyncio.CancelledError:
        if not manager.draining:
            with contextlib.suppress(Exception):
                current = await client.get_task(task.uid)
                if current.cancellation_requested and current.status not in TERMINAL:
                    await _settle_claimed_task(
                        client,
                        manager,
                        task,
                        claim,
                        status="canceled",
                    )
        raise
    except (BackendError, LeaseLostError):
        raise
    except Exception as error:
        failed = await _settle_execution_failure(client, manager, task, claim, error)
        failed = await _task_with_history(client, failed, history_length=history_length)
        yield _status_update(failed, history_length=history_length)


def _stream_payload(
    payload: dict[str, Any],
    *,
    json_rpc: bool,
    request_id: object,
) -> dict[str, Any]:
    if not json_rpc:
        return payload
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": payload,
    }


def _message_stream_response(
    *,
    client: MainSequenceClient,
    manager: SessionRuntimeManager,
    task: AgentTask,
    prompt: str,
    output_contract: StrictJsonContract,
    max_output_bytes: int,
    provenance: TurnProvenance,
    config: TauSDKSettings | None = None,
    history_length: int = DEFAULT_TASK_HISTORY_LENGTH,
    json_rpc: bool = False,
    request_id: object = None,
) -> StreamingResponse:
    async def stream() -> AsyncIterator[bytes]:
        try:
            async for payload in _stream_task_events(
                client,
                manager,
                task,
                prompt=prompt,
                output_contract=output_contract,
                max_output_bytes=max_output_bytes,
                provenance=provenance,
                config=config,
                history_length=history_length,
            ):
                yield _sse(
                    _stream_payload(
                        payload,
                        json_rpc=json_rpc,
                        request_id=request_id,
                    )
                )
            manager.mark_response_delivered(task.context_id)
        except asyncio.CancelledError:
            await manager.cancel(task.context_id)
            if not manager.draining:
                with contextlib.suppress(Exception):
                    await manager.evict(task.context_id)
            raise
        except Exception as error:
            detail = getattr(error, "detail", None)
            safe_detail = dict(detail) if isinstance(detail, dict) else {}
            payload = {
                "error": {
                    "code": "task_terminalization_unknown",
                    "message": (
                        "Task execution stopped before its terminal state could be persisted"
                    ),
                    "detail": {
                        "taskId": task.task_id,
                        "correlationId": safe_detail.get("correlationId", task.task_id),
                        "retryable": True,
                    },
                },
                "final": True,
            }
            yield _sse(
                _stream_payload(
                    payload,
                    json_rpc=json_rpc,
                    request_id=request_id,
                )
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _existing_task_stream_response(
    *,
    client: MainSequenceClient,
    task: AgentTask,
    history_length: int = DEFAULT_TASK_HISTORY_LENGTH,
    json_rpc: bool = False,
    request_id: object = None,
) -> StreamingResponse:
    async def stream() -> AsyncIterator[bytes]:
        current = task
        previous: tuple[str, str] | None = None
        while True:
            marker = (current.status, str(current.status_timestamp))
            if marker != previous:
                current = await _task_with_history(client, current, history_length=history_length)
                payload: dict[str, Any] = {
                    "task": _task_payload(current, history_length=history_length)
                }
                if current.status in TERMINAL:
                    payload["final"] = True
                yield _sse(
                    _stream_payload(
                        payload,
                        json_rpc=json_rpc,
                        request_id=request_id,
                    )
                )
                previous = marker
            if current.status in TERMINAL:
                break
            await asyncio.sleep(1)
            current = await client.get_task_by_protocol_id(current.task_id)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _status_update(
    task: AgentTask,
    *,
    history_length: int | None = None,
) -> dict[str, Any]:
    payload = _task_payload(task, history_length=history_length)
    return {
        "statusUpdate": {
            "taskId": task.task_id,
            "contextId": task.context_id,
            "status": payload["status"],
            "final": task.status in TERMINAL,
        }
    }


def _artifact_update_from_event(task: AgentTask, payload: dict[str, Any]) -> dict[str, Any]:
    artifact_id = str(payload.get("artifact_id") or payload.get("artifactId") or "")
    return {
        "artifactUpdate": {
            "taskId": task.task_id,
            "contextId": task.context_id,
            "artifact": {
                "artifactId": artifact_id,
                "name": str(payload.get("name") or ""),
                "parts": payload.get("parts") or [],
                "metadata": payload.get("metadata") or {},
            },
            "append": bool(payload.get("append", False)),
            "lastChunk": bool(payload.get("lastChunk", payload.get("last_chunk", False))),
        }
    }


async def _task_subscription_events(
    *,
    client: MainSequenceClient,
    snapshot: AgentTaskSnapshot,
    poll_interval_seconds: float,
    after_sequence: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    task = snapshot.task
    cursor = snapshot.event_cursor if after_sequence is None else max(0, after_sequence)
    yield {"task": _task_payload(task), "eventCursor": cursor}
    while True:
        page = await client.list_task_events(
            task.uid,
            after_sequence=cursor,
            limit=100,
        )
        for event in page.events:
            if event.event_type in {"output_added", "output_updated"}:
                payload = _artifact_update_from_event(task, event.payload)
            elif event.event_type == "status_changed":
                task = (await client.get_task_snapshot(task.uid)).task
                payload = _status_update(task)
            else:
                cursor = event.sequence
                continue
            payload["eventCursor"] = event.sequence
            yield payload
            cursor = event.sequence
            if task.status in TERMINAL:
                return
        if page.events:
            cursor = max(cursor, page.next_cursor)
        if page.has_more:
            continue
        latest = await client.get_task_snapshot(task.uid)
        task = latest.task
        if task.status in TERMINAL:
            payload = _status_update(task)
            payload["eventCursor"] = latest.event_cursor
            yield payload
            return
        await asyncio.sleep(poll_interval_seconds)


def _task_subscription_response(
    *,
    client: MainSequenceClient,
    snapshot: AgentTaskSnapshot,
    poll_interval_seconds: float,
    after_sequence: int | None = None,
    json_rpc: bool = False,
    request_id: object = None,
) -> StreamingResponse:
    async def stream() -> AsyncIterator[bytes]:
        async for payload in _task_subscription_events(
            client=client,
            snapshot=snapshot,
            poll_interval_seconds=poll_interval_seconds,
            after_sequence=after_sequence,
        ):
            yield _sse(
                _stream_payload(
                    payload,
                    json_rpc=json_rpc,
                    request_id=request_id,
                )
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _task_id(body: dict[str, Any]) -> str:
    value = body.get("taskId")
    return str(value).strip() if value else str(uuid.uuid4())


def _durable_task_execution_input(
    task: AgentTask,
    config: TauSDKSettings,
) -> tuple[str, StrictJsonContract, TurnProvenance]:
    message = task.latest_message or {}
    if message.get("role") != A2AMessageDirection.REQUESTER.value or not isinstance(
        message.get("parts"), list
    ):
        raise HTTPException(
            status_code=409,
            detail="Durable Task dispatch has no executable requester Message",
        )
    body = {
        "message": {
            "messageId": str(message.get("messageId") or ""),
            "role": A2AProtocolRole.USER.value,
            "contextId": task.context_id,
            "parts": message["parts"],
            "metadata": message.get("metadata") or {},
            "extensions": message.get("extensions") or [],
        }
    }
    _normalized, prompt = _request_parts(body, config)
    execution = task.metadata.get("execution", {})
    raw_contract = execution.get("output_contract", {}) if isinstance(execution, dict) else {}
    mode = str(raw_contract.get("mode") or "none")
    if mode not in {"none", "json", "json_object", "json_schema"}:
        raise HTTPException(status_code=409, detail="Durable Task output contract is invalid")
    contract = StrictJsonContract(
        mode=mode,  # type: ignore[arg-type]
        schema=(
            raw_contract.get("schema") if isinstance(raw_contract.get("schema"), dict) else None
        ),
        repair_attempts=max(0, min(int(raw_contract.get("repair_attempts", 3)), 10)),
    )
    raw_provenance = execution.get("provenance", {}) if isinstance(execution, dict) else {}
    provenance: TurnProvenance = {
        str(key): str(value)
        for key, value in raw_provenance.items()
        if isinstance(key, str) and isinstance(value, str)
    }
    if not provenance:
        provenance = {"channel": "a2a", "origin": "agent", "actorKind": "agent"}
    return prompt, contract, provenance


@router.post("/internal/a2a/task-dispatch", status_code=200)
async def task_dispatch_available(
    body: dict[str, Any],
    client: BackendDep,
    manager: RuntimeManagerDep,
    config: SettingsDep,
) -> dict[str, Any]:
    """Handle the bounded post-wake hint; the backend claim is the authority."""

    task_uid = str(body.get("task_uid") or "").strip()
    dispatch_uid = str(body.get("dispatch_uid") or "").strip()
    if not task_uid or not dispatch_uid:
        raise HTTPException(status_code=400, detail="task_uid and dispatch_uid are required")
    task = await client.get_task(task_uid)
    if task.status != "submitted":
        return {"accepted": True, "scheduled": False, "task_uid": task.uid}
    prompt, output_contract, provenance = _durable_task_execution_input(task, config)
    claim = await _claim_backend_task(
        client,
        manager,
        task,
        dispatch_uid=dispatch_uid,
    )
    _execution, scheduled = manager.create_a2a_task_execution(
        task.uid,
        _execute_task(
            client,
            manager,
            task,
            prompt=prompt,
            output_contract=output_contract,
            max_output_bytes=config.max_turn_output_bytes,
            provenance=provenance,
            config=config,
            claim=claim,
        ),
        name=f"a2a-task-{task.task_id}",
    )
    return {"accepted": True, "scheduled": scheduled, "task_uid": task.uid}


async def _resume_caller_delivery(
    *,
    manager: SessionRuntimeManager,
    task_uid: str,
    task_id: str,
    task_status: str,
    caller_agent_session_uid: str,
) -> None:
    prompt = (
        "A delegated asynchronous A2A Task has new actionable state. "
        f"Task {task_id} ({task_uid}) is {task_status}. "
        "Use the Main Sequence A2A get/wait tools with that Task UID, then continue "
        "the original work."
    )
    async for _event in manager.prompt(
        caller_agent_session_uid,
        prompt,
        provenance={
            "channel": "a2a",
            "origin": "agent",
            "actorKind": "platform",
            "actorUid": "mainsequence",
        },
    ):
        pass
    manager.mark_response_delivered(caller_agent_session_uid)


@router.post("/internal/a2a/task-caller-delivery", status_code=200)
async def task_caller_delivery_available(
    body: dict[str, Any],
    manager: RuntimeManagerDep,
) -> dict[str, Any]:
    delivery_uid = str(body.get("delivery_uid") or "").strip()
    task_uid = str(body.get("task_uid") or "").strip()
    task_id = str(body.get("task_id") or "").strip()
    task_status = str(body.get("status") or "").strip()
    caller_agent_session_uid = str(body.get("caller_agent_session_uid") or "").strip()
    event_cursor = body.get("event_cursor")
    if not all((delivery_uid, task_uid, task_id, task_status, caller_agent_session_uid)):
        raise HTTPException(status_code=400, detail="Caller-delivery signal is incomplete")
    if not isinstance(event_cursor, int) or isinstance(event_cursor, bool) or event_cursor < 1:
        raise HTTPException(status_code=400, detail="event_cursor must be a positive integer")
    if manager.session_turn_active(caller_agent_session_uid):
        raise HTTPException(
            status_code=409,
            detail="Caller AgentSession already has an active turn",
        )
    event_payload: dict[str, JSONValue] = {
        "deliveryUid": delivery_uid,
        "taskUid": task_uid,
        "taskId": task_id,
        "state": task_status,
        "eventCursor": event_cursor,
    }
    try:
        persisted = await manager.persist_platform_event(
            caller_agent_session_uid,
            ("io.mainsequence.a2a.task-delivery/v1", event_payload),
            idempotency_key=delivery_uid,
        )
    except BackendError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not persisted:
        return {
            "accepted": True,
            "event_persisted": False,
            "scheduled": False,
            "delivery_uid": delivery_uid,
        }
    _execution, scheduled = manager.create_a2a_caller_delivery(
        delivery_uid,
        _resume_caller_delivery(
            manager=manager,
            task_uid=task_uid,
            task_id=task_id,
            task_status=task_status,
            caller_agent_session_uid=caller_agent_session_uid,
        ),
        name=f"a2a-caller-delivery-{delivery_uid}",
    )
    return {
        "accepted": True,
        "event_persisted": persisted,
        "scheduled": scheduled,
        "delivery_uid": delivery_uid,
    }


@router.post(f"{REST_BASE}/message:send")
async def message_send(
    body: dict[str, Any],
    client: BackendDep,
    manager: RuntimeManagerDep,
    config: SettingsDep,
    request: Request,
    a2a_extensions: Annotated[
        str | None,
        Header(alias="A2A-Extensions"),
    ] = None,
) -> dict[str, Any]:
    try:
        provenance = _a2a_turn_provenance(config, request)
    except CallerIdentityError as error:
        return _caller_identity_rejection(request, error)  # type: ignore[return-value]
    message, prompt = _request_parts(body, config)
    history_length = _body_history_length(body)
    _bind_a2a_context(
        request,
        method="message/send",
        message=message,
        task_id=body.get("taskId"),
    )
    output_contract = _output_contract(body)
    response_kind, was_explicit, return_immediately = _response_kind(
        body,
        a2a_extensions=a2a_extensions,
    )
    if was_explicit:
        await _require_advertised_response_kind(
            client,
            context_id=str(message["contextId"]),
            response_kind=response_kind,
        )
    continuation_task_id = str(message.get("taskId") or "").strip()
    if config.local_mode and (continuation_task_id or response_kind is ResponseKind.TASK):
        await manager.get(str(message["contextId"]))
    if continuation_task_id:
        if response_kind is not ResponseKind.TASK:
            raise HTTPException(
                status_code=400,
                detail="A Task continuation requires configuration.responseKind 'task'",
            )
        existing_task = await client.get_task_by_protocol_id(continuation_task_id)
        continued_message = _task_message_for_backend(
            message,
            context_id=existing_task.context_id,
            task_id=existing_task.task_id,
            local_mode=config.local_mode,
        )
        task = await client.continue_task(existing_task.uid, continued_message)
        if config.local_mode:
            task = await _persist_local_recovery_contract(
                client,
                task,
                output_contract=output_contract,
                provenance=provenance,
            )
        await _schedule_task_accelerator(
            client,
            manager,
            task,
            prompt=prompt,
            output_contract=output_contract,
            max_output_bytes=config.max_turn_output_bytes,
            provenance=provenance,
            config=config,
        )
        if return_immediately:
            task = await _task_with_history(client, task, history_length=history_length)
            return {"task": _task_payload(task, history_length=history_length)}
        task = await _wait_for_task_return_state(
            client,
            task,
            poll_interval_seconds=config.a2a_task_event_poll_interval_seconds,
            wait_timeout_seconds=config.a2a_task_wait_timeout_seconds,
        )
        task = await _task_with_history(client, task, history_length=history_length)
        return {"task": _task_payload(task, history_length=history_length)}
    if response_kind is ResponseKind.TASK:
        creation = await _create_backend_task(
            client,
            message=message,
            task_id=_task_id(body),
            local_mode=config.local_mode,
            output_contract=output_contract,
            provenance=provenance,
        )
        task = creation.task
        if creation.created:
            await _schedule_task_accelerator(
                client,
                manager,
                task,
                prompt=prompt,
                output_contract=output_contract,
                max_output_bytes=config.max_turn_output_bytes,
                provenance=provenance,
                config=config,
            )
            if return_immediately:
                task = await _task_with_history(client, task, history_length=history_length)
                return {"task": _task_payload(task, history_length=history_length)}
            task = await _wait_for_task_return_state(
                client,
                task,
                poll_interval_seconds=config.a2a_task_event_poll_interval_seconds,
                wait_timeout_seconds=config.a2a_task_wait_timeout_seconds,
            )
            task = await _task_with_history(client, task, history_length=history_length)
            return {"task": _task_payload(task, history_length=history_length)}
        if config.local_mode and task.status == "submitted":
            recovered_prompt, recovered_contract, recovered_provenance = (
                _recovery_execution_contract(task, config)
            )
            await _schedule_task_accelerator(
                client,
                manager,
                task,
                prompt=recovered_prompt,
                output_contract=recovered_contract,
                max_output_bytes=config.max_turn_output_bytes,
                provenance=recovered_provenance,
                config=config,
            )
        if not return_immediately and task.status not in TASK_RETURN_STATES:
            task = await _wait_for_task_return_state(
                client,
                task,
                poll_interval_seconds=config.a2a_task_event_poll_interval_seconds,
                wait_timeout_seconds=config.a2a_task_wait_timeout_seconds,
            )
        task = await _task_with_history(client, task, history_length=history_length)
        return {"task": _task_payload(task, history_length=history_length)}
    result = await _execute_message(
        manager,
        context_id=str(message["contextId"]),
        prompt=prompt,
        output_contract=output_contract,
        max_output_bytes=config.max_turn_output_bytes,
        provenance=provenance,
    )
    return {"message": result}


@router.post(f"{REST_BASE}/message:stream")
async def message_stream(
    body: dict[str, Any],
    client: BackendDep,
    manager: RuntimeManagerDep,
    config: SettingsDep,
    request: Request,
    a2a_extensions: Annotated[
        str | None,
        Header(alias="A2A-Extensions"),
    ] = None,
) -> StreamingResponse:
    try:
        provenance = _a2a_turn_provenance(config, request)
    except CallerIdentityError as error:
        return _caller_identity_rejection(request, error)  # type: ignore[return-value]
    _response_kind(
        body,
        a2a_extensions=a2a_extensions,
        streaming=True,
    )
    message, prompt = _request_parts(body, config)
    history_length = _body_history_length(body)
    _bind_a2a_context(
        request,
        method="message/stream",
        message=message,
        task_id=body.get("taskId"),
        streaming=True,
    )
    output_contract = _output_contract(body)
    if config.local_mode:
        await manager.get(str(message["contextId"]))
    creation = await _create_backend_task(
        client,
        message=message,
        task_id=_task_id(body),
        local_mode=config.local_mode,
        output_contract=output_contract,
        provenance=provenance,
    )
    task = creation.task
    if not creation.created:
        if config.local_mode and task.status == "submitted":
            recovered_prompt, recovered_contract, recovered_provenance = (
                _recovery_execution_contract(task, config)
            )
            await _schedule_task_accelerator(
                client,
                manager,
                task,
                prompt=recovered_prompt,
                output_contract=recovered_contract,
                max_output_bytes=config.max_turn_output_bytes,
                provenance=recovered_provenance,
                config=config,
            )
        return _existing_task_stream_response(
            client=client,
            task=task,
            history_length=history_length,
        )
    return _message_stream_response(
        client=client,
        manager=manager,
        task=task,
        prompt=prompt,
        output_contract=output_contract,
        max_output_bytes=config.max_turn_output_bytes,
        provenance=provenance,
        config=config,
        history_length=history_length,
    )


@router.get(f"{REST_BASE}/tasks")
async def list_tasks(
    client: BackendDep,
    contextId: str | None = None,
    historyLength: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    history_length = _history_length(historyLength)
    tasks = await client.list_tasks(
        context_id=contextId or "",
        history_length=history_length,
    )
    return {"tasks": [_task_payload(task, history_length=history_length) for task in tasks]}


@router.post(f"{REST_BASE}/tasks/{{task_id}}:cancel")
async def cancel_task(
    task_id: str,
    client: BackendDep,
    manager: RuntimeManagerDep,
) -> dict[str, Any]:
    task = await client.get_task_by_protocol_id(task_id)
    await manager.cancel(task.context_id)
    return {"task": _task_payload(await client.cancel_task(task.uid))}


@router.get(f"{REST_BASE}/tasks/{{task_id}}:subscribe", response_model=None)
async def subscribe_task(
    task_id: str,
    client: BackendDep,
    config: SettingsDep,
    after_sequence: int | None = Query(default=None, alias="afterSequence", ge=0),
) -> StreamingResponse | JSONResponse:
    snapshot = await client.get_task_snapshot_by_protocol_id(task_id)
    if snapshot.task.status in TERMINAL:
        return _unsupported_operation_rest_error(
            "SubscribeToTask is unavailable for a terminal Task; use GetTask."
        )
    return _task_subscription_response(
        client=client,
        snapshot=snapshot,
        poll_interval_seconds=config.a2a_task_event_poll_interval_seconds,
        after_sequence=after_sequence,
    )


@router.get(f"{REST_BASE}/tasks/{{task_id}}")
async def get_task(
    task_id: str,
    client: BackendDep,
    historyLength: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    history_length = _history_length(historyLength)
    task = await client.get_task_by_protocol_id(task_id, history_length=history_length)
    return {"task": _task_payload(task, history_length=history_length)}


@router.get(f"{REST_BASE}/tasks/{{task_id}}/pushNotificationConfigs")
async def list_push_configs(task_id: str) -> JSONResponse:
    return _push_notification_rest_error()


@router.post(f"{REST_BASE}/tasks/{{task_id}}/pushNotificationConfigs")
async def set_push_config(task_id: str) -> JSONResponse:
    return _push_notification_rest_error()


@router.get(f"{REST_BASE}/tasks/{{task_id}}/pushNotificationConfigs/{{config_id}}")
async def get_push_config(
    task_id: str,
    config_id: str,
) -> JSONResponse:
    return _push_notification_rest_error()


@router.delete(f"{REST_BASE}/tasks/{{task_id}}/pushNotificationConfigs/{{config_id}}")
async def delete_push_config(
    task_id: str,
    config_id: str,
) -> JSONResponse:
    return _push_notification_rest_error()


@router.get(f"{REST_BASE}/extendedAgentCard")
async def extended_agent_card(
    client: BackendDep,
    agent_session_uid: str | None = Query(default=None),
    session_uid: str | None = Query(default=None),
    context_id: str | None = Query(default=None, alias="contextId"),
) -> dict[str, Any]:
    resolved_uid = agent_session_uid or session_uid or context_id
    if not resolved_uid:
        raise HTTPException(status_code=400, detail="agent_session_uid is required")
    envelope = await client.get_agent_card(resolved_uid)
    payload = envelope.model_dump(mode="json")
    payload["agent_card"] = _effective_agent_card(envelope.agent_card)
    return payload


@router.post("/api/a2a/rpc", response_model=None)
async def json_rpc(
    body: dict[str, Any],
    client: BackendDep,
    manager: RuntimeManagerDep,
    config: SettingsDep,
    request: Request,
    a2a_extensions: Annotated[
        str | None,
        Header(alias="A2A-Extensions"),
    ] = None,
) -> dict[str, Any] | StreamingResponse:
    request_id = body.get("id")
    method = str(body.get("method") or "")
    _bind_a2a_context(
        request,
        method=method or "invalid",
        request_id=request_id,
        streaming=method
        in {
            "SendStreamingMessage",
            "message/stream",
            "SubscribeToTask",
            "tasks/subscribe",
        },
    )
    if body.get("jsonrpc") != "2.0":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    if method in PUSH_NOTIFICATION_RPC_METHODS:
        return _push_notification_json_rpc_error(request_id)
    params = body.get("params", {})
    if not isinstance(params, dict):
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": "params must be an object"},
        }

    def task_id() -> str:
        value = str(params.get("id") or params.get("taskId") or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="task id is required")
        return value

    provenance: TurnProvenance | None = None
    if method in PROTECTED_MESSAGE_RPC_METHODS:
        try:
            provenance = _a2a_turn_provenance(config, request)
        except CallerIdentityError as error:
            return _caller_identity_json_rpc_error(request, request_id, error)

    try:
        if method in {"SendMessage", "message/send"}:
            result = await message_send(
                params,
                client,
                manager,
                config,
                request,
                a2a_extensions,
            )
        elif method in {"SendStreamingMessage", "message/stream"}:
            _response_kind(
                params,
                a2a_extensions=a2a_extensions,
                streaming=True,
            )
            message, prompt = _request_parts(params, config)
            history_length = _body_history_length(params)
            _bind_a2a_context(
                request,
                method=method,
                request_id=request_id,
                message=message,
                task_id=params.get("taskId") or params.get("id"),
                streaming=True,
            )
            output_contract = _output_contract(params)
            if config.local_mode:
                await manager.get(str(message["contextId"]))
            creation = await _create_backend_task(
                client,
                message=message,
                task_id=_task_id(params),
                local_mode=config.local_mode,
                output_contract=output_contract,
                provenance=provenance,
            )
            task = creation.task
            if not creation.created:
                if config.local_mode and task.status == "submitted":
                    recovered_prompt, recovered_contract, recovered_provenance = (
                        _recovery_execution_contract(task, config)
                    )
                    await _schedule_task_accelerator(
                        client,
                        manager,
                        task,
                        prompt=recovered_prompt,
                        output_contract=recovered_contract,
                        max_output_bytes=config.max_turn_output_bytes,
                        provenance=recovered_provenance,
                        config=config,
                    )
                return _existing_task_stream_response(
                    client=client,
                    task=task,
                    history_length=history_length,
                    json_rpc=True,
                    request_id=request_id,
                )
            assert provenance is not None
            return _message_stream_response(
                client=client,
                manager=manager,
                task=task,
                prompt=prompt,
                output_contract=output_contract,
                max_output_bytes=config.max_turn_output_bytes,
                provenance=provenance,
                config=config,
                history_length=history_length,
                json_rpc=True,
                request_id=request_id,
            )
        elif method in {"GetTask", "tasks/get"}:
            result = await get_task(
                task_id(),
                client,
                _history_length(params.get("historyLength")),
            )
        elif method in {"ListTasks", "tasks/list"}:
            context_id = str(params.get("contextId") or "")
            result = await list_tasks(
                client,
                contextId=context_id,
                historyLength=_history_length(params.get("historyLength")),
            )
        elif method in {"CancelTask", "tasks/cancel"}:
            result = await cancel_task(
                task_id(),
                client,
                manager,
            )
        elif method in {"SubscribeToTask", "tasks/subscribe"}:
            snapshot = await client.get_task_snapshot_by_protocol_id(task_id())
            if snapshot.task.status in TERMINAL:
                return _unsupported_operation_json_rpc_error(
                    request_id,
                    "SubscribeToTask is unavailable for a terminal Task; use GetTask.",
                )
            after_sequence_value = params.get("afterSequence")
            if after_sequence_value is not None:
                try:
                    after_sequence_value = max(0, int(after_sequence_value))
                except (TypeError, ValueError) as error:
                    raise HTTPException(
                        status_code=400,
                        detail="afterSequence must be a non-negative integer",
                    ) from error
            return _task_subscription_response(
                client=client,
                snapshot=snapshot,
                poll_interval_seconds=config.a2a_task_event_poll_interval_seconds,
                after_sequence=after_sequence_value,
                json_rpc=True,
                request_id=request_id,
            )
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except HTTPException as error:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": str(error.detail)},
        }
