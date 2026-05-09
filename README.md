# MCP Server Plugin for Dify

**Version:** 1.0.0  
**Author:** hjlarry  
**Minimum Dify Version:** 1.3.0  

Expose any Dify workflow or chat application as an MCP (Model Context Protocol) tool.  
One endpoint = one or many MCP tools. Works with **any** MCP client.

## What's New in 1.0.0

- **Full protocol version negotiation** — supports 2024-11-05 through 2025-11-25
- **Mcp-Session-Id header** — proper session management per MCP spec
- **MCP-Protocol-Version header** — required by 2025-06-18+ clients
- **GET /mcp SSE streaming** — server→client notifications (required by Devin, Claude Code)
- **Multi-tool support** — one endpoint can expose multiple Dify apps as separate MCP tools
- **All MCP methods** — tools, resources, prompts, ping, shutdown, cancellation, completion, roots, elicitation
- **Security hardening** — constant-time token comparison, session TTL, safe error messages
- **Backward compatible** — legacy SSE transport still supported

## Quick Start

### 1. Create a Dify App

Create a workflow or chat app in Dify. Define its input variables — these become the MCP tool's parameters.

### 2. Add MCP Server Endpoint

1. Go to your app → **Endpoints** tab
2. Click **Add Endpoint** → select **MCP Server**
3. Fill in the configuration (see below)
4. Save

### 3. Connect Your MCP Client

Copy the endpoint URL and configure your MCP client.

---

## Configuration

### Simple Mode (One Tool)

| Field | Description |
|---|---|
| **Primary App** | Select your Dify app |
| **Primary App Type** | `Workflow` or `Chat` |
| **Primary Tool Schema (JSON)** | JSON schema describing the tool |
| **Auth Bearer Token** | Optional. If set, clients must include `Authorization: Bearer <token>` |

**Example schema:**

```json
{
  "name": "web_search",
  "title": "Web Search",
  "description": "Search the web for current information",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "The search query"
      }
    },
    "required": ["query"]
  }
}
```

### Multi-Tool Mode

Add additional tools via the **Additional Tools (JSON array)** field. Each tool must specify its own `_app_id` and `_app_type`.

```json
[
  {
    "name": "get_weather",
    "title": "Get Weather",
    "description": "Get current weather for a location",
    "inputSchema": {
      "type": "object",
      "properties": {
        "location": {"type": "string", "description": "City name"}
      },
      "required": ["location"]
    },
    "_app_id": "your-second-app-id",
    "_app_type": "workflow"
  }
]
```

---

## Client Setup Instructions

### Devin for Terminal

Add to `~/.devin/config.local.json`:

```json
{
  "mcpServers": {
    "dify-tools": {
      "url": "https://your-dify-instance/e/xxxxx/mcp",
      "transport": "http",
      "headers": {
        "Authorization": "Bearer your-token"
      }
    }
  }
}
```

Then run: `devin mcp login dify-tools` (if OAuth is configured) or just restart Devin.

### Windsurf

Add to Windsurf MCP settings (`~/.config/windsurf/mcp.json` or via Settings → MCP):

```json
{
  "mcpServers": {
    "dify-tools": {
      "url": "https://your-dify-instance/e/xxxxx/mcp",
      "transport": "http",
      "headers": {
        "Authorization": "Bearer your-token"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add -s user -t http dify-tools https://your-dify-instance/e/xxxxx/mcp -H "Authorization: Bearer your-token"
```

### Cursor

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "dify-tools": {
      "url": "https://your-dify-instance/e/xxxxx/mcp",
      "transport": "http",
      "headers": {
        "Authorization": "Bearer your-token"
      }
    }
  }
}
```

### OpenCode

Add to `~/.config/opencode/mcp.json`:

```json
{
  "mcpServers": {
    "dify-tools": {
      "url": "https://your-dify-instance/e/xxxxx/mcp",
      "transport": "http",
      "headers": {
        "Authorization": "Bearer your-token"
      }
    }
  }
}
```

### Generic MCP Client

Any client supporting Streamable HTTP transport:

```json
{
  "mcpServers": {
    "dify-tools": {
      "url": "https://your-dify-instance/e/xxxxx/mcp",
      "transport": "http"
    }
  }
}
```

---

## Protocol Support Matrix

| Feature | 2024-11-05 | 2025-03-26 | 2025-06-18 | 2025-11-25 |
|---|---|---|---|---|
| **Transport** | HTTP+SSE | Streamable HTTP | Streamable HTTP | Streamable HTTP |
| **tools/list** | Yes | Yes | Yes | Yes |
| **tools/call** | Yes | Yes | Yes | Yes |
| **resources/list** | Yes | Yes | Yes | Yes |
| **prompts/list** | Yes | Yes | Yes | Yes |
| **ping** | Yes | Yes | Yes | Yes |
| **shutdown** | Yes | Yes | Yes | Yes |
| **notifications/cancelled** | No | Yes | Yes | Yes |
| **completion/complete** | No | Yes | Yes | Yes |
| **roots/list** | No | Yes | Yes | Yes |
| **elicitation** | No | No | Yes | Yes |
| **Mcp-Session-Id** | No | Yes | Yes | Yes |
| **MCP-Protocol-Version header** | No | No | Yes | Yes |

---

## Security

- **Authentication**: Optional Bearer token with constant-time comparison (prevents timing attacks)
- **Session TTL**: Sessions expire after 30 minutes of inactivity (configurable)
- **Safe errors**: Internal errors never leak stack traces or sensitive data
- **Input validation**: All JSON-RPC requests are validated before processing
- **No arbitrary code execution**: The plugin only invokes pre-configured Dify apps through the SDK

### Best Practices

1. **Always use HTTPS** in production
2. **Set an auth token** for any publicly accessible endpoint
3. **Use the plugin within your private network** when possible
4. **Regularly rotate** your auth tokens

---

## Troubleshooting

### "Session not found or expired"

The client needs to re-initialize. This happens after 30 minutes of inactivity or if the server was restarted. Most MCP clients handle this automatically.

### "Method not found"

Your MCP client is using a method not supported by this server. Check the protocol version compatibility.

### Connection works with curl but not with Devin/Claude Code

Make sure:
1. The server returns `Mcp-Session-Id` header in the initialize response
2. The server returns `MCP-Protocol-Version` header
3. GET `/mcp` returns an SSE stream (not 405)
4. The protocol version negotiation is working

### Devin specific: "Legacy SSE is not supported"

Devin for Terminal does NOT support legacy SSE transport. Make sure you're using the `/mcp` endpoint (Streamable HTTP), not `/sse`.

---

## Changelog

### 1.0.0
- Complete rewrite of MCP protocol handling
- Full version negotiation (2024-11-05 through 2025-11-25)
- Session management with Mcp-Session-Id and MCP-Protocol-Version headers
- GET /mcp SSE streaming for server→client communication
- Multi-tool support (one endpoint = many tools)
- All MCP methods: tools, resources, prompts, ping, shutdown, cancellation, completion, roots, elicitation
- Security: constant-time token comparison, session TTL, safe error messages
- Backward compatible with legacy SSE transport

### 0.0.4
- Add response to the `ping` method
- Add `Authorization: Bearer` token validator
- Fix log formatting

### 0.0.3
- Fix SSE non-existent key error logs
- Add debug logging
- Streamable HTTP support for object and array responses

### 0.0.2
- Add Streamable HTTP protocol support
- Update dify-plugin-sdk version