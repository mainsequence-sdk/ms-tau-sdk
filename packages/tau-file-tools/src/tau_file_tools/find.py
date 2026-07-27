"""Tau glob-based file discovery tool."""

from __future__ import annotations

import asyncio
import fnmatch
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

DEFAULT_LIMIT = 1_000
EXCLUDED_DIRECTORIES = {".git", "node_modules"}


def _matches(relative_path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(relative_path, pattern) or fnmatch.fnmatchcase(
        Path(relative_path).name, pattern
    )


def create_find_tool(*, cwd: str | Path | None = None) -> AgentTool:
    """Create a Tau tool that finds files and directories by glob."""
    root = root_path(cwd)

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, on_update
        check_cancelled(signal)
        pattern = string_arg(arguments, "pattern", required=True)
        raw_path = string_arg(arguments, "path", required=False)
        limit = positive_int_arg(arguments, "limit", default=DEFAULT_LIMIT)
        search_path = resolve_path(root, raw_path)

        def collect() -> tuple[list[str], bool]:
            if not search_path.exists():
                raise ValueError(f"Path not found: {search_path}")
            if not search_path.is_dir():
                raise ValueError(f"Not a directory: {search_path}")

            matches: list[str] = []
            limit_reached = False
            for item in search_path.rglob("*"):
                relative = item.relative_to(search_path)
                if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
                    continue
                display = relative.as_posix() + ("/" if item.is_dir() else "")
                candidate = display.removesuffix("/")
                if not _matches(candidate, pattern):
                    continue
                if len(matches) >= limit:
                    limit_reached = True
                    break
                matches.append(display)
            return sorted(matches, key=str.casefold), limit_reached

        matches, limit_reached = await asyncio.to_thread(collect)
        check_cancelled(signal)
        if not matches:
            return AgentToolResult(
                content=[TextContent(text="No files found matching pattern")],
                details={"path": str(search_path), "pattern": pattern},
            )

        output, byte_limit_reached = truncate_bytes("\n".join(matches))
        notices: list[str] = []
        details: dict[str, JSONValue] = {"path": str(search_path), "pattern": pattern}
        if limit_reached:
            notices.append(f"{limit} results limit reached. Refine pattern or increase limit")
            details["result_limit_reached"] = limit
        if byte_limit_reached:
            notices.append(f"{DEFAULT_MAX_OUTPUT_BYTES // 1024}KB limit reached")
            details["byte_limit_reached"] = DEFAULT_MAX_OUTPUT_BYTES
        if notices:
            output += f"\n\n[{'. '.join(notices)}]"
        return AgentToolResult(content=[TextContent(text=output)], details=details)

    return AgentTool(
        name="find",
        label="find",
        description=(
            "Search for files by glob pattern. Returns matching paths relative to the search "
            f"directory. Output is truncated to {DEFAULT_LIMIT} results or "
            f"{DEFAULT_MAX_OUTPUT_BYTES // 1024}KB."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Glob such as '*.py', '**/*.json', or 'src/**/*.py'",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search (default: current directory)",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": f"Maximum results (default: {DEFAULT_LIMIT})",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        execute_fn=execute,
        prompt_snippet="Find files by glob",
        prompt_guidelines=("Use find instead of shell traversal for file discovery.",),
    )


__all__ = ["create_find_tool"]
