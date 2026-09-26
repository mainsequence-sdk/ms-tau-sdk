import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult

from ms_tau_sdk.backend.models import AgentCardEnvelope
from ms_tau_sdk.errors import (
    ExtensionSourceError,
    ToolValidationError,
    ToolWorkbenchError,
)
from ms_tau_sdk.runtime.extensions import ProjectExtensionState
from ms_tau_sdk.runtime.manager import SessionRuntimeManager
from ms_tau_sdk.runtime.session import ActiveSessionRuntime
from ms_tau_sdk.settings import TauSDKSettings


def _manager(tmp_path):
    settings = TauSDKSettings(
        _env_file=None,
        local_mode=True,
        auth_mode="jwt",
        local_provider="openai",
        local_model="gpt-5.4",
        workspace=tmp_path,
        startup_dependencies_enabled=False,
    )
    backend = Mock()
    backend.get_agent_card = AsyncMock(
        return_value=AgentCardEnvelope(
            agent_session_uid="session-1",
            agent_uid="agent-1",
            agent_card={"name": "Fixture Agent", "skills": [{"name": "Review"}]},
        )
    )
    return SessionRuntimeManager(settings=settings, backend=backend, providers=Mock()), backend


def _loaded_runtime(manager, tmp_path, execute_fn=None):
    entry = tmp_path / ".tau" / "extensions" / "fixture" / "extension.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("def setup(tau):\n    pass\n", encoding="utf-8")
    execute = execute_fn or AsyncMock(
        return_value=AgentToolResult(content=[TextContent(text="done")], details={"ok": True})
    )
    project_tool = AgentTool(
        name="project_lookup",
        label="Project lookup",
        description="Look up one project value.",
        parameters={
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
        execute_fn=execute,
    )
    core_tool = AgentTool(
        name="read",
        label="Read",
        description="Read a file.",
        parameters={"type": "object", "properties": {}},
        execute_fn=AsyncMock(),
    )
    metadata = SimpleNamespace(
        name="fixture",
        source_id=f"extension:{entry.resolve().as_uri()}",
        source="project",
        hidden=False,
        path=entry,
    )
    coding_session = SimpleNamespace(
        tools=(core_tool, project_tool),
        extension_names=("fixture",),
        extension_tool_sources={"project_lookup": "fixture"},
        extension_runtime=SimpleNamespace(extension_metadata=(metadata,), diagnostics=()),
        is_running=False,
    )
    state = ProjectExtensionState(enabled=True)
    state.update_from_session(coding_session)
    runtime = ActiveSessionRuntime(
        session_uid="session-1",
        holder_id=manager.holder_id,
        coding_session=coding_session,
        storage=SimpleNamespace(),
        provider=object(),
        provider_name="openai",
        model="gpt-5.4",
        runtime_activity="idle",
        project_extension_state=state,
    )
    manager._runtimes["session-1"] = runtime
    return runtime, project_tool, execute, entry


@pytest.mark.asyncio
async def test_inspection_uses_loaded_catalog_and_never_loads_missing_session(tmp_path):
    manager, backend = _manager(tmp_path)
    runtime, _tool, _execute, _entry = _loaded_runtime(manager, tmp_path)

    missing = await manager.local_agent_inspection("missing")
    assert missing == {
        "available": False,
        "reason": "session_not_loaded",
        "sessionUid": "missing",
    }
    backend.get_agent_card.assert_not_awaited()

    result = await manager.local_agent_inspection("session-1")
    assert result["available"] is True
    assert result["agentCard"]["name"] == "Fixture Agent"
    tools = {tool["name"]: tool for tool in result["tools"]}
    assert tools["project_lookup"]["category"] == "project_extension"
    assert tools["project_lookup"]["testable"] is True
    assert tools["project_lookup"]["sourceUid"]
    assert tools["read"]["category"] == "tau_coding"
    assert tools["read"]["testable"] is False
    assert result["catalogDigest"] == runtime.project_extension_state.tool_catalog_digest


@pytest.mark.asyncio
async def test_project_source_is_opaque_bounded_and_workspace_scoped(tmp_path):
    manager, _backend = _manager(tmp_path)
    _runtime, _tool, _execute, entry = _loaded_runtime(manager, tmp_path)
    inspection = await manager.local_agent_inspection("session-1")
    opaque = inspection["extensions"][0]["sourceUid"]
    result = manager.local_extension_source("session-1", opaque)
    assert result["path"] == ".tau/extensions/fixture/extension.py"
    assert "def setup" in result["content"]
    with pytest.raises(ExtensionSourceError):
        manager.local_extension_source("session-1", "0" * 24)

    entry.unlink()
    entry.symlink_to(tmp_path / "outside.py")
    (tmp_path / "outside.py").write_text("secret", encoding="utf-8")
    with pytest.raises(ExtensionSourceError, match="Symlinked"):
        manager.local_extension_source("session-1", opaque)


@pytest.mark.asyncio
async def test_project_tool_validation_confirmation_and_execution(tmp_path):
    manager, _backend = _manager(tmp_path)
    runtime, _tool, execute, _entry = _loaded_runtime(manager, tmp_path)
    digest = runtime.project_extension_state.tool_catalog_digest

    with pytest.raises(ToolValidationError):
        manager.validate_project_tool_test(
            "session-1", "project_lookup", catalog_digest=digest, arguments={}
        )
    with pytest.raises(ToolWorkbenchError, match="Only project extension"):
        manager.validate_project_tool_test("session-1", "read", catalog_digest=digest, arguments={})

    validated = manager.validate_project_tool_test(
        "session-1",
        "project_lookup",
        catalog_digest=digest,
        arguments={"symbol": "MSFT"},
    )
    execution = await manager.start_project_tool_test(
        "session-1",
        "project_lookup",
        catalog_digest=digest,
        arguments=validated["arguments"],
        confirmation=validated["confirmation"],
    )
    events = [event async for event in execution.events()]
    assert [event["type"] for event in events] == ["started", "completed", "finished"]
    assert events[1]["result"]["content"][0]["text"] == "done"
    execute.assert_awaited_once()
    assert execute.await_args.args[1] == {"symbol": "MSFT"}
    assert runtime.lock.locked() is False

    with pytest.raises(ToolWorkbenchError, match="confirmation"):
        await manager.start_project_tool_test(
            "session-1",
            "project_lookup",
            catalog_digest=digest,
            arguments={"symbol": "MSFT"},
            confirmation=validated["confirmation"],
        )


@pytest.mark.asyncio
async def test_project_tool_test_can_be_cancelled(tmp_path):
    started = asyncio.Event()

    async def wait_forever(tool_call_id, arguments, signal=None, on_update=None):
        del tool_call_id, arguments, signal, on_update
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    manager, _backend = _manager(tmp_path)
    runtime, _tool, _execute, _entry = _loaded_runtime(manager, tmp_path, wait_forever)
    digest = runtime.project_extension_state.tool_catalog_digest
    validated = manager.validate_project_tool_test(
        "session-1",
        "project_lookup",
        catalog_digest=digest,
        arguments={"symbol": "MSFT"},
    )
    execution = await manager.start_project_tool_test(
        "session-1",
        "project_lookup",
        catalog_digest=digest,
        arguments=validated["arguments"],
        confirmation=validated["confirmation"],
    )
    await started.wait()
    assert manager.cancel_project_tool_test("session-1", execution.test_uid) is True
    events = [event async for event in execution.events()]
    assert [event["type"] for event in events][-2:] == ["cancelled", "finished"]
    assert runtime.lock.locked() is False


@pytest.mark.asyncio
async def test_project_tool_test_immediate_cancellation_releases_session_lock(tmp_path):
    manager, _backend = _manager(tmp_path)
    runtime, _tool, _execute, _entry = _loaded_runtime(manager, tmp_path)
    digest = runtime.project_extension_state.tool_catalog_digest
    validated = manager.validate_project_tool_test(
        "session-1",
        "project_lookup",
        catalog_digest=digest,
        arguments={"symbol": "MSFT"},
    )
    execution = await manager.start_project_tool_test(
        "session-1",
        "project_lookup",
        catalog_digest=digest,
        arguments=validated["arguments"],
        confirmation=validated["confirmation"],
    )

    assert manager.cancel_project_tool_test("session-1", execution.test_uid) is True
    events = [event async for event in execution.events()]

    assert [event["type"] for event in events][-2:] == ["cancelled", "finished"]
    assert runtime.lock.locked() is False
