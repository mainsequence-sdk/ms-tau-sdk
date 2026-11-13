"""Each Agent of a repository loads its own prompt, skills and extensions from `.tau` (ADR 0026).

The platform resolves `harness_agent.spec.runtime_resources` from the Agent's workflow declaration
and returns it at session bootstrap. These tests load real Tau sessions through the runtime
manager, so they cover what the model is given, not only the configuration the SDK builds.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from structlog.testing import capture_logs
from tau_ai.fake import FakeProvider
from tau_coding.tools import ToolInputError

from ms_tau_sdk.backend.models import (
    AgentSession,
    ProviderControl,
    ProviderCredential,
    RuntimeLease,
    RuntimeState,
    SessionEntryList,
    TauRuntimeBootstrap,
)
from ms_tau_sdk.errors import ConfigurationError
from ms_tau_sdk.resources.selection import (
    RUNTIME_RESOURCES_SCHEMA_VERSION,
    parse_runtime_resources,
)
from ms_tau_sdk.runtime.extensions import project_resources_summary, validate_tool_catalog
from ms_tau_sdk.runtime.manager import RUNTIME_CAPABILITIES, SessionRuntimeManager
from ms_tau_sdk.settings import TauSDKSettings

FULL_PROMPT = "You are the full assistant."
RESEARCH_PROMPT = "You are the research Agent. You never trade."
SHARED_APPEND = "SHARED APPEND: cite every source."
SHARED_AGENTS = "SHARED AGENTS: answer in the customer's language."
TASK_CONTROLS = ["task_request_authorization", "task_request_input"]

_EXTENSION = """
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult


async def _execute(tool_call_id, arguments, signal=None, on_update=None):
    return AgentToolResult(content=[TextContent(text="ok")])


def setup(tau):
    for name in {names!r}:
        tau.register_tool(
            AgentTool(
                name=name,
                label=name,
                description=name,
                parameters={{"type": "object", "properties": {{}}}},
                execute_fn=_execute,
            )
        )
