# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Keep outbound MCP transport diagnostics useful without logging private payloads."""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, AsyncContextManager, Iterator

_redact_transport_logs: ContextVar[bool] = ContextVar("redact_mcp_transport_logs", default=False)
_TRANSPORT_LOGGERS = (
    "httpx2",
    "client",  # Official SDK ClientSession logger.
    "mcp.client.streamable_http",
    "mcp.client.sse",
    "mcp.client.stdio",
    "mcp.shared.jsonrpc_dispatcher",
)


class _TransportLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not _redact_transport_logs.get():
            return True

        # HTTPX logs URLs, and SDK parsing errors can include entire response
        # bodies (including reflected credentials) in their exception chains.
        # Preserve severity, logger identity, HTTP status and exception class,
        # but never serialize untrusted transport messages or traceback text.
        status = None
        if record.name == "httpx2" and isinstance(record.args, tuple) and len(record.args) >= 4:
            candidate = record.args[3]
            if isinstance(candidate, int) and 100 <= candidate <= 599:
                status = candidate
        exception = record.exc_info[1] if record.exc_info else None
        error_type = type(exception).__name__ if exception is not None else None
        message = "Outbound MCP transport diagnostic"
        if status is not None:
            message += f"; http_status={status}"
        if error_type is not None:
            message += f"; error_type={error_type}"
        record.msg = message
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        # Some logging integrations precompute this attribute.
        record.message = message
        return True


_transport_log_filter = _TransportLogFilter()


@contextmanager
def redact_mcp_transport_logs() -> Iterator[None]:
    """Redact only this task and SDK tasks created in its context.

    Install permanent, context-aware filters rather than changing logger levels
    or adding/removing filters per request, which would race concurrent callers.
    """
    for name in _TRANSPORT_LOGGERS:
        logging.getLogger(name).addFilter(_transport_log_filter)
    token = _redact_transport_logs.set(True)
    try:
        yield
    finally:
        _redact_transport_logs.reset(token)


class _RedactedTransportContext:
    def __init__(self, manager: AsyncContextManager[Any]) -> None:
        self._manager = manager

    async def __aenter__(self) -> Any:
        with redact_mcp_transport_logs():
            return await self._manager.__aenter__()

    async def __aexit__(self, *exc_info: Any) -> bool | None:
        with redact_mcp_transport_logs():
            return await self._manager.__aexit__(*exc_info)


def redact_mcp_transport_context(manager: AsyncContextManager[Any]) -> AsyncContextManager[Any]:
    """Protect transport entry/teardown without changing the owner's context.

    SDK background tasks inherit the entry scope. Tool calls still need their
    own scope because they can run in tasks created after connection setup.
    """
    return _RedactedTransportContext(manager)
