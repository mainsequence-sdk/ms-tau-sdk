"""Tau extension proving project-workspace import behavior."""

from __future__ import annotations

from collections.abc import Mapping

from flat_dependency import flat_value
from project_fixture.service import project_value
from tau_agent.messages import TextContent
from tau_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from tau_agent.types import JSONValue
from tau_coding.extensions import ToolCallHookResult

from .formatting import sibling_value


async def execute_fixture(
    tool_call_id: str,
    arguments: Mapping[str, JSONValue],
    signal: ToolCancellationToken | None = None,
    on_update: ToolUpdateCallback | None = None,
) -> AgentToolResult:
    del tool_call_id, signal, on_update
    details = {
        "src": project_value(),
        "flat": flat_value(),
        "sibling": sibling_value(),
        "hooked": arguments.get("hooked") is True,
    }
    return AgentToolResult(
        content=[TextContent(text="CodeRepository extension imports succeeded")],
        details=details,
    )


def setup(tau) -> None:
    tau.register_tool(
        AgentTool(
            name="verify_project_imports",
            label="Verify Project Imports",
            description="Verify CodeRepository source, flat, and extension-relative imports.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            execute_fn=execute_fixture,
        )
    )

    def add_hook_marker(event, context):
        del context
        if event.tool_name != "verify_project_imports":
            return None
        return ToolCallHookResult(arguments={**event.arguments, "hooked": True})

    tau.on("tool_call", add_hook_marker)
