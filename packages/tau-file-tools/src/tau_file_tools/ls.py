"""Tau directory-listing tool."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue

from ._common import (
    DEFAULT_MAX_OUTPUT_BYTES,
    check_cancelled,
    positive_int_arg,
    resolve_path,
    root_path,
    string_arg,
    truncate_bytes,
)

DEFAULT_LIMIT = 500


def create_ls_tool(*, cwd: str | Path | None = None) -> AgentTool:
    """Create a Tau tool that lists one directory including dotfiles."""
    root = root_path(cwd)

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, on_update
        check_cancelled(signal)
        raw_path = string_arg(arguments, "path", required=False)
        limit = positive_int_arg(arguments, "limit", default=DEFAULT_LIMIT)
        path = resolve_path(root, raw_path)

        def list_entries() -> tuple[list[str], bool]:
            if not path.exists():
                raise ValueError(f"Path not found: {path}")
            if not path.is_dir():
                raise ValueError(f"Not a directory: {path}")
            entries = sorted(path.iterdir(), key=lambda item: (item.name.casefold(), item.name))
            limited = len(entries) > limit
            formatted = [
                f"{item.name}/" if item.is_dir() else item.name
                for item in entries[:limit]
            ]
            return formatted, limited

        entries, limit_reached = await asyncio.to_thread(list_entries)
        check_cancelled(signal)
        if not entries:
            return AgentToolResult(content=[TextContent(text="(empty directory)")])

        output, byte_limit_reached = truncate_bytes("\n".join(entries))
        notices: list[str] = []
        details: dict[str, JSONValue] = {"path": str(path)}
        if limit_reached:
            notices.append(f"{limit} entries limit reached. Use limit={limit * 2} for more")
            details["entry_limit_reached"] = limit
        if byte_limit_reached:
            notices.append(f"{DEFAULT_MAX_OUTPUT_BYTES // 1024}KB limit reached")
            details["byte_limit_reached"] = DEFAULT_MAX_OUTPUT_BYTES
        if notices:
            output += f"\n\n[{'. '.join(notices)}]"
        return AgentToolResult(content=[TextContent(text=output)], details=details)

    return AgentTool(
        name="ls",
        label="ls",
        description=(
            "List directory contents. Returns entries sorted alphabetically, with '/' suffix "
            f"for directories. Includes dotfiles. Output is truncated to {DEFAULT_LIMIT} entries "
            f"or {DEFAULT_MAX_OUTPUT_BYTES // 1024}KB."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to list (default: current directory)",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": f"Maximum entries (default: {DEFAULT_LIMIT})",
                },
            },
            "additionalProperties": False,
        },
        execute_fn=execute,
        prompt_snippet="List directory contents",
        prompt_guidelines=("Use ls to inspect a directory without invoking a shell.",),
    )


__all__ = ["create_ls_tool"]
