"""Real HTTP regressions for the in-process FastMCP4 migration.

Use test storage/fake models from server fixtures, never production data or keys.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

import openviking.server.mcp_endpoint as endpoint
from openviking.server.auth.plugins import ApiKeyAuthPlugin
from openviking.server.config import ServerConfig
from openviking.server.identity import ResolvedIdentity, Role
from openviking_cli.exceptions import UnauthenticatedError

HEADERS = {
    "accept": "application/json, text/event-stream",
    "mcp-protocol-version": "2025-11-25",
}


async def rpc(client, method, params=None, headers=None):
    response = await client.post(
        "/mcp",
        headers=HEADERS | (headers or {}),
        json={
            "jsonrpc": "2.0",
            "id": 71,
            "method": method,
            "params": params or {},
        },
    )
    assert response.status_code == 200, response.text
    text = response.text
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        messages = [
            json.loads(line[5:].strip()) for line in text.splitlines() if line.startswith("data:")
        ]
        data = next(item for item in messages if item.get("id") == 71)
    else:
        data = response.json()
    return data


async def call(client, name, arguments=None, headers=None):
    data = await rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, headers)
    assert "error" not in data, data
    return data["result"]


async def test_http_all_legacy_tools_and_native_skill_resources(app):
    async with endpoint.mcp_lifespan():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://testserver"
        ) as client:
            data = await rpc(client, "tools/list")
            names = {tool["name"] for tool in data["result"]["tools"]}
            assert len(names) == 29
            assert {
                "read",
                "write",
                "edit",
                "privacy_tree",
                "list_resources",
                "read_resource",
            } <= names
            result = await call(client, "list_resources")
            assert not result.get("isError")
            resources = (await rpc(client, "resources/list"))["result"]["resources"]
            guides = [x["uri"] for x in resources if x["uri"].endswith("/SKILL.md")]
            assert len(guides) == 5
            for uri in guides:
                result = await call(client, "read_resource", {"uri": uri})
                assert not result.get("isError"), result
                assert "description:" in str(result["content"])
            result = await call(client, "read_resource", {"uri": "file:///etc/passwd"})
            assert result.get("isError") is True
            result = await call(
                client, "read_resource", {"uri": "skill://openviking-memory/../../private"}
            )
            assert result.get("isError") is True


async def test_http_write_read_edit_and_exact_error(app):
    uri = "viking://resources/fastmcp4-test/note.md"
    async with endpoint.mcp_lifespan():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://testserver"
        ) as client:
            result = await call(
                client, "write", {"uri": uri, "content": "alpha marker", "wait": True}
            )
            assert not result.get("isError"), result
            result = await call(client, "read", {"uri": uri})
            assert "alpha marker" in str(result["content"]), result
            result = await call(
                client,
                "edit",
                {"uri": uri, "old_string": "alpha", "new_string": "beta", "wait": True},
            )
            assert not result.get("isError"), result
            result = await call(client, "read", {"uris": [uri]})
            assert "beta marker" in str(result["content"]), result
            result = await call(client, "search", {"query": "q", "max_tokens": 12})
            assert result.get("isError") is True
            result = await call(
                client, "search", {"query": "q", "mode": "list", "query_expansion": "off"}
            )
            assert result.get("isError") is True
            assert result["structuredContent"]["error"]["code"] == "INVALID_ARGUMENT"
            result = await call(client, "forget", {"uri": uri})
            assert not result.get("isError"), result


class TestKeys:
    def is_root_key(self, key):
        return key == "root-fixture"

    async def resolve_identity(self, key):
        if key not in {"alpha-fixture", "beta-fixture"}:
            raise UnauthenticatedError("Invalid API Key")
        user = key.removesuffix("-fixture")
        return ResolvedIdentity(role=Role.USER, account_id="default", user_id=user)


async def test_http_auth_and_concurrent_identity_are_preserved(app, monkeypatch):
    app.state.config = ServerConfig(auth_mode="api_key", root_api_key="root-fixture")
    app.state.api_key_manager = TestKeys()
    app.state.auth_plugin = ApiKeyAuthPlugin()
    app.state.oauth_provider = SimpleNamespace()
    seen = []

    async def read_visible(uri, ctx, **kwargs):
        await asyncio.sleep(0.005)
        seen.append((ctx.user.user_id, ctx.actor_peer_id))
        return f"{ctx.user.user_id}:{ctx.actor_peer_id}"

    fake = SimpleNamespace(
        fs=SimpleNamespace(read_visible=read_visible),
        mcps=SimpleNamespace(read_live_context=AsyncMock(return_value=None)),
    )
    monkeypatch.setattr(endpoint, "get_service", lambda: fake)
    async with endpoint.mcp_lifespan():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://testserver"
        ) as client:
            for headers in (
                {},
                {"authorization": "Bearer invalid"},
                {"authorization": "Bearer root-fixture"},
            ):
                response = await client.post(
                    "/mcp",
                    headers=HEADERS | headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/list",
                    },
                )
                assert response.status_code in {401, 403}
                if response.status_code == 401:
                    assert "resource_metadata=" in response.headers["www-authenticate"]

            async def one(user):
                headers = {
                    "authorization": f"Bearer {user}-fixture",
                    "x-openviking-actor-peer": f"{user}-peer",
                }
                value = await call(
                    client, "read", {"uri": f"viking://user/{user}/note.md"}, headers
                )
                assert not value.get("isError"), value
                assert f"{user}:{user}-peer" in str(value["content"])
                guide = await call(
                    client, "read_resource", {"uri": "skill://openviking-memory/SKILL.md"}, headers
                )
                assert not guide.get("isError"), guide

            await asyncio.gather(*(one(user) for user in ["alpha", "beta"] * 5))
    assert len(seen) == 10
    assert set(seen) == {("alpha", "alpha-peer"), ("beta", "beta-peer")}
    assert endpoint._mcp_ctx.get() is None
    assert endpoint._mcp_http_request.get() is None


async def test_downstream_mcp_registry_uses_sdk2_http_and_timeouts():
    """Exercise the OpenViking MCP client too, not just its server adapter."""
    import socket

    import uvicorn
    from fastmcp import FastMCP

    from openviking.service.mcp_service import MCPService

    downstream = FastMCP("downstream-fixture")

    @downstream.tool
    def echo(value: str) -> str:
        return value

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            downstream.http_app(path="/mcp", stateless_http=True),
            log_level="warning",
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        assert server.started
        registry = object.__new__(MCPService)
        config = {"name": "echo", "url": f"http://127.0.0.1:{port}/mcp"}
        initialized, tools = await registry._discover(config, {})
        assert initialized.server_info.name == "downstream-fixture"
        assert [tool.name for tool in tools] == ["echo"]
        document = registry._tool_document(config, tools[0])
        assert document["tool"]["inputSchema"]["properties"]["value"]["type"] == "string"
        async with registry._session(config, {}, timeout_seconds=5) as session:
            await session.initialize()
            result = await session.call_tool(
                "echo", {"value": "sdk2-roundtrip"}, read_timeout_seconds=5
            )
            assert result.is_error is False
            assert "sdk2-roundtrip" in str(result.content)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        listener.close()


async def test_native_skills_and_resource_tools_share_verified_sources():
    from fastmcp import Client
    from fastmcp.skills import SkillsClientExtension

    async with Client(endpoint.mcp, extensions=[SkillsClientExtension()]) as client:
        entries = await client.list_skills()
        assert len(entries) == 5
        names = set()
        for entry in entries:
            skill = await client.read_skill(entry.uri)
            names.add(skill.name)
            result = await client.call_tool(
                "read_resource", {"uri": f"skill://{skill.name}/SKILL.md"}
            )
            assert not result.is_error
            assert "description:" in str(result.content)
        assert names == {
            "openviking-memory",
            "openviking-skills",
            "openviking-rule-search",
            "ov-resources",
            "ov-experience-memory",
        }
        assert len(await client.list_tools()) == 29
