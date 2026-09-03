"""Canonical Main Sequence A2A requester/responder wire roles."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class A2AMessageDirection(StrEnum):
    REQUESTER = "ROLE_REQUESTER"
    RESPONDER = "ROLE_RESPONDER"


def has_a2a_direction(
    message: Mapping[str, object],
    direction: A2AMessageDirection,
) -> bool:
    """Check the actual Main Sequence A2A wire role."""

    return message.get("role") == direction.value


__all__ = ["A2AMessageDirection", "has_a2a_direction"]
