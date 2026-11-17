// RAI chatbot playground: zero-dependency Node server (Node 18+).
// Serves the UI and relays chat requests to the RAI provider gateway, so the
// browser never talks to the gateway directly (no CORS, credential stays server-side).
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { McpClient } from "./mcp.js";

const here = path.dirname(fileURLToPath(import.meta.url));

// Minimal .env loader (no dotenv dependency).
try {
  for (const line of fs.readFileSync(path.join(here, ".env"), "utf8").split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/i);
    if (m && !(m[1] in process.env)) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
} catch { /* no .env */ }

const PORT = Number(process.env.PORT ?? 3600);
const RAI_BASE_URL = (process.env.RAI_BASE_URL ?? "http://localhost:8001").replace(/\/+$/, "");
const ENV_CREDENTIAL = process.env.RAI_RUNTIME_CREDENTIAL ?? "";

// Preset models are suggestions only; the UI also accepts any custom model id.
// The model must be registered in the RAI catalog and allowed for the runtime identity.
const PROVIDERS = {
  anthropic: {
    label: "Anthropic (Claude)",
    models: ["claude-sonnet-4-5", "claude-opus-4-5", "claude-haiku-4-5"],
  },
  openai: {
    label: "OpenAI",
    models: ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"],
  },
  "google-ai": {
    label: "Google AI (Gemini API)",
    models: ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
  },
  "vertex-ai": {
    label: "Google Vertex AI",
    models: ["gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"],
    extra: {
      project: process.env.GCP_PROJECT_ID ?? "",
      location: process.env.GCP_LOCATION ?? "global",
    },
  },
  "azure-openai": {
    label: "Azure OpenAI",
    models: ["gpt-4o"], // deployment name
    extra: { apiVersion: process.env.AZURE_API_VERSION ?? "2024-10-21" },
  },
  ollama: {
    label: "Ollama (local)",
    models: ["llama3.2", "qwen2.5", "mistral"],
  },
};

/** Build the provider-native upstream request that the gateway understands. */
function buildRequest(p, { model, messages, system, temperature, maxTokens, extra = {} }) {
  const hasTemp = typeof temperature === "number" && !Number.isNaN(temperature);
  const toText = (m) => ({ role: m.role, content: m.content });

  switch (p) {
    case "openai":
    case "azure-openai":
    case "ollama": {
      const body = {
        messages: [...(system ? [{ role: "system", content: system }] : []), ...messages.map(toText)],
        ...(hasTemp ? { temperature } : {}),
      };
      if (p === "openai") {
        body.model = model;
        body.max_completion_tokens = maxTokens;
        return { path: "/providers/openai/v1/chat/completions", body, headers: {} };
      }
      if (p === "ollama") {
        body.model = model;
        body.max_tokens = maxTokens;
        return { path: "/providers/ollama/v1/chat/completions", body, headers: {} };
      }
      body.max_completion_tokens = maxTokens;
      const v = extra.apiVersion || "2024-10-21";
      return {
        path: `/providers/azure-openai/openai/deployments/${encodeURIComponent(model)}/chat/completions?api-version=${encodeURIComponent(v)}`,
        body,
        headers: { "api-version": v },
      };
    }
    case "anthropic":
      return {
        path: "/providers/anthropic/v1/messages",
        headers: { "anthropic-version": "2023-06-01" },
        body: {
          model,
          max_tokens: maxTokens,
          ...(system ? { system } : {}),
          ...(hasTemp ? { temperature } : {}),
          messages: messages.map(toText),
        },
      };
    case "google-ai":
    case "vertex-ai": {
      const body = {
        contents: messages.map((m) => ({
          role: m.role === "assistant" ? "model" : "user",
          parts: [{ text: m.content }],
        })),
        ...(system ? { systemInstruction: { parts: [{ text: system }] } } : {}),
        generationConfig: { maxOutputTokens: maxTokens, ...(hasTemp ? { temperature } : {}) },
      };
      if (p === "google-ai") {
        return { path: `/providers/google-ai/v1beta/models/${encodeURIComponent(model)}:generateContent`, body, headers: {} };
      }
      const project = extra.project || process.env.GCP_PROJECT_ID;
      const location = extra.location || "global";
      if (!project) throw new Error("Vertex AI needs a GCP project id (set it in the UI or GCP_PROJECT_ID).");
      return {
        path: `/providers/vertex-ai/v1beta1/projects/${encodeURIComponent(project)}/locations/${encodeURIComponent(location)}/publishers/google/models/${encodeURIComponent(model)}:generateContent`,
        body,
        headers: {},
      };
    }
    default:
      throw new Error(`Unknown provider: ${p}`);
  }
}

