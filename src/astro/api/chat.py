"""Durable assistant-ui chat streaming."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from astro.protocols.assistant_ui import AssistantUiEncoder
from astro.runtime.manager import SessionRuntimeManager

from .dependencies import runtime_manager
from .models import ChatRequest

router = APIRouter(prefix="/api")
RuntimeManagerDep = Annotated[SessionRuntimeManager, Depends(runtime_manager)]
logger = structlog.get_logger(__name__)


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
    manager: RuntimeManagerDep,
) -> StreamingResponse:
    try:
        prompt = body.prompt_text()
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    async def stream() -> AsyncIterator[bytes]:
        encoder = AssistantUiEncoder()
        try:
            async for event in manager.prompt(body.session_uid, prompt):
                for payload in encoder.encode(event):
                    yield encoder.sse(payload)
            if not encoder.finished:
                yield encoder.sse({"type": "finish", "finishReason": "stop"})
            yield encoder.done()
        except asyncio.CancelledError:
            await manager.cancel(body.session_uid)
            raise
        except Exception as error:
            logger.exception(
                "chat.stream.failed",
                message="Chat stream failed",
                session_uid=body.session_uid,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            yield encoder.sse(
                {
                    "type": "error",
                    "errorText": str(error),
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
        yield AssistantUiEncoder.sse(
            {"type": "text-delta", "id": "text-0", "textDelta": text}
        )
        yield AssistantUiEncoder.sse({"type": "text-end", "id": "text-0"})
        yield AssistantUiEncoder.sse({"type": "finish", "finishReason": "stop"})
        yield AssistantUiEncoder.done()

    return StreamingResponse(stream(), media_type="text/event-stream")
