"""
Streamable HTTP MCP endpoint — handles POST /mcp.

Full MCP protocol support with thread-safety, auth, validation, and streaming.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from werkzeug import Request, Response
from dify_plugin import Endpoint
from dify_plugin.config.logger_format import plugin_logger_handler

from mcp import MCPHandler, JSONRPCResponse, MCPMethod
from mcp.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)

MAX_BODY_SIZE = 1_048_576  # 1MB

_handlers: dict[str, MCPHandler] = {}


def _validate_and_parse_tools(settings: Mapping) -> list[dict[str, Any]]:
    """Parse and validate tool configurations from settings.
    Priority: custom JSON override > simple fields > empty."""
    tools: list[dict[str, Any]] = []

    legacy_schema = settings.get("app-input-schema")
    if legacy_schema:
        try:
            parsed = json.loads(legacy_schema)
            if not isinstance(parsed, dict):
                logger.error(f"app-input-schema must be a JSON object, got {type(parsed).__name__}")
            else:
                parsed["_app_id"] = settings.get("app", {}).get("app_id")
                parsed["_app_type"] = settings.get("app-type", "workflow")
                tools.append(parsed)
                return tools
        except json.JSONDecodeError as e:
            logger.error(f"Invalid app-input-schema JSON: {e}")

    tool_name = (settings.get("tool-name") or "").strip()
    if tool_name:
        tool_title = (settings.get("tool-title") or tool_name).strip()
        tool_description = (settings.get("tool-description") or "").strip()
        app_id = settings.get("app", {}).get("app_id")
        app_type = settings.get("app-type", "workflow")
        tools.append({
            "name": tool_name, "title": tool_title, "description": tool_description,
            "inputSchema": {"type": "object", "properties": {}},
            "_app_id": app_id, "_app_type": app_type,
        })

    tools_json = settings.get("tools-json")
    if tools_json:
        try:
            parsed = json.loads(tools_json)
            if isinstance(parsed, list):
                for i, entry in enumerate(parsed):
                    if not isinstance(entry, dict):
                        logger.error(f"tools-json[{i}] must be an object, got {type(entry).__name__}")
                        continue
                    if "name" not in entry or "inputSchema" not in entry:
                        logger.error(f"tools-json[{i}] missing required field")
                        continue
                    tools.append(entry)
            else:
                logger.error(f"tools-json must be a JSON array, got {type(parsed).__name__}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid tools-json JSON: {e}")

    return tools


def _get_or_create_handler(settings: Mapping) -> MCPHandler:
    """Get or create a thread-safe MCPHandler. Does NOT capture session."""
    app_id = settings.get("app", {}).get("app_id", "default")
    tools_json_hash = hash(settings.get("tools-json", "") or "")
    legacy_hash = hash(settings.get("app-input-schema", "") or "")
    cache_key = f"{app_id}:{tools_json_hash}:{legacy_hash}"

    if cache_key not in _handlers:
        tools = _validate_and_parse_tools(settings)
        session_ttl = int(settings.get("session-ttl", "30") or "30") * 60
        rate_limit_val = int(settings.get("rate-limit", "0") or "0")

        _handlers[cache_key] = MCPHandler(
            server_name="Dify MCP Server", server_version="1.0.0",
            session_ttl=session_ttl, tools=tools, tool_invoker=None,
            auth_token=settings.get("auth-token"),
        )
        _handlers[cache_key].rate_limiter = RateLimiter(
            rate=rate_limit_val / 60.0 if rate_limit_val > 0 else 0,
            burst=rate_limit_val if rate_limit_val > 0 else 0,
        )

    return _handlers[cache_key]


def _make_tool_invoker(handler: MCPHandler, session_storage):
    """Create a fresh tool_invoker bound to the CURRENT request session.
    Called on EVERY request to avoid stale session closures (CRITICAL FIX)."""
    tools = handler._raw_tools

    def tool_invoker(tool_name: str, arguments: dict[str, Any]):
        tool_config = None
        for t in tools:
            if t.get("name") == tool_name:
                tool_config = t
                break
        if not tool_config:
            raise ValueError(f"Unknown tool: {tool_name}")

        target_app_id = tool_config.get("_app_id")
        target_app_type = tool_config.get("_app_type", "workflow")
        if not target_app_id:
            raise ValueError(f"No app_id configured for tool: {tool_name}")

        if target_app_type == "chat":
            result = session_storage.session.app.chat.invoke(
                app_id=target_app_id,
                query=arguments.get("query", arguments.get("input_for_search", "")),
                inputs=arguments, response_mode="streaming",
            )
            final_text = ""
            for event in result:
                if isinstance(event, dict):
                    data = event.get("data", event)
                    if isinstance(data, dict):
                        chunk_text = data.get("answer", data.get("text", ""))
                        final_text += chunk_text
                        yield {"type": "progress", "text": chunk_text}
                    elif isinstance(data, str):
                        final_text += data
                        yield {"type": "progress", "text": data}
                elif isinstance(event, str):
                    final_text += event
                    yield {"type": "progress", "text": event}
            yield {"type": "result", "content": [{"type": "text", "text": final_text}], "isError": False}
        else:
            yield {"type": "progress", "text": f"Running {tool_name}..."}
            result = session_storage.session.app.workflow.invoke(
                app_id=target_app_id, inputs=arguments, response_mode="blocking",
            )
            outputs = result.get("data", {}).get("outputs", {})
            text_parts = []
            for v in outputs.values():
                if isinstance(v, str):
                    text_parts.append(v)
                elif isinstance(v, (dict, list)):
                    text_parts.append(json.dumps(v, ensure_ascii=False))
                else:
                    text_parts.append(str(v))
            final_text = "\n".join(text_parts) if text_parts else "(empty output)"
            yield {"type": "result", "content": [{"type": "text", "text": final_text}], "isError": False}

    return tool_invoker


class StreamableHTTPEndpoint(Endpoint):
    """Streamable HTTP MCP endpoint — POST /mcp."""

    def _invoke(self, r: Request, values: Mapping, settings: Mapping) -> Response:
        logger.info(f"MCP POST request from {r.remote_addr}")

        handler = _get_or_create_handler(settings)
        handler.tool_invoker = _make_tool_invoker(handler, self)

        # Rate limit check
        if hasattr(handler, 'rate_limiter') and not handler.rate_limiter.acquire():
            return Response(
                json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": "Rate limit exceeded. Retry later."}}),
                status=429, content_type="application/json", headers={"Retry-After": "1"},
            )

        # Body size check
        content_length = r.content_length
        if content_length and content_length > MAX_BODY_SIZE:
            return Response(
                json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Request body too large"}}),
                status=413, content_type="application/json",
            )

        # Content-Type validation
        content_type = r.headers.get("Content-Type", "")
        if r.method == "POST" and content_type and not content_type.startswith("application/json"):
            return Response(
                json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Content-Type must be application/json"}}),
                status=415, content_type="application/json",
            )

        # Parse body
        try:
            body = r.get_json(force=True, silent=False) or {}
        except Exception:
            return Response(
                json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}),
                status=400, content_type="application/json",
            )

        # Auth check
        request_id = body.get("id") if isinstance(body, dict) else None
        auth_header = r.headers.get("Authorization")
        auth_error = handler.check_auth(auth_header, request_id=request_id)
        if auth_error:
            return Response(json.dumps(auth_error.to_dict()), status=401, content_type="application/json")

        # Session
        session_id = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")

        # Handle request
        response, new_session_id, extra_headers = handler.handle_request(body, session_id)

        # Build headers
        response_headers: dict[str, str] = {}
        response_headers.update(extra_headers)
        active_sid = new_session_id or session_id
        if new_session_id and new_session_id != session_id:
            response_headers["Mcp-Session-Id"] = new_session_id
        if active_sid:
            session = handler.get_session(active_sid)
            if session:
                response_headers["MCP-Protocol-Version"] = session.protocol_version

        # SSE streaming for generator responses
        if hasattr(response, '__iter__') and not isinstance(response, (dict, str, JSONRPCResponse)):
            def sse_stream():
                try:
                    yield f"event: endpoint\ndata: {json.dumps({'sessionId': active_sid})}\n\n"
                    for chunk in response:
                        yield chunk
                except GeneratorExit:
                    logger.info("SSE stream disconnected by client")
            response_headers["Content-Type"] = "text/event-stream"
            response_headers["Cache-Control"] = "no-cache"
            response_headers["Connection"] = "keep-alive"
            response_headers["X-Accel-Buffering"] = "no"
            return Response(sse_stream(), status=200, content_type="text/event-stream", headers=response_headers)

        response_headers["Content-Type"] = "application/json"

        # Notifications
        method = body.get("method", "")
        if method.startswith("notifications/") or body.get("id") is None:
            return Response("", status=202, content_type="application/json", headers=response_headers)

        # Status code
        status_code = 200
        if response.error:
            code = response.error.get("code")
            if code in (-32700, -32600, -32602): status_code = 400
            elif code == -32002: status_code = 400
            elif code == -32601: status_code = 404

        return Response(json.dumps(response.to_dict()), status=status_code, content_type="application/json", headers=response_headers)