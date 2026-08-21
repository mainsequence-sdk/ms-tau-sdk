"""Stateless Tau model execution without a durable coding session."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from tau_agent.harness import AgentHarness, AgentHarnessConfig
from tau_agent.messages import AssistantMessage, TextContent, UserMessage

from astro.backend.client import MainSequenceClient
from astro.logging import conversation_log_fields
from astro.protocols.strict_json import (
    StrictJsonError,
    build_repair_prompt,
    build_strict_json_contract,
    validate_strict_json,
)
from astro.providers.factory import ProviderFactory
from astro.settings import Settings

from .dependencies import backend, provider_factory, settings

router = APIRouter(prefix="/api/llm")
BackendDep = Annotated[MainSequenceClient, Depends(backend)]
ProviderFactoryDep = Annotated[ProviderFactory, Depends(provider_factory)]
SettingsDep = Annotated[Settings, Depends(settings)]
logger = structlog.get_logger(__name__)


class LlmMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str


class ResponseFormat(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["text", "json", "json_object"] = "text"
    strict: bool = False


class StatelessChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    messages: list[LlmMessage] = Field(min_length=1)
    max_tokens: int | None = Field(default=None, gt=0)
    response_format: ResponseFormat | str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    agent_session_uid: str


def _astro_metadata(body: StatelessChatRequest) -> dict[str, Any]:
    value = body.metadata.get("astro", {})
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="metadata.astro must be an object")
    supported = {"provider", "json_repair", "omit_reasoning", "timeout_seconds"}
    unknown = sorted(set(value) - supported)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported metadata.astro controls: {', '.join(unknown)}",
        )
    return value


def _strict_json_mode(value: ResponseFormat | str | None) -> str | None:
    if isinstance(value, str):
        return "json_object" if value in {"json", "json_object", "dictionary"} else None
    if value is None:
        return None
    return value.type if value.strict or value.type in {"json", "json_object"} else None


def _history_and_prompt(
    messages: list[LlmMessage],
) -> tuple[str, list[UserMessage | AssistantMessage], str]:
    last_user = next(
        (index for index in range(len(messages) - 1, -1, -1) if messages[index].role == "user"),
        None,
    )
    if last_user is None:
        raise HTTPException(status_code=400, detail="messages must contain a user message")
    if any(message.role != "system" for message in messages[last_user + 1 :]):
        raise HTTPException(status_code=400, detail="The final conversational message must be user")

    system = "\n\n".join(
        message.content for message in messages if message.role == "system"
    ).strip()
    history: list[UserMessage | AssistantMessage] = []
    for message in messages[:last_user]:
        if message.role == "user":
            history.append(UserMessage(content=message.content))
        elif message.role == "assistant":
            history.append(
                AssistantMessage(
                    content=[TextContent(text=message.content)],
                    api="astro-stateless",
                    provider="astro",
                    model="history",
                )
            )
    return system, history, messages[last_user].content


async def _run_prompt(
    harness: AgentHarness,
    prompt: str,
    *,
    timeout_seconds: float,
) -> AssistantMessage:
    async with asyncio.timeout(timeout_seconds):
        async for _event in harness.prompt(prompt):
            pass
    for message in reversed(harness.messages):
        if isinstance(message, AssistantMessage):
            if message.stop_reason == "error":
                raise HTTPException(
                    status_code=502,
                    detail=message.error_message or "Provider returned an error",
                )
            return message
    raise HTTPException(status_code=502, detail="Provider returned no assistant message")


@router.post("/chat")
async def stateless_chat(
    body: StatelessChatRequest,
    client: BackendDep,
    providers: ProviderFactoryDep,
    runtime_settings: SettingsDep,
) -> dict[str, Any]:
    metadata = _astro_metadata(body)
    provider_name = str(metadata.get("provider") or "openai").strip()
    timeout_seconds = min(max(float(metadata.get("timeout_seconds") or 120), 1), 900)
    credential = await client.hydrate_provider_credential(
        provider_name,
        session_uid=body.agent_session_uid,
        holder_id=f"llm-passthrough/{uuid.uuid4()}",
    )
    providers.validate_selection(provider_name, body.model)
    provider = providers.build(
        credential,
        model=body.model,
        max_tokens=body.max_tokens,
        timeout_seconds=timeout_seconds,
    )
    system, history, prompt = _history_and_prompt(body.messages)
    logger.info(
        "llm.turn.started",
        message="Started stateless Tau turn",
        provider=provider_name,
        model=body.model,
        **conversation_log_fields(
            prompt,
            include_excerpt=runtime_settings.log_payloads,
            message_count=len(body.messages),
        ),
    )
    response_format: object = (
        body.response_format.model_dump(mode="json")
        if isinstance(body.response_format, ResponseFormat)
        else body.response_format
    )
    repair = metadata.get("json_repair", {})
    repair_attempts = int(repair.get("attempts", 3)) if isinstance(repair, dict) else 3
    contract = build_strict_json_contract(
        response_format,
        force_strict=_strict_json_mode(body.response_format) is not None,
        repair_attempts=repair_attempts,
    )
    if contract.enabled:
        system = (
            f"{system}\n\n" if system else ""
        ) + "Return only valid JSON. Do not wrap it in Markdown fences."
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model=body.model,
            system=system,
            tools=[],
            max_turns=1,
        ),
        messages=history,
    )
    try:
        message = await _run_prompt(harness, prompt, timeout_seconds=timeout_seconds)
        text = message.text.strip()
        parsed: Any = None
        if contract.enabled:
            original_text = text
            for attempt in range(contract.repair_attempts + 1):
                try:
                    text, parsed = validate_strict_json(text, contract)
                    break
                except StrictJsonError as error:
                    if attempt >= contract.repair_attempts:
                        raise HTTPException(
                            status_code=502,
                            detail=(
                                f"Provider did not return valid {contract.mode}: {error}"
                            ),
                        ) from error
                    message = await _run_prompt(
                        harness,
                        build_repair_prompt(
                            invalid_text=original_text,
                            validation_error=str(error),
                            contract=contract,
                            attempt=attempt + 1,
                        ),
                        timeout_seconds=timeout_seconds,
                    )
                    text = message.text.strip()
        usage = message.usage
        return {
            "ok": True,
            "provider": provider_name,
            "model": body.model,
            "message": {"role": "assistant", "content": text},
            **({"json": parsed} if contract.enabled else {}),
            "finish_reason": message.stop_reason,
            "usage": {
                "input_tokens": usage.input,
                "output_tokens": usage.output,
                "total_tokens": usage.total_tokens,
            },
        }
    finally:
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()
