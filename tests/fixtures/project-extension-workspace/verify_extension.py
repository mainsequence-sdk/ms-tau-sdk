"""Run inside the final executor image to verify Tau project extension loading."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from tau_coding.extensions import ExtensionRuntime
from tau_coding.resources import TauResourcePaths


async def main() -> None:
    workspace = Path("/workspace")
    assert sys.executable == "/opt/venv/bin/python", sys.executable
    assert os.environ["PYTHONPATH"] == "/workspace/src:/workspace"
    assert str(workspace / "src") in sys.path
    assert str(workspace) in sys.path

    runtime = ExtensionRuntime()
    runtime.load(
        TauResourcePaths(root=workspace / ".runtime-tau", cwd=workspace),
        include_project_dir=True,
    )
    assert runtime.extension_names == ("import_fixture",), runtime.extension_names
    assert runtime.diagnostics == (), runtime.diagnostics
    assert runtime.extension_tool_sources == {
        "verify_project_imports": "import_fixture",
    }

    tool = runtime.compose_tools([])[0]
    result = await tool.execute("verification", {})
    assert result.details == {
        "src": "src-import-ok",
        "flat": "flat-import-ok",
        "sibling": "sibling-import-ok",
        "hooked": True,
    }


asyncio.run(main())
