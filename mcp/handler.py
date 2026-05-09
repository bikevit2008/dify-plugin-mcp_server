"""
MCP Protocol Handler — core logic for all MCP methods.

Thread-safe. Handles all protocol versions 2024-11-05 through 2025-11-25.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Callable

from .types import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    LATEST_PROTOCOL_VERSION,
    LEGACY_PROTOCOL_VERSION,
    METHOD_NOT_FOUND,
    MCPMethod,
    MCPSession,
    PARSE_ERROR,
    PROTOCOL_VERSIONS,
    SERVER_NOT_INITIALIZED,
    JSONRPCRequest,
    JSONRPCResponse,
    constant_time_compare,
    negotiate_version,
)

logger = logging.getLogger(__name__)

ToolInvoker = Callable[[str, dict[str, Any]], dict[str, Any]]

MAX_BODY_SIZE = 1_048_576  # 1MB
MAX_SESSIONS = 10_000
REQUIRED_TOOL_FIELDS = {"name", "inputSchema"}


class MCPHandler:
    """Thread-safe MCP protocol handler."""

    def __init__(
        self,
        server_name: str = "Dify MCP Server",
        server_version: str = "1.0.0",
        session_ttl: int = 1800,
        tools: list[dict[str, Any]] | None = None,
        tool_invoker: ToolInvoker | None = None,
        auth_token: str | None = None,
    ) -> None:
        self.server_name = server_name
        self.server_version = server_version
        self.session_ttl = session_ttl
        self._raw_tools = tools or []
        self.tool_invoker = tool_invoker
        self.auth_token = auth_token
        self._sessions: dict[str, MCPSession] = {}
        self._lock = threading.Lock()
        # Separate lookup for internal app routing (never exposed to clients)
        self._tool_routing: dict[str, dict[str, str]] = {}

    # ── Public tool list (strips internal fields) ────────────────────

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Return tools safe for client exposure (no _app_id, _app_type)."""
        return [
            {k: v for k, v in t.items() if not k.startswith("_")}
            for t in self._raw_tools
        ]

    # ── Session management (thread-safe) ────────────────────────────

    def create_session(self, protocol_version: str, client_info: dict[str, Any]) -> MCPSession:
        with self._lock:
            self._cleanup_expired()
            if len(self._sessions) >= MAX_SESSIONS:
                raise RuntimeError("Maximum session limit reached")
            session_id = uuid.uuid4().hex
            session = MCPSession(
                session_id=session_id,
                protocol_version=protocol_version,
                client_info=client_info,
            )
            self._sessions[session_id] = session
            return session

    def get_session(self, session_id: str) -> MCPSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session.is_expired(self.session_ttl):
                del self._sessions[session_id]
                return None
            if session:
                session.touch()
            return session

    def _cleanup_expired(self) -> None:
        # Must be called under lock
        expired = [
            sid for sid, s in self._sessions.items()
            if s.is_expired(self.session_ttl)
        ]
        for sid in expired:
            del self._sessions[sid]

    def remove_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    # ── Auth (receives request context for proper id) ────────────────

    def check_auth(self, auth_header: str | None, request_id: int | str | None = None) -> JSONRPCResponse | None:
        if not self.auth_token:
            return None
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONRPCResponse(
                id=request_id,
                error={"code": -32000, "message": "Invalid or missing Authorization header"},
            )
        token = auth_header.removeprefix("Bearer ").strip()
        if not constant_time_compare(token, self.auth_token):
            return JSONRPCResponse(
                id=request_id,
                error={"code": -32000, "message": "Invalid or missing token"},
            )
        return None

    # ── Request dispatch ─────────────────────────────────────────────

    def handle_request(
        self, body: dict[str, Any], session_id: str | None = None
    ) -> tuple[JSONRPCResponse, str | None, dict[str, str]]:
        """
        Handle a JSON-RPC request. Thread-safe.
        Returns (response, session_id, extra_headers).
        """
        extra_headers: dict[str, str] = {}

        # Parse JSON-RPC
        try:
            req = JSONRPCRequest.parse(body)
        except Exception:
            return JSONRPCResponse(id=None, error={"code": PARSE_ERROR, "message": "Parse error"}), None, extra_headers

        # Validate jsonrpc version
        if req.jsonrpc != "2.0":
            return JSONRPCResponse(id=req.id, error={"code": INVALID_REQUEST, "message": "Invalid JSON-RPC version"}), None, extra_headers

        method = MCPMethod.from_string(req.method)

        # Initialize — allowed without session
        if method == MCPMethod.INITIALIZE:
            response, new_sid = self._handle_initialize(req)
            return response, new_sid, extra_headers

        # Ping — allowed without session (health check)
        if method == MCPMethod.PING:
            return self._handle_ping(req), session_id, extra_headers

        # All other methods require a valid session
        session = self.get_session(session_id) if session_id else None
        if not session:
            return JSONRPCResponse(
                id=req.id,
                error={"code": SERVER_NOT_INITIALIZED, "message": "Session not found or expired. Re-initialize."},
            ), None, extra_headers

        # Require initialized notification before any tool/resource/prompt methods
        if not session.initialized:
            if method == MCPMethod.INITIALIZED:
                return self._handle_initialized(session, req), session_id, extra_headers
            return JSONRPCResponse(
                id=req.id,
                error={"code": SERVER_NOT_INITIALIZED, "message": "Not initialized"},
            ), session_id, extra_headers

        # Dispatch
        handlers: dict[MCPMethod | None, Callable] = {
            MCPMethod.TOOLS_LIST: self._handle_tools_list,
            MCPMethod.TOOLS_CALL: self._handle_tools_call,
            MCPMethod.RESOURCES_LIST: self._handle_resources_list,
            MCPMethod.RESOURCES_READ: self._handle_resources_read,
            MCPMethod.RESOURCES_TEMPLATES_LIST: self._handle_resources_templates_list,
            MCPMethod.PROMPTS_LIST: self._handle_prompts_list,
            MCPMethod.PROMPTS_GET: self._handle_prompts_get,
            MCPMethod.SHUTDOWN: self._handle_shutdown,
            MCPMethod.CANCELLED: self._handle_cancelled,
            MCPMethod.COMPLETION_COMPLETE: self._handle_completion,
            MCPMethod.ROOTS_LIST: self._handle_roots_list,
            MCPMethod.ELICITATION_CREATE: self._handle_elicitation,
        }

        handler = handlers.get(method)
        if handler:
            return handler(session, req), session_id, extra_headers

        return JSONRPCResponse(
            id=req.id,
            error={"code": METHOD_NOT_FOUND, "message": f"Method not found: {req.method}"},
        ), session_id, extra_headers

    # ── Lifecycle handlers ───────────────────────────────────────────

    def _handle_initialize(self, req: JSONRPCRequest) -> tuple[JSONRPCResponse, str | None]:
        client_version = req.params.get("protocolVersion", LEGACY_PROTOCOL_VERSION)
        negotiated = negotiate_version(client_version)
        if negotiated is None:
            return JSONRPCResponse(
                id=req.id,
                error={"code": INVALID_PARAMS, "message": f"Unsupported protocol version: {client_version}"},
            ), None

        try:
            session = self.create_session(negotiated, req.params.get("clientInfo", {}))
        except RuntimeError:
            return JSONRPCResponse(
                id=req.id,
                error={"code": INTERNAL_ERROR, "message": "Server at capacity, try again later"},
            ), None

        logger.info(f"MCP session created: {session.session_id}, protocol: {negotiated}")

        capabilities: dict[str, Any] = {"tools": {"listChanged": False}}

        if negotiated >= "2025-03-26":
            capabilities["prompts"] = {"listChanged": False}
            capabilities["resources"] = {"subscribe": False, "listChanged": False}

        return JSONRPCResponse(
            id=req.id,
            result={
                "protocolVersion": negotiated,
                "capabilities": capabilities,
                "serverInfo": {"name": self.server_name, "version": self.server_version},
                "instructions": f"Use tools/list to discover available Dify tools. Session TTL: {self.session_ttl}s.",
            },
        ), session.session_id

    def _handle_ping(self, req: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(id=req.id, result={})

    def _handle_initialized(self, session: MCPSession, req: JSONRPCRequest) -> JSONRPCResponse:
        session.initialized = True
        logger.info(f"MCP session initialized: {session.session_id}")
        return JSONRPCResponse(id=req.id, result={})

    def _handle_shutdown(self, session: MCPSession, req: JSONRPCRequest) -> JSONRPCResponse:
        self.remove_session(session.session_id)
        logger.info(f"MCP session shutdown: {session.session_id}")
        # Per MCP lifecycle spec: server should signal exit after shutdown
        return JSONRPCResponse(id=req.id, result={"message": "Shutdown complete"})

    # ── Tool handlers ────────────────────────────────────────────────

    def _handle_tools_list(self, session: MCPSession, req: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(
            id=req.id,
            result={
                "tools": self.tools,
                "nextCursor": None,  # Explicit: all results returned
            },
        )

    def _handle_tools_call(self, session: MCPSession, req: JSONRPCRequest) -> JSONRPCResponse:
        tool_name = req.params.get("name", "")
        arguments = req.params.get("arguments", {})

        # Validate tool exists (before checking invoker — better error message)
        known_names = {t["name"] for t in self.tools}
        if tool_name not in known_names:
            return JSONRPCResponse(
                id=req.id,
                error={"code": INVALID_PARAMS, "message": f"Unknown tool: {tool_name}. Available: {', '.join(sorted(known_names))}"},
            )

        if not self.tool_invoker:
            return JSONRPCResponse(
                id=req.id,
                error={"code": INTERNAL_ERROR, "message": "No tool invoker configured"},
            )

        try:
            result = self.tool_invoker(tool_name, arguments)
            return JSONRPCResponse(
                id=req.id,
                result={
                    "content": result.get("content", []),
                    "isError": result.get("isError", False),
                },
            )
        except ValueError as e:
            return JSONRPCResponse(
                id=req.id,
                error={"code": INVALID_PARAMS, "message": str(e)},
            )
        except Exception:
            logger.exception(f"Tool call failed: {tool_name}")
            return JSONRPCResponse(
                id=req.id,
                error={"code": INTERNAL_ERROR, "message": "Internal tool execution error"},
            )

    # ── Resource handlers ────────────────────────────────────────────

    def _handle_resources_list(self, session: MCPSession, req: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(id=req.id, result={"resources": []})

    def _handle_resources_read(self, session: MCPSession, req: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(id=req.id, error={"code": INVALID_PARAMS, "message": "Resource not found"})

    def _handle_resources_templates_list(self, session: MCPSession, req: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(id=req.id, result={"resourceTemplates": []})

    # ── Prompt handlers ──────────────────────────────────────────────

    def _handle_prompts_list(self, session: MCPSession, req: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(id=req.id, result={"prompts": []})

    def _handle_prompts_get(self, session: MCPSession, req: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(id=req.id, error={"code": INVALID_PARAMS, "message": "Prompt not found"})

    # ── Utility handlers ─────────────────────────────────────────────

    def _handle_cancelled(self, session: MCPSession, req: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(id=req.id, result={})

    def _handle_completion(self, session: MCPSession, req: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(id=req.id, result={"completion": {"values": [], "total": 0, "hasMore": False}})

    # ── Client feature handlers ──────────────────────────────────────

    def _handle_roots_list(self, session: MCPSession, req: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(id=req.id, result={"roots": []})

    def _handle_elicitation(self, session: MCPSession, req: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(id=req.id, error={"code": METHOD_NOT_FOUND, "message": "Elicitation not supported"})