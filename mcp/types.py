"""
MCP Protocol Type Definitions.

Covers all protocol versions: 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Protocol versions we support (ordered from oldest to newest)
PROTOCOL_VERSIONS = [
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
]
LATEST_PROTOCOL_VERSION = PROTOCOL_VERSIONS[-1]
LEGACY_PROTOCOL_VERSION = PROTOCOL_VERSIONS[0]


class MCPMethod(str, Enum):
    """All MCP JSON-RPC methods across protocol versions."""

    # Lifecycle
    INITIALIZE = "initialize"
    INITIALIZED = "notifications/initialized"
    PING = "ping"
    SHUTDOWN = "shutdown"
    EXIT = "exit"

    # Tools
    TOOLS_LIST = "tools/list"
    TOOLS_CALL = "tools/call"
    TOOLS_LIST_CHANGED = "notifications/tools/list_changed"

    # Resources
    RESOURCES_LIST = "resources/list"
    RESOURCES_READ = "resources/read"
    RESOURCES_TEMPLATES_LIST = "resources/templates/list"
    RESOURCES_SUBSCRIBE = "resources/subscribe"
    RESOURCES_UNSUBSCRIBE = "resources/unsubscribe"
    RESOURCES_UPDATED = "notifications/resources/updated"
    RESOURCES_LIST_CHANGED = "notifications/resources/list_changed"

    # Prompts
    PROMPTS_LIST = "prompts/list"
    PROMPTS_GET = "prompts/get"
    PROMPTS_LIST_CHANGED = "notifications/prompts/list_changed"

    # Utilities
    CANCELLED = "notifications/cancelled"
    PROGRESS = "notifications/progress"

    # Client features (server → client)
    ROOTS_LIST = "roots/list"
    ROOTS_LIST_CHANGED = "notifications/roots/list_changed"
    SAMPLING_CREATE_MESSAGE = "sampling/createMessage"
    ELICITATION_CREATE = "elicitation/create"

    # Completion
    COMPLETION_COMPLETE = "completion/complete"

    @classmethod
    def from_string(cls, value: str) -> MCPMethod | None:
        try:
            return cls(value)
        except ValueError:
            return None


@dataclass
class JSONRPCRequest:
    """Parsed JSON-RPC 2.0 request."""
    jsonrpc: str
    id: int | str | None
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, data: dict[str, Any]) -> JSONRPCRequest:
        return cls(
            jsonrpc=data.get("jsonrpc", "2.0"),
            id=data.get("id"),
            method=data.get("method", ""),
            params=data.get("params", {}),
        )

    @property
    def is_notification(self) -> bool:
        return self.id is None


@dataclass
class JSONRPCResponse:
    """JSON-RPC 2.0 response."""
    jsonrpc: str = "2.0"
    id: int | str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error:
            d["error"] = self.error
        else:
            d["result"] = self.result if self.result is not None else {}
        return d


# Standard JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
SERVER_NOT_INITIALIZED = -32002


@dataclass
class MCPSession:
    """MCP session state."""
    session_id: str
    protocol_version: str = LEGACY_PROTOCOL_VERSION
    client_info: dict[str, Any] = field(default_factory=dict)
    initialized: bool = False
    created_at: float = field(default_factory=time.monotonic)
    last_active: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_active = time.monotonic()

    def is_expired(self, ttl_seconds: int) -> bool:
        return (time.monotonic() - self.last_active) > ttl_seconds


def negotiate_version(client_version: str) -> str | None:
    """Negotiate the highest protocol version both sides support.
    Returns None only if client_version is unrecognizably malformed.
    """
    if client_version in PROTOCOL_VERSIONS:
        return client_version
    # Try to find the highest version <= client_version
    for v in reversed(PROTOCOL_VERSIONS):
        if v <= client_version:
            return v
    # Client version is older than our oldest — return oldest supported
    return PROTOCOL_VERSIONS[0]


def constant_time_compare(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    return hmac.compare_digest(
        a.encode("utf-8") if isinstance(a, str) else a,
        b.encode("utf-8") if isinstance(b, str) else b,
    )