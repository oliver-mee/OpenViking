"""Inspect FastMCP through public APIs while retaining legacy wire assertions.

OpenViking domain tests compare protocol objects/legacy tuples, not framework
component wrappers. This adapter does not mock handlers, authentication or I/O.
"""


async def list_tools(server):
    return [tool.to_mcp_tool() for tool in await server.list_tools()]


async def call_tool(server, name, arguments=None):
    return (await server.call_tool(name, arguments)).to_mcp_result()
