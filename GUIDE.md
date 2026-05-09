# MCP Server Plugin — Developer Guide

## Architecture

```
mcp/
├── types.py      # Protocol types, version negotiation, session model
├── handler.py    # Core MCP protocol logic (stateless, injectable)
└── __init__.py   # Public API exports

endpoints/
├── http_post.py  # Streamable HTTP POST /mcp — main handler
├── http_get.py   # Streamable HTTP GET /mcp — SSE streaming
├── sse.py        # Legacy SSE transport (2024-11-05)
├── messages.py   # Legacy SSE messages endpoint
└── auth.py       # Bearer token validation

group/
└── mcp-server.yaml  # UI configuration (settings form)
```

## Design Decisions

### 1. Handler is stateless with injected dependencies

`MCPHandler` doesn't depend on Dify SDK directly. All Dify calls go through a `tool_invoker` callback injected at construction time. This makes the handler testable without a Dify runtime.

### 2. Session store is in-memory

Sessions live in `MCPHandler._sessions` dict. This is acceptable because:
- Dify plugin daemon runs plugins as subprocesses — memory is per-plugin
- Sessions have TTL (30 min default) — auto-cleanup
- No external dependency (Redis, DB) needed

### 3. Version negotiation is optimistic

We support all versions from 2024-11-05 to 2025-11-25. If a client sends a version we don't know:
- Older than 2024-11-05 → return 2024-11-05
- Newer than 2025-11-25 → return 2025-11-25
- Between known versions → return the highest supported <= client version

### 4. Multi-tool via JSON config

Due to Dify Plugin SDK limitations (no dynamic list fields in settings UI), multi-tool config is a JSON text field. Each tool specifies `_app_id` and `_app_type` to route calls to the correct Dify app.

## Testing

### Unit tests (MCPHandler)

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from mcp.handler import MCPHandler
# ... test code ...
"
```

### Integration test (curl)

```bash
# Initialize
curl -X POST https://your-instance/e/xxxxx/mcp \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer your-token' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# List tools (include Mcp-Session-Id from initialize response)
curl -X POST https://your-instance/e/xxxxx/mcp \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer your-token' \
  -H 'Mcp-Session-Id: <session-id>' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'

# Call tool
curl -X POST https://your-instance/e/xxxxx/mcp \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer your-token' \
  -H 'Mcp-Session-Id: <session-id>' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"web_search","arguments":{"query":"test"}}}'
```

## Adding New Protocol Versions

1. Add the version string to `PROTOCOL_VERSIONS` in `mcp/types.py`
2. Update `LATEST_PROTOCOL_VERSION`
3. Add any version-specific capability logic in `_handle_initialize`
4. Update the protocol support matrix in README.md

## Security Considerations

### What we protect against

- **Timing attacks**: `constant_time_compare` for token validation
- **Session hijacking**: UUID-based session IDs, TTL expiry
- **Information leakage**: Internal errors return generic messages, not stack traces
- **Replay attacks**: Sessions expire after inactivity

### What we don't protect against (by design)

- **DDoS**: Rate limiting is configurable but not enforced in-code (handled by Dify/nginx layer)
- **CSRF**: Not applicable — MCP is API-to-API, no browser cookies
- **XSS**: Not applicable — no HTML rendering

### What the Dify platform handles

- TLS termination (HTTPS)
- Request size limits
- Network-level rate limiting
- Plugin sandboxing (process isolation)