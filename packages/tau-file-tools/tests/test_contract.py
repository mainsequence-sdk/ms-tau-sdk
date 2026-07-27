from tau_agent.tools import AgentTool

from tau_file_tools import create_file_tools


def test_factories_return_independent_tau_tools(tmp_path):
    tools = create_file_tools(cwd=tmp_path)

    assert all(isinstance(tool, AgentTool) for tool in tools)
    assert [tool.name for tool in tools] == ["grep", "find", "ls"]
    assert len({id(tool) for tool in tools}) == 3
