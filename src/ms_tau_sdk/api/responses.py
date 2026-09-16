"""Agent-targeted, sessionless Tau responses using canonical A2A messages."""

from __future__ import annotations

import asyncio
import base64
import time
import uuid
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any
from xml.etree import ElementTree

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pypdf import PdfReader
from tau_agent.harness import AgentHarness
from tau_agent.messages import AssistantMessage, ImageContent, TextContent, UserMessage

from ms_tau_sdk.agents import AgentExecutionSnapshot
from ms_tau_sdk.agents.sessionless import create_sessionless_harness
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.models import ProviderCredential
from ms_tau_sdk.logging import bind_request_log_fields, conversation_log_fields
from ms_tau_sdk.protocols.a2a_message import (
    IMAGE_MEDIA_TYPES,
    PreparedA2AInput,
    agent_message,
    output_contract,
    prepare_a2a_input,
    sse,
)
from ms_tau_sdk.protocols.strict_json import (
    StrictJsonContract,
    StrictJsonError,
    build_repair_prompt,
    validate_strict_json,
)
from ms_tau_sdk.providers.factory import ProviderFactory
from ms_tau_sdk.settings import TauSDKSettings

from .dependencies import backend, provider_factory, settings

router = APIRouter(prefix="/api/agents")
BackendDep = Annotated[MainSequenceClient, Depends(backend)]
ProviderFactoryDep = Annotated[ProviderFactory, Depends(provider_factory)]
SettingsDep = Annotated[TauSDKSettings, Depends(settings)]
logger = structlog.get_logger(__name__)

INFERENCE_EXTENSION_URI = "https://mainsequence.ai/a2a/extensions/agent-inference/v1"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _snapshot(config: TauSDKSettings, agent_uid: str) -> AgentExecutionSnapshot:
    snapshot = config.agent_execution_snapshot
    if snapshot is None or snapshot.agent_uid != agent_uid:
        raise HTTPException(status_code=404, detail="agent_not_found")
    if snapshot.harness != "tau":
        raise HTTPException(status_code=409, detail="agent_harness_not_supported")
    return snapshot


def _require_a2a_content_type(request: Request) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/a2a+json":
        raise HTTPException(
            status_code=415,
            detail="Content-Type must be application/a2a+json",
        )


def _inference_controls(body: dict[str, Any]) -> dict[str, Any]:
    metadata = body.get("metadata", {})
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=400, detail="metadata must be an object")
    value = metadata.get(INFERENCE_EXTENSION_URI, {})
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=400,
            detail=f"metadata.{INFERENCE_EXTENSION_URI} must be an object",
        )
    allowed = {"provider", "model", "thinking", "maxOutputTokens", "timeoutSeconds"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported agent inference controls: {', '.join(unknown)}",
        )
    return value


def _selection(
    snapshot: AgentExecutionSnapshot,
    controls: dict[str, Any],
) -> tuple[str, str, str | None, dict[str, str]]:
    def optional_string(key: str) -> str:
        if key not in controls:
            return ""
        value = controls[key]
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(status_code=400, detail=f"{key} must be a non-empty string")
        return value.strip()

    requested_provider = optional_string("provider")
    requested_model = optional_string("model")
    requested_thinking = optional_string("thinking")
    if (
        requested_provider
        and not requested_model
        and requested_provider != snapshot.default_provider
    ):
        raise HTTPException(
            status_code=400,
            detail="invalid_model_override: provider override requires model",
        )
    provider_name = requested_provider or snapshot.default_provider
    model = requested_model or snapshot.default_model
    if not provider_name or not model:
        raise HTTPException(status_code=409, detail="agent_model_not_configured")
    inherited_thinking = snapshot.default_thinking
    thinking_input = requested_thinking if "thinking" in controls else inherited_thinking or None
    sources = {
        "provider": "request" if requested_provider else "agent_default",
        "model": "request" if requested_model else "agent_default",
        "thinking": (
            "request"
            if "thinking" in controls
            else "agent_default"
            if inherited_thinking
            else "tau_execution_default"
        ),
    }
    return provider_name, model, thinking_input, sources


def _bounded_control(
    controls: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = controls.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status_code=400, detail=f"{key} must be an integer")
    normalized = value
    if normalized < minimum or normalized > maximum:
        raise HTTPException(
            status_code=400,
            detail=f"{key} must be between {minimum} and {maximum}",
        )
    return normalized


def _extract_pdf(path: Path, *, max_characters: int) -> str:
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError("encrypted PDF files are not supported")
    chunks: list[str] = []
    size = 0
    for page in reader.pages:
        value = (page.extract_text() or "").strip()
        if not value:
            continue
        size += len(value)
        if size > max_characters:
            raise ValueError("extracted PDF text exceeds the request limit")
        chunks.append(value)
    if not chunks:
        raise ValueError("PDF contains no extractable text")
    return "\n\n".join(chunks)