/** Pull the reply text and token usage out of each provider's response shape. */
function parseResponse(p, json) {
  try {
    if (p === "anthropic") {
      return {
        text: (json.content ?? []).filter((b) => b.type === "text").map((b) => b.text).join(""),
        usage: json.usage && { input: json.usage.input_tokens, output: json.usage.output_tokens },
      };
    }
    if (p === "google-ai" || p === "vertex-ai") {
      const parts = json.candidates?.[0]?.content?.parts ?? [];
      const u = json.usageMetadata;
      return {
        text: parts.map((x) => x.text ?? "").join(""),
        usage: u && { input: u.promptTokenCount, output: u.candidatesTokenCount, total: u.totalTokenCount },
      };
    }
    const u = json.usage;
    return {
      text: json.choices?.[0]?.message?.content ?? "",
      usage: u && { input: u.prompt_tokens, output: u.completion_tokens, total: u.total_tokens },
    };
  } catch {
    return { text: "", usage: null };
  }
}

async function callGateway(payload) {
  const { provider, credential } = payload;
  const key = credential || ENV_CREDENTIAL;
  if (!key) {
    return { ok: false, status: 400, error: "No runtime credential. Set RAI_RUNTIME_CREDENTIAL in .env or paste it in the UI." };
  }
  let req;
  try {
    req = buildRequest(provider, payload);
  } catch (e) {
    return { ok: false, status: 400, error: e.message };
  }
  const url = RAI_BASE_URL + req.path;
  const started = Date.now();
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", "x-rai-agent-key": key, ...req.headers },
      body: JSON.stringify(req.body),
      signal: AbortSignal.timeout(120_000),
    });
    const raw = await res.text();
    let json = null;
    try { json = JSON.parse(raw); } catch { /* non-JSON error body */ }
    const parsed = json && res.ok ? parseResponse(provider, json) : { text: "", usage: null };
    const raiHeaders = {};
    res.headers.forEach((v, k) => { if (/^x-rai|receipt|request-id|retry-after/i.test(k)) raiHeaders[k] = v; });
    return {
      ok: res.ok,
      status: res.status,
      latencyMs: Date.now() - started,
      url,
      text: parsed.text,
      usage: parsed.usage,
      headers: raiHeaders,
      error: res.ok ? undefined : (json?.error?.message ?? json?.detail?.message ?? (typeof json?.detail === "string" ? json.detail : null) ?? raw.slice(0, 1000)),
      raw: json ?? raw.slice(0, 4000),
    };
  } catch (e) {
    return {
      ok: false,
      status: 502,
      latencyMs: Date.now() - started,
      url,
      error: `Cannot reach gateway at ${RAI_BASE_URL}: ${e.cause?.code ?? e.message}`,
    };
  }
}

async function readJson(req) {
  let data = "";
  for await (const c of req) {
    data += c;
    if (data.length > 5_000_000) throw new Error("Body too large");
  }
  return data ? JSON.parse(data) : {};
}

// ---- MCP servers (in memory only; tokens are never written to disk) ----
const mcpServers = new Map(); // name -> { client, cfg, tools, info }

const mcpSummary = (name, s) => ({
  name,
  transport: s.cfg.transport,
  target: s.cfg.transport === "http" ? s.cfg.url : [s.cfg.command, ...(s.cfg.args ?? [])].join(" "),
  server: s.info,
  tools: s.tools,
  toolCount: s.tools.length,
});

