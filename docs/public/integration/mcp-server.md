# RAI management MCP server

**Status:** Alpha

The control plane exposes a **Streamable HTTP** MCP endpoint with **OAuth 2.0 (PKCE)** so tools like Cursor can manage your RAI organization using your existing user session tokens.

## Endpoint

| Item | URL (local default) |
|------|---------------------|
| MCP transport | `http://127.0.0.1:8001/mcp` |
| OAuth authorization server metadata | `http://127.0.0.1:8001/mcp/.well-known/oauth-authorization-server` |
| Protected resource metadata (RFC 9728) | `http://127.0.0.1:8001/.well-known/oauth-protected-resource/mcp` |
| Browser login (OAuth step) | `http://127.0.0.1:8001/mcp/oauth/login` |

Configure the public base URL with `MCP_SERVER_PUBLIC_BASE_URL` when the API is behind a reverse proxy.

## Cursor configuration

Add an HTTP MCP server pointing at `/mcp` on your API host. Cursor will discover OAuth metadata, register a client (dynamic client registration is enabled by default), and open the browser login page using the same credentials as the control-plane UI.

Example `.cursor/mcp.json` fragment (URL only — OAuth is negotiated automatically):

```json
{
  "mcpServers": {
    "raicentral": {
      "type": "http",
      "url": "http://127.0.0.1:8001/mcp"
    }
  }
}
```

## Tools

The server exposes tools for:

- **Registry** — AI systems, agents, versions
- **Runtime identity** — identities, credentials, delegations
- **Resource catalog** — list with filters
- **Policy** — RAI rules, policy library, content rules, tool rules, budget rules
- **Evidence** — trajectories, audit explorer search
- **Risk** — overview, findings, monitors, monitor rules, signals

Each tool calls the same `/v1/*` management APIs as the UI, enforced with your OAuth bearer token (membership permissions apply).

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MCP_SERVER_ENABLED` | `true` | Set `false` to disable mounting |
| `MCP_SERVER_PUBLIC_BASE_URL` | `http://127.0.0.1:8001` | Issuer/resource URL host |
| `MCP_SERVER_MOUNT_PATH` | `/mcp` | Mount path |
| `MCP_OAUTH_ENABLE_DCR` | `true` | Dynamic client registration |
| `MCP_OAUTH_SCOPES` | `rai:read,rai:manage` | Advertised OAuth scopes |

## Security notes

- Tokens are the same JWTs issued by `/v1/auth/login`; governance permissions are not elevated for MCP.
- OAuth authorization codes and clients are held in memory (suitable for single-instance deployments). Use a shared store before multi-replica production.
- For production, set `MCP_SERVER_PUBLIC_BASE_URL` to your HTTPS API origin.