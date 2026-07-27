"""Independent filesystem discovery tools for Tau agents."""

from pathlib import Path

from tau_agent.tools import AgentTool

from .find import create_find_tool
from .grep import create_grep_tool
from .ls import create_ls_tool


def create_file_tools(*, cwd: str | Path | None = None) -> list[AgentTool]:
    """Create grep, find, and ls tools rooted at ``cwd``."""
    return [
        create_grep_tool(cwd=cwd),
        create_find_tool(cwd=cwd),
        create_ls_tool(cwd=cwd),
    ]


__all__ = [
    "create_file_tools",
    "create_find_tool",
    "create_grep_tool",
    "create_ls_tool",
]
