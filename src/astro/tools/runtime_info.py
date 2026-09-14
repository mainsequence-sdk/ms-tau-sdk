"""Runtime diagnostics exposed to the active agent."""

from __future__ import annotations

import json
import platform
import sys
from collections.abc import Mapping
from pathlib import Path

from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue

from astro import __version__
from astro.runtime.extensions import ProjectExtensionState


def create_runtime_info_tool(
    *,
    session_uid: str,
    cwd: Path,
    provider: str,
    model: str,
    project_extensions: ProjectExtensionState | None = None,
) -> AgentTool:
    extension_state = project_extensions or ProjectExtensionState(enabled=False)

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal, on_update
        data: dict[str, JSONValue] = {
            "runtime": "astro-tau",
            "runtime_version": __version__,
            "session_uid": session_uid,
            "python": platform.python_version(),
            "python_executable": sys.executable,
            "cwd": str(cwd),
            "provider": provider,
            "model": model,
            **extension_state.details(),
        }
        return AgentToolResult(
            content=[TextContent(text=json.dumps(data, indent=2))],
            details=data,
        )

    return AgentTool(
        name="runtime_info",
        label="Runtime Info",
        description=(
            "Return the active Astro/Tau runtime, Python interpreter, code repository path, "
            "session, provider, model, and project-extension catalog state."
        ),
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        execute_fn=execute,
        prompt_snippet="Inspect the active runtime environment",
    )
