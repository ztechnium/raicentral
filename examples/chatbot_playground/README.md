# RAI Chat Playground

A tiny, dependency-free Node.js chatbot (Node 18+) for testing every provider route of the RAI gateway:
Anthropic (Claude), OpenAI, Google AI, Vertex AI, Azure OpenAI, Ollama.

```bash
cd examples/chatbot_playground
cp .env.example .env        # set RAI_BASE_URL and RAI_RUNTIME_CREDENTIAL
npm start                   # http://localhost:3000
```

The credential can also be pasted in the UI. No `npm install` is needed.

## What you get
- Provider and model dropdowns, plus a free-text model/deployment field. Vertex has project and location fields, and Azure has an API version field.
- System prompt, temperature and max tokens.
- Multi-turn chat. Each reply shows HTTP status, latency, token usage and any `x-rai-*` / receipt headers. Expand a reply to see the exact gateway URL and the raw response.
- **Test provider** and **Test all** send a one-word prompt to check connectivity, auth, policy and the model catalog for each provider.
- A gateway health indicator, from `/v1/providers/_gateway/status`.

## Two approaches (the "Approach" dropdown)
- **Direct RAI API (fetch)**: the server builds the provider-native request and POSTs it to `<RAI_BASE_URL>/providers/<provider>/...`. No SDK involved.
- **Official provider SDK**: the same request goes through each provider's own SDK, pointed at the gateway with its base URL:
  Anthropic → `@anthropic-ai/sdk` (`<gw>/providers/anthropic`), OpenAI / Ollama / Azure OpenAI → `openai` (`<gw>/providers/<p>/v1`),
  Google AI / Vertex AI → `@google/genai` (`<gw>/providers/genai`, same as `examples/gemini_gateway_ts`).
  The runtime credential is sent as `x-rai-agent-key` via the SDK's default headers; the SDK API key is a dummy.
  Run `npm install` once for this mode. The direct approach needs no install. MCP tool calling works in both. The `@google/genai` SDK does not expose response headers, so no `x-rai-*` headers are shown for Google in SDK mode.

## How it works
The browser calls this server (`/api/chat`). The server builds the provider-native request, sends it to
`<RAI_BASE_URL>/providers/<provider>/...` with `x-rai-agent-key`, and returns the normalized reply.
Provider API keys never appear here. The gateway attaches its own.

## Reading failures
- **gateway unreachable**: check `RAI_BASE_URL` and that the gateway is running.
- **401/403**: bad runtime credential, or the model isn't allowed for that identity's agent version.
- **404 / model not found**: the model isn't registered in the RAI catalog for that provider. Use the custom model field.
- **502/upstream errors**: the gateway has no provider credential configured server-side (for example `GCP_PROJECT_ID` for Vertex).

## MCP tools tab
Connect an MCP server (for example GitHub) and list all its tools, so you can write tool rules against them.

- **Remote:** URL `https://api.githubcopilot.com/mcp/` plus a GitHub PAT as the bearer token.
- **Local (stdio):** command `npx`, args `-y @modelcontextprotocol/server-github`, env `GITHUB_PERSONAL_ACCESS_TOKEN=…`.
- **Name:** use the name your RAI catalog registers the MCP under (for example `github`). RAI matches rules on server and tool name.
- Each tool shows its name, its `server.tool` rule key, its description and its input schema. "Copy tool names" and "Copy JSON" export them.
- **Check with RAI** sends the call to `/v1/runtime/client-hooks/authorize`. This is the decision path tool rules use (pick the client type under "Check with RAI as"). It shows allow or block, the reason, and the exact payload.
- **Check & call** runs the tool only if RAI returns `allowed`. **Call directly** bypasses RAI, which is useful for comparison.

The MCP server and the tool must be declared on the agent version of the runtime identity whose credential you use. Otherwise RAI blocks the call with an "MCP not on agent version" reason. MCP tokens are kept in server memory only and are gone when the server restarts.

## Chat with MCP tools (zero-config)
On startup the server auto-connects:
- **`dummy`**: a built-in MCP server with no credentials and no side effects: `get_weather`, `calculator`, `read_file`, `create_issue`, `send_email`, `delete_file`, `transfer_money`. Safe tools and dangerous ones are mixed, so tool rules have something to allow or block. Disable it with `DUMMY_MCP=0`.
- **`github`**: only if `GITHUB_TOKEN` is set in `.env`. No other config is needed.

In the Chat tab, "Let the model use MCP tools" gives the model every connected tool. It names them `<server>__<tool>`. Each tool call the model makes is first sent to RAI (`/v1/runtime/client-hooks/authorize`) when "Authorize every tool call with RAI" is on. Allowed calls run. Blocked calls return the RAI reason to the model, and the reply shows an ALLOWED / BLOCKED badge with the decision for every call. Use the "Try:" buttons for quick prompts. For tool rules, use the `dummy.<tool>` or `github.<tool>` keys listed in the MCP tools tab.
