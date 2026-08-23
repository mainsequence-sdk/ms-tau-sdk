"""Standard A2A REST, JSON-RPC, and SSE transport over Tau sessions."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from astro.backend.client import MainSequenceClient
from astro.backend.models import AgentTask
from astro.protocols.strict_json import (
    StrictJsonContract,
    StrictJsonError,
    build_repair_prompt,
    build_strict_json_contract,
    validate_strict_json,
)
from astro.runtime.manager import SessionRuntimeManager
from astro.settings import Settings

from .dependencies import backend, runtime_manager, settings

router = APIRouter()
BackendDep = Annotated[MainSequenceClient, Depends(backend)]
RuntimeManagerDep = Annotated[SessionRuntimeManager, Depends(runtime_manager)]
SettingsDep = Annotated[Settings, Depends(settings)]
logger = structlog.get_logger(__name__)

REST_BASE = "/api/a2a/v1"
TERMINAL = {"completed", "failed", "canceled", "rejected"}
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
RESPONSE_KIND_EXTENSION_URI = (
    "https://mainsequence.ai/a2a/extensions/response-kind/v1"
)
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


class ResponseKind(StrEnum):
    MESSAGE = "message"
    TASK = "task"


def _activated_extensions(value: str | None) -> set[str]:
    return {
        extension.strip()
        for extension in str(value or "").split(",")
        if extension.strip()
    }


def _response_kind(
    body: dict[str, Any],
    *,
    a2a_extensions: str | None,
    streaming: bool = False,
) -> tuple[ResponseKind, bool]:
    configuration = body.get("configuration", {})
    if not isinstance(configuration, dict):
        raise HTTPException(status_code=400, detail="configuration must be an object")
    if "returnImmediately" in configuration:
        raise HTTPException(
            status_code=400,
            detail=(
                "configuration.returnImmediately is not supported; use "
                "configuration.responseKind with 'message' or 'task'"
            ),
        )
    if "responseKind" not in configuration:
        return ResponseKind.MESSAGE, False
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
                "configuration.responseKind requires A2A-Extensions: "
                f"{RESPONSE_KIND_EXTENSION_URI}"
            ),
        )
    return response_kind, True


def _response_kind_extension(response_kinds: list[str]) -> dict[str, Any]:
    return {
        "uri": RESPONSE_KIND_EXTENSION_URI,
        "description": (
            "Select whether message:send returns a completed message or an "
            "asynchronous task."
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
            if isinstance(extension, dict)
            and extension.get("uri") != RESPONSE_KIND_EXTENSION_URI
        ]
        if isinstance(extensions, list)
        else []
    )
    preserved.append(_response_kind_extension(_supported_response_kinds(agent_card)))
    capabilities["extensions"] = preserved
    normalized["capabilities"] = capabilities
    return normalized


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
                f"responseKind '{response_kind.value}' is not advertised by the "
                "receiving agent"
            ),
        )


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


def _message_text(parts: object) -> str:
    if not isinstance(parts, list):
        raise HTTPException(status_code=400, detail="message.parts must be an array")
    text = "\n".join(
        str(part["text"])
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ).strip()
    return text


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-") or "item"


def _materialize_pdfs(
    *,
    parts: object,
    context_id: str,
    message_id: str,
    config: Settings,
) -> list[Path]:
    if not isinstance(parts, list):
        raise HTTPException(status_code=400, detail="message.parts must be an array")
    files: list[Path] = []
    for index, part in enumerate(parts):
        if not isinstance(part, dict) or "text" in part or "data" in part:
            continue
        if "url" in part:
            raise HTTPException(
                status_code=400,
                detail=f"message.parts[{index}].url is not supported",
            )
        if "raw" not in part:
            raise HTTPException(
                status_code=400,
                detail=f"message.parts[{index}] must use standard Part.raw",
            )
        if part.get("mediaType") != "application/pdf":
            raise HTTPException(
                status_code=400,
                detail=f"message.parts[{index}].mediaType must be application/pdf",
            )
        filename = str(part.get("filename") or "")
        if not filename or Path(filename).name != filename or not filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=400,
                detail=f"message.parts[{index}].filename must be a safe PDF filename",
            )
        try:
            raw = base64.b64decode(str(part["raw"]), validate=True)
        except (ValueError, binascii.Error) as error:
            raise HTTPException(
                status_code=400,
                detail=f"message.parts[{index}].raw is not valid base64",
            ) from error
        if len(raw) > config.a2a_max_inline_file_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"message.parts[{index}].raw exceeds the inline file limit",
            )
        if not raw.startswith(b"%PDF-"):
            raise HTTPException(
                status_code=400,
                detail=f"message.parts[{index}].raw is not a PDF",
            )
        digest = hashlib.sha256(raw).hexdigest()[:12]
        directory = (
            config.a2a_asset_root
            / _safe_component(context_id)
            / "a2a-inputs"
            / _safe_component(message_id)
        )
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / f"{index}-{digest}-{_safe_component(filename)}"
        path.write_bytes(raw)
        os.chmod(path, 0o600)
        files.append(path)
    return files


def _request_parts(body: dict[str, Any], config: Settings) -> tuple[dict[str, Any], str]:
    message = body.get("message")
    if not isinstance(message, dict):
        raise HTTPException(status_code=400, detail="message must be an object")
    context_id = str(message.get("contextId") or "").strip()
    message_id = str(message.get("messageId") or "").strip()
    if not context_id or not message_id:
        raise HTTPException(
            status_code=400,
            detail="message.contextId and message.messageId are required",
        )
    parts = message.get("parts")
    text = _message_text(parts)
    files = _materialize_pdfs(
        parts=parts,
        context_id=context_id,
        message_id=message_id,
        config=config,
    )
    if not text and not files:
        raise HTTPException(status_code=400, detail="message.parts has no usable content")
    if files:
        attachment_lines = "\n".join(f"- {path}" for path in files)
        text = (
            f"{text}\n\nAttached PDF files:\n{attachment_lines}"
            if text
            else f"Review the attached PDF files:\n{attachment_lines}"
        )
    return message, text


def _output_contract(body: dict[str, Any]) -> StrictJsonContract:
    configuration = body.get("configuration", {})
    modes = configuration.get("acceptedOutputModes", []) if isinstance(configuration, dict) else []
    metadata = body.get("metadata", {})
    extension = (
        metadata.get("https://mainsequence.ai/a2a/extensions/output-contract/v1", {})
        if isinstance(metadata, dict)
        else {}
    )
    strict = bool(extension.get("strict")) if isinstance(extension, dict) else False
    response_format = body.get("responseFormat", body.get("response_format"))
    repair: object = body.get("jsonRepair", body.get("json_repair", {}))
    if isinstance(extension, dict):
        response_format = extension.get(
            "responseFormat",
            extension.get("response_format", response_format),
        )
        repair = extension.get("jsonRepair", extension.get("json_repair", repair))
    attempts_value = repair.get("attempts", 3) if isinstance(repair, dict) else repair
    try:
        attempts = int(attempts_value) if isinstance(attempts_value, int | str) else 3
    except (TypeError, ValueError):
        attempts = 3
    return build_strict_json_contract(
        response_format,
        force_strict=strict or "application/json" in modes,
        repair_attempts=attempts,
    )


def _agent_message(
    *,
    context_id: str,
    text: str,
    strict_json: bool,
) -> dict[str, Any]:
    if strict_json:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise HTTPException(
                status_code=502,
                detail=f"Agent did not return strict JSON: {error}",
            ) from error
        parts = [{"data": data, "mediaType": "application/json"}]
    else:
        parts = [{"text": text}]
    return {
        "kind": "message",
        "messageId": str(uuid.uuid4()),
        "role": "ROLE_AGENT",
        "contextId": context_id,
        "parts": parts,
    }


def _task_payload(task: AgentTask) -> dict[str, Any]:
    artifacts = [
        {
            "artifactId": str(output.get("artifact_id") or output.get("uid")),
            "name": output.get("name", ""),
            "parts": output.get("parts", []),
        }
        for output in task.outputs
    ]
    latest = task.latest_message or {}
    if not artifacts and latest.get("role") == "agent":
        artifacts = [
            {
                "artifactId": str(latest.get("message_id") or latest.get("uid")),
                "parts": latest.get("parts", []),
            }
        ]
    timestamp = task.status_timestamp or datetime.now(UTC)
    status: dict[str, Any] = {
        "state": STATE_MAP.get(task.status, "TASK_STATE_UNKNOWN"),
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
    }
    if task.status_message:
        status["message"] = task.status_message
    return {
        "kind": "task",
        "id": task.task_id,
        "contextId": task.context_id,
        "status": status,
        "artifacts": artifacts,
    }


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
            detail="Agent output exceeded ASTRO_MAX_TURN_OUTPUT_BYTES",
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
                detail="Agent output exceeded ASTRO_MAX_TURN_OUTPUT_BYTES",
            )


async def _collect_turn(
    manager: SessionRuntimeManager,
    context_id: str,
    prompt: str,
    *,
    max_output_bytes: int,
) -> str:
    accumulator = _TurnAccumulator(max_output_bytes=max_output_bytes)
    async for event in manager.prompt(context_id, prompt):
        accumulator.consume(event)
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
) -> str:
    text = await _collect_turn(
        manager,
        context_id,
        prompt,
        max_output_bytes=max_output_bytes,
    )
    if not contract.enabled:
        return text
    original_text = text
    for attempt in range(contract.repair_attempts + 1):
        try:
            canonical, _value = validate_strict_json(text, contract)
            return canonical
        except StrictJsonError as error:
            if attempt >= contract.repair_attempts:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Agent did not satisfy the strict JSON output contract after "
                        f"{contract.repair_attempts} repair attempts: {error}"
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
            )
    raise AssertionError("strict JSON repair loop did not terminate")


async def _create_backend_task(
    client: MainSequenceClient,
    *,
    message: dict[str, Any],
    task_id: str,
) -> AgentTask:
    context_id = str(message["contextId"])
    session = await client.get_session(context_id)
    if not session.agent_uid:
        raise HTTPException(status_code=409, detail="Session has no bound agent")
    return await client.create_task(
        {
            "agent_uid": session.agent_uid,
            "agent_session_uid": context_id,
            "context_id": context_id,
            "task_id": task_id,
            "initial_message": {
                "message_id": message["messageId"],
                "role": "user",
                "parts": message["parts"],
                "metadata": message.get("metadata", {}),
                "extensions": message.get("extensions", []),
                "reference_task_ids": message.get("referenceTaskIds", []),
            },
            "metadata": {"transport": "a2a"},
        }
    )


async def _execute_task(
    client: MainSequenceClient,
    manager: SessionRuntimeManager,
    task: AgentTask,
    *,
    prompt: str,
    output_contract: StrictJsonContract,
    max_output_bytes: int,
) -> dict[str, Any]:
    await client.update_task_status(task.uid, status="working")
    try:
        text = await _collect_validated_turn(
            manager,
            task.context_id,
            prompt,
            output_contract,
            max_output_bytes=max_output_bytes,
        )
        message = _agent_message(
            context_id=task.context_id,
            text=text,
            strict_json=output_contract.enabled,
        )
        await client.add_task_message(
            task.uid,
            {
                "message_id": message["messageId"],
                "role": "agent",
                "parts": message["parts"],
            },
        )
        await client.update_task_status(task.uid, status="completed")
        return message
    except asyncio.CancelledError:
        await manager.cancel(task.context_id)
        await client.cancel_task(task.uid)
        raise
    except Exception as error:
        await client.update_task_status(
            task.uid,
            status="failed",
            status_message={"message": str(error)},
        )
        raise


async def _execute_message(
    manager: SessionRuntimeManager,
    *,
    context_id: str,
    prompt: str,
    output_contract: StrictJsonContract,
    max_output_bytes: int,
) -> dict[str, Any]:
    text = await _collect_validated_turn(
        manager,
        context_id,
        prompt,
        output_contract,
        max_output_bytes=max_output_bytes,
    )
    return _agent_message(
        context_id=context_id,
        text=text,
        strict_json=output_contract.enabled,
    )


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
) -> AsyncIterator[dict[str, Any]]:
    working = await client.update_task_status(task.uid, status="working")
    yield {"task": _task_payload(working)}
    artifact_id = str(uuid.uuid4())
    chunks: list[str] = []
    output_bytes = 0
    emitted = False
    try:
        if output_contract.enabled:
            text = await _collect_validated_turn(
                manager,
                task.context_id,
                prompt,
                output_contract,
                max_output_bytes=max_output_bytes,
            )
            chunks.append(text)
            if text:
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
            async for event in manager.prompt(task.context_id, prompt):
                value = _event_text(event.type, event.data, has_chunks=bool(chunks))
                if not value:
                    continue
                output_bytes = _append_bounded(
                    chunks,
                    value,
                    current_bytes=output_bytes,
                    max_output_bytes=max_output_bytes,
                )
                yield _artifact_update(
                    task,
                    artifact_id=artifact_id,
                    part={"text": value},
                    append=emitted,
                    last_chunk=False,
                )
                emitted = True

        text = "".join(chunks).strip()
        message = _agent_message(
            context_id=task.context_id,
            text=text,
            strict_json=output_contract.enabled,
        )
        await client.add_task_message(
            task.uid,
            {
                "message_id": message["messageId"],
                "role": "agent",
                "parts": message["parts"],
            },
        )
        completed = await client.update_task_status(task.uid, status="completed")
        if emitted and not output_contract.enabled:
            yield _artifact_update(
                task,
                artifact_id=artifact_id,
                part={"text": ""},
                append=True,
                last_chunk=True,
            )
        yield {"task": _task_payload(completed), "final": True}
    except asyncio.CancelledError:
        await manager.cancel(task.context_id)
        await client.cancel_task(task.uid)
        raise
    except Exception as error:
        await client.update_task_status(
            task.uid,
            status="failed",
            status_message={"message": str(error)},
        )
        raise


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
            ):
                yield _sse(
                    _stream_payload(
                        payload,
                        json_rpc=json_rpc,
                        request_id=request_id,
                    )
                )
        except asyncio.CancelledError:
            await manager.cancel(task.context_id)
            raise
        except Exception as error:
            payload = {
                "error": {
                    "code": "task_failed",
                    "message": str(error),
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


def _task_id(body: dict[str, Any]) -> str:
    value = body.get("taskId")
    return str(value).strip() if value else str(uuid.uuid4())


@router.post(f"{REST_BASE}/message:send")
async def message_send(
    body: dict[str, Any],
    client: BackendDep,
    manager: RuntimeManagerDep,
    config: SettingsDep,
    a2a_extensions: Annotated[
        str | None,
        Header(alias="A2A-Extensions"),
    ] = None,
) -> dict[str, Any]:
    message, prompt = _request_parts(body, config)
    output_contract = _output_contract(body)
    response_kind, was_explicit = _response_kind(
        body,
        a2a_extensions=a2a_extensions,
    )
    if was_explicit:
        await _require_advertised_response_kind(
            client,
            context_id=str(message["contextId"]),
            response_kind=response_kind,
        )
    if response_kind is ResponseKind.TASK:
        task = await _create_backend_task(client, message=message, task_id=_task_id(body))
        manager.create_background_task(
            _execute_task(
                client,
                manager,
                task,
                prompt=prompt,
                output_contract=output_contract,
                max_output_bytes=config.max_turn_output_bytes,
            ),
            name=f"a2a-task-{task.task_id}",
        )
        return {"task": _task_payload(task)}
    result = await _execute_message(
        manager,
        context_id=str(message["contextId"]),
        prompt=prompt,
        output_contract=output_contract,
        max_output_bytes=config.max_turn_output_bytes,
    )
    return {"message": result}


@router.post(f"{REST_BASE}/message:stream")
async def message_stream(
    body: dict[str, Any],
    client: BackendDep,
    manager: RuntimeManagerDep,
    config: SettingsDep,
    a2a_extensions: Annotated[
        str | None,
        Header(alias="A2A-Extensions"),
    ] = None,
) -> StreamingResponse:
    _response_kind(
        body,
        a2a_extensions=a2a_extensions,
        streaming=True,
    )
    message, prompt = _request_parts(body, config)
    output_contract = _output_contract(body)
    task = await _create_backend_task(client, message=message, task_id=_task_id(body))
    return _message_stream_response(
        client=client,
        manager=manager,
        task=task,
        prompt=prompt,
        output_contract=output_contract,
        max_output_bytes=config.max_turn_output_bytes,
    )


@router.get(f"{REST_BASE}/tasks")
async def list_tasks(client: BackendDep, contextId: str | None = None) -> dict[str, Any]:
    tasks = await client.list_tasks(context_id=contextId or "")
    return {"tasks": [_task_payload(task) for task in tasks]}


@router.get(f"{REST_BASE}/tasks/{{task_id}}")
async def get_task(task_id: str, client: BackendDep) -> dict[str, Any]:
    return {"task": _task_payload(await client.get_task_by_protocol_id(task_id))}


@router.post(f"{REST_BASE}/tasks/{{task_id}}:cancel")
async def cancel_task(
    task_id: str,
    client: BackendDep,
    manager: RuntimeManagerDep,
) -> dict[str, Any]:
    task = await client.get_task_by_protocol_id(task_id)
    await manager.cancel(task.context_id)
    return {"task": _task_payload(await client.cancel_task(task.uid))}


@router.get(f"{REST_BASE}/tasks/{{task_id}}:subscribe")
async def subscribe_task(task_id: str, client: BackendDep) -> StreamingResponse:
    async def stream() -> AsyncIterator[bytes]:
        previous: tuple[str, str] | None = None
        while True:
            task = await client.get_task_by_protocol_id(task_id)
            marker = (task.status, str(task.status_timestamp))
            if marker != previous:
                yield _sse({"task": _task_payload(task)})
                previous = marker
            if task.status in TERMINAL:
                break
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


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
    a2a_extensions: Annotated[
        str | None,
        Header(alias="A2A-Extensions"),
    ] = None,
) -> dict[str, Any] | StreamingResponse:
    request_id = body.get("id")
    if body.get("jsonrpc") != "2.0":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    method = str(body.get("method") or "")
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

    try:
        if method in {"SendMessage", "message/send"}:
            result = await message_send(
                params,
                client,
                manager,
                config,
                a2a_extensions,
            )
        elif method in {"SendStreamingMessage", "message/stream"}:
            _response_kind(
                params,
                a2a_extensions=a2a_extensions,
                streaming=True,
            )
            message, prompt = _request_parts(params, config)
            output_contract = _output_contract(params)
            task = await _create_backend_task(
                client,
                message=message,
                task_id=_task_id(params),
            )
            return _message_stream_response(
                client=client,
                manager=manager,
                task=task,
                prompt=prompt,
                output_contract=output_contract,
                max_output_bytes=config.max_turn_output_bytes,
                json_rpc=True,
                request_id=request_id,
            )
        elif method in {"GetTask", "tasks/get"}:
            result = await get_task(task_id(), client)
        elif method in {"ListTasks", "tasks/list"}:
            context_id = str(params.get("contextId") or "")
            result = await list_tasks(client, contextId=context_id)
        elif method in {"CancelTask", "tasks/cancel"}:
            result = await cancel_task(
                task_id(),
                client,
                manager,
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
