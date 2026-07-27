"""Thin Astro adapter for the standalone tau-web-access package."""

from __future__ import annotations

from pathlib import Path

import httpx
from tau_agent.tools import AgentTool
from tau_web_access import SearchResultStore, WebAccessSettings, create_web_tools


def build_web_tools(
    *,
    cwd: Path,
    store: SearchResultStore,
    client: httpx.AsyncClient,
) -> list[AgentTool]:
    return create_web_tools(
        settings=WebAccessSettings(),
        store=store,
        client=client,
        cwd=cwd,
    )
