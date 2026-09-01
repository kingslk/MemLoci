import asyncio

from apps.mcp.server import mcp


def test_mcp_exposes_read_tools_and_usage_instructions() -> None:
    assert mcp is not None
    tools = asyncio.run(mcp.list_tools())
    assert mcp.instructions
    assert "memory_context" in mcp.instructions
    assert "仍会查整个 Project" in mcp.instructions
    assert sorted(tool.name for tool in tools) == [
        "code_context",
        "code_search",
        "code_trace",
        "evidence_open",
        "list_projects",
        "memory_compare",
        "memory_context",
        "memory_expand",
    ]
