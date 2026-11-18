"""Network acceptance tests for modern and legacy MCP clients.

Use the ordinary OpenViking app route, isolated server fixtures and fake models.
No production credentials, model calls or storage are used.
"""

import asyncio
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.skills import SkillsClientExtension

import openviking.server.mcp_endpoint as endpoint
from openviking.server.auth.plugins import ApiKeyAuthPlugin
from openviking.server.config import ServerConfig
from openviking.server.identity import ResolvedIdentity, Role
from openviking_cli.exceptions import UnauthenticatedError


class DirectoryKeys:
    """Deterministic identity directory, through the actual API-key auth plugin."""

    def __init__(self):
        self.directory_calls = []

    def is_root_key(self, key):
        return key == "root-fixture"

    async def resolve_identity(self, key):
        if key not in {"alpha-fixture", "beta-fixture"}:
            raise UnauthenticatedError("Invalid API key")
        user = key.removesuffix("-fixture")
        return ResolvedIdentity(role=Role.USER, account_id=f"account-{user}", user_id=user)

    async def list_users_page(self, account_id, **kwargs):
        self.directory_calls.append(("users", account_id, kwargs))
        return {
            "users": [
                {"user_id": f"{account_id}-visible", "api_key": "fixture-secret-must-be-filtered"}
            ],
            "total": 1,
        }

    async def get_groups(self, account_id):
        self.directory_calls.append(("groups", account_id, {}))
        return [{"group_id": f"{account_id}-group", "members": ["not-part-of-directory-result"]}]


@pytest_asyncio.fixture
async def protocol_server(app):
    keys = DirectoryKeys()
    app.state.config = ServerConfig(auth_mode="api_key", root_api_key="root-fixture")
    app.state.api_key_manager = keys
    app.state.auth_plugin = ApiKeyAuthPlugin()
    app.state.oauth_provider = SimpleNamespace()
    trace = SimpleNamespace(methods=[], resets=[], cancellation_processed=asyncio.Event())

    async def traced(scope, receive, send):
        body = bytearray()
        method = None

        async def read_request():
            nonlocal method
            message = await receive()
            if message["type"] == "http.request":
                body.extend(message.get("body", b""))
                if not message.get("more_body"):
                    try:
                        data = json.loads(body)
                    except (ValueError, UnicodeError):
                        pass
                    else:
                        method = data.get("method")
                        trace.methods.append(method)
            return message

        try:
            await app(scope, read_request, send)
        finally:
            # Check in the actual serving task after the identity middleware exits.
            trace.resets.append(
                (method, endpoint._mcp_ctx.get() is None, endpoint._mcp_http_request.get() is None)
            )
            if method == "notifications/cancelled":
                trace.cancellation_processed.set()

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            traced,
            lifespan="off",  # service fixture already owns storage; enter MCP lifespan explicitly.
            log_level="critical",
            timeout_graceful_shutdown=3,
        )
    )

    async def serve():
        # A pytest async-generator fixture enters/exits on different tasks.
        # Keep AnyIO's lifespan cancel scope entirely in the server task.
        async with endpoint.mcp_lifespan():
            await server.serve(sockets=[listener])

    task = asyncio.create_task(serve())
    try:
        for _ in range(300):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        assert server.started
        yield SimpleNamespace(url=f"http://127.0.0.1:{port}/mcp", keys=keys, trace=trace)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        listener.close()


def client_for(server, mode, user="alpha", *, skills=False):
    transport = StreamableHttpTransport(
        server.url,
        headers={
            "Authorization": f"Bearer {user}-fixture",
            "X-OpenViking-Actor-Peer": f"{user}-peer",
        },
    )
    return Client(
        transport,
        mode=mode,
        extensions=[SkillsClientExtension()] if skills else [],
        timeout=5,
    )


@pytest.mark.parametrize("mode,version", [("auto", "2026-07-28"), ("legacy", "2025-11-25")])
async def test_http_protocol_discovery_skills_and_parent_directory_state(
    protocol_server, mode, version
):
    server = protocol_server
    async with client_for(server, mode, skills=True) as client:
        assert client.session.protocol_version == version
        if mode == "auto":
            assert "server/discover" in server.trace.methods
            assert "initialize" not in server.trace.methods
            assert client.initialize_result is None
        else:
            assert "initialize" in server.trace.methods
            assert client.initialize_result.protocol_version == version

        tools = await client.list_tools()
        assert len(tools) == 29
        assert {"list_resources", "read_resource", "list_users", "list_groups"} <= {
            tool.name for tool in tools
        }

        skill_bytes = {}
        if mode == "auto":
            entries = await client.list_skills()
            assert len(entries) == 5
            for entry in entries:
                skill = await client.read_skill(entry.uri)
                skill_bytes[f"skill://{skill.name}/SKILL.md"] = skill.files["SKILL.md"]
        else:
            # Native Skills is a modern extension. Legacy clients use the same
            # guides through standard resources and the ordinary tool bridge.
            with pytest.raises(RuntimeError, match="requires a modern MCP connection"):
                await client.list_skills()

        listed = await client.call_tool_mcp("list_resources", {})
        listed_uris = {item["uri"] for item in json.loads(listed.content[0].text) if "uri" in item}
        resources = await client.list_resources()
        guide_uris = {str(item.uri) for item in resources if str(item.uri).endswith("/SKILL.md")}
        assert len(guide_uris) == 5
        if mode == "auto":
            assert guide_uris == set(skill_bytes)
        for uri in sorted(guide_uris):
            assert uri in listed_uris
            native = await client.read_resource(uri)
            bridged = await client.call_tool_mcp("read_resource", {"uri": uri})
            assert not bridged.is_error
            assert len(native) == len(bridged.content) == 1
            assert bridged.content[0].text == native[0].text
            assert "description:" in native[0].text
            if mode == "auto":
                assert native[0].text.encode() == skill_bytes[uri]

        # FastMCP changes scope["app"] for its child app. Directory tools must
        # still consult the parent OpenViking app's manager and current identity.
        for user in ("alpha", "beta"):
            async with client_for(server, mode, user=user) as directory_client:
                users = await directory_client.call_tool_mcp("list_users", {})
                groups = await directory_client.call_tool_mcp("list_groups", {})
                assert not users.is_error, users
                assert not groups.is_error, groups
                assert users.structured_content == {
                    "users": [{"user_id": f"account-{user}-visible"}],
                    "total": 1,
                }
                assert groups.structured_content == {
                    "groups": [{"group_id": f"account-{user}-group"}]
                }
                assert "fixture-secret" not in str(users)
                assert "not-part-of-directory-result" not in str(groups)
        assert [row[:2] for row in server.keys.directory_calls] == [
            ("users", "account-alpha"),
            ("groups", "account-alpha"),
            ("users", "account-beta"),
            ("groups", "account-beta"),
        ]
    assert server.trace.resets
    assert all(
        identity_reset and request_reset for _, identity_reset, request_reset in server.trace.resets
    )


