import pytest

from tau_file_tools import create_find_tool


@pytest.mark.asyncio
async def test_find_matches_relative_paths_and_excludes_internal_directories(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "agent.py").write_text("", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ignored.py").write_text("", encoding="utf-8")

    result = await create_find_tool(cwd=tmp_path).execute("call-1", {"pattern": "*.py"})

    assert result.text == "src/agent.py"


@pytest.mark.asyncio
async def test_find_returns_stable_empty_message(tmp_path):
    result = await create_find_tool(cwd=tmp_path).execute("call-1", {"pattern": "*.rs"})

    assert result.text == "No files found matching pattern"
