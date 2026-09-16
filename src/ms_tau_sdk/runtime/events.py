"""Stable Main Sequence TAU SDK event translation from Tau's internal event models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TauRuntimeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    data: dict[str, Any] = Field(default_factory=dict)


def translate_tau_event(event: object) -> TauRuntimeEvent:
    if hasattr(event, "model_dump"):
        payload = event.model_dump(mode="json", by_alias=True, exclude_none=True)
    elif isinstance(event, dict):
        payload = dict(event)
    else:
        payload = {"value": str(event)}
    event_type = str(payload.pop("type", event.__class__.__name__))

    if event_type == "message_update":
        assistant_event = payload.get("assistantMessageEvent", {})
        if isinstance(assistant_event, dict):
            subtype = assistant_event.get("type")
            if isinstance(subtype, str):
                data = dict(assistant_event)
                data.pop("type", None)
                return TauRuntimeEvent(type=str(subtype), data=data)
    return TauRuntimeEvent(type=event_type, data=payload)
