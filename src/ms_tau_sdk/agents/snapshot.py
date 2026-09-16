"""Immutable agent configuration supplied by the deployment control plane."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentExecutionSnapshot(BaseModel):
    """Bounded local configuration for agent-targeted, sessionless execution."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    agent_uid: str = Field(min_length=1)
    name: str = ""
    description: str = ""
    harness: Literal["tau"] = "tau"
    default_provider: str = ""
    default_model: str = ""
    default_thinking: str = ""
    instructions: str = ""
    allowed_input_media_types: tuple[str, ...] = (
        "text/plain",
        "text/markdown",
        "application/json",
        "text/csv",
    )
    sessionless_capabilities: tuple[str, ...] = ()
    max_turns: int = Field(default=1, ge=1, le=16)

    @field_validator("agent_uid", "default_provider", "default_model", "default_thinking")
    @classmethod
    def strip_identity_and_defaults(cls, value: str) -> str:
        return value.strip()

    @field_validator("allowed_input_media_types")
    @classmethod
    def normalize_media_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(item.strip().lower() for item in value if item.strip()))
        if not normalized:
            raise ValueError("allowed_input_media_types must not be empty")
        return normalized