async function connectMcp(b) {
  const name = String(b.name ?? "").trim();
  if (!/^[\w.-]+$/.test(name)) throw new Error("Server name: letters, digits, _ . - only (use the name your RAI catalog knows, e.g. github).");
  const transport = b.transport === "stdio" ? "stdio" : "http";
  const cfg = { transport };
  if (transport === "http") {
    if (!/^https?:\/\//.test(b.url ?? "")) throw new Error("Enter the MCP server URL (https://…).");
    cfg.url = b.url.trim();
    cfg.headers = { ...(b.headers ?? {}), ...(b.token ? { authorization: `Bearer ${b.token}` } : {}) };
  } else {
    if (!b.command?.trim()) throw new Error("Enter the command to launch the server.");
    cfg.command = b.command.trim();
    cfg.args = Array.isArray(b.args) ? b.args : String(b.args ?? "").split(/\s+/).filter(Boolean);
    cfg.env = b.env ?? {};
  }
  mcpServers.get(name)?.client.close();
  const client = new McpClient(cfg);
  try {
    await client.connect();
    const tools = await client.listTools();
    const entry = { client, cfg, tools, info: client.serverInfo };
    mcpServers.set(name, entry);
    return mcpSummary(name, entry);
  } catch (e) {
    client.close();
    throw e;
  }
}

/** Ask RAI whether this MCP tool call is allowed (the same endpoint the Cursor/Claude hooks use). */
async function raiAuthorize({ server, tool, args, credential, clientType, hookEvent }) {
  const key = credential || ENV_CREDENTIAL;
  if (!key) return { ok: false, status: 400, error: "No runtime credential (set RAI_RUNTIME_CREDENTIAL or paste it in the UI)." };
  const native_payload = clientType === "cursor"
    ? { command: server, tool_name: tool, tool_input: args }
    : { mcp_server_name: server, tool_name: tool, tool_input: args };
  try {
    const res = await fetch(`${RAI_BASE_URL}/v1/runtime/client-hooks/authorize`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-rai-agent-key": key },
      body: JSON.stringify({ client_type: clientType, hook_event: hookEvent, native_payload, correlation_id: `playground-${Date.now()}` }),
      signal: AbortSignal.timeout(30_000),
    });
    const body = await res.json().catch(() => null);
    return { ok: res.ok, status: res.status, sent: native_payload, decision: body, error: res.ok ? undefined : (body?.detail?.message ?? JSON.stringify(body?.detail ?? body)) };
  } catch (e) {
    return { ok: false, status: 502, error: `Cannot reach gateway at ${RAI_BASE_URL}: ${e.cause?.code ?? e.message}` };
  }
}

async function handleMcp(req, res, pathname) {
  const parts = pathname.split("/").filter(Boolean); // api, mcp, [name, [action]]
  const name = parts[2] ? decodeURIComponent(parts[2]) : null;
  const action = parts[3];

  if (!name && req.method === "GET") {
    return send(res, 200, { servers: [...mcpServers].map(([n, s]) => mcpSummary(n, s)) });
  }
  if (!name && req.method === "POST") {
    try { return send(res, 200, await connectMcp(await readJson(req))); }
    catch (e) { return send(res, 200, { error: e.message }); }
  }
  const s = name && mcpServers.get(name);
  if (!s) return send(res, 404, { error: `MCP server "${name}" is not connected` });

  if (req.method === "DELETE" && !action) {
    s.client.close(); mcpServers.delete(name);
    return send(res, 200, { ok: true });
  }
  if (req.method === "POST" && action === "refresh") {
    try { s.tools = await s.client.listTools(); return send(res, 200, mcpSummary(name, s)); }
    catch (e) { return send(res, 200, { error: e.message }); }
  }
  if (req.method === "POST" && action === "call") {
    const b = await readJson(req);
    const args = b.arguments ?? {};
    const out = { server: name, tool: b.tool };
    if (b.check) {
      out.rai = await raiAuthorize({
        server: name, tool: b.tool, args, credential: b.credential,
        clientType: b.clientType || "cursor", hookEvent: b.hookEvent || "beforeMCPExecution",
      });
      // Only run the tool if RAI explicitly allowed it (or the user asked to check only).
      if (b.execute === false || !out.rai.ok || out.rai.decision?.allowed !== true) {
        out.executed = false;
        return send(res, 200, out);
      }
    }
    const started = Date.now();
    try {
      out.result = await s.client.callTool(b.tool, args);
      out.executed = true;
    } catch (e) {
      out.executed = true;
      out.toolError = e.message;
    }
    out.latencyMs = Date.now() - started;
    return send(res, 200, out);
  }
  send(res, 404, { error: "not found" });
}

