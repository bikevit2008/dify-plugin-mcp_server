"""
Legacy SSE MCP endpoint — handles GET /sse.

Maintains backward compatibility with older MCP clients
that use the 2024-11-05 HTTP+SSE transport.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Mapping

from werkzeug import Request, Response
from dify_plugin import Endpoint
from dify_plugin.config.logger_format import plugin_logger_handler

from .auth import validate_bearer_token

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


def _create_sse_message(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


class SSEEndpoint(Endpoint):
    """Legacy SSE endpoint for 2024-11-05 transport."""

    def _invoke(self, r: Request, values: Mapping, settings: Mapping) -> Response:
        logger.info(f"Legacy SSE request from {r.remote_addr}")

        auth_error = validate_bearer_token(r, settings)
        if auth_error:
            return auth_error

        session_id = uuid.uuid4().hex

        def generate():
            try:
                endpoint = f"messages/?session_id={session_id}"
                yield _create_sse_message("endpoint", endpoint)

                while True:
                    if self.session.storage.exist(session_id):
                        message = self.session.storage.get(session_id)
                        message_str = message.decode("utf-8") if isinstance(message, bytes) else str(message)
                        self.session.storage.delete(session_id)
                        yield _create_sse_message("message", message_str)
                    # SSE comment as heartbeat (standards-compliant)
                    yield ": heartbeat\n\n"
                    time.sleep(0.5)
            except GeneratorExit:
                logger.info(f"Legacy SSE client disconnected: {session_id}")

        return Response(generate(), status=200, content_type="text/event-stream")