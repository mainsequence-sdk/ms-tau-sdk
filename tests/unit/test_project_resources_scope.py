"""The Agent reads its resources from `.tau` alone unless the host admits the repository's.

ADR 0025: the repository root's `AGENTS.md`, the `AGENTS.md` files above the workspace,
`.agents/AGENTS.md`, `.agents/skills` and `.agents/prompts` instruct the coding agents that
develop the repository. `TAU_INCLUDE_DEVELOPMENT_RESOURCES` reads them for an Agent that is a
coding agent over its own repository.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from tau_ai.fake import FakeProvider
from tau_coding import CodingSession, CodingSessionConfig
from tau_coding import session as tau_session

from ms_tau_sdk.errors import ConfigurationError
from ms_tau_sdk.resources import context_compat
from ms_tau_sdk.resources.context_compat import (
    EXCLUDED_CONTEXT_MESSAGE,
    install_project_context_filter,
)
from ms_tau_sdk.resources.loader import tau_resource_paths
from ms_tau_sdk.runtime.extensions import log_project_resources, project_resources_summary
from ms_tau_sdk.tools.skill_read import create_skill_read_tool
from ms_tau_sdk.tools.task_control import create_task_control_tools


class _MemoryStorage:
    def __init__(self) -> None:
        self.entries = []

    async def read_all(self):
        return list(self.entries)

    async def append(self, entry):
        self.entries.append(entry)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _skill(folder: Path, description: str) -> None:
    _write(
        folder / "SKILL.md",
        f"---\nname: {folder.name}\ndescription: {description}\n---\nBody of {folder.name}.\n",
    )


def _prompt(path: Path, description: str) -> None:
    _write(path, f"---\ndescription: {description}\n---\nTemplate {path.stem}.\n")


def _repository(tmp_path: Path) -> Path:
    """A repository whose Agent runs from the `service` workspace below its root."""
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    _write(repository / "AGENTS.md", "REPOSITORY ROOT AGENTS: run the pre-push gate.")
    workspace = repository / "service"
    _write(workspace / "AGENTS.md", "WORKSPACE AGENTS: you are a dual-mandate coding agent.")
    _write(workspace / ".agents/AGENTS.md", "DOT AGENTS: development notes.")
    _write(workspace / ".agents/agent_card.json", '{"name": "CRM assistant"}')
    _skill(workspace / ".agents/skills/ms_tau_sdk/tau-local-development", "SDK dev skill")
    _skill(workspace / ".agents/skills/crm-development", "Repository dev skill")
    _prompt(workspace / ".agents/prompts/release-checklist.md", "Development prompt")
    _write(workspace / ".tau/SYSTEM.md", "You are the CRM assistant.")
    _write(workspace / ".tau/AGENTS.md", "TAU AGENTS: answer in the customer's language.")
    _skill(workspace / ".tau/skills/crm-core", "Agent skill")
    _skill(workspace / ".tau/skills/solution-selling/crm-prospecting", "Grouped Agent skill")
    _prompt(workspace / ".tau/prompts/weekly-review.md", "Agent prompt")
    return workspace


async def _load_session(
    workspace: Path, state_home: Path, *, include_development_resources: bool = False
) -> CodingSession:
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
            resource_paths=tau_resource_paths(
                workspace,
                state_home=state_home,
                include_development_resources=include_development_resources,
            ),
            project_extensions_enabled=True,
            trust_override="approve",
        )
    )
    return session


def _relative(paths, workspace: Path) -> set[str]:
    return {
        Path(os.path.relpath(Path(path).resolve(), workspace.resolve())).as_posix()
        for path in paths
    }


@pytest.mark.asyncio
async def test_the_agent_reads_tau_alone_by_default(tmp_path):
    workspace = _repository(tmp_path)

    session = await _load_session(workspace, tmp_path / "state")

    assert [Path(f.path) for f in session.context_files] == [
        (workspace / ".tau/AGENTS.md").resolve()
    ]
    prompt = session.system_prompt
    assert prompt.startswith("You are the CRM assistant.")
    assert "TAU AGENTS" in prompt
    for development_text in ("REPOSITORY ROOT AGENTS", "WORKSPACE AGENTS", "DOT AGENTS"):
        assert development_text not in prompt
    assert {skill.name for skill in session.skills} == {"crm-core", "crm-prospecting"}
    assert "tau-local-development" not in prompt and "crm-development" not in prompt
    template_names = {template.name for template in session.prompt_templates}
    assert "weekly-review" in template_names
    assert "release-checklist" not in template_names
    excluded = {
        Path(diagnostic.path)
        for diagnostic in session.resource_diagnostics
        if diagnostic.kind == "context" and diagnostic.message == EXCLUDED_CONTEXT_MESSAGE
    }
    assert excluded == {
        (workspace.parent / "AGENTS.md").resolve(),
        (workspace / "AGENTS.md").resolve(),
    }
    read = next(tool for tool in session.tools if tool.name == "read")
    with pytest.raises(Exception, match="outside the available skills"):
        await read.execute("call-1", {"path": ".agents/skills/crm-development/SKILL.md"})

    # Development files added or changed before a reload stay outside the Agent's prompt.
    _write(workspace / "AGENTS.md", "WORKSPACE AGENTS: changed before reload.")
    _skill(workspace / ".agents/skills/added-dev-skill", "Added before reload")
    _skill(workspace / ".tau/skills/added-agent-skill", "Added before reload")
    await session.reload()
    assert "changed before reload" not in session.system_prompt
    assert {skill.name for skill in session.skills} == {
        "crm-core",
        "crm-prospecting",
        "added-agent-skill",
    }
    await session.aclose()


@pytest.mark.asyncio
async def test_the_host_admits_the_repository_as_a_coding_harness(tmp_path):
    workspace = _repository(tmp_path)

    session = await _load_session(workspace, tmp_path / "state", include_development_resources=True)

    assert _relative((f.path for f in session.context_files), workspace) == {
        "../AGENTS.md",
        "AGENTS.md",
        ".tau/AGENTS.md",
        ".agents/AGENTS.md",
    }
    prompt = session.system_prompt
    assert prompt.startswith("You are the CRM assistant.")
    for text in ("REPOSITORY ROOT AGENTS", "WORKSPACE AGENTS", "DOT AGENTS", "TAU AGENTS"):
        assert text in prompt
    assert {skill.name for skill in session.skills} == {
        "crm-core",
        "crm-prospecting",
        "tau-local-development",
        "crm-development",
    }
    template_names = {template.name for template in session.prompt_templates}
    assert {"weekly-review", "release-checklist"} <= template_names
    assert not [d for d in session.resource_diagnostics if d.kind == "context"]
    read = next(tool for tool in session.tools if tool.name == "read")
    result = await read.execute("call-1", {"path": ".agents/skills/crm-development/SKILL.md"})
    assert "Body of crm-development." in result.content[0].text
    await session.aclose()


def test_the_filter_is_locked_to_the_reviewed_tau_version(monkeypatch):
    monkeypatch.setattr(context_compat, "version", lambda name: "0.4.3")

    with pytest.raises(ConfigurationError, match=r"tau-ai==0\.4\.2; found 0\.4\.3"):
        install_project_context_filter()


def test_the_filter_refuses_a_changed_tau_discovery(monkeypatch):
    def unknown_discovery(paths=None):
        return (), ()

    monkeypatch.setattr(tau_session, "discover_project_context_with_diagnostics", unknown_discovery)

    with pytest.raises(ConfigurationError, match="compatibility review required"):
        install_project_context_filter()


@pytest.mark.asyncio
async def test_the_selected_resources_are_logged_and_inspectable(tmp_path):
    workspace = _repository(tmp_path)
    session = await _load_session(workspace, tmp_path / "state")

    summary = project_resources_summary(session, cwd=workspace, development_resources=False)
    assert summary == {
        "developmentResources": False,
        "systemPromptFiles": [".tau/SYSTEM.md"],
        "contextFiles": [".tau/AGENTS.md"],
        "skills": ["crm-core", "crm-prospecting"],
        "promptTemplates": sorted(template.name for template in session.prompt_templates),
    }

    with patch("ms_tau_sdk.runtime.extensions._resources_logger") as structured_logger:
        log_project_resources(
            session,
            session_uid="session-1",
            cwd=workspace,
            development_resources=False,
            phase="load",
        )

    structured_logger.info.assert_any_call(
        "runtime.project_resources.load.completed",
        session_uid="session-1",
        phase="load",
        development_resources=False,
        system_prompt_files=[".tau/SYSTEM.md"],
        context_files=[".tau/AGENTS.md"],
        skill_count=2,
        prompt_template_count=len(session.prompt_templates),
    )
    excluded = [
        call.kwargs
        for call in structured_logger.info.call_args_list
        if call.args == ("runtime.project_resources.diagnostic",)
        and call.kwargs["resource_kind"] == "context"
    ]
    assert {entry["resource_path"] for entry in excluded} == {"../AGENTS.md", "AGENTS.md"}
    assert all(entry["message"] == EXCLUDED_CONTEXT_MESSAGE for entry in excluded)
    assert all(str(workspace) not in str(entry) for entry in excluded)
    await session.aclose()