"""


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _skill(folder):
    _write(
        folder / "SKILL.md",
        f"---\nname: {folder.name}\ndescription: The {folder.name} skill.\n---\nBody.\n",
    )


def _workspace(tmp_path):
    """One repository shared by a full assistant and a research Agent."""
    workspace = tmp_path / "workspace"
    _write(workspace / ".tau/SYSTEM.md", FULL_PROMPT)
    _write(workspace / ".tau/system_prompts/research.md", RESEARCH_PROMPT)
    _write(workspace / ".tau/APPEND_SYSTEM.md", SHARED_APPEND)
    _write(workspace / ".tau/AGENTS.md", SHARED_AGENTS)
    _skill(workspace / ".tau/skills/market-research")
    _skill(workspace / ".tau/skills/trading/order-entry")
    _write(
        workspace / ".tau/extensions/research_tools/extension.py",
        _EXTENSION.format(names=["search_filings", "summarize_filing"]),
    )
    _write(
        workspace / ".tau/extensions/trading_tools.py",
        _EXTENSION.format(names=["place_order"]),
    )
    return workspace


def _settings(workspace, state_home, *, tool_only=True):
    return TauSDKSettings(
        _env_file=None,
        runtime_credential_id="credential-id",
        workspace=workspace,
        tau_state_home=state_home,
        startup_dependencies_enabled=False,
        exclude_base_tools=tool_only,
        exclude_mainsequence_mcp=True,
    )


def _bootstrap(runtime_resources=None, *, session_uid="session-1"):
    extra = {} if runtime_resources is None else {"runtime_resources": runtime_resources}
    return TauRuntimeBootstrap(
        session=AgentSession(
            uid=session_uid,
            agent_uid="agent-1",
            harness="tau",
            harness_protocol="tau-session-v1",
            harness_version="1",
            active_provider="fake",
            active_model="fake-model",
        ),
        lease=RuntimeLease(
            lease_token=f"lease-{session_uid}",
            holder_id="holder",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ),
        runtime_state=RuntimeState(
            harness="tau",
            harness_protocol="tau-session-v1",
            harness_version="1",
            runtime_activity="loading",
            activity_revision=1,
            activity_sequence=0,
            runtime_capabilities=RUNTIME_CAPABILITIES,
        ),
        history=SessionEntryList(entries=[], next_sequence=0),
        provider_credentials={},
        provider_control=ProviderControl(
            schema_version=1,
            catalog_digest=f"sha256:{'0' * 64}",
            provider="fake",
            model={
                "model": "fake-model",
                "api": "openai-completions",
                "input": ["text"],
                "reasoning": False,
                "thinking_levels": [],
            },
        ),
        runtime_capabilities=RUNTIME_CAPABILITIES,
        **extra,
    )


def _manager(workspace, state_home, *bootstraps, tool_only=True):
    backend = AsyncMock()
    backend.auth = Mock()
    backend.bootstrap_tau_runtime.side_effect = list(bootstraps)
    backend.provider_credential_from_hydration = Mock(
        return_value=ProviderCredential(provider="fake", api_key="secret")
    )
    backend.patch_runtime_activity.return_value = RuntimeState(
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="1",
        runtime_activity="idle",
        activity_revision=2,
        activity_sequence=1,
        applied=True,
        runtime_capabilities=RUNTIME_CAPABILITIES,
    )
    providers = Mock()
    providers.for_session_credential.return_value = SimpleNamespace(
        provider=FakeProvider([]),
        name="fake",
        model="fake-model",
        thinking_level=None,
    )
    manager = SessionRuntimeManager(
        settings=_settings(workspace, state_home, tool_only=tool_only),
        backend=backend,
        providers=providers,
    )
    return manager, backend


async def _load(tmp_path, runtime_resources, *, workspace=None):
    workspace = workspace or _workspace(tmp_path)
    manager, backend = _manager(workspace, tmp_path / "state", _bootstrap(runtime_resources))
    runtime = await manager.get("session-1")
    return manager, backend, runtime


def _project_tools(runtime):
    return sorted(
        tool.name
        for tool in runtime.coding_session.tools
        if tool.name in runtime.coding_session.extension_tool_sources
    )


RESEARCH_AGENT = {
    "system_prompt": ".tau/system_prompts/research.md",
    "skills": ["market-research"],
    "extensions": ["research_tools"],
}


@pytest.mark.asyncio
async def test_without_a_selection_the_agent_loads_the_whole_tau_directory(tmp_path):
    manager, backend, runtime = await _load(tmp_path, None)

    request = backend.bootstrap_tau_runtime.await_args.args[1]
    assert request.supported_runtime_resources_schema_versions == [RUNTIME_RESOURCES_SCHEMA_VERSION]
    session = runtime.coding_session
    assert session.system_prompt.startswith(FULL_PROMPT)
    assert {skill.name for skill in session.skills} == {"market-research", "order-entry"}
    assert _project_tools(runtime) == ["place_order", "search_filings", "summarize_filing"]
    assert runtime.runtime_resources is None
    await manager.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(("relays", "announced"), [(True, [1]), (False, [])])
async def test_the_bootstrap_announces_the_model_relay_only_when_hosted(
    tmp_path, relays, announced
):
    # ADR 0030: a hosted runtime lists the relay and parses a relayed entry; local mode does not.
    workspace = _workspace(tmp_path)
    manager, backend = _manager(workspace, tmp_path / "state", _bootstrap(None))
    backend.relays_model_calls = relays
    await manager.get("session-1")

    request = backend.bootstrap_tau_runtime.await_args.args[1]
    assert request.supported_model_relay_versions == announced
    assert backend.provider_credential_from_hydration.call_args.kwargs == {"relayed": relays}
    await manager.aclose()


@pytest.mark.asyncio
async def test_two_agents_of_one_repository_load_different_prompts_skills_and_tools(tmp_path):
    workspace = _workspace(tmp_path)
    full = {
        "system_prompt": ".tau/SYSTEM.md",
        "skills": ["market-research", "order-entry"],
        "extensions": ["research_tools", "trading_tools.py"],
    }
    full_manager, _backend, full_runtime = await _load(tmp_path, full, workspace=workspace)
    research_manager, _backend, research_runtime = await _load(
        tmp_path, RESEARCH_AGENT, workspace=workspace
    )

    full_prompt = full_runtime.coding_session.system_prompt
    research_prompt = research_runtime.coding_session.system_prompt
    assert full_prompt.startswith(FULL_PROMPT)
    assert research_prompt.startswith(RESEARCH_PROMPT)
    assert FULL_PROMPT not in research_prompt
    # The branch-wide instructions apply to every Agent of the repository.
    for prompt in (full_prompt, research_prompt):
        assert SHARED_APPEND in prompt
        assert SHARED_AGENTS in prompt
    assert {skill.name for skill in research_runtime.coding_session.skills} == {"market-research"}
    assert "order-entry" not in research_prompt
    assert _project_tools(full_runtime) == ["place_order", "search_filings", "summarize_filing"]
    assert _project_tools(research_runtime) == ["search_filings", "summarize_filing"]
    assert sorted(tool.name for tool in research_runtime.coding_session.tools) == sorted(
        ["read", *TASK_CONTROLS, "search_filings", "summarize_filing"]
    )
    await full_manager.aclose()
    await research_manager.aclose()


@pytest.mark.asyncio
async def test_each_omitted_field_keeps_todays_behavior_for_that_resource(tmp_path):
    manager, _backend, runtime = await _load(tmp_path, {"extensions": ["trading_tools.py"]})

    session = runtime.coding_session
    assert session.system_prompt.startswith(FULL_PROMPT)
    assert {skill.name for skill in session.skills} == {"market-research", "order-entry"}
    assert _project_tools(runtime) == ["place_order"]
    await manager.aclose()


@pytest.mark.asyncio
async def test_empty_lists_load_no_skills_and_no_project_extensions(tmp_path):
    manager, _backend, runtime = await _load(tmp_path, {"skills": [], "extensions": []})

    session = runtime.coding_session
    assert session.skills == ()
    assert _project_tools(runtime) == []
    assert sorted(tool.name for tool in session.tools) == sorted(["read", *TASK_CONTROLS])
    assert runtime.project_extension_state.loaded_extension_count == 0
    await manager.aclose()


@pytest.mark.asyncio
async def test_the_selected_skill_is_the_only_one_the_skill_read_tool_serves(tmp_path):
    manager, _backend, runtime = await _load(tmp_path, {"skills": ["order-entry"]})

    read = next(tool for tool in runtime.coding_session.tools if tool.name == "read")
    served = await read.execute("call-1", {"path": ".tau/skills/trading/order-entry/SKILL.md"})
    assert "Body." in served.content[0].text
    with pytest.raises(ToolInputError, match="outside the available skills"):
        await read.execute("call-2", {"path": ".tau/skills/market-research/SKILL.md"})
    await manager.aclose()


@pytest.mark.asyncio
async def test_reload_keeps_the_selection(tmp_path):
    manager, _backend, runtime = await _load(tmp_path, RESEARCH_AGENT)

    await runtime.reload()

    session = runtime.coding_session
    assert session.system_prompt.startswith(RESEARCH_PROMPT)
    assert {skill.name for skill in session.skills} == {"market-research"}
    assert _project_tools(runtime) == ["search_filings", "summarize_filing"]
    await manager.aclose()


@pytest.mark.asyncio
async def test_a_skill_removed_before_reload_fails_the_reload(tmp_path):
    workspace = _workspace(tmp_path)
    manager, _backend, runtime = await _load(tmp_path, RESEARCH_AGENT, workspace=workspace)
    (workspace / ".tau/skills/market-research/SKILL.md").unlink()

    with pytest.raises(ConfigurationError, match="Selected skills did not load: market-research"):
        await runtime.reload()
    await manager.aclose()


@pytest.mark.asyncio
async def test_selected_extensions_pass_the_tool_only_catalog_check(tmp_path):
    manager, _backend, runtime = await _load(tmp_path, RESEARCH_AGENT)

    # ADR 0011: with base tools and Main Sequence MCP excluded, every tool must be the project's.
    metadata = runtime.coding_session.extension_runtime.extension_metadata
    assert {item.source for item in metadata if item.name == "research_tools"} == {"explicit"}
    validate_tool_catalog(
        runtime.coding_session,
        sdk_tool_names=frozenset({"read", *TASK_CONTROLS}),
        require_complete_project_catalog=True,
        reserved_sdk_tool_names=frozenset({"read"}),
    )
    await manager.aclose()


@pytest.mark.asyncio
async def test_the_selection_is_logged_and_inspectable(tmp_path):
    workspace = _workspace(tmp_path)
    with capture_logs() as logs:
        manager, _backend, runtime = await _load(tmp_path, RESEARCH_AGENT, workspace=workspace)

    expected = {
        "systemPrompt": ".tau/system_prompts/research.md",
        "skills": ["market-research"],
        "extensions": [".tau/extensions/research_tools"],
    }
    loaded = next(log for log in logs if log["event"] == "runtime.project_resources.load.completed")
    assert loaded["runtime_resources"] == expected
    assert loaded["system_prompt_files"][0] == ".tau/system_prompts/research.md"
    assert ".tau/SYSTEM.md" not in loaded["system_prompt_files"]
    assert str(workspace) not in str(loaded)
    summary = project_resources_summary(
        runtime.coding_session,
        cwd=workspace,
        development_resources=False,
        selection=runtime.runtime_resources.summary(),
    )
    assert summary["selection"] == expected
    assert summary["skills"] == ["market-research"]
    await manager.aclose()


@pytest.mark.asyncio
async def test_a_local_inspection_counts_selected_extensions_as_project_extensions(tmp_path):
    workspace = _workspace(tmp_path)
    manager, _backend, _runtime = await _load(tmp_path, RESEARCH_AGENT, workspace=workspace)
    manager.settings.local_mode = True
    manager.backend.get_agent_card.return_value = SimpleNamespace(
        agent_uid="agent-1", agent_session_uid="session-1", agent_card={}
    )

    inspection = await manager.local_agent_inspection("session-1")

    extension = next(item for item in inspection["extensions"] if item["name"] == "research_tools")
    assert extension["origin"] == "project"
    assert extension["entryPath"] == ".tau/extensions/research_tools/extension.py"
    tool = next(item for item in inspection["tools"] if item["name"] == "search_filings")
    assert tool["category"] == "project_extension"
    assert tool["testable"] is True
    source = manager.local_extension_source("session-1", extension["sourceUid"])
    assert source["path"] == ".tau/extensions/research_tools/extension.py"
    assert inspection["resources"]["selection"]["extensions"] == [".tau/extensions/research_tools"]
    await manager.aclose()


def _break_prompt(workspace):
    (workspace / ".tau/system_prompts/research.md").unlink()


def _binary_prompt(workspace):
    (workspace / ".tau/system_prompts/research.md").write_bytes(b"\xff\xfe not utf-8")


def _linked_prompt(workspace):
    target = workspace / ".tau/system_prompts/research.md"
    target.rename(workspace / "elsewhere.md")
    os.symlink(workspace / "elsewhere.md", target)


def _linked_prompt_folder(workspace):
    folder = workspace / ".tau/system_prompts"
    folder.rename(workspace / "prompts_elsewhere")
    os.symlink(workspace / "prompts_elsewhere", folder)


def _prompt_folder(workspace):
    target = workspace / ".tau/system_prompts/research.md"
    target.unlink()
    target.mkdir()


@pytest.mark.parametrize(
    ("breakage", "message"),
    [
        (_break_prompt, "does not exist"),
        (_binary_prompt, "is not UTF-8"),
        (_linked_prompt, "must not be a symbolic link"),
        (_linked_prompt_folder, "must not be a symbolic link"),
        (_prompt_folder, "is not a file"),
    ],
)
@pytest.mark.asyncio
async def test_an_unusable_system_prompt_fails_the_load_and_releases_the_lease(
    tmp_path, breakage, message
):
    workspace = _workspace(tmp_path)
    breakage(workspace)
    manager, backend = _manager(workspace, tmp_path / "state", _bootstrap(RESEARCH_AGENT))

    with (
        patch("ms_tau_sdk.runtime.manager.CodingSession.load") as load,
        pytest.raises(ConfigurationError, match=message),
    ):
        await manager.get("session-1")

    load.assert_not_called()
    backend.release_runtime_lease.assert_awaited_once()
    assert backend.release_runtime_lease.await_args.args[1].reason == "runtime_load_failed"
    await manager.aclose()


def _linked_extension(workspace):
    root = workspace / ".tau/extensions"
    (root / "research_tools").rename(workspace / "research_tools")
    os.symlink(workspace / "research_tools", root / "research_tools")


def _not_an_extension(workspace):
    (workspace / ".tau/extensions/research_tools/extension.py").rename(
        workspace / ".tau/extensions/research_tools/helpers.py"
    )


@pytest.mark.parametrize(
    ("runtime_resources", "breakage", "message"),
    [
        ({"skills": ["portfolio"]}, None, "Selected skills did not load: portfolio"),
        ({"extensions": ["missing_tools"]}, None, "missing_tools does not exist"),
        ({"extensions": ["research_tools"]}, _linked_extension, "must not be a symbolic link"),
        ({"extensions": ["research_tools"]}, _not_an_extension, "is not an extension Tau"),
    ],
)
@pytest.mark.asyncio
async def test_a_selection_that_names_nothing_loadable_fails_the_load(
    tmp_path, runtime_resources, breakage, message
):
    workspace = _workspace(tmp_path)
    if breakage is not None:
        breakage(workspace)
    manager, backend = _manager(workspace, tmp_path / "state", _bootstrap(runtime_resources))

    with pytest.raises(ConfigurationError, match=message):
        await manager.get("session-1")

    backend.release_runtime_lease.assert_awaited_once()
    assert manager._runtimes == {}
    await manager.aclose()


@pytest.mark.asyncio
async def test_an_extension_that_fails_to_import_fails_the_load(tmp_path):
    workspace = _workspace(tmp_path)
    _write(workspace / ".tau/extensions/trading_tools.py", "raise RuntimeError('broken')\n")
    # With Tau's base tools the ADR 0011 tool-only check does not apply; the selection check does.
    manager, backend = _manager(
        workspace,
        tmp_path / "state",
        _bootstrap({"extensions": ["trading_tools.py"]}),
        tool_only=False,
    )

    with pytest.raises(ConfigurationError, match="Selected extensions did not load: trading"):
        await manager.get("session-1")

    backend.release_runtime_lease.assert_awaited_once()
    await manager.aclose()


@pytest.mark.parametrize(
    ("runtime_resources", "message"),
    [
        ({"system_prompt": ".tau/SYSTEM.md", "agent": "x"}, "Extra inputs are not permitted"),
        (["research_tools"], "must be an object"),
        ({"system_prompt": "SYSTEM.md"}, "must be a file under .tau/"),
        ({"system_prompt": "/srv/.tau/SYSTEM.md"}, "plain workspace-relative path"),
        ({"system_prompt": ".tau/../SYSTEM.md"}, "plain workspace-relative path"),
        ({"system_prompt": ".tau//SYSTEM.md"}, "plain workspace-relative path"),
        ({"system_prompt": ".tau\\SYSTEM.md"}, "plain workspace-relative path"),
        ({"system_prompt": ".tau/skills/market-research/SKILL.md"}, "must be outside"),
        ({"system_prompt": ".tau/prompts/research.md"}, "must be outside"),
        ({"system_prompt": ".tau/extensions/README.md"}, "must be outside"),
        ({"system_prompt": ".tau/system_prompts/research.txt"}, "Markdown"),
        ({"system_prompt": 5}, "valid string"),
        ({"skills": "market-research"}, "valid tuple"),
        ({"skills": ["trading/order-entry"]}, "one folder or file name"),
        ({"skills": [".."]}, "one folder or file name"),
        ({"skills": [""]}, "one folder or file name"),
        ({"skills": [3]}, "valid string"),
        ({"skills": ["market-research", "market-research"]}, "listed more than once"),
        ({"extensions": [".tau/extensions/research_tools"]}, "one folder or file name"),
        ({"extensions": ["_private.py"]}, "must not start with"),
        ({"extensions": [".hidden"]}, "must not start with"),
        ({"extensions": ["a.py", "a.py"]}, "listed more than once"),
    ],
)
@pytest.mark.asyncio
async def test_a_malformed_selection_fails_the_load_and_releases_the_lease(
    tmp_path, runtime_resources, message
):
    workspace = _workspace(tmp_path)
    manager, backend = _manager(workspace, tmp_path / "state", _bootstrap(runtime_resources))

    with (
        patch("ms_tau_sdk.runtime.manager.CodingSession.load") as load,
        pytest.raises(ConfigurationError, match=message),
    ):
        await manager.get("session-1")

    load.assert_not_called()
    backend.release_runtime_lease.assert_awaited_once()
    await manager.aclose()


def test_null_fields_and_an_empty_object_select_everything():
    assert parse_runtime_resources(None) is None
    for raw in ({}, {"system_prompt": None, "skills": None, "extensions": None}):
        selection = parse_runtime_resources(raw)
        assert selection is not None
        assert (selection.system_prompt, selection.skills, selection.extensions) == (
            None,
            None,
            None,
        )


@pytest.mark.asyncio
async def test_one_failed_entry_of_a_selected_extension_folder_fails_the_load(tmp_path):
    workspace = _workspace(tmp_path)
    bundle = workspace / ".tau/extensions/bundle"
    _write(bundle / "pyproject.toml", '[tool.tau]\nextensions = ["good.py", "bad.py"]\n')
    _write(bundle / "good.py", _EXTENSION.format(names=["good_tool"]))
    _write(bundle / "bad.py", "raise RuntimeError('broken')\n")
    manager, backend = _manager(
        workspace, tmp_path / "state", _bootstrap({"extensions": ["bundle"]}), tool_only=False
    )

    with pytest.raises(ConfigurationError, match="Selected extensions failed to load: bundle"):
        await manager.get("session-1")

    backend.release_runtime_lease.assert_awaited_once()
    await manager.aclose()
