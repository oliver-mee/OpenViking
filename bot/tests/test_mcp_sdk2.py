"""Real SDK2 transport contracts for VikingBot and OpenViking's MCP registry."""

import asyncio
import json
import socket
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import uvicorn
from vikingbot.agent.tools.base import ToolContext
from vikingbot.agent.tools.mcp import connect_mcp_servers
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.config.schema import MCPServerConfig

SERVER_SOURCE = """
import asyncio
from fastmcp import FastMCP
from mcp_types import CallToolResult, ToolAnnotations

server = FastMCP("outbound-sdk2-fixture", list_page_size=1)

@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def nested(payload: dict[str, list[str]]) -> dict:
    return {"payload": payload}

@server.tool
def structured() -> CallToolResult:
    return CallToolResult(content=[], structured_content={"resultValue": "structured-only"})

@server.tool
def rejected() -> CallToolResult:
    return CallToolResult(
        content=[],
        structured_content={"error": {"code": "CONFLICT", "detail": "fixture-only"}},
        is_error=True,
    )

@server.tool
async def slow(seconds: float) -> str:
    await asyncio.sleep(seconds)
    return "finished"

@server.tool
def tail() -> str:
    return "last catalog page"

if __name__ == "__main__":
    server.run(transport="stdio", show_banner=False)
"""


@asynccontextmanager
async def downstream(tmp_path, transport, *, expected_query=None):
    """Serve actual MCP traffic; fixtures use no production data or credentials."""
    if transport == "stdio":
        script = tmp_path / "mcp_fixture.py"
        script.write_text(SERVER_SOURCE)
        yield MCPServerConfig(
            type="stdio",
            command=sys.executable,
            args=[str(script)],
            tool_timeout=5,
        )
        return

    namespace = {"__name__": "outbound_mcp_fixture"}
    exec(compile(SERVER_SOURCE, "<mcp-fixture>", "exec"), namespace)
    path = "/sse" if transport == "sse" else "/mcp"
    app = namespace["server"].http_app(
        path=path,
        transport="sse" if transport == "sse" else "http",
        stateless_http=True,
    )

    # Verify configured headers survive both SSE requests and streamable HTTP.
    async def authenticated(scope, receive, send):
        if scope["type"] == "http":
            if expected_query is not None:
                assert scope["query_string"] == expected_query
            headers = dict(scope["headers"])
            if headers.get(b"authorization") != b"Bearer fixture-credential":
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"fixture auth required"})
                return
        await app(scope, receive, send)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(authenticated, log_level="critical"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(300):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        assert server.started
        yield MCPServerConfig(
            type=transport,
            url=f"http://127.0.0.1:{port}{path}",
            headers={"Authorization": "Bearer fixture-credential"},
            tool_timeout=5,
        )
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        listener.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["stdio", "sse", "streamableHttp"])
async def test_bot_sdk2_transports_paginate_preserve_results_and_cancel(tmp_path, transport):
    async with downstream(tmp_path, transport) as cfg:
        registry = ToolRegistry()
        async with AsyncExitStack() as stack:
            await connect_mcp_servers({"fixture": cfg}, registry, stack)
            assert set(registry.tool_names) == {
                "mcp_fixture_nested",
                "mcp_fixture_structured",
                "mcp_fixture_rejected",
                "mcp_fixture_slow",
                "mcp_fixture_tail",
            }
            context = ToolContext()
            payload = {"inputSchema": ["camelCase", "中文"]}
            nested = registry.get("mcp_fixture_nested")
            assert nested.parameters["properties"]["payload"]["type"] == "object"
            result = await nested.execute(context, payload=payload)
            assert json.loads(result) == {"payload": payload}
            assert await registry.get("mcp_fixture_tail").execute(context) == "last catalog page"
            result = await registry.get("mcp_fixture_structured").execute(context)
            assert json.loads(result) == {"resultValue": "structured-only"}
            result = await registry.get("mcp_fixture_rejected").execute(context)
            assert result.startswith("Error:")
            assert "CONFLICT" in result

            pending = asyncio.create_task(
                registry.get("mcp_fixture_slow").execute(context, seconds=20)
            )
            await asyncio.sleep(0.05)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            # Cancellation must not poison the connection for the next call.
            assert await registry.get("mcp_fixture_tail").execute(context) == "last catalog page"


@pytest.mark.asyncio
async def test_bot_sdk2_tool_timeout_is_bounded_and_connection_recovers(tmp_path):
    async with downstream(tmp_path, "streamableHttp") as cfg:
        cfg.tool_timeout = 1
        registry = ToolRegistry()
        async with AsyncExitStack() as stack:
            await connect_mcp_servers({"fixture": cfg}, registry, stack)
            result = await asyncio.wait_for(
                registry.get("mcp_fixture_slow").execute(ToolContext(), seconds=20), 3
            )
            assert result.startswith("Error: MCP tool call timed out after 1s")
            assert (
                await registry.get("mcp_fixture_tail").execute(ToolContext()) == "last catalog page"
            )


