"""Tau's `read` tool limited to the files of discovered skills."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from pathlib import Path

from tau_agent.tools import AgentTool
from tau_coding.skills import Skill
from tau_coding.tools import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MAX_OUTPUT_LINES,
    DEFAULT_READ_OPERATIONS,
    ToolInputError,
    create_read_tool_definition,
)


def create_skill_read_tool(*, cwd: Path, skills: Callable[[], Iterable[Skill]]) -> AgentTool:
    """Create a `read` tool that serves only the files of the session's discovered skills.

    Tau lists skills in the system prompt only when a tool named `read` exists. With the coding
    tools excluded, this tool keeps project skills available without exposing any other file.
    `skills` is called on every read, so a Tau reload changes what the tool serves.
    """

    def validate_path(path: Path) -> None:
        resolved = path.resolve()
        if not any(
            resolved == skill.path.resolve() or resolved.is_relative_to(skill.path.parent.resolve())
            for skill in skills()
        ):
            raise ToolInputError(f"Path is outside the available skills: {path}")
        DEFAULT_READ_OPERATIONS.validate_path(path)

    definition = create_read_tool_definition(
        cwd=cwd,
        operations=dataclasses.replace(DEFAULT_READ_OPERATIONS, validate_path=validate_path),
    )
    return dataclasses.replace(
        definition,
        description=(
            "Read a file of one of the available skills: its SKILL.md or another file in its "
            "skill directory, such as a referenced document. Every other path is rejected. "
            f"Output is truncated to {DEFAULT_MAX_OUTPUT_LINES} lines or "
            f"{DEFAULT_MAX_OUTPUT_BYTES // 1024}KB (whichever is hit first). Use offset/limit "
            "for large files. When you need the full file, continue with offset until complete."
        ),
        prompt_snippet="Read the files of the available skills",
        prompt_guidelines=(),
    ).to_agent_tool()