def _extract_docx(path: Path, *, max_characters: int) -> str:
    with zipfile.ZipFile(path) as archive:
        try:
            document = archive.read("word/document.xml")
        except KeyError as error:
            raise ValueError("DOCX is missing word/document.xml") from error
    root = ElementTree.fromstring(document)
    chunks = [node.text for node in root.iter() if node.tag.endswith("}t") and node.text]
    text = " ".join(chunks).strip()
    if not text:
        raise ValueError("DOCX contains no extractable text")
    if len(text) > max_characters:
        raise ValueError("extracted DOCX text exceeds the request limit")
    return text


async def _user_message(prepared: PreparedA2AInput, *, max_characters: int) -> UserMessage:
    text_sections = (
        [prepared.prompt(include_file_manifest=False)]
        if prepared.text or prepared.data_parts
        else []
    )
    content: list[TextContent | ImageContent] = []
    for file in prepared.files:
        if file.media_type in IMAGE_MEDIA_TYPES:
            content.append(
                ImageContent(
                    data=base64.b64encode(file.path.read_bytes()).decode("ascii"),
                    mime_type=file.media_type,
                )
            )
            continue
        try:
            if file.media_type == "application/pdf":
                extracted = await asyncio.to_thread(
                    _extract_pdf,
                    file.path,
                    max_characters=max_characters,
                )
            elif file.media_type == DOCX_MEDIA_TYPE:
                extracted = await asyncio.to_thread(
                    _extract_docx,
                    file.path,
                    max_characters=max_characters,
                )
            else:
                extracted = file.path.read_text(encoding="utf-8")
                if len(extracted) > max_characters:
                    raise ValueError("text attachment exceeds the request limit")
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            raise HTTPException(
                status_code=422,
                detail=f"attachment_processing_failed: {file.filename}: {error}",
            ) from error
        text_sections.append(f"Attachment {file.filename}:\n{extracted}")
    prompt = "\n\n".join(section for section in text_sections if section).strip()
    if prompt:
        content.insert(0, TextContent(text=prompt))
    elif content:
        content.insert(0, TextContent(text="Analyze the attached image."))
    return UserMessage(content=content)


async def _run_prompt(
    harness: AgentHarness,
    prompt: str | UserMessage,
    *,
    timeout_seconds: float,
) -> AssistantMessage:
    async with asyncio.timeout(timeout_seconds):
        events = (
            harness.prompt(prompt) if isinstance(prompt, str) else harness.prompt_message(prompt)
        )
        async for _event in events:
            pass
    for message in reversed(harness.messages):
        if isinstance(message, AssistantMessage):
            if message.stop_reason == "error":
                raise HTTPException(
                    status_code=502,
                    detail=message.error_message or "agent_response_failed",
                )
            if not message.text.strip():
                raise HTTPException(status_code=502, detail="agent_response_failed: empty answer")
            return message
    raise HTTPException(status_code=502, detail="agent_response_failed: no assistant message")


async def _validated_response(
    harness: AgentHarness,
    prompt: UserMessage,
    *,
    contract: StrictJsonContract,
    timeout_seconds: float,
) -> tuple[AssistantMessage, str]:
    message = await _run_prompt(harness, prompt, timeout_seconds=timeout_seconds)
    text = message.text.strip()
    if not contract.enabled:
        return message, text
    for attempt in range(contract.repair_attempts + 1):
        try:
            text, _parsed = validate_strict_json(text, contract)
            return message, text
        except StrictJsonError as error:
            if attempt >= contract.repair_attempts:
                raise HTTPException(
                    status_code=502,
                    detail=f"Agent did not return valid {contract.mode}: {error}",
                ) from error
            message = await _run_prompt(
                harness,
                build_repair_prompt(
                    invalid_text=text,
                    validation_error=str(error),
                    contract=contract,
                    attempt=attempt + 1,
                ),
                timeout_seconds=timeout_seconds,
            )
            text = message.text.strip()
    raise AssertionError("strict JSON repair loop did not terminate")


