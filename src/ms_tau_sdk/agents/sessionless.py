"""Construction boundary for sessionless Tau harness execution."""

from __future__ import annotations

from tau_agent.harness import AgentHarness, AgentHarnessConfig
from tau_agent.provider import ModelProvider


def create_sessionless_harness(
    *,
    provider: ModelProvider,
    model: str,
    system: str,
    max_turns: int,
) -> AgentHarness:
    """Create a sessionless harness without persistence or coding tools."""
    return AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model=model,
            system=system,
            tools=[],
            max_turns=max_turns,
        )
    )