// ---- Chat with MCP tools: model <-> tool loop, each call optionally authorized by RAI ----
const MAX_TOOL_ROUNDS = 6;

/** Tools exposed to the model as "<server>__<tool>" (provider tool names allow [A-Za-z0-9_-], max 64). */
function exposedTools() {
  const list = [];
  for (const [server, s] of mcpServers) {
    for (const t of s.tools) {
      list.push({ exposed: `${server}__${t.name}`.replace(/[^\w-]/g, "_").slice(0, 64), server, tool: t.name, description: t.description ?? "", schema: t.inputSchema ?? { type: "object", properties: {} } });
    }
  }
  return list;
}

// Gemini's function schema accepts only a JSON-schema subset.
const geminiSchema = (s) => {
  if (Array.isArray(s)) return s.map(geminiSchema);
  if (!s || typeof s !== "object") return s;
  const drop = new Set(["$schema", "additionalProperties", "$id", "$ref", "$defs", "definitions", "default", "examples", "exclusiveMinimum", "exclusiveMaximum", "propertyNames", "patternProperties"]);
  return Object.fromEntries(Object.entries(s).filter(([k]) => !drop.has(k)).map(([k, v]) => [k, geminiSchema(v)]));
};

function withTools(p, req, native, tools, system) {
  const body = req.body;
  if (p === "anthropic") {
    body.messages = native;
    body.tools = tools.map((t) => ({ name: t.exposed, description: t.description, input_schema: t.schema }));
  } else if (p === "google-ai" || p === "vertex-ai") {
    body.contents = native;
    body.tools = [{ functionDeclarations: tools.map((t) => ({ name: t.exposed, description: t.description.slice(0, 1000), parameters: geminiSchema(t.schema) })) }];
  } else {
    body.messages = [...(system ? [{ role: "system", content: system }] : []), ...native];
    body.tools = tools.map((t) => ({ type: "function", function: { name: t.exposed, description: t.description, parameters: t.schema } }));
  }
}

function initialNative(p, messages) {
  if (p === "google-ai" || p === "vertex-ai") {
    return messages.map((m) => ({ role: m.role === "assistant" ? "model" : "user", parts: [{ text: m.content }] }));
  }
  return messages.map((m) => ({ role: m.role, content: m.content }));
}

/** -> { calls:[{id,name,args}], assistant: native assistant message to append } */
function extractCalls(p, json) {
  if (p === "anthropic") {
    const calls = (json.content ?? []).filter((b) => b.type === "tool_use").map((b) => ({ id: b.id, name: b.name, args: b.input ?? {} }));
    return { calls, assistant: { role: "assistant", content: json.content } };
  }
  if (p === "google-ai" || p === "vertex-ai") {
    const content = json.candidates?.[0]?.content ?? { role: "model", parts: [] };
    const calls = (content.parts ?? []).filter((x) => x.functionCall).map((x, i) => ({ id: `${x.functionCall.name}#${i}`, name: x.functionCall.name, args: x.functionCall.args ?? {} }));
    return { calls, assistant: { role: "model", parts: content.parts ?? [] } };
  }
  const msg = json.choices?.[0]?.message ?? { role: "assistant", content: "" };
  const calls = (msg.tool_calls ?? []).map((c) => {
    let args = {}; try { args = JSON.parse(c.function.arguments || "{}"); } catch { /* keep {} */ }
    return { id: c.id, name: c.function.name, args };
  });
  return { calls, assistant: msg };
}

