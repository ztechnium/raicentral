/**
 * Call Gemini on Vertex AI through the RAI provider-compatible gateway (TypeScript).
 *
 * The app talks the Vertex AI REST protocol (no SDK, no Google credentials). Only two
 * things point at RAI: the base URL (`/providers/vertex-ai`) and the runtime credential.
 * The gateway authenticates the runtime, evaluates policy, attaches its own Google
 * service-account token, pins the governed project/location, meters usage, and issues
 * a receipt.
 */

const MODEL = process.env.GEMINI_MODEL ?? "gemini-3.1-flash-lite";
const RAI_BASE_URL = (process.env.RAI_BASE_URL ?? "http://localhost:8001").replace(/\/+$/, "");
const RUNTIME_CREDENTIAL = process.env.RAI_RUNTIME_CREDENTIAL || "rai_rt_17a87c6436d484e73082b429.9b11ZOGFZjGm8RLyNPcdVXKOcHTdvYJ6LO0i8_mzJF0";
// The gateway overrides these with its governed GCP_PROJECT_ID / GCP_LOCATION,
// but the Vertex path shape requires them.
const PROJECT = process.env.GCP_PROJECT_ID ?? "lapeep-account-2";
const LOCATION = process.env.GCP_LOCATION ?? "global";

if (!RUNTIME_CREDENTIAL) {
  console.error("Set RAI_RUNTIME_CREDENTIAL (your RaiCentral runtime credential).");
  process.exit(1);
}

const MODEL_URL =
  `${RAI_BASE_URL}/providers/vertex-ai/projects/${PROJECT}/locations/${LOCATION}` +
  `/publishers/google/models/${MODEL}`;

interface GenerateContentResponse {
  candidates?: { content?: { parts?: { text?: string }[] } }[];
  usageMetadata?: Record<string, unknown>;
}

function body(prompt: string, maxOutputTokens: number): string {
  return JSON.stringify({
    contents: [{ role: "user", parts: [{ text: prompt }] }],
    generationConfig: { maxOutputTokens },
  });
}

async function call(url: string, payload: string): Promise<Response> {
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-rai-agent-key": RUNTIME_CREDENTIAL!,
    },
    body: payload,
  });
  if (!res.ok) {
    // Governance denials (model not allowed, budget, approval) and upstream errors land here.
    throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  }
  return res;
}

function textOf(r: GenerateContentResponse): string {
  return (r.candidates?.[0]?.content?.parts ?? []).map((p) => p.text ?? "").join("");
}

async function nonStreaming(): Promise<void> {
  console.log("--- generateContent (non-stream) ---");
  const res = await call(
    `${MODEL_URL}:generateContent`,
    body("In one sentence, what does an AI governance gateway do?", 200),
  );
  console.log("receipt:", res.headers.get("x-rai-receipt-id"));
  const json = (await res.json()) as GenerateContentResponse;
  console.log(textOf(json));
  console.log("usage:", json.usageMetadata);
}

async function streaming(): Promise<void> {
  console.log("\n--- streamGenerateContent (SSE) ---");
  const res = await call(
    `${MODEL_URL}:streamGenerateContent?alt=sse`,
    body("List three benefits of routing LLM calls through a gateway.", 300),
  );
  const decoder = new TextDecoder();
  let buffer = "";
  for await (const chunk of res.body as unknown as AsyncIterable<Uint8Array>) {
    buffer += decoder.decode(chunk, { stream: true });
    let nl: number;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line.startsWith("data:")) continue;
      const data = line.slice(5).trim();
      if (!data || data === "[DONE]") continue;
      process.stdout.write(textOf(JSON.parse(data) as GenerateContentResponse));
    }
  }
  console.log();
}

async function main(): Promise<void> {
  console.log(`Gateway: ${RAI_BASE_URL}/providers/vertex-ai  model: ${MODEL}\n`);
  try {
    await nonStreaming();
    await streaming();
  } catch (err) {
    console.error("Request failed:", err instanceof Error ? err.message : err);
    process.exit(1);
  }
}

void main();
