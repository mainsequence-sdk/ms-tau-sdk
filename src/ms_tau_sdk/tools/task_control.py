"""Tau tools for structured A2A Task interruption."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue

from ms_tau_sdk.runtime.task_context import active_task_execution


def _inactive_result() -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(text="No asynchronous A2A Task is active in this turn.")],
        details={"accepted": False, "reason": "task_context_missing"},
    )


async def _request_input(
    tool_call_id: str,
    arguments: Mapping[str, JSONValue],
    signal: ToolCancellationToken | None = None,
    on_update: ToolUpdateCallback | None = None,
) -> AgentToolResult:
    del tool_call_id, on_update
    if signal is not None and signal.is_cancelled():
        return AgentToolResult(
            content=[TextContent(text="Task input request was cancelled.")],
            details={"accepted": False, "cancelled": True},
        )
    context = active_task_execution()
    if context is None:
        return _inactive_result()
    context.request_interruption(
        status="input_required",
        text=str(arguments["prompt"]),
        details={
            "reason": str(arguments["reason"]),
            "inputSchema": cast(JSONValue, arguments.get("schema") or {}),
        },
    )
    return AgentToolResult(
        content=[TextContent(text="The Task is now waiting for caller input. Stop this turn.")],
        details={"accepted": True, "state": "input_required"},
    )


async def _request_authorization(
    tool_call_id: str,
    arguments: Mapping[str, JSONValue],
    signal: ToolCancellationToken | None = None,
    on_update: ToolUpdateCallback | None = None,
) -> AgentToolResult:
    del tool_call_id, on_update
    if signal is not None and signal.is_cancelled():
        return AgentToolResult(
            content=[TextContent(text="Task authorization request was cancelled.")],
            details={"accepted": False, "cancelled": True},
        )
    context = active_task_execution()
    if context is None:
        return _inactive_result()
    context.request_interruption(
        status="auth_required",
        text=str(arguments["prompt"]),
        details={
            "requirementReference": str(arguments["requirement_reference"]),
        },
    )
    return AgentToolResult(
        content=[
            TextContent(
                text="The Task is now waiting for out-of-band authorization. Stop this turn."
            )
        ],
        details={"accepted": True, "state": "auth_required"},
    )


def create_task_control_tools() -> list[AgentTool]:
    return [
        AgentTool(
            name="task_request_input",
            label="Request Task Input",
            description=(
                "Interrupt the active asynchronous A2A Task and request structured input "
                "from its caller. Use only when progress cannot continue without that input."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "minLength": 1, "maxLength": 128},
                    "prompt": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "schema": {"type": "object"},
                },
                "required": ["reason", "prompt"],
                "additionalProperties": False,
            },
            execute_fn=_request_input,
            execution_mode="sequential",
        ),
        AgentTool(
            name="task_request_authorization",
            label="Request Task Authorization",
            description=(
                "Interrupt the active asynchronous A2A Task for an existing backend-owned "
                "authorization requirement. Never include credentials or secret values."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "requirement_reference": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 255,
                    },
                    "prompt": {"type": "string", "minLength": 1, "maxLength": 2000},
                },
                "required": ["requirement_reference", "prompt"],
                "additionalProperties": False,
            },
            execute_fn=_request_authorization,
            execution_mode="sequential",
        ),
    ]