@pytest.mark.asyncio
async def test_registry_sdk2_preserves_wire_schema_and_upstream_error(tmp_path, monkeypatch):
    from openviking.service.mcp_service import MCPService

    private_query = "unusual_credential=fixture-private-registry-query"
    async with downstream(tmp_path, "streamableHttp", expected_query=private_query.encode()) as cfg:
        registry = object.__new__(MCPService)
        private_url = cfg.url + "?" + private_query
        server = {"name": "fixture", "url": private_url}
        initialized, tools = await registry._discover(server, cfg.headers)
        assert initialized.server_info.name == "outbound-sdk2-fixture"
        assert len(tools) == 5
        docs = {tool.name: registry._tool_document(server, tool) for tool in tools}
        assert server["url"] == private_url
        assert all(doc["address"] == cfg.url + "?__query_redacted__" for doc in docs.values())
        assert private_query not in json.dumps(docs)
        nested = docs["nested"]["tool"]
        assert nested["inputSchema"]["properties"]["payload"]["type"] == "object"
        assert nested["annotations"]["readOnlyHint"] is True
        assert "input_schema" not in nested

        # The registry's identity/storage gate is tested elsewhere. Here its real
        # public invocation consumes a saved schema and an actual SDK2 transport.
        monkeypatch.setattr(registry, "_registered", AsyncMock(return_value=(server, cfg.headers)))
        monkeypatch.setattr(
            registry,
            "_parse_tool_uri",
            lambda uri, ctx: (
                uri,
                "viking://user/fixture/tools",
                "fixture",
                uri.rsplit("/", 1)[-1][:-5],
            ),
        )
        monkeypatch.setattr(
            registry,
            "_read_tool_document",
            AsyncMock(side_effect=lambda uri, server_name, name, ctx: docs[name]),
        )
        ctx = SimpleNamespace()
        payload = {"inputSchema": ["camelCase", "中文"]}
        result = await registry.invoke(
            "viking://user/fixture/tools/fixture/nested.json", {"payload": payload}, ctx
        )
        assert result["structuredContent"] == {"payload": payload}
        assert result["isError"] is False
        assert "structured_content" not in result
        result = await registry.invoke("viking://user/fixture/tools/fixture/rejected.json", {}, ctx)
        assert result["isError"] is True
        assert result["structuredContent"]["error"]["code"] == "CONFLICT"