async def _execute(
    *,
    agent_uid: str,
    body: dict[str, Any],
    request: Request,
    client: MainSequenceClient,
    providers: ProviderFactory,
    config: TauSDKSettings,
) -> dict[str, Any]:
    snapshot = _snapshot(config, agent_uid)
    controls = _inference_controls(body)
    provider_name, model, thinking_input, sources = _selection(snapshot, controls)
    timeout_seconds = float(
        _bounded_control(
            controls,
            "timeoutSeconds",
            default=120,
            minimum=1,
            maximum=900,
        )
    )
    max_tokens = _bounded_control(
        controls,
        "maxOutputTokens",
        default=4096,
        minimum=1,
        maximum=131072,
    )
    prepared = prepare_a2a_input(
        body,
        config,
        context_policy="forbidden",
        allowed_media_types=set(snapshot.allowed_input_media_types),
    )
    run_uid = str(uuid.uuid4())
    bind_request_log_fields(
        request.scope,
        agent_uid=agent_uid,
        agent_run_uid=run_uid,
        turn_uid=str(uuid.uuid4()),
        model_call_uid=str(uuid.uuid4()),
        a2a_message_id=str(prepared.message["messageId"]),
    )
    credential = None
    provider = None
    started_at = time.monotonic()
    try:
        evidence = await client.hydrate_provider_credential(
            provider_name,
            model=model,
            agent_uid=agent_uid,
            holder_id=f"agent-response/{run_uid}",
        )
        try:
            thinking = providers.validate_execution(
                evidence.provider_control,
                provider_name=provider_name,
                model=model,
                thinking_level=thinking_input,
                media_types={item.media_type for item in prepared.files},
            )
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        credential = evidence.credential
        cached_credential = credential
        cached_provider_control = evidence.provider_control
        credential_refresh_lock = asyncio.Lock()

        async def resolve_credential() -> ProviderCredential:
            nonlocal cached_credential
            if not ProviderFactory._credential_expires_soon(cached_credential):
                return cached_credential
            async with credential_refresh_lock:
                if ProviderFactory._credential_expires_soon(cached_credential):
                    refreshed = await client.hydrate_provider_credential(
                        provider_name,
                        model=model,
                        agent_uid=agent_uid,
                        holder_id=f"agent-response/{run_uid}",
                    )
                    providers.validate_execution(
                        refreshed.provider_control,
                        provider_name=provider_name,
                        model=model,
                        thinking_level=thinking,
                    )
                    if refreshed.provider_control.model != cached_provider_control.model:
                        raise RuntimeError(
                            "Provider-control execution capability changed during "
                            "credential refresh"
                        )
                    cached_credential = refreshed.credential
                return cached_credential

        provider = providers.build(
            credential,
            provider_control=evidence.provider_control,
            model=model,
            credential_resolver=resolve_credential,
            thinking_level=thinking,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        contract = output_contract(body)
        system = snapshot.instructions
        if contract.enabled:
            system = (
                f"{system}\n\n" if system else ""
            ) + "Return only valid JSON. Do not wrap it in Markdown fences."
        harness = create_sessionless_harness(
            provider=provider,
            model=model,
            system=system,
            max_turns=snapshot.max_turns,
        )
        user_message = await _user_message(
            prepared,
            max_characters=config.max_turn_output_bytes,
        )
        logger.info(
            "agent.run.started",
            message="Started agent-targeted sessionless response",
            agent_type="tau_sessionless_response",
            model_provider=provider_name,
            model_name=model,
            thinking_level=thinking,
            **conversation_log_fields(
                prepared.text,
                include_excerpt=config.log_payloads,
                message_count=1,
            ),
        )
        response, text = await _validated_response(
            harness,
            user_message,
            contract=contract,
            timeout_seconds=timeout_seconds,
        )
        usage = response.usage
        inference_metadata = {
            "agentUid": agent_uid,
            "resolved": {
                "provider": provider_name,
                "model": model,
                "thinking": thinking,
                "sources": sources,
            },
            "finishReason": response.stop_reason,
            "usage": {
                "inputTokens": usage.input,
                "outputTokens": usage.output,
                "totalTokens": usage.total_tokens,
            },
            "capabilitiesEligible": list(snapshot.sessionless_capabilities),
            "capabilitiesUsed": [],
        }
        logger.info(
            "agent.run.completed",
            message="Completed agent-targeted sessionless response",
            agent_type="tau_sessionless_response",
            model_provider=provider_name,
            model_name=model,
            thinking_level=thinking,
            duration_ms=round((time.monotonic() - started_at) * 1000, 3),
            input_tokens=usage.input,
            output_tokens=usage.output,
            total_tokens=usage.total_tokens,
            outcome="success",
        )
        return agent_message(
            text=text,
            strict_json=contract.enabled,
            metadata={INFERENCE_EXTENSION_URI: inference_metadata},
        )
    except TimeoutError as error:
        raise HTTPException(status_code=504, detail="agent_response_timeout") from error
    finally:
        prepared.cleanup()
        if provider is not None:
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()


@router.post("/{agent_uid}/responses", response_model=None)
async def create_agent_response(
    agent_uid: str,
    body: dict[str, Any],
    request: Request,
    client: BackendDep,
    providers: ProviderFactoryDep,
    config: SettingsDep,
) -> JSONResponse:
    _require_a2a_content_type(request)
    message = await _execute(
        agent_uid=agent_uid,
        body=body,
        request=request,
        client=client,
        providers=providers,
        config=config,
    )
    return JSONResponse(
        {"message": message},
        media_type="application/a2a+json",
    )


@router.post("/{agent_uid}/responses/stream", response_model=None)
async def stream_agent_response(
    agent_uid: str,
    body: dict[str, Any],
    request: Request,
    client: BackendDep,
    providers: ProviderFactoryDep,
    config: SettingsDep,
) -> StreamingResponse:
    _require_a2a_content_type(request)

    async def stream() -> AsyncIterator[bytes]:
        try:
            message = await _execute(
                agent_uid=agent_uid,
                body=body,
                request=request,
                client=client,
                providers=providers,
                config=config,
            )
            if not await request.is_disconnected():
                yield sse({"message": message, "final": True})
        except asyncio.CancelledError:
            raise
        except Exception as error:
            detail = error.detail if isinstance(error, HTTPException) else str(error)
            yield sse(
                {
                    "error": {"code": "agent_response_failed", "message": str(detail)},
                    "final": True,
                }
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