function resultMessages(p, results) {
  if (p === "anthropic") {
    return [{ role: "user", content: results.map((r) => ({ type: "tool_result", tool_use_id: r.call.id, content: r.text, ...(r.isError ? { is_error: true } : {}) })) }];
  }
  if (p === "google-ai" || p === "vertex-ai") {
    return [{ role: "user", parts: results.map((r) => ({ functionResponse: { name: r.call.name, response: r.isError ? { error: r.text } : { result: r.text } } })) }];
  }
  return results.map((r) => ({ role: "tool", tool_call_id: r.call.id, content: r.text }));
}

const mcpText = (result) =>
  (result?.content ?? []).map((c) => (c.type === "text" ? c.text : `[${c.type}]`)).join("\n").slice(0, 8000) || JSON.stringify(result ?? {}).slice(0, 8000);

async function runToolCall(call, tools, opts) {
  const t = tools.find((x) => x.exposed === call.name);
  const trace = { server: t?.server, tool: t?.tool ?? call.name, args: call.args };
  if (!t) return { call, isError: true, text: `Unknown tool ${call.name}`, trace };
  if (opts.raiCheck) {
    trace.rai = await raiAuthorize({ server: t.server, tool: t.tool, args: call.args, credential: opts.credential, clientType: opts.clientType, hookEvent: opts.hookEvent });
    const d = trace.rai.decision;
    if (!trace.rai.ok || d?.allowed !== true) {
      trace.allowed = false;
      const why = trace.rai.ok ? `${d?.reason_code ?? "denied"}: ${d?.human_explanation ?? ""}` : trace.rai.error;
      return { call, isError: true, text: `Blocked by RAI policy. ${why}`.trim(), trace };
    }
  }
  trace.allowed = true;
  try {
    const r = await mcpServers.get(t.server).client.callTool(t.tool, call.args);
    trace.result = mcpText(r);
    return { call, isError: !!r?.isError, text: trace.result, trace };
  } catch (e) {
    trace.error = e.message;
    return { call, isError: true, text: `Tool failed: ${e.message}`, trace };
  }
}

async function chatWithTools(b) {
  const key = b.credential || ENV_CREDENTIAL;
  if (!key) return { ok: false, status: 400, error: "No runtime credential. Set RAI_RUNTIME_CREDENTIAL in .env or paste it in the UI." };
  const tools = exposedTools();
  const native = initialNative(b.provider, b.messages ?? []);
  const toolCalls = [];
  const usage = { input: 0, output: 0 };
  let last, totalMs = 0;
  for (let round = 0; round <= MAX_TOOL_ROUNDS; round++) {
    let req;
    try {
      req = buildRequest(b.provider, { ...b, messages: [] });
      withTools(b.provider, req, native, tools, b.system);
    } catch (e) { return { ok: false, status: 400, error: e.message, toolCalls }; }
    const url = RAI_BASE_URL + req.path;
    const started = Date.now();
    let res, raw;
    try {
      res = await fetch(url, { method: "POST", headers: { "content-type": "application/json", "x-rai-agent-key": key, ...req.headers }, body: JSON.stringify(req.body), signal: AbortSignal.timeout(120_000) });
      raw = await res.text();
    } catch (e) {
      return { ok: false, status: 502, url, toolCalls, error: `Cannot reach gateway at ${RAI_BASE_URL}: ${e.cause?.code ?? e.message}` };
    }
    totalMs += Date.now() - started;
    let json = null; try { json = JSON.parse(raw); } catch { /* error body */ }
    const headers = {}; res.headers.forEach((v, k) => { if (/^x-rai|receipt|request-id/i.test(k)) headers[k] = v; });
    last = { status: res.status, url, headers, raw: json ?? raw.slice(0, 4000) };
    if (!res.ok) {
      return { ok: false, ...last, latencyMs: totalMs, toolCalls, error: json?.error?.message ?? json?.detail?.message ?? (typeof json?.detail === "string" ? json.detail : null) ?? raw.slice(0, 1000) };
    }
    const parsed = parseResponse(b.provider, json);
    usage.input += parsed.usage?.input ?? 0; usage.output += parsed.usage?.output ?? 0;
    const { calls, assistant } = extractCalls(b.provider, json);
    if (!calls.length || round === MAX_TOOL_ROUNDS) {
      return { ok: true, ...last, latencyMs: totalMs, text: parsed.text || (calls.length ? "(stopped: too many tool rounds)" : ""), usage, toolCalls };
    }
    native.push(assistant);
    const results = [];
    for (const c of calls) { const r = await runToolCall(c, tools, b); results.push(r); toolCalls.push(r.trace); }
    native.push(...resultMessages(b.provider, results));
  }
}

