// Dummy MCP server (stdio, newline-delimited JSON-RPC). No credentials, no side effects:
// every tool returns canned data. Mix of read-only and "dangerous" tools so tool rules
// have something meaningful to allow, block or require approval for.
import readline from "node:readline";

const TOOLS = [
  { name: "get_weather", description: "Get the current weather for a city (read-only).",
    props: { city: { type: "string", description: "City name" } }, required: ["city"],
    run: ({ city }) => `Weather in ${city}: 22°C, sunny.` },
  { name: "calculator", description: "Evaluate a basic arithmetic expression (read-only).",
    props: { expression: { type: "string", description: "e.g. 12 * (3 + 4)" } }, required: ["expression"],
    run: ({ expression }) => {
      if (!/^[\d\s+\-*/().%]+$/.test(expression)) throw new Error("Only digits and + - * / ( ) % allowed");
      return String(Function(`"use strict";return (${expression})`)());
    } },
  { name: "read_file", description: "Read a file from the (fake) file system (read-only).",
    props: { path: { type: "string" } }, required: ["path"],
    run: ({ path }) => `Contents of ${path}: "quarterly numbers: 1,2,3"` },
  { name: "create_issue", description: "Create an issue in a repository (write, GitHub-like).",
    props: { repo: { type: "string" }, title: { type: "string" }, body: { type: "string" } }, required: ["repo", "title"],
    run: ({ repo, title }) => `Created issue #42 "${title}" in ${repo}` },
  { name: "send_email", description: "Send an email to a recipient (external side effect).",
    props: { to: { type: "string" }, subject: { type: "string" }, body: { type: "string" } }, required: ["to", "subject"],
    run: ({ to, subject }) => `Email "${subject}" sent to ${to}` },
  { name: "delete_file", description: "Permanently delete a file (destructive).",
    props: { path: { type: "string" } }, required: ["path"],
    run: ({ path }) => `Deleted ${path}` },
  { name: "transfer_money", description: "Transfer money to an account (financial, high risk).",
    props: { amount: { type: "number" }, to_account: { type: "string" } }, required: ["amount", "to_account"],
    run: ({ amount, to_account }) => `Transferred ${amount} to ${to_account}` },
];

const out = (id, result, error) =>
  console.log(JSON.stringify({ jsonrpc: "2.0", id, ...(error ? { error } : { result }) }));

readline.createInterface({ input: process.stdin }).on("line", (line) => {
  let m; try { m = JSON.parse(line); } catch { return; }
  if (m.id == null) return; // notification
  if (m.method === "initialize") {
    return out(m.id, { protocolVersion: m.params?.protocolVersion ?? "2025-03-26", capabilities: { tools: {} }, serverInfo: { name: "rai-dummy-mcp", version: "1.0.0" } });
  }
  if (m.method === "tools/list") {
    return out(m.id, { tools: TOOLS.map((t) => ({ name: t.name, description: t.description, inputSchema: { type: "object", properties: t.props, required: t.required } })) });
  }
  if (m.method === "tools/call") {
    const t = TOOLS.find((x) => x.name === m.params?.name);
    if (!t) return out(m.id, null, { code: -32602, message: `Unknown tool ${m.params?.name}` });
    try {
      return out(m.id, { content: [{ type: "text", text: t.run(m.params.arguments ?? {}) }] });
    } catch (e) {
      return out(m.id, { isError: true, content: [{ type: "text", text: e.message }] });
    }
  }
  out(m.id, null, { code: -32601, message: "Method not found" });
});
