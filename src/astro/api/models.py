"""HTTP request and response models."""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from astro.backend.models import TauThinkingLevel


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ChatRequest(ApiModel):
    session_uid: str = Field(
        validation_alias=AliasChoices(
            "sessionUid",
            "runtime_session_uid",
            "runtimeSessionUid",
            "agent_session_uid",
        )
    )
    message: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)

    def prompt_text(self) -> str:
        if self.message and self.message.strip():
            return self.message.strip()
        for message in reversed(self.messages):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts = [
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
                text = "\n".join(part for part in parts if part).strip()
                if text:
                    return text
        raise ValueError("Request does not contain a user message")


class CancelRequest(ApiModel):
    session_uid: str = Field(
        validation_alias=AliasChoices(
            "sessionUid",
            "runtime_session_uid",
            "runtimeSessionUid",
        )
    )
    message: str | None = None


class SessionConfigPatch(ApiModel):
    session_uid: str = Field(
        validation_alias=AliasChoices(
            "sessionUid",
            "runtime_session_uid",
            "runtimeSessionUid",
        )
    )
    provider: str | None = None
    model: str | None = None
    thinking_level: TauThinkingLevel | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "thinking_level",
            "thinkingLevel",
            "reasoningEffort",
        ),
    )