const send = (res, status, obj) => {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(obj));
};

const server = http.createServer(async (req, res) => {
  try {
    const { pathname } = new URL(req.url, "http://x");
    if (req.method === "GET" && (pathname === "/" || pathname === "/index.html")) {
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      return res.end(fs.readFileSync(path.join(here, "public", "index.html")));
    }
    if (req.method === "GET" && pathname === "/api/config") {
      return send(res, 200, { gateway: RAI_BASE_URL, hasEnvCredential: !!ENV_CREDENTIAL, providers: PROVIDERS });
    }
    if (req.method === "GET" && pathname === "/api/health") {
      try {
        const r = await fetch(`${RAI_BASE_URL}/v1/providers/_gateway/status`, { signal: AbortSignal.timeout(5000) });
        return send(res, 200, { reachable: true, status: r.status, body: await r.json().catch(() => null) });
      } catch (e) {
        return send(res, 200, { reachable: false, error: e.cause?.code ?? e.message });
      }
    }
    if (req.method === "POST" && pathname === "/api/chat") {
      const b = await readJson(req);
      const payload = {
        provider: b.provider,
        model: b.model,
        messages: b.messages ?? [],
        system: b.system || "",
        temperature: b.temperature === "" || b.temperature == null ? undefined : Number(b.temperature),
        maxTokens: Number(b.maxTokens) || 1024,
        extra: b.extra ?? {},
        credential: b.credential,
      };
      if (b.useTools && mcpServers.size) {
        return send(res, 200, await chatWithTools({
          ...payload,
          raiCheck: b.raiCheck !== false,
          clientType: b.clientType || "cursor",
          hookEvent: b.hookEvent || "beforeMCPExecution",
        }));
      }
      return send(res, 200, await callGateway(payload));
    }
    if (pathname.startsWith("/api/mcp")) return await handleMcp(req, res, pathname);
    send(res, 404, { error: "not found" });
  } catch (e) {
    send(res, 500, { error: e.message });
  }
});

server.listen(PORT, () => {
  console.log(`RAI chatbot playground: http://localhost:${PORT}`);
  console.log(`Gateway: ${RAI_BASE_URL}  credential from env: ${ENV_CREDENTIAL ? "yes" : "no (paste in UI)"}`);
  autoConnectMcp();
});

/** Zero-config MCP servers: a credential-free dummy server, plus GitHub when GITHUB_TOKEN is set. */
async function autoConnectMcp() {
  const jobs = [];
  if (process.env.DUMMY_MCP !== "0") {
    jobs.push(["dummy", { name: "dummy", transport: "stdio", command: process.execPath, args: [path.join(here, "dummy-mcp.js")] }]);
  }
  if (process.env.GITHUB_TOKEN) {
    jobs.push(["github", { name: "github", transport: "http", url: process.env.GITHUB_MCP_URL ?? "https://api.githubcopilot.com/mcp/", token: process.env.GITHUB_TOKEN }]);
  }
  for (const [name, cfg] of jobs) {
    try {
      const s = await connectMcp(cfg);
      console.log(`MCP ${name}: connected, ${s.toolCount} tools`);
    } catch (e) {
      console.log(`MCP ${name}: failed (${e.message})`);
    }
  }
}