@pytest.mark.asyncio
@pytest.mark.parametrize("client", ["registry", "bot"])
@pytest.mark.parametrize("http_status", [401, 200])
async def test_outbound_mcp_errors_do_not_log_reflected_credentials(
    monkeypatch, caplog, client, http_status
):
    """SDK parser failures and HTTP URL logs must not bypass public redaction."""
    import logging

    import httpx2

    from openviking.service.mcp_service import MCPService
    from openviking_cli.exceptions import UnavailableError

    private_query = "fixture-private-query-9aeb63d1"
    private_auth = "fixture-private-auth-f447f203"
    url = f"http://fixture.invalid/mcp?token={private_query}"
    headers = {"Authorization": f"Bearer {private_auth}"}
    original_client = httpx2.AsyncClient

    def respond(request):
        assert request.headers["authorization"] == headers["Authorization"]
        # A real downstream can reflect a credential in an invalid JSON body.
        # The official SDK logs its validation exception before our error gate.
        return httpx2.Response(
            http_status,
            json={"private": private_auth},
            request=request,
        )

    def make_client(**kwargs):
        return original_client(transport=httpx2.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", make_client)
    caplog.set_level(logging.DEBUG)
    if client == "registry":
        service = object.__new__(MCPService)
        with pytest.raises(UnavailableError) as caught:
            async with service._session(
                {"name": "fixture", "url": url}, headers, timeout_seconds=1
            ) as session:
                await session.initialize()
        public_error = json.dumps(
            {
                "code": caught.value.code,
                "message": str(caught.value),
                "details": caught.value.details,
            }
        )
        assert caught.value.code == "UNAVAILABLE"
        assert private_query not in public_error
        assert private_auth not in public_error
    else:
        registry = ToolRegistry()
        cfg = MCPServerConfig(type="streamableHttp", url=url, headers=headers, tool_timeout=1)
        async with AsyncExitStack() as stack:
            await connect_mcp_servers({"fixture": cfg}, registry, stack)
        assert not registry.tool_names

    assert private_query not in caplog.text
    assert private_auth not in caplog.text
    assert f"http_status={http_status}" in caplog.text
    if http_status == 200:
        assert "error_type=ValidationError" in caplog.text
    for record in caplog.records:
        if record.getMessage().startswith("Outbound MCP transport diagnostic"):
            assert record.args == ()
            assert record.exc_info is None
            assert record.exc_text is None
            assert record.stack_info is None


@pytest.mark.asyncio
async def test_outbound_log_scope_protects_children_and_teardown_only(caplog):
    """A connection must not change another task's or its owner's logging."""
    import logging

    from openviking_cli.utils.mcp_logging import redact_mcp_transport_context

    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("mcp.client.streamable_http")
    release = asyncio.Event()
    private = "fixture-private-background-981ce9a1"
    public = "unrelated-caller-log-remains-visible"

    async def background():
        await release.wait()
        logger.warning(private)

    @asynccontextmanager
    async def connection():
        logger.warning(private)
        task = asyncio.create_task(background())
        try:
            yield task
        finally:
            try:
                raise RuntimeError(private)
            except RuntimeError:
                logger.exception(private, stack_info=True)

    async with AsyncExitStack() as stack:
        task = await stack.enter_async_context(redact_mcp_transport_context(connection()))
        logger.warning(public)
        release.set()
        await task
    logger.warning("owner-after-teardown")

    assert private not in caplog.text
    assert public in caplog.text
    assert "owner-after-teardown" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert (
        sum(
            record.getMessage().startswith("Outbound MCP transport diagnostic")
            for record in caplog.records
        )
        == 3
    )


@pytest.mark.parametrize(
    ("url", "public_url"),
    [
        (
            "https://fixture.invalid/mcp?unusual_private_name=fixture-secret&region=private-region",
            "https://fixture.invalid/mcp?__query_redacted__",
        ),
        (
            "https://fixture.invalid/mcp?credential-without-a-key",
            "https://fixture.invalid/mcp?__query_redacted__",
        ),
        (
            "https://fixture.invalid/mcp?%73ensitive=percent-encoded-secret",
            "https://fixture.invalid/mcp?__query_redacted__",
        ),
        ("https://fixture.invalid/mcp", "https://fixture.invalid/mcp"),
        ("http://127.0.0.1:12345/mcp/", "http://127.0.0.1:12345/mcp/"),
    ],
)
def test_registry_public_endpoint_metadata_removes_entire_query(url, public_url):
    from copy import deepcopy
    from urllib.parse import urlsplit

    from mcp.types import Tool

    from openviking.service.mcp_service import MCPService

    server = {
        "name": "fixture",
        "url": url,
        "headers": {"Authorization": "Bearer fixture-private-header"},
    }
    original = deepcopy(server)
    tool = Tool(name="fixture", input_schema={"type": "object", "properties": {}})
    summary = MCPService._public_server(server, 1, "viking://user/fixture/tools")
    document = MCPService._tool_document(server, tool)

    assert summary["url"] == public_url
    assert document["address"] == public_url
    assert summary["header_names"] == ["Authorization"]
    serialized = json.dumps([summary, document])
    assert "fixture-private-header" not in serialized
    query = urlsplit(url).query
    if query:
        assert urlsplit(summary["url"]).query == "__query_redacted__"
        # Redact arbitrary credential keys, values and bare query strings.
        assert all(
            part not in serialized for field in query.split("&") for part in field.split("=")
        )
    assert server == original


@pytest.mark.asyncio
async def test_registry_explicit_sync_republishes_redacted_address_without_schema_change(
    monkeypatch,
):
    from copy import deepcopy

    from mcp.types import Tool

    from openviking.service.mcp_service import MCPService

    registry = object.__new__(MCPService)
    registry._locks = {}
    root = "viking://user/fixture/tools"
    ctx = SimpleNamespace(account_id="fixture-account")
    private_url = "https://fixture.invalid/mcp?private-query=fixture-secret"
    server = {"name": "fixture", "url": private_url, "headers": {}}
    tool = Tool(name="fixture", input_schema={"type": "object", "properties": {}})
    current = registry._tool_document(server, tool)
    previous = {**current, "uri": root + "/fixture/fixture.json", "address": private_url}
    stored = {"servers": {"fixture": deepcopy(server)}}

    monkeypatch.setattr(registry, "_resolve_tools_root", lambda *args: root)
    monkeypatch.setattr(registry, "_registered", AsyncMock(return_value=(server, {})))
    monkeypatch.setattr(
        registry,
        "_discover",
        AsyncMock(return_value=(SimpleNamespace(protocol_version="2025-11-25"), [tool])),
    )
    monkeypatch.setattr(registry, "_catalog", AsyncMock(return_value=[previous]))
    publisher = AsyncMock()
    monkeypatch.setattr(registry, "_publish_catalog", publisher)
    monkeypatch.setattr(registry, "_affected_mounts", AsyncMock(return_value=[]))
    monkeypatch.setattr(registry, "_load", AsyncMock(return_value=stored))
    monkeypatch.setattr(
        registry, "_registry_path_for_root", lambda *args: "/fixture/private-registry"
    )
    writer = AsyncMock()
    monkeypatch.setattr(registry, "_write_document", writer)

    result = await registry.sync("fixture", ctx, wait=True)
    published = publisher.await_args.args[1]
    assert len(published) == 1
    assert published[0]["address"] == "https://fixture.invalid/mcp?__query_redacted__"
    assert published[0]["revision"] == previous["revision"]
    assert published[0]["input_revision"] == previous["input_revision"]
    assert result["changes"] == {
        "added": [],
        "changed": [],
        "unchanged": ["fixture"],
        "removed": [],
    }
    # Only generated public metadata changes. Private connection material stays
    # available for later discovery/invocation and is never replaced by a marker.
    assert writer.await_args.args[1]["servers"]["fixture"]["url"] == private_url
    registry._discover.assert_awaited_once_with(server, {})
