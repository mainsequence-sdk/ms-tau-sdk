"""A2A message direction mapped onto the protocol's v1 wire enum.

The A2A v1 protobuf calls the requester-side value ``ROLE_USER`` and the
responder-side value ``ROLE_AGENT``. Those names describe protocol direction;
they do not identify whether the authenticated principal is a human or an
agent. Keep the protocol spelling confined to this codec so application code
can use the accurate requester/responder vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class A2AMessageDirection(StrEnum):
    REQUESTER = "requester"
    RESPONDER = "responder"


_A2A_V1_PROTOJSON_ROLE_BY_DIRECTION = {
    A2AMessageDirection.REQUESTER: "ROLE_USER",
    A2AMessageDirection.RESPONDER: "ROLE_AGENT",
}


def a2a_v1_wire_role(direction: A2AMessageDirection) -> str:
    """Return the A2A v1 ProtoJSON enum for a transport direction."""

    return _A2A_V1_PROTOJSON_ROLE_BY_DIRECTION[direction]


def has_a2a_v1_direction(
    message: Mapping[str, object],
    direction: A2AMessageDirection,
) -> bool:
    """Check a wire Message without treating its role as principal identity."""

    return message.get("role") == a2a_v1_wire_role(direction)


__all__ = ["A2AMessageDirection", "a2a_v1_wire_role", "has_a2a_v1_direction"]
