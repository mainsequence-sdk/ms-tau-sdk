from tau_agent.tools import AgentTool

from tau_web_access import WebAccessSettings, create_web_tools


def test_tool_names_and_tau_contract(tmp_path):
    tools = create_web_tools(
        settings=WebAccessSettings(allow_private_networks=True),
        cwd=tmp_path,
    )

    assert all(isinstance(tool, AgentTool) for tool in tools)
    assert [tool.name for tool in tools] == [
        "web_search",
        "code_search",
        "fetch_content",
        "get_search_content",
    ]
