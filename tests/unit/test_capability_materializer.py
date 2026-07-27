from unittest.mock import AsyncMock

import pytest

from astro.backend.models import (
    AgentCapability,
    CapabilityContent,
    SessionCapabilityBinding,
)
from astro.capabilities import materialize_session_capabilities
from astro.errors import ConfigurationError


def _binding(path: str) -> SessionCapabilityBinding:
    return SessionCapabilityBinding(
        uid="binding-1",
        capability_uid="capability-1",
        capability=AgentCapability(
            uid="capability-1",
            kind="skill",
            source_type="inline",
            capability_path=path,
            has_content=True,
        ),
        source_type="inline",
    )


@pytest.mark.asyncio
async def test_materializes_session_skill_into_private_agents_root(tmp_path):
    backend = AsyncMock()
    backend.list_session_capabilities.return_value = [
        _binding("skills/research/SKILL.md")
    ]
    backend.get_capability_content.return_value = CapabilityContent(
        content="# Research\n"
    )

    agents_root = await materialize_session_capabilities(
        backend=backend,
        session_uid="session-1",
        asset_root=tmp_path,
    )

    skill = agents_root / "skills" / "research" / "SKILL.md"
    assert skill.read_text() == "# Research\n"
    assert skill.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_rejects_unsafe_session_skill_path(tmp_path):
    backend = AsyncMock()
    backend.list_session_capabilities.return_value = [
        _binding("skills/../secrets/SKILL.md")
    ]

    with pytest.raises(ConfigurationError, match="unsafe skill path"):
        await materialize_session_capabilities(
            backend=backend,
            session_uid="session-1",
            asset_root=tmp_path,
        )

    backend.get_capability_content.assert_not_awaited()
