/**
 * Call Gemini on Vertex AI through the RAI provider-compatible gateway using the
 * official `@google/genai` SDK.
 *
 * Only two things point at RAI: `setDefaultBaseUrls` (`<gateway>/providers/genai`)
 * and the RaiCentral runtime credential (sent as `x-rai-agent-key`). The application
 * chooses the project and location. The gateway authenticates the runtime, evaluates
 * policy, attaches its own Google service-account token, meters usage, and issues a
 * receipt. The application holds no Google credential.
 */
import { GoogleGenAI, setDefaultBaseUrls } from "@google/genai";
import dotenv from "dotenv";
dotenv.config();
const MODEL = process.env.GEMINI_MODEL;
const RAI_BASE_URL = (process.env.RAI_BASE_URL ?? "http://localhost:8001").replace(/\/+$/, "");
const RUNTIME_CREDENTIAL = process.env.RAI_RUNTIME_CREDENTIAL;
const PROJECT = process.env.GCP_PROJECT_ID ?? "lapeep-account-2";
// Gemini 3.x models are served from "global"; pick the region your model supports.
const LOCATION = process.env.GCP_LOCATION ?? "global";

if (!RUNTIME_CREDENTIAL) {
  console.error("Set RAI_RUNTIME_CREDENTIAL (your RaiCentral runtime credential).");
  process.exit(1);
}

// The SDK insists on Google auth in Vertex mode. The gateway supplies the real token,
// so give the SDK a no-op auth client instead of letting it look for local ADC.
const noGoogleAuth = { getRequestHeaders: async () => new Headers() };

// One base URL for every SDK client in this process. The gateway tells Vertex AI from the
// Gemini Developer API by the request path, so no per-client baseUrl is needed.
setDefaultBaseUrls({
  vertexUrl: `${RAI_BASE_URL}/providers/genai`,
  geminiUrl: `${RAI_BASE_URL}/providers/genai`,
});

const ai = new GoogleGenAI({
  vertexai: true,
  project: PROJECT,
  location: LOCATION,
  googleAuthOptions: { authClient: noGoogleAuth as never },
  httpOptions: { headers: { "x-rai-agent-key": RUNTIME_CREDENTIAL } },
});

const prompts =  {
  simple: "In one sentence, what does an AI governance gateway do?",
  contentRule: "I need to know how I can make RAI system gateway by ny self",
  hi: "hi"
}

async function nonStreaming(): Promise<void> {
  console.log("--- generateContent (non-stream) ---");
  const res = await ai.models.generateContent({
    model: MODEL,
    contents: prompts.hi,
    config: { maxOutputTokens: 200 },
  });
  console.log(res.text);
  console.log("usage:", res.usageMetadata);
}

// async function streaming(): Promise<void> {
//   console.log("\n--- generateContentStream (SSE) ---");
//   const stream = await ai.models.generateContentStream({
//     model: MODEL,
//     contents: "List three benefits of routing LLM calls through a gateway.",
//     config: { maxOutputTokens: 300 },
//   });
//   for await (const chunk of stream) {
//     process.stdout.write(chunk.text ?? "");
//   }
//   console.log();
// }

async function main(): Promise<void> {
  console.log(`Gateway: ${RAI_BASE_URL}/providers/vertex-ai  project: ${PROJECT}  location: ${LOCATION}  model: ${MODEL}\n`);
  try {
    await nonStreaming();
    // await streaming();
  } catch (err) {
    // Governance denials (model not allowed, budget, approval) and upstream errors land here.
    console.error("Request failed:", err instanceof Error ? err.message : err);
    process.exit(1);
  }
}

void main();
