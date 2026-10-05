"""Durable assistant-ui chat streaming."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ms_tau_sdk.errors import BackendConflictError
from ms_tau_sdk.logging import bind_request_log_fields, conversation_log_fields
from ms_tau_sdk.protocols.assistant_ui import AssistantUiEncoder
from ms_tau_sdk.runtime.manager import SessionRuntimeManager
from ms_tau_sdk.runtime.provenance import (
    CallerIdentityError,
    build_turn_provenance,
    turn_provenance_from_request,
)

from .dependencies import runtime_manager
from .models import ChatRequest
from .request_identity import require_session_access, verified_user_uid

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
    if manager.settings.local_mode:
        provenance = {
            **build_turn_provenance("chat"),
            "actorKind": "user",
            "actorUid": "local-mainsequence-user",
        }
    else:
        try:
            provenance = turn_provenance_from_request(
                "chat",
                request.headers,
                verified_user_uid=verified_user_uid(manager.settings),
            )
        except CallerIdentityError as error:
            logger.warning(
                "turn.caller_identity_rejected",
                message="Chat route rejected a request without a valid caller identity",
                route=request.url.path,
                failing_headers=list(error.failing_headers),
            )
            return JSONResponse(  # type: ignore[return-value]
                status_code=error.status_code,
                content=error.body(),
            )
    if manager.settings.local_mode:
        session_uid = manager.settings.local_session_uid(body.session_uid)
    elif body.session_uid:
        session_uid = body.session_uid
        if manager.settings.request_identity_mode == "assertion":
            await require_session_access(manager.backend, session_uid)
    else:
        raise HTTPException(status_code=422, detail="sessionUid is required")
    try:
        prompt = body.prompt_text()
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    bind_request_log_fields(
        request.scope,
        session_uid=session_uid,
        agent_session_uid=session_uid,
        is_streaming=True,
        **conversation_log_fields(
            prompt,
            include_excerpt=manager.settings.log_payloads,
            message_count=len(body.messages) if body.messages else None,
        ),
    )
    # A local turn is recorded as a chat session and outlives this request, so a
    # reload or a closed tab leaves it running; only session/cancel stops it. A
    # managed turn stays bound to the request that streams it.
    local_turn = (
        await manager.start_local_chat_turn(session_uid, prompt, provenance=provenance)
        if manager.settings.local_mode
        else None
    )
    events = (
        local_turn.events()
        if local_turn is not None
        else manager.prompt(session_uid, prompt, provenance=provenance)
    )

    async def stream() -> AsyncIterator[bytes]:
        encoder = AssistantUiEncoder()
        try:
            async for event in events:
                for payload in encoder.encode(event):
                    yield encoder.sse(payload)
            try:
                for payload in encoder.finalize():
                    yield encoder.sse(payload)
                yield encoder.done()
            finally:
                if local_turn is None:
                    manager.mark_response_delivered(session_uid)
        except asyncio.CancelledError:
            if local_turn is None and not encoder.finished:
                await manager.cancel(session_uid)
            raise
        except Exception as error:
            logger.exception(
                "chat.stream.failed",
                message="Chat stream failed",
                session_uid=session_uid,
                agent_session_uid=session_uid,
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
        finally:
            if local_turn is not None:
                local_turn.detach()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "x-vercel-ai-ui-message-stream": "v1",
            "X-Agent-Session-Uid": session_uid,
        },
    )


@router.post("/chat/mock")
async def mock_chat(body: dict[str, object]) -> StreamingResponse:
    text = str(body.get("message") or "Main Sequence TAU SDK Tau runtime is available.")

    async def stream() -> AsyncIterator[bytes]:
        yield AssistantUiEncoder.sse({"type": "text-start", "id": "text-0"})
        yield AssistantUiEncoder.sse({"type": "text-delta", "id": "text-0", "textDelta": text})
        yield AssistantUiEncoder.sse({"type": "text-end", "id": "text-0"})
        yield AssistantUiEncoder.sse({"type": "finish", "finishReason": "stop"})
        yield AssistantUiEncoder.done()

    return StreamingResponse(stream(), media_type="text/event-stream")
