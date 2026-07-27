import pytest

from tau_file_tools import create_ls_tool
from tau_file_tools._common import truncate_bytes


@pytest.mark.asyncio
async def test_ls_sorts_and_marks_directories(tmp_path):
    (tmp_path / ".env").write_text("", encoding="utf-8")
    (tmp_path / "Zoo").mkdir()
    (tmp_path / "alpha.txt").write_text("", encoding="utf-8")

    result = await create_ls_tool(cwd=tmp_path).execute("call-1", {})

    assert result.text.splitlines() == [".env", "alpha.txt", "Zoo/"]


@pytest.mark.asyncio
async def test_ls_rejects_non_directory(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="Not a directory"):
        await create_ls_tool(cwd=tmp_path).execute("call-1", {"path": "file.txt"})


@pytest.mark.asyncio
async def test_ls_reports_result_limit(tmp_path):
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text("", encoding="utf-8")

    result = await create_ls_tool(cwd=tmp_path).execute("call-1", {"limit": 2})

    assert result.details["entry_limit_reached"] == 2
    assert "2 entries limit reached" in result.text


def test_output_byte_truncation_preserves_valid_utf8():
    output, truncated = truncate_bytes("abcd\u00e9fgh", max_bytes=6)

    assert output == "abcd\u00e9"
    assert truncated is True
    assert len(output.encode()) <= 6
