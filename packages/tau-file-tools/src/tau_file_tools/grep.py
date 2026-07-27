"""Tau ripgrep-backed content search tool."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Mapping
from pathlib import Path

from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue

from ._common import (
    DEFAULT_MAX_OUTPUT_BYTES,
    bool_arg,
    check_cancelled,
    communicate_with_cancellation,
    nonnegative_int_arg,
    positive_int_arg,
    resolve_path,
    root_path,
    string_arg,
    truncate_bytes,
)

DEFAULT_LIMIT = 100
MAX_LINE_LENGTH = 2_000


def create_grep_tool(*, cwd: str | Path | None = None) -> AgentTool:
    """Create a Tau tool that searches text with ripgrep."""
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
        glob = string_arg(arguments, "glob", required=False)
        ignore_case = bool_arg(arguments, "ignoreCase")
        literal = bool_arg(arguments, "literal")
        context = nonnegative_int_arg(arguments, "context", default=0)
        limit = positive_int_arg(arguments, "limit", default=DEFAULT_LIMIT)
        search_path = resolve_path(root, raw_path)
        if not search_path.exists():
            raise ValueError(f"Path not found: {search_path}")

        ripgrep = shutil.which("rg")
        if ripgrep is None:
            raise RuntimeError("ripgrep (rg) is required but is not available on PATH")

        command = [ripgrep, "--json", "--line-number", "--color=never", "--hidden"]
        if ignore_case:
            command.append("--ignore-case")
        if literal:
            command.append("--fixed-strings")
        if glob:
            command.extend(["--glob", glob])
        command.extend([pattern, str(search_path)])

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await communicate_with_cancellation(process, signal)
        check_cancelled(signal)
        if process.returncode not in {0, 1}:
            message = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(message or f"ripgrep exited with code {process.returncode}")

        matches: list[tuple[Path, int]] = []
        for raw_line in stdout.splitlines():
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event.get("data", {})
            path_text = data.get("path", {}).get("text")
            line_number = data.get("line_number")
            if isinstance(path_text, str) and isinstance(line_number, int):
                matches.append((Path(path_text), line_number))
            if len(matches) > limit:
                break

        if not matches:
            return AgentToolResult(content=[TextContent(text="No matches found")])

        lines_truncated = False
        blocks: list[str] = []
        is_directory = search_path.is_dir()
        for file_path, line_number in matches[:limit]:
            try:
                file_lines = file_path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                file_lines = []
            display_path = (
                file_path.relative_to(search_path).as_posix()
                if is_directory and file_path.is_relative_to(search_path)
                else file_path.name
            )
            if not file_lines:
                blocks.append(f"{display_path}:{line_number}: (unable to read file)")
                continue
            start = max(1, line_number - context)
            end = min(len(file_lines), line_number + context)
            for current in range(start, end + 1):
                line = file_lines[current - 1]
                if len(line) > MAX_LINE_LENGTH:
                    line = line[:MAX_LINE_LENGTH]
                    lines_truncated = True
                separator = ":" if current == line_number else "-"
                blocks.append(f"{display_path}{separator}{current}{separator} {line}")

        output, byte_limit_reached = truncate_bytes("\n".join(blocks))
        notices: list[str] = []
        details: dict[str, JSONValue] = {
            "path": str(search_path),
            "pattern": pattern,
            "match_count": min(len(matches), limit),
        }
        if len(matches) > limit:
            notices.append(f"{limit} matches limit reached. Refine pattern or increase limit")
            details["match_limit_reached"] = limit
        if byte_limit_reached:
            notices.append(f"{DEFAULT_MAX_OUTPUT_BYTES // 1024}KB limit reached")
            details["byte_limit_reached"] = DEFAULT_MAX_OUTPUT_BYTES
        if lines_truncated:
            notices.append(f"Some lines truncated to {MAX_LINE_LENGTH} characters")
            details["lines_truncated"] = True
        if notices:
            output += f"\n\n[{'. '.join(notices)}]"
        return AgentToolResult(content=[TextContent(text=output)], details=details)

    return AgentTool(
        name="grep",
        label="grep",
        description=(
            "Search file contents for a regex or literal pattern. Returns file paths and line "
            f"numbers. Respects .gitignore. Output is truncated to {DEFAULT_LIMIT} matches or "
            f"{DEFAULT_MAX_OUTPUT_BYTES // 1024}KB."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Search regex or literal string",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search (default: current directory)",
                },
                "glob": {"type": "string", "description": "Optional file glob, such as '*.py'"},
                "ignoreCase": {"type": "boolean", "default": False},
                "literal": {"type": "boolean", "default": False},
                "context": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "default": DEFAULT_LIMIT},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        execute_fn=execute,
        prompt_snippet="Search file contents",
        prompt_guidelines=("Use grep for content discovery instead of shell pipelines.",),
    )


__all__ = ["create_grep_tool"]
