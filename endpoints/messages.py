"""
Legacy SSE Messages endpoint — handles POST /messages/.

Receives JSON-RPC requests for SSE-based sessions.
Delegates to the shared MCPHandler for protocol logic.
Bridges SSE transport session IDs with MCPHandler session IDs.
"""
from __future__ import annotations

import json
import logging
from typing import Mapping

from werkzeug import Request, Response
from dify_plugin import Endpoint
from dify_plugin.config.logger_format import plugin_logger_handler

from .auth import validate_bearer_token

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


class MessageEndpoint(Endpoint):
    """Legacy SSE messages endpoint."""

    def _invoke(self, r: Request, values: Mapping, settings: Mapping) -> Response:
        logger.info(f"Legacy messages request from {r.remote_addr}")

        auth_error = validate_bearer_token(r, settings)
        if auth_error:
            return auth_error

        from .http_post import _get_or_create_handler, _make_tool_invoker
        handler = _get_or_create_handler(settings)
        handler.tool_invoker = _make_tool_invoker(handler, self)

        sse_session_id = r.args.get("session_id", "")

        # Resolve MCP session from SSE session mapping
        mcp_session_id = None
        mapping_key = f"_mcp_sid_{sse_session_id}"
        if self.session.storage.exist(mapping_key):
            mcp_session_id = self.session.storage.get(mapping_key)
            mcp_session_id = mcp_session_id.decode("utf-8") if isinstance(mcp_session_id, bytes) else str(mcp_session_id)

        body = r.get_json(force=True, silent=False) or {}

        response, new_session_id, extra_headers = handler.handle_request(body, mcp_session_id or None)

        # If initialize created a new MCP session, store the mapping
        if new_session_id and new_session_id != mcp_session_id:
            self.session.storage.set(mapping_key, new_session_id.encode("utf-8"))

        # Store response in storage for the SSE poll loop
        # Use the SSE session_id so the SSE endpoint can find it
        self.session.storage.set(
            sse_session_id,
            json.dumps(response.to_dict()).encode("utf-8"),
        )

        return Response("", status=202, content_type="application/json")