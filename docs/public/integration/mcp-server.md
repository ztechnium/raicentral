# RAI management MCP server

**Status:** Alpha

The control plane exposes a **Streamable HTTP** MCP endpoint with **OAuth 2.0 (PKCE)** so tools like Cursor can manage your RAI organization using your existing user session tokens.

## Public URL

Every URL the server advertises is built from one public base URL:

1. `MCP_SERVER_PUBLIC_BASE_URL`, when set.
2. Otherwise `API_PUBLIC_BASE_URL`, when it is HTTPS or a loopback address. Docker Compose fills it from `NEXT_PUBLIC_API_BASE_URL`.
3. Otherwise `http://127.0.0.1:8001`.

OAuth requires HTTPS for anything other than `localhost` and `127.0.0.1`. An explicit non-loopback `http://` value stops the API at startup.

## Endpoints

`<base>` is the public base URL. `<origin>` is its scheme and host, and `<prefix>` its path (empty at the root, `/api` for `https://rai.example.com/api`).

| Item | URL |
|------|-----|
| MCP transport | `<base>/mcp` |
| Protected resource metadata (RFC 9728) | `<origin>/.well-known/oauth-protected-resource<prefix>/mcp` |
| Authorization server metadata (RFC 8414) | `<origin>/.well-known/oauth-authorization-server<prefix>/mcp` |
| Same metadata, path-appended | `<base>/mcp/.well-known/oauth-authorization-server` |
| Browser sign-in (OAuth step) | `<base>/mcp/oauth/login` |

### Reverse proxy

The proxy must:

- strip `<prefix>` before forwarding API requests;
- forward `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server` from the origin root to the API **unchanged**;
- send the public `Host` header (a request with an unknown host is rejected with `421`);
- not buffer responses (server-sent events).

[`deploy/nginx/raicentral.conf.example`](../../../deploy/nginx/raicentral.conf.example) does all four.

## Cursor configuration

Add an HTTP MCP server pointing at `<base>/mcp`. Cursor discovers the OAuth metadata, registers a client (dynamic client registration is enabled by default), and opens the browser sign-in page. Use the same credentials as the console.

```json
{
  "mcpServers": {
    "raicentral": {
      "type": "http",
      "url": "https://rai.example.com/api/mcp"
    }
  }
}
```

For local development the URL is `http://localhost:8001/mcp`.

## Check discovery

```bash
curl -i -X POST https://rai.example.com/api/mcp
# 401, with a WWW-Authenticate header naming the resource metadata URL

curl -fsS https://rai.example.com/.well-known/oauth-protected-resource/api/mcp
curl -fsS https://rai.example.com/.well-known/oauth-authorization-server/api/mcp
```

No response may mention `localhost` or `127.0.0.1`.

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
| `MCP_SERVER_PUBLIC_BASE_URL` | derived, see above | Issuer and resource base URL |
| `API_PUBLIC_BASE_URL` | from `NEXT_PUBLIC_API_BASE_URL` in Compose | Public API URL |
| `MCP_SERVER_MOUNT_PATH` | `/mcp` | Mount path |
| `MCP_OAUTH_ENABLE_DCR` | `true` | Dynamic client registration |
| `MCP_OAUTH_SCOPES` | `rai:read,rai:manage` | Advertised OAuth scopes |

## Security notes

- Tokens are the same JWTs issued by `/v1/auth/login`; governance permissions are not elevated for MCP.
- Refreshing a token rotates the underlying session. Sign-out of all sessions, a password change, or deactivation ends MCP access.
- OAuth authorization codes, registered clients and refresh tokens are held in memory. After an API restart, clients must register and sign in again. Run a single API process.
- A failed sign-in consumes the authorization request: start the sign-in again from the client.
- Dynamic client registration is open to anyone who can reach the API. Set `MCP_OAUTH_ENABLE_DCR=false` or `MCP_SERVER_ENABLED=false` if you do not use it.