def install_read_probe(monkeypatch, reader):
    monkeypatch.setattr(
        endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(read_visible=reader),
            mcps=SimpleNamespace(read_live_context=AsyncMock(return_value=None)),
        ),
    )


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_http_token_bound_progress_delivers_request_milestones(
    protocol_server, monkeypatch, mode
):
    async def reader(uri, ctx, **kwargs):
        return "progress-fixture"

    install_read_probe(monkeypatch, reader)
    progress = []

    async def on_progress(current, total, message):
        progress.append((current, total, message))

    async with client_for(protocol_server, mode) as client:
        result = await client.call_tool_mcp(
            "read", {"uri": "viking://resources/progress.md"}, progress_handler=on_progress
        )
        assert not result.is_error, result
        assert "progress-fixture" in str(result.content)
    assert [(current, total) for current, total, _ in progress] == [(0, 1), (1, 1)]
    assert progress[0][2] == "Request started."
    assert "background processing status" in progress[1][2]


async def test_http_without_progress_token_emits_no_unsolicited_notifications(
    protocol_server, monkeypatch
):
    async def reader(uri, ctx, **kwargs):
        return "no-progress-token"

    install_read_probe(monkeypatch, reader)
    async with httpx.AsyncClient() as client:
        response = await client.post(
            protocol_server.url,
            headers={
                "Authorization": "Bearer alpha-fixture",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-11-25",
            },
            json={
                "jsonrpc": "2.0",
                "id": 101,
                "method": "tools/call",
                "params": {"name": "read", "arguments": {"uri": "viking://resources/note.md"}},
            },
        )
    assert response.status_code == 200, response.text
    if response.headers["content-type"].startswith("text/event-stream"):
        messages = [
            json.loads(line[5:].strip())
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
    else:
        messages = [response.json()]
    assert len(messages) == 1, messages
    assert messages[0]["id"] == 101
    assert not messages[0]["result"].get("isError")


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_http_cancellation_contract_and_subsequent_identity(
    protocol_server, monkeypatch, mode
):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    finished = asyncio.Event()
    release = asyncio.Event()
    seen = []

    async def reader(uri, ctx, **kwargs):
        seen.append((ctx.account_id, ctx.user.user_id, ctx.actor_peer_id))
        if uri.endswith("/blocked.md"):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            finally:
                finished.set()
        return f"{ctx.account_id}:{ctx.user.user_id}:{ctx.actor_peer_id}"

    install_read_probe(monkeypatch, reader)
    async with client_for(protocol_server, mode) as alpha:
        pending = asyncio.create_task(
            alpha.call_tool_mcp("read", {"uri": "viking://resources/blocked.md"})
        )
        try:
            await asyncio.wait_for(started.wait(), 3)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            if mode == "auto":
                # Modern MCP cancellation must reach the actual read handler.
                await asyncio.wait_for(cancelled.wait(), 3)
                assert finished.is_set()
            else:
                # Legacy cancellation is a separate POST. The stateless server
                # cannot identify another request's dispatcher; acknowledging
                # the notification is not evidence that server work stopped.
                await asyncio.wait_for(protocol_server.trace.cancellation_processed.wait(), 3)
                assert not cancelled.is_set()
                assert not finished.is_set()
            async with client_for(protocol_server, mode, user="beta") as beta:
                result = await beta.call_tool_mcp(
                    "read", {"uri": "viking://resources/following.md"}
                )
                assert not result.is_error, result
                assert "account-beta:beta:beta-peer" in str(result.content)
            again = await alpha.call_tool_mcp("read", {"uri": "viking://resources/again.md"})
            assert not again.is_error, again
            assert "account-alpha:alpha:alpha-peer" in str(again.content)
            if mode == "legacy":
                assert not finished.is_set()
                release.set()
                await asyncio.wait_for(finished.wait(), 3)
                assert not cancelled.is_set()
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    assert seen == [
        ("account-alpha", "alpha", "alpha-peer"),
        ("account-beta", "beta", "beta-peer"),
        ("account-alpha", "alpha", "alpha-peer"),
    ]
    assert all(
        identity_reset and request_reset
        for _, identity_reset, request_reset in protocol_server.trace.resets
    )
