import sys
from pathlib import Path

from tau_agent.messages import AssistantMessage, TextContent, ToolCall, ToolResultMessage
from tau_agent.provider_events import AssistantDoneEvent
from tau_agent.tools import AgentTool, AgentToolResult
from tau_ai.fake import FakeProvider
from tau_coding import CodingSession, CodingSessionConfig
from tau_coding.resources import TauResourcePaths

from astro.runtime.extensions import ProjectExtensionState

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures/project-extension-workspace"


class _MemoryStorage:
    def __init__(self) -> None:
        self.entries = []

    async def read_all(self):
        return list(self.entries)

    async def append(self, entry):
        self.entries.append(entry)


async def _load_fixture_session(
    *,
    enabled: bool,
    provider: FakeProvider | None = None,
) -> CodingSession:
    async def execute_base(tool_call_id, arguments, signal=None, on_update=None):
        del tool_call_id, arguments, signal, on_update
        return AgentToolResult(content=[TextContent(text="base tool")])

    return await CodingSession.load(
        CodingSessionConfig(
            provider=provider or FakeProvider([]),
            provider_name="fake",
            model="fake-model",
            storage=_MemoryStorage(),
            cwd=FIXTURE_ROOT,
            tools=[
                AgentTool(
                    name="verify_project_imports",
                    label="Base Tool",
                    description="A base tool intentionally overridden by the extension.",
                    parameters={"type": "object", "properties": {}},
                    execute_fn=execute_base,
                )
            ],
            resource_paths=TauResourcePaths(
                root=FIXTURE_ROOT / ".runtime-tau",
                cwd=FIXTURE_ROOT,
                agents_root=None,
            ),
            project_extensions_enabled=enabled,
        )
    )


async def test_tau_loads_and_invokes_code_repository_extension(monkeypatch):
    monkeypatch.syspath_prepend(str(FIXTURE_ROOT))
    monkeypatch.syspath_prepend(str(FIXTURE_ROOT / "src"))

    session = await _load_fixture_session(enabled=True)

    assert session.extension_names == ("import_fixture",)
    assert session.extension_tool_sources == {
        "verify_project_imports": "import_fixture",
    }
    assert [tool.name for tool in session.tools] == ["verify_project_imports"]
    assert session.tools[0].label == "Verify Project Imports"
    result = await session.tools[0].execute("call-1", {})
    assert result.details == {
        "src": "src-import-ok",
        "flat": "flat-import-ok",
        "sibling": "sibling-import-ok",
        "hooked": True,
    }

    state = ProjectExtensionState(enabled=True)
    state.update_from_session(session)
    assert state.loaded_extension_count == 1
    assert state.project_tool_count == 1
    assert state.tool_catalog_digest.startswith("sha256:")
    assert len(state.tool_catalog_digest) == 71


async def test_durable_tau_turn_invokes_the_project_extension_tool(monkeypatch):
    monkeypatch.syspath_prepend(str(FIXTURE_ROOT))
    monkeypatch.syspath_prepend(str(FIXTURE_ROOT / "src"))
    provider = FakeProvider(
        [
            [
                AssistantDoneEvent(
                    reason="toolUse",
                    message=AssistantMessage(
                        model="fake-model",
                        stop_reason="toolUse",
                        content=[
                            ToolCall(
                                id="tool-call-1",
                                name="verify_project_imports",
                                arguments={},
                            )
                        ],
                    ),
                )
            ],
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        model="fake-model",
                        stop_reason="stop",
                        content="Project tool completed.",
                    ),
                )
            ],
        ]
    )
    session = await _load_fixture_session(enabled=True, provider=provider)

    events = [event async for event in session.prompt("Verify the project imports.")]

    assert events
    assert [tool.name for tool in provider.calls[0][3]] == ["verify_project_imports"]
    tool_results = [
        message for message in provider.calls[1][2] if isinstance(message, ToolResultMessage)
    ]
    assert len(tool_results) == 1
    assert tool_results[0].tool_name == "verify_project_imports"
    assert tool_results[0].details == {
        "src": "src-import-ok",
        "flat": "flat-import-ok",
        "sibling": "sibling-import-ok",
        "hooked": True,
    }


async def test_tau_keeps_project_extensions_disabled_without_deployment_opt_in(monkeypatch):
    monkeypatch.syspath_prepend(str(FIXTURE_ROOT))
    monkeypatch.syspath_prepend(str(FIXTURE_ROOT / "src"))

    session = await _load_fixture_session(enabled=False)

    assert session.extension_names == ()
    assert session.extension_tool_sources == {}
    assert [tool.label for tool in session.tools] == ["Base Tool"]


async def test_coding_session_close_delivers_project_shutdown_hook(tmp_path):
    extension_dir = tmp_path / ".tau/extensions/shutdown_probe"
    extension_dir.mkdir(parents=True)
    (extension_dir / "extension.py").write_text(
        """from pathlib import Path


def setup(tau):
    async def record_shutdown(event, context):
        Path(context.cwd, "shutdown-reason.txt").write_text(event.reason)

    tau.on("session_shutdown", record_shutdown)
"""
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            provider_name="fake",
            model="fake-model",
            storage=_MemoryStorage(),
            cwd=tmp_path,
            tools=[],
            resource_paths=TauResourcePaths(
                root=tmp_path / ".runtime-tau",
                cwd=tmp_path,
                agents_root=None,
            ),
            project_extensions_enabled=True,
        )
    )

    assert session.extension_names == ("shutdown_probe",)
    await session.aclose()

    assert (tmp_path / "shutdown-reason.txt").read_text() == "quit"


def test_executor_import_fixture_covers_both_python_layouts_and_sibling_imports():
    extension = (FIXTURE_ROOT / ".tau/extensions/import_fixture/extension.py").read_text()

    assert "from project_fixture.service import project_value" in extension
    assert "from flat_dependency import flat_value" in extension
    assert "from .formatting import sibling_value" in extension
    assert str(FIXTURE_ROOT / "src") not in sys.path
