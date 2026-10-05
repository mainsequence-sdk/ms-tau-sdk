from pathlib import Path

import pytest
from tau_ai.fake import FakeProvider
from tau_coding import CodingSession, CodingSessionConfig
from tau_coding.skills import Skill
from tau_coding.tools import ToolInputError

from ms_tau_sdk.errors import ConfigurationError
from ms_tau_sdk.resources.loader import tau_resource_paths
from ms_tau_sdk.runtime.extensions import validate_tool_catalog
from ms_tau_sdk.runtime.session import ActiveSessionRuntime
from ms_tau_sdk.tools.skill_read import create_skill_read_tool
from ms_tau_sdk.tools.task_control import create_task_control_tools

SKILL_MD = """---
name: pg-guide
description: Postgres query guide
---
Follow references/query-authoring.md.
"""


class _MemoryStorage:
    def __init__(self) -> None:
        self.entries = []

    async def read_all(self):
        return list(self.entries)

    async def append(self, entry):
        self.entries.append(entry)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    skill = workspace / ".tau/skills/pg-guide"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (skill / "references/query-authoring.md").write_text("Use time_bucket.", encoding="utf-8")
    (workspace / "app.py").write_text("WORKSPACE = True", encoding="utf-8")
    return workspace


async def _load_session(workspace: Path, state_home: Path) -> CodingSession:
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            provider_name="fake",
            model="fake-model",
            storage=_MemoryStorage(),
            cwd=workspace,
            tools=[
                create_skill_read_tool(cwd=workspace, skills=lambda: session.skills),
                *create_task_control_tools(),
            ],
            resource_paths=tau_resource_paths(workspace, state_home=state_home),
            project_extensions_enabled=True,
            trust_override="approve",
        )
    )
    return session


async def test_skill_read_serves_only_files_inside_discovered_skills(tmp_path):
    workspace = _workspace(tmp_path)
    skill_dir = workspace / ".tau/skills/pg-guide"
    outside = tmp_path / "outside.txt"
    outside.write_text("credential", encoding="utf-8")
    (skill_dir / "references/escape.md").symlink_to(outside)
    skills = [Skill(name="pg-guide", path=skill_dir / "SKILL.md", content=SKILL_MD)]
    read = create_skill_read_tool(cwd=workspace, skills=lambda: skills)

    result = await read.execute("call-1", {"path": str(skill_dir / "SKILL.md")})
    assert "Follow references/query-authoring.md." in result.content[0].text
    result = await read.execute(
        "call-2", {"path": ".tau/skills/pg-guide/references/query-authoring.md"}
    )
    assert "Use time_bucket." in result.content[0].text

    for path in (
        "app.py",
        str(outside),
        ".tau/skills/pg-guide/../../../../outside.txt",
        str(skill_dir / "references/escape.md"),
        str(tmp_path / "missing.txt"),
    ):
        with pytest.raises(ToolInputError, match="outside the available skills"):
            await read.execute("call-3", {"path": path})

    skills.clear()
    with pytest.raises(ToolInputError, match="outside the available skills"):
        await read.execute("call-4", {"path": str(skill_dir / "SKILL.md")})


@pytest.mark.parametrize("project_system_prompt", [False, True])
async def test_tau_lists_skills_when_skill_read_replaces_base_tools(
    tmp_path, project_system_prompt
):
    workspace = _workspace(tmp_path)
    if project_system_prompt:
        (workspace / ".tau/SYSTEM.md").write_text("Project instructions.", encoding="utf-8")

    session = await _load_session(workspace, tmp_path / "state")

    prompt = session.system_prompt
    assert "<available_skills>" in prompt
    assert "<name>pg-guide</name>" in prompt
    assert prompt.startswith("Project instructions.") is project_system_prompt
    read = next(tool for tool in session.tools if tool.name == "read")
    result = await read.execute(
        "call-1", {"path": str(workspace / ".tau/skills/pg-guide/references/query-authoring.md")}
    )
    assert "Use time_bucket." in result.content[0].text
    await session.aclose()


async def test_project_cannot_replace_skill_read_after_tau_reload(tmp_path):
    workspace = _workspace(tmp_path)
    extension = workspace / ".tau/extensions/project_tool/extension.py"
    extension.parent.mkdir(parents=True)

    def source(tool_name):
        return f'''from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult


async def execute(tool_call_id, arguments, signal=None, on_update=None):
    return AgentToolResult(content=[TextContent(text="project result")])


def setup(tau):
    tau.register_tool(AgentTool(
        name="{tool_name}",
        label="Project Tool",
        description="Project-owned tool",
        parameters={{"type": "object", "properties": {{}}}},
        execute_fn=execute,
    ))
'''

    extension.write_text(source("project_tool"), encoding="utf-8")
    session = await _load_session(workspace, tmp_path / "state")
    sdk_names = frozenset({"read", "task_request_input", "task_request_authorization"})

    def check_catalog(current_session):
        validate_tool_catalog(
            current_session,
            sdk_tool_names=sdk_names,
            require_complete_project_catalog=True,
            reserved_sdk_tool_names=frozenset({"read"}),
        )

    runtime = ActiveSessionRuntime(
        session_uid="session-1",
        holder_id="holder",
        coding_session=session,
        storage=_MemoryStorage(),
        provider=object(),
        validate_tool_catalog=check_catalog,
    )
    check_catalog(session)
    assert {tool.name for tool in session.tools} == sdk_names | {"project_tool"}
    extension.write_text(source("read"), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="cannot replace reserved SDK tools: read"):
        await runtime.reload()
    await session.aclose()
