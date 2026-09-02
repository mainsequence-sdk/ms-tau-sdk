"""Per-turn provenance stamps for Tau sessions (ADR-28, Tau amendment).

A bounded ``user`` turn is stamped with a tau-native custom entry so backend
history can tell a human turn from an agent turn without a new chat role. The
stamp is derived from the route that received the request only; nothing from
the request body is trusted for it.
"""

from __future__ import annotations

from typing import Literal

PROVENANCE_NAMESPACE = "io.mainsequence.provenance"

type TurnChannel = Literal["chat", "a2a", "responses"]
type TurnOrigin = Literal["user", "agent"]
type TurnProvenance = dict[str, str]

_ORIGIN_BY_CHANNEL: dict[str, TurnOrigin] = {
    "chat": "user",
    "a2a": "agent",
    "responses": "agent",
}


def build_turn_provenance(channel: TurnChannel) -> TurnProvenance:
    """Return the stamp for a user turn that arrived on ``channel``.

    ``/api/chat`` is the human-facing route; the A2A and sessionless routes are
    agent-facing, so ``origin`` follows the channel.
    """

    try:
        origin = _ORIGIN_BY_CHANNEL[channel]
    except KeyError as exc:
        raise ValueError(f"Unknown turn channel: {channel!r}") from exc
    return {"channel": channel, "origin": origin}
