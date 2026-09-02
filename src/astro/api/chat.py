"""Durable assistant-ui chat streaming."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from astro.errors import BackendConflictError
from astro.logging import bind_request_log_fields, conversation_log_fields
from astro.protocols.assistant_ui import AssistantUiEncoder
from astro.runtime.manager import SessionRuntimeManager
from astro.runtime.provenance import CallerIdentityError, turn_provenance_from_request

from .dependencies import runtime_manager
from .models import ChatRequest

router = APIRouter(prefix="/api")
RuntimeManagerDep = Annotated[SessionRuntimeManager, Depends(runtime_manager)]
logger = structlog.get_logger(__name__)
MODEL_PROVIDER_CREDENTIAL_UNAVAILABLE = "model_provider_credential_unavailable"


def _backend_user_message(error: Exception) -> str | None:
    if not isinstance(error, BackendConflictError) or not isinstance(error.detail, dict):
        return None
    if error.detail.get("error_code") != MODEL_PROVIDER_CREDENTIAL_UNAVAILABLE:
        return None
    detail = str(error.detail.get("error_detail") or "").strip()
    return detail or "The selected provider credential is unavailable."


@router.get("/chat")
async def chat_info() -> dict[str, object]:
    return {
        "ok": True,
        "runtime": "tau",
        "message": "Use POST /api/chat with an assistant-ui data-stream payload.",
    }


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    manager: RuntimeManagerDep,
) -> StreamingResponse:
    try:
        provenance = turn_provenance_from_request("chat", request.headers)
    except CallerIdentityError as error:
        logger.warning(
            "turn.caller_identity_rejected",
            message="Chat route rejected a request without a valid caller identity",
            route=request.url.path,
            failing_headers=list(error.failing_headers),
        )
        return JSONResponse(status_code=error.status_code, content=error.body())  # type: ignore[return-value]
    try:
        prompt = body.prompt_text()
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    bind_request_log_fields(
        request.scope,
        session_uid=body.session_uid,
        agent_session_uid=body.session_uid,
        is_streaming=True,
        **conversation_log_fields(
            prompt,
            include_excerpt=manager.settings.log_payloads,
            message_count=len(body.messages) if body.messages else None,
        ),
    )

    async def stream() -> AsyncIterator[bytes]:
        encoder = AssistantUiEncoder()
        try:
            async for event in manager.prompt(body.session_uid, prompt, provenance=provenance):
                for payload in encoder.encode(event):
                    yield encoder.sse(payload)
            try:
                for payload in encoder.finalize():
                    yield encoder.sse(payload)
                yield encoder.done()
            finally:
                manager.mark_response_delivered(body.session_uid)
        except asyncio.CancelledError:
            if not encoder.finished:
                await manager.cancel(body.session_uid)
            raise
        except Exception as error:
            logger.exception(
                "chat.stream.failed",
                message="Chat stream failed",
                session_uid=body.session_uid,
                agent_session_uid=body.session_uid,
                error_type=type(error).__name__,
            )
            user_message = _backend_user_message(error) if not encoder.finished else None
            if user_message is not None:
                yield encoder.sse({"type": "text-start", "id": "text-error"})
                yield encoder.sse(
                    {
                        "type": "text-delta",
                        "id": "text-error",
                        "textDelta": user_message,
                    }
                )
                yield encoder.sse({"type": "text-end", "id": "text-error"})
                yield encoder.sse({"type": "finish", "finishReason": "stop"})
            else:
                yield encoder.sse(
                    {
                        "type": "error",
                        "errorText": (
                            f"Conversation output completed, but saving failed: {error}"
                            if encoder.finished
                            else str(error)
                        ),
                    }
                )
            yield encoder.done()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "x-vercel-ai-ui-message-stream": "v1",
        },
    )


@router.post("/chat/mock")
async def mock_chat(body: dict[str, object]) -> StreamingResponse:
    text = str(body.get("message") or "Astro Tau runtime is available.")

    async def stream() -> AsyncIterator[bytes]:
        yield AssistantUiEncoder.sse({"type": "text-start", "id": "text-0"})
        yield AssistantUiEncoder.sse({"type": "text-delta", "id": "text-0", "textDelta": text})
        yield AssistantUiEncoder.sse({"type": "text-end", "id": "text-0"})
        yield AssistantUiEncoder.sse({"type": "finish", "finishReason": "stop"})
        yield AssistantUiEncoder.done()

    return StreamingResponse(stream(), media_type="text/event-stream")
