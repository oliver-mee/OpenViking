# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Unexpected backend failures must not disclose secrets in MCP results or logs."""

import asyncio
import json
import logging
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server import mcp_endpoint as endpoint
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.fastmcp_contract import call_tool

SECRET = "redaction-test-secret-never-log"
URI = "viking://resources/redaction-fixture.md"


@pytest.fixture(autouse=True)
def identity():
    token = endpoint._mcp_ctx.set(
        RequestContext(
            user=UserIdentifier.the_default_user("redaction-fixture"),
            role=Role.ROOT,
        )
    )
    yield
    endpoint._mcp_ctx.reset(token)


def _dump(result):
    if hasattr(result, "model_dump"):
        return json.dumps(result.model_dump(by_alias=True), default=str)
    return str(result)


@pytest.mark.parametrize("tool", ["write", "edit", "glob", "add_resource", "add_skill", "health"])
async def test_unexpected_errors_are_redacted_on_wire_and_in_logs(tool, monkeypatch, caplog):
    failure = RuntimeError(
        f"api key rejected; Authorization: Bearer {SECRET}; "
        f"https://user:{SECRET}@private.invalid/private-path"
    )
    fs = SimpleNamespace(
        write=AsyncMock(side_effect=failure),
        read_visible=AsyncMock(side_effect=failure),
        glob=AsyncMock(side_effect=failure),
    )
    service = SimpleNamespace(
        fs=fs,
        resources=SimpleNamespace(add_resource=AsyncMock(side_effect=failure)),
    )
    monkeypatch.setattr(endpoint, "get_service", lambda: service)
    monkeypatch.setattr(endpoint, "install_skills", AsyncMock(side_effect=failure))
    if tool == "health":
        monkeypatch.setattr(endpoint, "get_service", lambda: (_ for _ in ()).throw(failure))
    arguments = {
        "write": {"uri": URI, "content": "fixture"},
        "edit": {"uri": URI, "old_string": "old", "new_string": "new"},
        "glob": {"uri": "viking://resources", "pattern": "*.md"},
        "add_resource": {"path": "https://example.com/fixture"},
        "add_skill": {"data": "---\nname: fixture\ndescription: Fixture\n---\nBody"},
        "health": {},
    }[tool]
    caplog.set_level(logging.ERROR, logger=endpoint.logger.name)

    result = await call_tool(endpoint.mcp, tool, arguments)
    wire = _dump(result)

    assert SECRET not in wire
    assert "private.invalid" not in wire
    assert "private-path" not in wire
    assert SECRET not in caplog.text
    assert "INTERNAL" in wire
    reference = re.search(r"Reference: ([0-9a-f]{32})", wire)
    assert reference, wire
    assert f"error_id={reference[1]}" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    if tool != "health":
        assert result.is_error is True


async def test_partial_grep_preserves_matches_but_redacts_failed_pattern(monkeypatch, caplog):
    async def grep(_uri, pattern, **_kwargs):
        if pattern == "bad":
            raise RuntimeError(SECRET)
        return {"matches": [{"uri": URI, "line": 7, "content": "retained match"}]}

    monkeypatch.setattr(
        endpoint, "get_service", lambda: SimpleNamespace(fs=SimpleNamespace(grep=grep))
    )
    caplog.set_level(logging.ERROR, logger=endpoint.logger.name)
    result = await call_tool(
        endpoint.mcp, "grep", {"uri": "viking://resources", "pattern": ["good", "bad"]}
    )
    wire = _dump(result)
    assert "retained match" in wire
    assert "bad: INTERNAL:" in wire
    assert SECRET not in wire + caplog.text


@pytest.mark.parametrize("failed_uri", ["viking://resources/bad.md", "viking://resources/bad.png"])
async def test_partial_batch_read_preserves_other_files_on_backend_error(
    failed_uri, monkeypatch, caplog
):
    async def read_visible(uri, **_kwargs):
        if uri == failed_uri:
            raise RuntimeError(SECRET)
        return "retained document"

    service = SimpleNamespace(
        fs=SimpleNamespace(
            read_visible=read_visible,
            stat=AsyncMock(side_effect=RuntimeError(SECRET)),
        ),
        mcps=SimpleNamespace(read_live_context=AsyncMock(return_value=None)),
    )
    monkeypatch.setattr(endpoint, "get_service", lambda: service)
    caplog.set_level(logging.ERROR, logger=endpoint.logger.name)
    result = await call_tool(endpoint.mcp, "read", {"uris": [URI, failed_uri]})
    wire = _dump(result)
    assert "retained document" in wire
    assert failed_uri in wire
    assert "INTERNAL" in wire
    assert SECRET not in wire + caplog.text


async def test_public_business_error_keeps_code_and_details(monkeypatch, caplog):
    failure = PermissionDeniedError("write permission required", resource=URI)
    monkeypatch.setattr(
        endpoint,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(write=AsyncMock(side_effect=failure))),
    )
    result = await call_tool(endpoint.mcp, "write", {"uri": URI, "content": "fixture"})
    assert result.is_error is True
    assert result.structured_content["error"] == {
        "code": "PERMISSION_DENIED",
        "message": "write permission required",
        "details": {"resource": URI},
    }
    assert "MCP unexpected failure" not in caplog.text


async def test_request_cancellation_is_not_converted_to_internal_error(monkeypatch, caplog):
    monkeypatch.setattr(
        endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(write=AsyncMock(side_effect=asyncio.CancelledError()))
        ),
    )
    with pytest.raises(asyncio.CancelledError):
        await call_tool(endpoint.mcp, "write", {"uri": URI, "content": "fixture"})
    assert "MCP unexpected failure" not in caplog.text
