// Minimal dependency-free MCP client: Streamable HTTP and stdio transports.
// Only what the playground needs: initialize, tools/list (paginated), tools/call.
import { spawn } from "node:child_process";

const PROTOCOL = "2025-03-26";
const CLIENT_INFO = { name: "rai-chat-playground", version: "0.1.0" };

export class McpClient {
  constructor(cfg) {
    this.cfg = cfg; // {transport:'http'|'stdio', url, headers, command, args, env}
    this.nextId = 1;
    this.sessionId = null;
    this.pending = new Map();
    this.proc = null;
    this.buf = "";
    this.serverInfo = null;
    this.stderr = "";
  }

  async connect() {
    if (this.cfg.transport === "stdio") this.#spawn();
    const res = await this.request("initialize", {
      protocolVersion: PROTOCOL,
      capabilities: {},
      clientInfo: CLIENT_INFO,
    });
    this.serverInfo = { ...res.serverInfo, protocolVersion: res.protocolVersion };
    await this.notify("notifications/initialized");
  }

  async listTools() {
    const tools = [];
    let cursor;
    do {
      const r = await this.request("tools/list", cursor ? { cursor } : {});
      tools.push(...(r.tools ?? []));
      cursor = r.nextCursor;
    } while (cursor && tools.length < 2000);
    return tools;
  }

  callTool(name, args) {
    return this.request("tools/call", { name, arguments: args ?? {} }, 120_000);
  }

  close() {
    for (const p of this.pending.values()) p.reject(new Error("closed"));
    this.pending.clear();
    if (this.proc) this.proc.kill();
    if (this.cfg.transport === "http" && this.sessionId) {
      fetch(this.cfg.url, { method: "DELETE", headers: this.#headers() }).catch(() => {});
    }
  }

  // ---- transport plumbing ----
  #headers() {
    return {
      ...(this.cfg.headers ?? {}),
      ...(this.sessionId ? { "mcp-session-id": this.sessionId } : {}),
      ...(this.serverInfo ? { "mcp-protocol-version": this.serverInfo.protocolVersion } : {}),
    };
  }

  #spawn() {
    const { command, args = [], env = {} } = this.cfg;
    const win = process.platform === "win32"; // shell needed there to resolve npx.cmd etc.
    const q = (s) => (win && /\s/.test(s) ? `"${s}"` : s);
    const opts = { env: { ...process.env, ...env }, stdio: ["pipe", "pipe", "pipe"] };
    // On Windows a shell is needed to resolve npx.cmd etc.; pass one command line to avoid the args+shell warning.
    this.proc = win ? spawn([q(command), ...args.map(q)].join(" "), { ...opts, shell: true }) : spawn(command, args, opts);
    this.proc.stdout.on("data", (d) => {
      this.buf += d;
      let i;
      while ((i = this.buf.indexOf("\n")) >= 0) {
        const line = this.buf.slice(0, i).trim();
        this.buf = this.buf.slice(i + 1);
        if (line) this.#dispatch(line);
      }
    });
    this.proc.stderr.on("data", (d) => { this.stderr = (this.stderr + d).slice(-2000); });
    const dead = (why) => {
      for (const p of this.pending.values()) p.reject(new Error(`${why}${this.stderr ? `: ${this.stderr.trim()}` : ""}`));
      this.pending.clear();
    };
    this.proc.on("error", (e) => dead(`cannot start "${command}" (${e.message})`));
    this.proc.on("exit", (code) => dead(`server exited (code ${code})`));
  }

  #dispatch(text) {
    let msg;
    try { msg = JSON.parse(text); } catch { return; }
    if (msg.id != null && this.pending.has(msg.id)) {
      const p = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      if (msg.error) p.reject(new Error(msg.error.message ?? JSON.stringify(msg.error)));
      else p.resolve(msg.result);
    }
  }

  notify(method, params) {
    const body = { jsonrpc: "2.0", method, ...(params ? { params } : {}) };
    if (this.cfg.transport === "stdio") {
      this.proc.stdin.write(JSON.stringify(body) + "\n");
      return Promise.resolve();
    }
    return fetch(this.cfg.url, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json, text/event-stream", ...this.#headers() },
      body: JSON.stringify(body),
    }).then(() => {});
  }

  request(method, params, timeoutMs = 30_000) {
    const id = this.nextId++;
    const body = { jsonrpc: "2.0", id, method, ...(params ? { params } : {}) };
    if (this.cfg.transport === "stdio") {
      return new Promise((resolve, reject) => {
        const t = setTimeout(() => { this.pending.delete(id); reject(new Error(`${method} timed out`)); }, timeoutMs);
        this.pending.set(id, {
          resolve: (v) => { clearTimeout(t); resolve(v); },
          reject: (e) => { clearTimeout(t); reject(e); },
        });
        this.proc.stdin.write(JSON.stringify(body) + "\n");
      });
    }
    return this.#httpRequest(id, body, timeoutMs);
  }

  async #httpRequest(id, body, timeoutMs) {
    const res = await fetch(this.cfg.url, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json, text/event-stream", ...this.#headers() },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
    const sid = res.headers.get("mcp-session-id");
    if (sid) this.sessionId = sid;
    if (!res.ok) {
      const t = await res.text();
      throw new Error(`HTTP ${res.status} from MCP server${t ? `: ${t.slice(0, 300)}` : ""}`);
    }
    const text = await res.text();
    const ctype = res.headers.get("content-type") ?? "";
    const messages = ctype.includes("text/event-stream")
      ? text.split(/\r?\n\r?\n/).map((ev) => ev.split(/\r?\n/).filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trimStart()).join("\n")).filter(Boolean)
      : [text];
    for (const m of messages) {
      let msg;
      try { msg = JSON.parse(m); } catch { continue; }
      if (msg.id === id) {
        if (msg.error) throw new Error(msg.error.message ?? JSON.stringify(msg.error));
        return msg.result;
      }
    }
    throw new Error("No response for request from MCP server");
  }
}
