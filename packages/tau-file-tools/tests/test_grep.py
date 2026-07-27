import asyncio
import shutil
import sys

import pytest

from tau_file_tools import create_grep_tool
from tau_file_tools._common import communicate_with_cancellation


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep is not installed")
async def test_grep_returns_path_line_and_context(tmp_path):
    (tmp_path / "sample.py").write_text("before\nneedle\nafter\n", encoding="utf-8")

    result = await create_grep_tool(cwd=tmp_path).execute(
        "call-1",
        {"pattern": "needle", "context": 1},
    )

    assert "sample.py-1- before" in result.text
    assert "sample.py:2: needle" in result.text
    assert "sample.py-3- after" in result.text


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep is not installed")
async def test_grep_supports_literal_search(tmp_path):
    (tmp_path / "sample.txt").write_text("a.b\naXb\n", encoding="utf-8")

    result = await create_grep_tool(cwd=tmp_path).execute(
        "call-1",
        {"pattern": "a.b", "literal": True},
    )

    assert result.text.startswith("sample.txt:1: a.b")
    assert "aXb" not in result.text


@pytest.mark.asyncio
async def test_subprocess_is_terminated_when_tool_is_cancelled():
    class Signal:
        checks = 0

        def is_cancelled(self):
            self.checks += 1
            return self.checks > 1

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(5)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    with pytest.raises(RuntimeError, match="Operation aborted"):
        await communicate_with_cancellation(process, Signal())

    assert process.returncode is not None
