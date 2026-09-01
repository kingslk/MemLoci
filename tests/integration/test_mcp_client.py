import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

EXPECTED_TOOLS = {
    "list_projects",
    "code_search",
    "code_context",
    "code_trace",
    "memory_context",
    "memory_expand",
    "memory_compare",
    "evidence_open",
}


async def _list_tools() -> set[str]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "apps.mcp.server"],
        cwd=Path.cwd(),
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            return {tool.name for tool in result.tools}


def test_real_mcp_client_handshake_and_tool_listing() -> None:
    assert asyncio.run(_list_tools()) == EXPECTED_TOOLS
