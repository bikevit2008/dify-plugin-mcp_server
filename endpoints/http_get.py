"""
Streamable HTTP MCP endpoint — handles GET /mcp.

Opens an SSE stream for server-to-client notifications.
Required by Devin, Claude Code, and other modern MCP clients.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Mapping

from werkzeug import Request, Response
from dify_plugin import Endpoint
from dify_plugin.config.logger_format import plugin_logger_handler

from mcp import MCPHandler

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


def _create_sse_event(event: str | None, data: str) -> str:
    """Create an SSE-formatted event."""
    lines = []
    if event:
        lines.append(f"event: {event}")
    lines.append(f"data: {data}")
    lines.append("")  # Empty line terminates the event
    return "\n".join(lines) + "\n"


class StreamableHTTPGetEndpoint(Endpoint):
    """Streamable HTTP MCP endpoint — GET /mcp for SSE streaming."""

    def _invoke(self, r: Request, values: Mapping, settings: Mapping) -> Response:
        logger.info(f"MCP GET (SSE) request from {r.remote_addr}")

        from .http_post import _get_or_create_handler, _make_tool_invoker
        handler = _get_or_create_handler(settings)
        handler.tool_invoker = _make_tool_invoker(handler, self)

        auth_header = r.headers.get("Authorization")
        auth_error = handler.check_auth(auth_header)
        if auth_error:
            return Response(
                json.dumps(auth_error.to_dict()),
                status=401,
                content_type="application/json",
            )

        session_id = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
        session = handler.get_session(session_id) if session_id else None

        if not session:
            return Response(
                json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32002, "message": "Session not found"}}),
                status=400,
                content_type="application/json",
            )

        heartbeat_interval = 30

        def generate():
            try:
                # Send initial endpoint event
                yield _create_sse_event("endpoint", json.dumps({
                    "sessionId": session.session_id,
                }))

                last_heartbeat = time.time()

                while True:
                    if session.is_expired(handler.session_ttl):
                        yield _create_sse_event("error", json.dumps({
                            "code": -32002,
                            "message": "Session expired",
                        }))
                        return

                    now = time.time()
                    if now - last_heartbeat >= heartbeat_interval:
                        # SSE comment as heartbeat (standards-compliant, not a JSON-RPC method)
                        yield ": heartbeat\n\n"
                        last_heartbeat = now

                    time.sleep(1)
            except GeneratorExit:
                logger.info(f"SSE client disconnected: {session.session_id}")

        return Response(
            generate(),
            status=200,
            content_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )